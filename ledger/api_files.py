"""File-backed endpoints: GeoJSON overlays, two-phase CSV imports, and the
ledger export download (specification §10/§11/§12).

Overlays: one active overlay; upload replaces (the old file is deleted);
validation rejects bad JSON / non-FeatureCollection / oversize / too many
features / out-of-range coordinates / disallowed geometry with 400 naming the
first offending feature index.

Imports: ``mode=dry_run`` validates the whole file, stores it under
``media/imports/{batch_id}.csv``, and returns the row report plus a one-shot
``commit_token`` (stored only as SHA-256, user-bound, 24 h expiry).
``mode=commit`` either reuses the stored dry-run file or verifies a
re-upload's SHA-256 against the batch (``file_hash_mismatch`` → 400), then
re-validates and applies passing rows — invalid rows are skipped with
``{line, field, message}`` errors, never silently applied.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiTypes, extend_schema

from . import constants as C
from .auth import CanWrite, IsAdmin
from .imports import ImportError_
from .media import (
    media_budget_bytes,
    media_bytes_total,
    media_root,
    request_body_over_limit,
    upload_exceeds,
)
from .models import ImportBatch, MapOverlay
from .serializers import ImportBatchSerializer, MapOverlaySerializer

ALLOWED_GEOMETRY_TYPES = {
    "Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon",
}


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------
class OverlayError(Exception):
    def __init__(self, detail: str, status_code: int = 400, feature_index: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.feature_index = feature_index


def validate_overlay_geojson(data: bytes) -> dict:
    """Parse and validate the upload; returns the decoded FeatureCollection."""
    if len(data) > C.MAX_OVERLAY_BYTES:
        raise OverlayError(
            f"overlay exceeds {C.MAX_OVERLAY_BYTES // (1024 * 1024)} MB", status_code=413
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise OverlayError("overlay is not valid UTF-8 JSON") from None
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise OverlayError("overlay must be a GeoJSON FeatureCollection")
    features = document.get("features")
    if not isinstance(features, list):
        raise OverlayError("overlay features must be an array")
    if len(features) > C.MAX_OVERLAY_FEATURES:
        raise OverlayError(
            f"overlay may carry at most {C.MAX_OVERLAY_FEATURES:,} features", status_code=413
        )
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise OverlayError("feature is not a GeoJSON Feature", feature_index=index)
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise OverlayError("feature has no geometry object", feature_index=index)
        gtype = geometry.get("type")
        if gtype not in ALLOWED_GEOMETRY_TYPES:
            raise OverlayError(f"geometry type {gtype!r} is not allowed", feature_index=index)
        _validate_coordinates(geometry.get("coordinates"), index)
    return document


def _validate_coordinates(coords, index: int, depth: int = 0) -> None:
    """Every coordinate pair: |lat| ≤ 80, |lon| ≤ 180 (GeoJSON is lon-first)."""
    if not isinstance(coords, list) or not coords:
        raise OverlayError("geometry coordinates must be a non-empty array", feature_index=index)
    if depth > 8:  # nesting beyond MultiPolygon's shape is malformed
        raise OverlayError("geometry coordinates nested too deeply", feature_index=index)
    first = coords[0]
    if isinstance(first, (int, float)) and not isinstance(first, bool):
        if len(coords) < 2:
            raise OverlayError("coordinate pair needs longitude and latitude", feature_index=index)
        lon, lat = coords[0], coords[1]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise OverlayError("coordinates must be numbers", feature_index=index)
        if not (-180.0 <= lon <= 180.0) or not (-80.0 <= lat <= 80.0):
            raise OverlayError(
                f"coordinate out of range (|lat| ≤ 80, |lon| ≤ 180): [{lon}, {lat}]",
                feature_index=index,
            )
        return
    for child in coords:
        _validate_coordinates(child, index, depth + 1)


@extend_schema(request=OpenApiTypes.BINARY, responses=OpenApiTypes.OBJECT)
class OverlayCollectionView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [CanWrite()]

    def get(self, request):
        overlays = MapOverlay.objects.order_by("-uploaded_at", "-id")[:50]
        return Response({"results": MapOverlaySerializer(overlays, many=True).data})

    def post(self, request):
        # Pre-parse guard: an oversized body is rejected before Django spools
        # it; the spooled upload's exact size is checked before any read.
        if request_body_over_limit(request, C.MAX_OVERLAY_BYTES):
            return Response(
                {"detail": f"overlay exceeds {C.MAX_OVERLAY_BYTES // (1024 * 1024)} MB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": ["required"]}, status=status.HTTP_400_BAD_REQUEST)
        if upload_exceeds(upload, C.MAX_OVERLAY_BYTES):
            return Response(
                {"detail": f"overlay exceeds {C.MAX_OVERLAY_BYTES // (1024 * 1024)} MB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        data = upload.read()
        try:
            document = validate_overlay_geojson(data)
        except OverlayError as exc:
            payload = {"detail": exc.detail}
            if exc.feature_index is not None:
                payload["feature_index"] = exc.feature_index
            return Response(payload, status=exc.status_code)
        # Overlays count against the single media budget (§2-18) — but an
        # upload REPLACES the active overlay(s), so the budget is judged on
        # the post-replacement net: current total - replaced bytes + new bytes.
        old_overlays = list(MapOverlay.objects.all())
        reclaimed = 0
        for old in old_overlays:
            if not old.geojson_path:
                continue
            try:
                reclaimed += (media_root() / old.geojson_path).stat().st_size
            except OSError:  # row without its file: nothing to reclaim
                continue
        if media_bytes_total() - reclaimed + len(data) > media_budget_bytes():
            return Response(
                {"detail": "media budget exceeded"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        name = (request.data.get("name") or getattr(upload, "name", "") or "overlay")[:128]
        with transaction.atomic():
            # One active overlay, upload replaces — but the NEW bytes land
            # before the old ones are deleted: a failed write leaves the
            # previous overlay fully intact (rollback covers the row; files
            # are only deleted after their replacement exists).
            overlay = MapOverlay.objects.create(
                name=name,
                geojson_path="",
                feature_count=len(document["features"]),
                byte_size=len(data),
                uploaded_by=request.user if request.user.is_authenticated else None,
            )
            relative = f"overlays/{overlay.pk}.geojson"
            target = media_root() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)  # raises → transaction rolls back, old overlay intact
            overlay.geojson_path = relative
            overlay.save(update_fields=["geojson_path"])
            for old in old_overlays:
                _delete_overlay_file(old)
                old.delete()
        return Response(MapOverlaySerializer(overlay).data, status=status.HTTP_201_CREATED)


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class OverlayActiveView(APIView):
    """The active overlay's bytes, served as application/geo+json."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        overlay = MapOverlay.objects.order_by("-uploaded_at", "-id").first()
        if overlay is None or not overlay.geojson_path:
            return Response({"detail": "no active overlay"}, status=status.HTTP_404_NOT_FOUND)
        path = media_root() / overlay.geojson_path
        if not path.exists():
            return Response(
                {"detail": "stored file missing", "hint": "upload the overlay again"},
                status=status.HTTP_410_GONE,
            )
        response = FileResponse(path.open("rb"), content_type="application/geo+json")
        response["X-Content-Type-Options"] = "nosniff"
        return response


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class OverlayDetailView(APIView):
    """GET is viewer-readable metadata; DELETE is editor+ (one permission
    rule for upload and delete, §11)."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [CanWrite()]

    def get(self, request, pk):
        from django.shortcuts import get_object_or_404

        overlay = get_object_or_404(MapOverlay, pk=pk)
        return Response(MapOverlaySerializer(overlay).data)

    def delete(self, request, pk):
        overlay = MapOverlay.objects.filter(pk=pk).first()
        if overlay is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        _delete_overlay_file(overlay)
        overlay.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _delete_overlay_file(overlay: MapOverlay) -> None:
    if not overlay.geojson_path:
        return
    path = media_root() / overlay.geojson_path
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - racing prune
        pass


# ---------------------------------------------------------------------------
# Two-phase CSV imports
# ---------------------------------------------------------------------------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@extend_schema(request=OpenApiTypes.BINARY, responses=OpenApiTypes.OBJECT)
class ImportView(APIView):
    """``POST /api/v1/imports/{kind}/`` — dry_run then commit (§10)."""

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [CanWrite]
    kind = ""

    def post(self, request, *args, **kwargs):
        from .imports import apply_import, validate_import

        # Pre-parse guard: reject a declared-oversized body before Django
        # spools it (the import lane's memory/disk door).
        if request_body_over_limit(request, C.MAX_UPLOAD_BYTES):
            return Response(
                {"detail": f"upload exceeds {C.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        mode = (request.data.get("mode") or "").strip().lower()
        if mode not in ("dry_run", "commit"):
            return Response(
                {"mode": ["must be dry_run or commit"]}, status=status.HTTP_400_BAD_REQUEST
            )
        upload = request.FILES.get("file")
        supplier = (request.data.get("supplier") or "")[:128]
        raw_allow_overwrite = request.data.get("allow_overwrite")
        allow_overwrite = str(raw_allow_overwrite or "").lower() in ("1", "true", "yes")

        if upload is not None:
            if upload_exceeds(upload, C.MAX_UPLOAD_BYTES):
                return Response(
                    {"detail": f"upload exceeds {C.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            data = upload.read()
            if len(data) > C.MAX_UPLOAD_BYTES:
                return Response(
                    {"detail": f"upload exceeds {C.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        else:
            data = None

        if mode == "dry_run":
            if data is None:
                return Response({"file": ["required for dry_run"]}, status=status.HTTP_400_BAD_REQUEST)
            return self._dry_run(data, getattr(upload, "name", "upload.csv"), supplier,
                                 allow_overwrite, request, validate_import)
        return self._commit(data, supplier, raw_allow_overwrite, request,
                            validate_import, apply_import)

    # ------------------------------------------------------------------
    def _dry_run(self, data, filename, supplier, allow_overwrite, request, validate_import):
        if media_bytes_total() + len(data) > media_budget_bytes():
            return Response(
                {"detail": "media budget exceeded"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        try:
            report = validate_import(self.kind, data)
        except ImportError_ as exc:
            return Response({"detail": exc.detail, **exc.extra}, status=exc.status_code)
        token = secrets.token_urlsafe(32)
        # The batch row and its stored file commit together: a failed file
        # write must not leave an orphan row with a live commit token behind.
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                kind=self.kind,
                filename=(filename or "upload.csv")[:255],
                file_sha256=hashlib.sha256(data).hexdigest(),
                file_path="",
                supplier=supplier,
                mode=ImportBatch.MODE_DRY_RUN,
                commit_token_hash=_hash_token(token),
                allow_overwrite=allow_overwrite,
                rows_ok=report["rows_ok"],
                rows_rejected=report["rows_rejected"],
                errors=report["errors"],
                created_by=request.user if request.user.is_authenticated else None,
            )
            relative = f"imports/{batch.pk}.csv"
            target = media_root() / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError:
                # Roll the row back and remove the partial file: the dry run
                # either exists completely or not at all.
                try:
                    target.unlink(missing_ok=True)
                except OSError:  # pragma: no cover - cleanup is best-effort
                    pass
                raise
            batch.file_path = relative
            batch.save(update_fields=["file_path"])
        return Response(
            {
                "batch": ImportBatchSerializer(batch).data,
                "commit_token": token,  # returned exactly once; stored hashed
                "rows_ok": report["rows_ok"],
                "rows_rejected": report["rows_rejected"],
                "errors": report["errors"],
                "warnings": report.get("warnings", []),
            },
            status=status.HTTP_200_OK,
        )

    def _commit(self, data, supplier, raw_allow_overwrite, request, validate_import, apply_import):
        token = request.data.get("commit_token") or ""
        batch = (
            ImportBatch.objects.filter(
                kind=self.kind,
                mode=ImportBatch.MODE_DRY_RUN,
                commit_token_hash=_hash_token(token),
            ).first()
            if token
            else None
        )
        if (
            batch is None
            or batch.token_expired()
            or batch.created_by_id != request.user.pk  # user-bound, single-use
        ):
            return Response({"detail": "commit_token invalid"}, status=status.HTTP_400_BAD_REQUEST)

        # The commit applies exactly what the dry run validated and reported.
        # A different supplier or a switched-on allow_overwrite would change
        # the conflict context the previewed report was computed under — that
        # requires a fresh dry run (a new token) under the new flags, never a
        # silent divergence from the operator's preview.
        if supplier and supplier != batch.supplier:
            return Response(
                {"detail": "commit_context_mismatch",
                 "hint": "the supplier differs from the dry run; run a new dry run"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if raw_allow_overwrite not in (None, ""):
            commit_allow = str(raw_allow_overwrite).lower() in ("1", "true", "yes")
            if commit_allow != bool(batch.allow_overwrite):
                return Response(
                    {"detail": "commit_context_mismatch",
                     "hint": "allow_overwrite differs from the dry run; run a new dry run"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if data is None:
            # Reuse the stored dry-run file (the hash binding's purpose).
            path = media_root() / batch.file_path
            if not batch.file_path or not path.exists():
                return Response(
                    {"detail": "stored import file missing; upload the file again"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            data = path.read_bytes()
        else:
            actual = hashlib.sha256(data).hexdigest()
            if actual != batch.file_sha256:
                return Response(
                    {"detail": "file_hash_mismatch",
                     "expected": batch.file_sha256, "actual": actual},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            report = validate_import(self.kind, data)  # commit re-validates everything
        except ImportError_ as exc:
            return Response({"detail": exc.detail, **exc.extra}, status=exc.status_code)
        with transaction.atomic():
            outcome = apply_import(
                self.kind, report["rows"], batch=batch, user=request.user,
            )
            batch.mode = ImportBatch.MODE_COMMIT
            batch.rows_ok = outcome["rows_ok"]
            batch.rows_rejected = outcome["rows_rejected"]
            batch.errors = outcome["errors"]
            batch.committed_at = timezone.now()
            batch.commit_token_hash = ""  # single-use: the token is spent
            batch.save(update_fields=[
                "mode", "rows_ok", "rows_rejected", "errors", "committed_at",
                "commit_token_hash",
            ])
        return Response(
            {
                "batch": ImportBatchSerializer(batch).data,
                "rows_ok": outcome["rows_ok"],
                "rows_rejected": outcome["rows_rejected"],
                "errors": outcome["errors"],
                "warnings": outcome.get("warnings", []),
            },
            status=status.HTTP_200_OK,
        )


class HydrantImportView(ImportView):
    kind = ImportBatch.KIND_HYDRANTS


class ParcelImportView(ImportView):
    kind = ImportBatch.KIND_PARCELS


class FlowTestImportView(ImportView):
    kind = ImportBatch.KIND_FLOW_TESTS


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class ImportHistoryView(APIView):
    """Batch history — commit tokens are never echoed (the hash stays server-side)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        batches = ImportBatch.objects.order_by("-created_at", "-id")[:50]
        return Response({"results": ImportBatchSerializer(batches, many=True).data})


# ---------------------------------------------------------------------------
# Ledger export download (the same document export_ledger writes)
# ---------------------------------------------------------------------------
@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class LedgerExportView(APIView):
    """Versioned JSON export as a download.  Admin-only by deliberate posture:
    the document carries user password hashes for restore, so it shares the
    import-restore lane's authorization rather than the generic read rule."""

    permission_classes = [IsAdmin]

    def get(self, request):
        from .export_document import build_export_payload

        payload = build_export_payload()
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        row_count = sum(len(rows) for rows in payload["tables"].values())
        from .exports import audit_export

        audit_export(path="/api/v1/exports/ledger/", kind="export_ledger",
                     row_count=row_count, user=request.user)
        response = HttpResponse(body, content_type="application/json")
        response["Content-Disposition"] = 'attachment; filename="ledger-export.json"'
        response["X-Content-Type-Options"] = "nosniff"
        return response
