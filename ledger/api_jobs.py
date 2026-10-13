"""Async job endpoints: coverage runs and evidence packs (specification §8/§9).

Both POSTs share one creation gate: a transaction takes
``pg_try_advisory_xact_lock(4242)`` (false → 409 — the loser never waits),
refuses while any run is RUNNING or pack RENDERING (single flight), inserts
the row and returns 202 + poll URL.  Job failures are not HTTP errors: the
poll endpoints answer 200 with ``status: "FAILED"`` + ``failure_reason`` +
``remediation``.
"""
from __future__ import annotations

from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiTypes, extend_schema

from . import constants as C
from .auth import CanWrite
from .coverage import ParcelIdsExceeded, normalize_parcel_filter, params_hash
from .executor import submit_coverage_run, submit_pack_render
from .models import CoverageResult, CoverageRun, DistrictSettings, EvidencePack, Hydrant, Parcel
from .models import settings_dump
from .serializers import CoverageResultSerializer, CoverageRunSerializer, EvidencePackSerializer

LOCK_NOT_ACQUIRED = {"detail": "A coverage or pack job is being created; retry."}
JOB_RUNNING = {"detail": "A coverage or pack job is already running."}

REMEDIATION = {
    C.FAILURE_PAIR_COUNT_EXCEEDED: (
        f"narrow the run with parcel_filter (parcel_ids ≤ {C.MAX_PARCEL_IDS:,} or a bbox)"
    ),
    C.FAILURE_MEDIA_BUDGET_EXCEEDED: (
        "disable include_attachments, prune attachments, or raise the media budget "
        "(manage.py prune_data / DistrictSettings.max_media_mb)"
    ),
    C.FAILURE_ORPHANED: "the job's lease expired (restart or crash); start a new run",
}

PACK_RATE_LIMIT_PER_MINUTE = 10


def _remediation(failure_reason: str) -> str:
    if not failure_reason:
        return ""
    for key, hint in REMEDIATION.items():
        if failure_reason.startswith(key):
            return hint
    return "inspect the server log for the failure detail; start a new job"


def _job_in_flight() -> bool:
    return (
        CoverageRun.objects.filter(status=C.RUN_STATUS_RUNNING).exists()
        or EvidencePack.objects.filter(status=C.PACK_STATUS_RENDERING).exists()
    )


def _paginate(request, queryset, serializer_class):
    """The API's uniform 50/page shape (§12: pagination everywhere)."""
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 50
    page = paginator.paginate_queryset(queryset, request)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


class _JobGateConflict(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload["detail"])
        self.payload = payload


def _all_sample_data() -> bool:
    return (
        not Hydrant.objects.filter(is_sample_data=False).exists()
        and not Parcel.objects.filter(is_sample_data=False).exists()
    )


def _insert_run_locked(*, parcel_filter, user) -> CoverageRun:
    """Insert the RUNNING row with frozen parameters.  Caller holds the lock."""
    district = DistrictSettings.load()
    echo = settings_dump(district)
    return CoverageRun.objects.create(
        status=C.RUN_STATUS_RUNNING,
        recency_days=district.recency_days,
        inspection_recency_days=district.inspection_recency_days,
        credit_radius_ft=district.credit_radius_ft,
        exclude_residual_below_target=district.exclude_residual_below_target,
        settings_echo=echo,
        parcel_filter=parcel_filter,
        params_sha256=params_hash(echo, parcel_filter),
        core_params_sha256=params_hash(echo, parcel_filter, core=True),
        created_by=user if getattr(user, "is_authenticated", False) else None,
        is_sample_data=_all_sample_data(),
    )


