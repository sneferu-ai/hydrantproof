"""The /api/v1/ JSON API (DRF, JSON renderer only — no browsable API).

Route discipline (asserted by the route-table test): no PUT anywhere; PATCH
only on hydrant and parcel detail; DELETE only on overlays and the admin-only
attachment deletion.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from django.http import Http404
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiTypes, extend_schema

from . import constants as C
from .auth import CanWrite, IsAdmin
from .hydraulics import HydraulicsError, compute_flow_test
from .media import request_body_over_limit, upload_exceeds
from .models import (
    Attachment,
    Deficiency,
    DistrictSettings,
    FlowTest,
    Hydrant,
    HydrantStatusEvent,
    Inspection,
    OosAcknowledgement,
    Parcel,
)
from .serializers import (
    AttachmentSerializer,
    ComputePreviewSerializer,
    DeficiencyResolveSerializer,
    DeficiencySerializer,
    DistrictSettingsSerializer,
    FlowTestCreateSerializer,
    FlowTestSerializer,
    HydrantCreateSerializer,
    HydrantSerializer,
    HydrantStatusEventSerializer,
    HydrantUpdateSerializer,
    InspectionCreateSerializer,
    InspectionSerializer,
    OosAcknowledgementCreateSerializer,
    OosAcknowledgementSerializer,
    ParcelSerializer,
    ParcelUpdateSerializer,
    StatusEventCreateSerializer,
)
from .services import (
    LedgerError,
    canonical_payload_hash,
    create_flow_test,
    create_inspection,
    create_status_event,
    derive_hydrants,
    overdue_rows,
    params_from_settings,
    record_attribute_changes,
    record_parcel_changes,
    resolve_deficiency,
    values_differ,
)


def _ledger_error(exc: LedgerError) -> Response:
    payload = {"detail": exc.detail}
    if exc.field:
        payload = {exc.field: [exc.detail]}
    return Response(payload, status=exc.status)


class EditorWriteMixin:
    """POST/PATCH/DELETE require ``editor``+; GET requires authentication."""

    permission_classes = [CanWrite]

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            from rest_framework.permissions import IsAuthenticated

            return [IsAuthenticated()]
        return [CanWrite()]


# ---------------------------------------------------------------------------
# Hydrants
# ---------------------------------------------------------------------------
@extend_schema(request=HydrantCreateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantListCreateView(EditorWriteMixin, APIView):
    def get(self, request):
        queryset = Hydrant.objects.all().order_by("asset_tag")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        q = request.query_params.get("q")
        if q:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(asset_tag__icontains=q) | Q(location_description__icontains=q)
                | Q(ownership__icontains=q) | Q(source_org__icontains=q)
            )
        readiness = request.query_params.get("readiness")
        capacity = request.query_params.get("class")
        recency = request.query_params.get("recency")
        derived_filters = readiness or capacity or recency
        params = params_from_settings()
        if derived_filters:
            # Derived-field filters must evaluate every row (a bounded bulk
            # load — the scale envelope's worst case, taken only on demand).
            hydrants = list(queryset)
            derivations = derive_hydrants(hydrants, params)
            if readiness:
                hydrants = [h for h in hydrants if derivations[h.pk].readiness == readiness]
            if capacity:
                hydrants = [h for h in hydrants if derivations[h.pk].capacity_class == capacity]
            if recency:
                hydrants = [h for h in hydrants if derivations[h.pk].test_recency_flag == recency]
            return _paginated(request, hydrants, HydrantSerializer, {"derivations": derivations})
        # The common case pages at the database and derives the 50-row page
        # only — the current-test pointer means no window functions (§5), so a
        # 10,000-hydrant list stays inside its 300 ms envelope (§15).
        from rest_framework.pagination import PageNumberPagination

        paginator = PageNumberPagination()
        paginator.page_size = 50
        page = paginator.paginate_queryset(queryset, request)
        page_rows = list(page) if page is not None else list(queryset)
        derivations = derive_hydrants(page_rows, params)
        serializer = HydrantSerializer(page_rows, many=True, context={"derivations": derivations})
        if page is not None:
            return paginator.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def post(self, request):
        serializer = HydrantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if Hydrant.objects.filter(asset_tag=data["asset_tag"]).exists():
            return Response({"asset_tag": ["a hydrant with this asset_tag already exists"]},
                            status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        try:
            hydrant = _create_hydrant_with_initial_event(data, user)
        except IntegrityError:
            # The pre-check is a courtesy; the unique index is the truth.  A
            # concurrent duplicate create must answer the documented 400, not
            # an unhandled 500.
            return Response({"asset_tag": ["a hydrant with this asset_tag already exists"]},
                            status=status.HTTP_400_BAD_REQUEST)
        except LedgerError as exc:
            return _ledger_error(exc)
        derivations = derive_hydrants([hydrant])
        return Response(
            HydrantSerializer(hydrant, context={"derivations": derivations}).data,
            status=status.HTTP_201_CREATED,
        )


def _create_hydrant_with_initial_event(data, user) -> Hydrant:
    with transaction.atomic():
        hydrant = Hydrant.objects.create(
            asset_tag=data["asset_tag"],
            lat=data["lat"],
            lon=data["lon"],
            location_description=data.get("location_description", ""),
            manufacturer=data.get("manufacturer", ""),
            model=data.get("model", ""),
            year_installed=data.get("year_installed"),
            main_size_inches=data.get("main_size_inches"),
            outlet_diameter_in=data.get("outlet_diameter_in") or "2.50",
            discharge_coefficient=data.get("discharge_coefficient") or "0.90",
            ownership=data.get("ownership", ""),
            source=Hydrant.SOURCE_MANUAL,
            source_org=data.get("source_org", ""),
            notes=data.get("notes", ""),
            created_by=user if user.is_authenticated else None,
            updated_by=user if user.is_authenticated else None,
        )
        # Every creation path writes an attributed initial status event.
        create_status_event(
            hydrant=hydrant,
            to_status=data.get("initial_status", C.STATUS_IN_SERVICE),
            reason=data.get("initial_status_reason") or "Initial record",
            expected_return_on=data.get("expected_return_on"),
            indefinite=data.get("indefinite", False),
            effective_on=data.get("effective_on"),
            user=user,
            is_initial=True,
        )
    return hydrant

@extend_schema(request=HydrantUpdateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantDetailView(EditorWriteMixin, APIView):
    def _get(self, pk):
        return get_object_or_404(Hydrant, pk=pk)

    def get(self, request, pk):
        hydrant = self._get(pk)
        derivations = derive_hydrants([hydrant])
        return Response(HydrantSerializer(hydrant, context={"derivations": derivations}).data)

    def patch(self, request, pk):
        hydrant = self._get(pk)
        serializer = HydrantUpdateSerializer(hydrant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        changes = []
        for field, new_value in validated.items():
            old_value = getattr(hydrant, field)
            if field == "source_org":
                # Ownership transfer is never implicit: only an explicit PATCH
                # field changes source_org, audited as ownership_transfer.
                if values_differ(old_value, new_value):
                    changes.append((field, old_value, new_value))
                    hydrant.source_org = new_value
                continue
            if values_differ(old_value, new_value):
                changes.append((field, old_value, new_value))
                setattr(hydrant, field, new_value)
        with transaction.atomic():
            hydrant.updated_by = request.user if request.user.is_authenticated else None
            hydrant.save()
            # Per-field reasons (§10): a source_org value in an in-app PATCH is
            # always an ownership transfer, even when the same request also
            # changes other attributes; every other field is a manual change.
            for change in changes:
                record_attribute_changes(
                    hydrant, [change],
                    reason="ownership_transfer" if change[0] == "source_org" else "manual",
                    user=request.user,
                )
        derivations = derive_hydrants([hydrant])
        return Response(HydrantSerializer(hydrant, context={"derivations": derivations}).data)


# ---------------------------------------------------------------------------
# Flow tests
# ---------------------------------------------------------------------------
@extend_schema(request=FlowTestCreateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantTestsView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        # The queryset pages at the database (COUNT + LIMIT); materializing it
        # first would load every test of a 10,000-hydrant district into worker
        # memory to return fifty rows (§15).
        tests = hydrant.tests.order_by("-tested_on", "-created_at", "-id")
        return _paginated(request, tests, FlowTestSerializer, {})

    def post(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        serializer = FlowTestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payload_hash = canonical_payload_hash(serializer.canonical_payload())

        client_uuid = data.get("client_uuid")
        if client_uuid:
            existing = FlowTest.objects.filter(client_uuid=client_uuid).first()
            if existing is not None:
                if existing.client_payload_hash == payload_hash:
                    body = FlowTestSerializer(existing).data
                    body["duplicate_client_uuid"] = True
                    return Response(body, status=status.HTTP_200_OK)
                return Response(
                    {"detail": "client_uuid_conflict", "hint": "use a new client_uuid"},
                    status=status.HTTP_409_CONFLICT,
                )

        supersedes = None
        if data.get("supersedes"):
            supersedes = get_object_or_404(FlowTest, pk=data["supersedes"])
        try:
            test = create_flow_test(
                hydrant=hydrant,
                tested_on=data["tested_on"],
                static_psi=data["static_psi"],
                residual_psi=data["residual_psi"],
                outlets=[dict(o) for o in data.get("outlets", [])],
                measured_total_gpm=data.get("measured_total_gpm"),
                crew_names=data.get("crew_names", ""),
                notes=data.get("notes", ""),
                supersedes=supersedes,
                client_uuid=client_uuid,
                client_payload_hash=payload_hash,
                user=request.user,
            )
        except LedgerError as exc:
            return _ledger_error(exc)
        return Response(FlowTestSerializer(test).data, status=status.HTTP_201_CREATED)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class HydrantTestDetailView(EditorWriteMixin, APIView):
    """Read-only detail: PATCH/DELETE answer 405 (no update/delete routes)."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, pk, test_id):
        test = get_object_or_404(FlowTest, pk=test_id, hydrant_id=pk)
        return Response(FlowTestSerializer(test).data)


# ---------------------------------------------------------------------------
# Attachments (tests and inspections)
# ---------------------------------------------------------------------------
@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class AttachmentCollectionView(EditorWriteMixin, APIView):
    parser_classes = [MultiPartParser, FormParser]

    def _owner(self, pk, owner_id):
        raise NotImplementedError

    def get(self, request, pk, owner_id):
        owner = self._owner(pk, owner_id)
        attachments = Attachment.objects.filter(
            owner_type=owner[0], owner_id=owner[1]
        ).order_by("created_at", "id")
        return Response(AttachmentSerializer(attachments, many=True).data)

    def post(self, request, pk, owner_id):
        from .media import MediaError, store_attachment

        owner_type, owner_pk = self._owner(pk, owner_id)
        # Reject an oversized body before Django spools it: the declared
        # Content-Length covers the multipart envelope, then the spooled
        # upload's own size is checked before any byte is read into memory.
        if request_body_over_limit(request, C.MAX_ATTACHMENT_BYTES):
            return Response(
                {"detail": f"attachment exceeds {C.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": ["required"]})
        if upload_exceeds(upload, C.MAX_ATTACHMENT_BYTES):
            return Response(
                {"detail": f"attachment exceeds {C.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        client_meta = {
            "gps_accuracy_m": request.data.get("gps_accuracy_m"),
            "captured_at": request.data.get("captured_at"),
        }
        try:
            attachment = store_attachment(
                owner_type=owner_type,
                owner_id=owner_pk,
                data=upload.read(),
                user=request.user,
                client_meta=client_meta,
            )
        except MediaError as exc:
            return Response({"detail": exc.detail}, status=exc.status)
        return Response(AttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class TestAttachmentCollectionView(AttachmentCollectionView):
    def _owner(self, pk, owner_id):
        test = get_object_or_404(FlowTest, pk=owner_id, hydrant_id=pk)
        return Attachment.OWNER_FLOW_TEST, test.pk


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class InspectionAttachmentCollectionView(AttachmentCollectionView):
    def _owner(self, pk, owner_id):
        inspection = get_object_or_404(Inspection, pk=owner_id, hydrant_id=pk)
        return Attachment.OWNER_INSPECTION, inspection.pk


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class AttachmentDetailView(APIView):
    """GET serves the stored bytes to any authenticated user (§4: viewers read
    everything; §5: served only via authenticated FileResponse).  DELETE is
    admin-only and audited.

    The route names the owning record; the row must actually belong to it, so
    an attachment id cannot be reached through another hydrant's URL.
    """

    permission_classes = [IsAuthenticated]
    owner_type = ""
    owner_model = None

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [IsAdmin()]
        return [IsAuthenticated()]

    def _get(self, hydrant_pk, owner_id, att_id):
        attachment = get_object_or_404(Attachment, pk=att_id)
        if attachment.owner_type != self.owner_type or attachment.owner_id != owner_id:
            raise Http404("attachment does not belong to this record")
        if not self.owner_model.objects.filter(pk=owner_id, hydrant_id=hydrant_pk).exists():
            raise Http404("attachment does not belong to this hydrant")
        return attachment

    def get(self, request, pk, owner_id, att_id):
        from django.http import FileResponse

        attachment = self._get(pk, owner_id, att_id)
        try:
            stream = attachment.file.open("rb")
        except (FileNotFoundError, ValueError):
            # The row exists but the stored bytes are gone (media-volume loss)
            # — the same honest 410 the pack download answers, never a 500.
            return Response(
                {"detail": "stored file missing", "hint": "upload the attachment again"},
                status=status.HTTP_410_GONE,
            )
        response = FileResponse(stream, content_type=C.STORED_IMAGE_CONTENT_TYPE)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = f'inline; filename="{attachment.pk}.jpg"'
        return response

    def delete(self, request, pk, owner_id, att_id):
        from .media import admin_delete_attachment

        attachment = self._get(pk, owner_id, att_id)
        reason = request.data.get("reason", "") if isinstance(request.data, dict) else ""
        admin_delete_attachment(attachment, actor=request.user, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestAttachmentDetailView(AttachmentDetailView):
    owner_type = Attachment.OWNER_FLOW_TEST
    owner_model = FlowTest


class InspectionAttachmentDetailView(AttachmentDetailView):
    owner_type = Attachment.OWNER_INSPECTION
    owner_model = Inspection


# ---------------------------------------------------------------------------
# Inspections / deficiencies
# ---------------------------------------------------------------------------
@extend_schema(request=InspectionCreateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantInspectionsView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        # Stable sort key (newest first) so paginated results never reshuffle
        # between requests; the queryset pages at the database.
        inspections = hydrant.inspections.order_by("-inspected_on", "-created_at", "-id")
        return _paginated(request, inspections, InspectionSerializer, {})

    def post(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        serializer = InspectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            inspection = create_inspection(
                hydrant=hydrant,
                inspected_on=data["inspected_on"],
                result=data["result"],
                deficiency_codes=data.get("deficiency_codes", []),
                inspector_names=data.get("inspector_names", ""),
                notes=data.get("notes", ""),
                user=request.user,
            )
        except LedgerError as exc:
            return _ledger_error(exc)
        return Response(InspectionSerializer(inspection).data, status=status.HTTP_201_CREATED)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class HydrantDeficienciesView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        rows = hydrant.deficiencies.order_by("status", "id")
        status_filter = request.query_params.get("status")
        if status_filter:
            rows = rows.filter(status=status_filter)
        return Response(DeficiencySerializer(rows, many=True).data)


@extend_schema(request=DeficiencyResolveSerializer, responses=OpenApiTypes.OBJECT)
class DeficiencyResolveView(EditorWriteMixin, APIView):
    def post(self, request, pk, def_id):
        deficiency = get_object_or_404(Deficiency, pk=def_id, hydrant_id=pk)
        serializer = DeficiencyResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            deficiency = resolve_deficiency(
                deficiency=deficiency,
                resolution_note=data["resolution_note"],
                resolved_at=data.get("resolved_at"),
                user=request.user,
                assigned_to=data.get("assigned_to"),
                due_on=data.get("due_on"),
            )
        except LedgerError as exc:
            return _ledger_error(exc)
        return Response(DeficiencySerializer(deficiency).data)


# ---------------------------------------------------------------------------
# Status events / acknowledgements
# ---------------------------------------------------------------------------
@extend_schema(request=StatusEventCreateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantStatusEventsView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        events = hydrant.status_events.order_by("effective_on", "changed_at", "id")
        return Response(HydrantStatusEventSerializer(events, many=True).data)

    def post(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        serializer = StatusEventCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reversal = None
        if data.get("reversal_of"):
            reversal = get_object_or_404(HydrantStatusEvent, pk=data["reversal_of"], hydrant_id=pk)
        if reversal is None and not data.get("to_status"):
            raise ValidationError({"to_status": ["required"]})
        try:
            event = create_status_event(
                hydrant=hydrant,
                to_status=data.get("to_status", ""),
                reason=data.get("reason", ""),
                expected_return_on=data.get("expected_return_on"),
                indefinite=data.get("indefinite", False),
                effective_on=data.get("effective_on"),
                reversal_of=reversal,
                user=request.user,
            )
        except LedgerError as exc:
            return _ledger_error(exc)
        return Response(HydrantStatusEventSerializer(event).data, status=status.HTTP_201_CREATED)


@extend_schema(request=OosAcknowledgementCreateSerializer, responses=OpenApiTypes.OBJECT)
class HydrantOosAcknowledgementsView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        rows = hydrant.oos_acknowledgements.order_by("-acknowledged_at", "-id")
        return Response(OosAcknowledgementSerializer(rows, many=True).data)

    def post(self, request, pk):
        hydrant = get_object_or_404(Hydrant, pk=pk)
        serializer = OosAcknowledgementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # The feature's object is an *expired* out-of-service hydrant (§7):
        # acknowledging an in-service, retired, or not-yet-due hydrant would
        # append a meaningless row to an append-only table.
        derived = derive_hydrants([hydrant])[hydrant.pk]
        if not derived.oos_expired:
            return Response(
                {"detail": "acknowledgements apply only to an expired out-of-service hydrant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # An acknowledgement inside its 30-day amber window is returned as-is:
        # repeated acknowledgements must not restart the window and hold an
        # expired OOS hydrant amber forever — it re-surfaces red on schedule.
        latest = hydrant.oos_acknowledgements.order_by("-acknowledged_at", "-id").first()
        if latest is not None:
            ack_age = (timezone.localdate() - latest.acknowledged_at.date()).days
            if ack_age <= C.OOS_ACK_AMBER_DAYS:
                return Response(OosAcknowledgementSerializer(latest).data, status=status.HTTP_200_OK)
        ack = OosAcknowledgement.objects.create(
            hydrant=hydrant,
            acknowledged_by=request.user if request.user.is_authenticated else None,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(OosAcknowledgementSerializer(ack).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Preview / overdue / notifications / parcels
# ---------------------------------------------------------------------------
@extend_schema(request=ComputePreviewSerializer, responses=OpenApiTypes.OBJECT)
class ComputePreviewView(APIView):
    """The single authoritative hydraulics preview (no JS parity required)."""

    permission_classes = [CanWrite]

    def post(self, request):
        serializer = ComputePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from .hydraulics import OutletReadingInput

        try:
            computation = compute_flow_test(
                static_psi=float(data["static_psi"]),
                residual_psi=float(data["residual_psi"]),
                outlets=[
                    OutletReadingInput(
                        diameter_in=float(o["outlet_diameter_in"]),
                        coefficient=float(o["outlet_coefficient"]),
                        pitot_psi=float(o["pitot_psi"]),
                    )
                    for o in data.get("outlets", [])
                ],
                measured_total_gpm=float(data["measured_total_gpm"])
                if data.get("measured_total_gpm") is not None else None,
            )
        except HydraulicsError as exc:
            return Response({exc.field_name: [exc.message]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "computed_total_gpm": computation.computed_total_gpm,
            "pitot_sum_gpm": computation.pitot_sum_gpm,
            "rated_gpm_20": computation.rated_gpm_20,
            "capacity_class": computation.capacity_class,
            "warnings": list(computation.warnings),
            "formula_version": computation.formula_version,
            "extrapolation_factor": computation.extrapolation_factor,
            "outlet_gpm": list(computation.outlet_gpm),
        })


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class OverdueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        kind = request.query_params.get("kind", "test")
        if kind not in ("test", "inspection"):
            return Response({"detail": "kind must be 'test' or 'inspection'"},
                            status=status.HTTP_400_BAD_REQUEST)
        rows = overdue_rows(kind=kind)
        return Response({
            "kind": kind,
            "count": len(rows),
            "results": [
                {
                    "hydrant": hydrant.pk,
                    "asset_tag": hydrant.asset_tag,
                    "status": hydrant.status,
                    "days_overdue": days,
                    "readiness": derived.readiness,
                    "gating_flags": list(derived.gating_flags),
                    "last_tested_on": derived.latest_tested_on,
                    "last_inspected_on": derived.latest_inspected_on,
                }
                for hydrant, days, derived in rows
            ],
        })


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class NotificationsView(APIView):
    """Ranked live notification list (the outbox seam)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .notifications import live_notification_rows

        return Response({"results": live_notification_rows()})


@extend_schema(request=ParcelSerializer, responses=OpenApiTypes.OBJECT)
class ParcelListCreateView(EditorWriteMixin, APIView):
    def get(self, request):
        queryset = Parcel.objects.all().order_by("parcel_id")
        q = request.query_params.get("q")
        if q:
            from django.db.models import Q

            queryset = queryset.filter(Q(parcel_id__icontains=q) | Q(address__icontains=q))
        if request.query_params.get("protected") == "true":
            queryset = queryset.filter(is_protected_structure=True)
        # Page at the database: the scale envelope seeds 50,000 parcels and a
        # 50-row response must never materialize the whole table (§15).
        return _paginated(request, queryset, ParcelSerializer, {})

    def post(self, request):
        data = request.data
        serializer = ParcelSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        if Parcel.objects.filter(parcel_id=data.get("parcel_id")).exists():
            return Response({"parcel_id": ["a parcel with this parcel_id already exists"]},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            parcel = Parcel.objects.create(**serializer.validated_data)
        except IntegrityError:
            # Two concurrent creates with the same parcel_id both pass the
            # pre-check; the loser must answer 400, not an unhandled 500.
            return Response({"parcel_id": ["a parcel with this parcel_id already exists"]},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(ParcelSerializer(parcel).data, status=status.HTTP_201_CREATED)


@extend_schema(request=ParcelUpdateSerializer, responses=OpenApiTypes.OBJECT)
class ParcelDetailView(EditorWriteMixin, APIView):
    def get(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk)
        return Response(ParcelSerializer(parcel).data)

    def patch(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk)
        serializer = ParcelUpdateSerializer(parcel, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = []
        for field, new_value in serializer.validated_data.items():
            old_value = getattr(parcel, field)
            if values_differ(old_value, new_value):
                changes.append((field, old_value, new_value))
                setattr(parcel, field, new_value)
        with transaction.atomic():
            parcel.save()
            if changes:
                record_parcel_changes(parcel, changes, user=request.user)
        return Response(ParcelSerializer(parcel).data)


def _latest_complete_run():
    from .models import CoverageRun

    return (
        CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE)
        .order_by("-finished_at", "-id")
        .first()
    )


def _no_complete_run():
    return Response(
        {"detail": "No completed coverage run", "hint": "POST /api/v1/coverage-runs/"},
        status=status.HTTP_404_NOT_FOUND,
    )


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class ParcelCoverageView(APIView):
    """The parcel's rows in the latest COMPLETE run (viewer reads everything)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .models import CoverageResult
        from .serializers import CoverageResultSerializer

        parcel = get_object_or_404(Parcel, pk=pk)
        latest = _latest_complete_run()
        if latest is None:
            return _no_complete_run()
        rows = CoverageResult.objects.filter(run=latest, parcel=parcel).order_by("distance_ft", "id")
        return Response({
            "run": latest.pk,
            "snapshot_ts": latest.snapshot_ts,
            "results": CoverageResultSerializer(rows, many=True).data,
        })


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class HydrantCoverageView(APIView):
    """The hydrant's rows in the latest COMPLETE run — the parcels it serves."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .models import CoverageResult
        from .serializers import CoverageResultSerializer

        hydrant = get_object_or_404(Hydrant, pk=pk)
        latest = _latest_complete_run()
        if latest is None:
            return _no_complete_run()
        rows = (
            CoverageResult.objects.filter(run=latest, hydrant=hydrant)
            .select_related("parcel")
            .order_by("distance_ft", "id")
        )
        return Response({
            "run": latest.pk,
            "snapshot_ts": latest.snapshot_ts,
            "results": CoverageResultSerializer(rows, many=True).data,
        })


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class DistrictSettingsView(APIView):
    """Read the district singleton.  Mutations go through the Django admin,
    which writes a SettingsAudit row for every changed field (§5)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        district = DistrictSettings.load()
        return Response(DistrictSettingsSerializer(district).data)


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------
def _paginated(request, rows, serializer_class, context):
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 50
    page = paginator.paginate_queryset(rows, request)
    serializer = serializer_class(page, many=True, context=context)
    return paginator.get_paginated_response(serializer.data)