def create_coverage_run(*, parcel_filter, user) -> CoverageRun:
    """Insert the RUNNING row under the advisory lock; freeze parameters."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [C.JOB_ADVISORY_LOCK_KEY])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise _JobGateConflict(LOCK_NOT_ACQUIRED)
        if _job_in_flight():
            raise _JobGateConflict(JOB_RUNNING)
        run = _insert_run_locked(parcel_filter=parcel_filter, user=user)
    return run


# ---------------------------------------------------------------------------
# Coverage runs
# ---------------------------------------------------------------------------
@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
class CoverageRunListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [CanWrite()]

    def get(self, request):
        runs = CoverageRun.objects.order_by("-created_at", "-id")
        return _paginate(request, runs, CoverageRunSerializer)

    def post(self, request):
        try:
            parcel_filter = normalize_parcel_filter(request.data.get("parcel_filter"))
        except ParcelIdsExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        except ValueError as exc:
            return Response({"parcel_filter": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            run = create_coverage_run(parcel_filter=parcel_filter, user=request.user)
        except _JobGateConflict as conflict:
            return Response(conflict.payload, status=status.HTTP_409_CONFLICT)
        submit_coverage_run(run.pk)
        return Response(
            {"id": run.pk, "status": run.status,
             "poll_url": f"/api/v1/coverage-runs/{run.pk}/"},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class CoverageRunDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        run = get_object_or_404(CoverageRun, pk=pk)
        data = dict(CoverageRunSerializer(run).data)
        data["poll_url"] = f"/api/v1/coverage-runs/{run.pk}/"
        if run.status == C.RUN_STATUS_FAILED:
            data["remediation"] = _remediation(run.failure_reason)
        if run.status == C.RUN_STATUS_COMPLETE:
            from .staleness import coverage_staleness

            data["staleness"] = coverage_staleness(run)
        return Response(data)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class CoverageRunResultsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        run = get_object_or_404(CoverageRun, pk=pk)
        rows = (
            CoverageResult.objects.filter(run=run)
            .select_related("parcel", "hydrant")
            .order_by("parcel_id", "id")
        )
        parcel = request.query_params.get("parcel")
        if parcel:
            rows = rows.filter(parcel__parcel_id=parcel)
        from rest_framework.pagination import PageNumberPagination

        paginator = PageNumberPagination()
        paginator.page_size = 50
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response(CoverageResultSerializer(page, many=True).data)


# ---------------------------------------------------------------------------
# Evidence packs
# ---------------------------------------------------------------------------
@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
class EvidencePackListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [CanWrite()]

    def get(self, request):
        packs = EvidencePack.objects.order_by("-created_at", "-id")
        return _paginate(request, packs, EvidencePackSerializer)

    def post(self, request):
        run_id = request.data.get("run_id")
        raw_include = request.data.get("include_attachments", False)
        # bool("false") is True — parse the string forms honestly (form posts).
        include_attachments = (
            raw_include if isinstance(raw_include, bool)
            else str(raw_include).strip().lower() in ("1", "true", "yes", "on")
        )
        if run_id is not None:
            try:
                run_id = int(run_id)
            except (TypeError, ValueError):
                return Response({"run_id": ["must be an integer"]},
                                status=status.HTTP_400_BAD_REQUEST)

        since = timezone.now() - timezone.timedelta(minutes=1)
        recent = EvidencePack.objects.filter(
            generated_by=request.user, created_at__gte=since
        ).count()
        if recent >= PACK_RATE_LIMIT_PER_MINUTE:
            response = Response(
                {"detail": "evidence-pack rate limit: 10 per minute per user"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = "60"
            return response

        run = None
        if run_id is not None:
            run = CoverageRun.objects.filter(pk=run_id, status=C.RUN_STATUS_COMPLETE).first()
            if run is None:
                return Response(
                    {"detail": "run_id must reference a COMPLETE coverage run"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if run.hydrant_count == 0 or run.protected_parcel_count == 0:
                return Response(
                    {"detail": "the referenced run covers zero hydrants or zero protected "
                               "parcels; a pack needs both"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            if not Hydrant.objects.exists() or not Parcel.objects.filter(
                is_protected_structure=True
            ).exists():
                return Response(
                    {"detail": "no hydrants or no protected parcels; a pack needs both"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            # One advisory-locked transaction covers the fresh run (if any) and
            # the pack row, so the single-flight rule can never be raced.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [C.JOB_ADVISORY_LOCK_KEY])
                    acquired = cursor.fetchone()[0]
                if not acquired:
                    raise _JobGateConflict(LOCK_NOT_ACQUIRED)
                if _job_in_flight():
                    raise _JobGateConflict(JOB_RUNNING)
                if run is None:
                    run = _insert_run_locked(parcel_filter=None, user=request.user)
                pack = EvidencePack.objects.create(
                    run=run,
                    status=C.PACK_STATUS_RENDERING,
                    include_attachments=include_attachments,
                    generated_by=request.user if request.user.is_authenticated else None,
                    is_sample_data=run.is_sample_data,
                )
        except _JobGateConflict as conflict:
            return Response(conflict.payload, status=status.HTTP_409_CONFLICT)
        submit_pack_render(pack.pk)
        return Response(
            {"id": pack.pk, "run_id": run.pk, "status": pack.status,
             "poll_url": f"/api/v1/evidence-packs/{pack.pk}/"},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class EvidencePackDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        pack = get_object_or_404(EvidencePack, pk=pk)
        data = dict(EvidencePackSerializer(pack).data)
        data["poll_url"] = f"/api/v1/evidence-packs/{pack.pk}/"
        if pack.status == C.PACK_STATUS_FAILED:
            data["remediation"] = _remediation(pack.failure_reason)
        if pack.status == C.PACK_STATUS_COMPLETE:
            data["zip_url"] = f"/api/v1/evidence-packs/{pack.pk}/zip/"
            data["pdf_url"] = f"/api/v1/evidence-packs/{pack.pk}/pdf/"
        return Response(data)


class _PackDownloadView(APIView):
    """Serve the stored bytes — never regenerate (§9)."""

    permission_classes = [IsAuthenticated]
    field = ""
    content_type = ""
    filename = ""

    def get(self, request, pk):
        from django.http import FileResponse

        pack = EvidencePack.objects.filter(pk=pk).first()
        if pack is None:
            # A pruned pack's row is gone: 404 with the hint.
            return Response(
                {"detail": "pack pruned", "hint": "generate a new pack"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if pack.status == C.PACK_STATUS_RENDERING:
            return Response(
                {"detail": "pack is still rendering", "hint": f"poll /api/v1/evidence-packs/{pack.pk}/"},
                status=status.HTTP_409_CONFLICT,
            )
        relative = getattr(pack, self.field)
        if pack.status == C.PACK_STATUS_FAILED or not relative:
            return Response(
                {"detail": "stored file missing", "hint": "generate a new pack"},
                status=status.HTTP_410_GONE,
            )
        from .media import media_root

        path = media_root() / relative
        if not path.exists():
            return Response(
                {"detail": "stored file missing", "hint": "generate a new pack"},
                status=status.HTTP_410_GONE,
            )
        from .exports import audit_export

        audit_export(
            path=f"/api/v1/evidence-packs/{pack.pk}/{self.filename.rsplit('.', 1)[-1]}/",
            kind=f"pack_{self.field}", row_count=1, user=request.user,
        )
        handle = path.open("rb")
        response = FileResponse(handle, content_type=self.content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = f'attachment; filename="hydrantproof-pack-{pack.pk}-{self.filename}"'
        return response


class PackZipDownloadView(_PackDownloadView):
    field = "zip_path"
    content_type = "application/zip"
    filename = "pack.zip"


class PackPdfDownloadView(_PackDownloadView):
    field = "pdf_path"
    content_type = "application/pdf"
    filename = "pack.pdf"
