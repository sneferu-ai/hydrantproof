"""Attachments, capture metadata and the single media budget.

Upload contract (specification §5/§17): content-sniffed JPEG/PNG/WebP only
(the filename extension is ignored), ≤ 5 MB, ≤ 4 per record, counted against
the attachment sub-budget at upload (413 when exceeded); capture metadata
(EXIF DateTimeOriginal and GPS, plus the client's ``gps_accuracy_m`` and
``captured_at``) is extracted **before** the pixels are re-encoded to JPEG
≤ 1600 px with EXIF stripped; ``sha256``/``byte_size`` describe the stored
bytes.  Files are served only through authenticated responses.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import constants as C
from .models import Attachment, MediaAudit

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class MediaError(Exception):
    """A rejected media operation; ``status`` is the HTTP status to answer."""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def sniff_image_type(head: bytes) -> str | None:
    """Content sniffing — the extension is never consulted."""
    if head.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if head.startswith(PNG_MAGIC):
        return "image/png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------------------------------------------------------------------------
# Budget accounting (one total budget, one attachment sub-budget)
# ---------------------------------------------------------------------------
def _tree_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:  # pragma: no cover - racing prune
                continue
    return total


def media_root() -> Path:
    return Path(settings.MEDIA_ROOT)


def attachment_bytes_total() -> int:
    return _tree_bytes(media_root() / "attachments")


def media_bytes_total() -> int:
    return sum(
        _tree_bytes(media_root() / sub) for sub in ("attachments", "overlays", "packs", "imports")
    )


def media_budget_bytes(max_media_mb: int | None = None) -> int:
    if max_media_mb is None:
        from .models import DistrictSettings

        max_media_mb = DistrictSettings.load().max_media_mb
    return int(max_media_mb) * 1024 * 1024


def attachment_budget_bytes(max_attachment_total_mb: int | None = None) -> int:
    if max_attachment_total_mb is None:
        from .models import DistrictSettings

        max_attachment_total_mb = DistrictSettings.load().max_attachment_total_mb
    return int(max_attachment_total_mb) * 1024 * 1024


def attachments_for(owner_type: str, owner_id: int):
    return Attachment.objects.filter(owner_type=owner_type, owner_id=owner_id)


# ---------------------------------------------------------------------------
# Upload guards (the memory-DoS door)
# ---------------------------------------------------------------------------
# The declared Content-Length covers the multipart envelope (boundaries and
# part headers) on top of the file bytes, so the pre-parse guard tolerates a
# small fixed overhead; the exact per-file limit is still enforced against the
# spooled upload's own size before any byte is read into memory.
MULTIPART_ENVELOPE_ALLOWANCE = 64 * 1024


def declared_content_length(request) -> int | None:
    """The request's declared body length, or ``None`` when absent/malformed."""
    raw = getattr(request, "META", {}).get("CONTENT_LENGTH") or ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def request_body_over_limit(request, limit_bytes: int) -> bool:
    """True when the request *declares* a body beyond ``limit_bytes``.

    Checked before ``request.FILES``/``request.data`` so Django never parses
    (spools) an oversized body: without this guard a multi-gigabyte POST is
    buffered to disk and then read wholesale into worker memory before the
    per-file 413 fires.  A request without Content-Length is not judged here —
    the upload's own ``size`` guard still applies before any read.
    """
    declared = declared_content_length(request)
    return declared is not None and declared > limit_bytes + MULTIPART_ENVELOPE_ALLOWANCE


def upload_exceeds(upload, limit_bytes: int) -> bool:
    """The spooled upload's exact size, checked before ``upload.read()``."""
    size = getattr(upload, "size", None)
    return size is not None and size > limit_bytes


# ---------------------------------------------------------------------------
# Capture metadata (extracted before the pixel strip)
# ---------------------------------------------------------------------------
def extract_capture_meta(data: bytes, client_meta: dict | None = None) -> dict:
    """EXIF DateTimeOriginal / GPS plus the client's accuracy and timestamp."""
    meta: dict = {}
    client_meta = client_meta or {}
    if client_meta.get("gps_accuracy_m") is not None:
        meta["gps_accuracy_m"] = client_meta.get("gps_accuracy_m")
    if client_meta.get("captured_at"):
        meta["captured_at"] = str(client_meta["captured_at"])[:64]
    try:
        from PIL import ExifTags, Image

        image = Image.open(io.BytesIO(data))
        exif = image.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                if tag in ("DateTimeOriginal", "DateTime"):
                    meta.setdefault("exif_datetime_original", str(value)[:64])
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            except Exception:
                gps_ifd = {}
            if gps_ifd:
                gps = _gps_decimal(gps_ifd)
                if gps:
                    meta["exif_gps_lat"], meta["exif_gps_lon"] = gps
    except Exception:
        # Metadata is best-effort; a missing EXIF block never blocks an upload.
        pass
    return meta


def _gps_decimal(gps_ifd: dict) -> tuple[float, float] | None:
    try:
        def to_deg(values):
            d, m, s = (float(values[0]), float(values[1]), float(values[2]))
            return d + m / 60.0 + s / 3600.0

        lat = to_deg(gps_ifd[2])
        if gps_ifd.get(1) == "S":
            lat = -lat
        lon = to_deg(gps_ifd[4])
        if gps_ifd.get(3) == "W":
            lon = -lon
        return round(lat, 7), round(lon, 7)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def store_attachment(
    *,
    owner_type: str,
    owner_id: int,
    data: bytes,
    user,
    is_sample_data: bool = False,
    client_meta: dict | None = None,
) -> Attachment:
    """Validate, extract metadata, re-encode to JPEG, persist one attachment."""
    if owner_type not in (Attachment.OWNER_FLOW_TEST, Attachment.OWNER_INSPECTION):
        raise MediaError("unknown attachment owner type")
    if len(data) > C.MAX_ATTACHMENT_BYTES:
        raise MediaError(
            f"attachment exceeds {C.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB", status=413
        )
    original_type = sniff_image_type(data[:32])
    if original_type not in C.ALLOWED_IMAGE_CONTENT_TYPES:
        raise MediaError("attachment must be a JPEG, PNG or WebP image", status=400)

    if attachments_for(owner_type, owner_id).count() >= C.MAX_ATTACHMENTS_PER_RECORD:
        raise MediaError(f"at most {C.MAX_ATTACHMENTS_PER_RECORD} attachments per record", status=413)

    capture_meta = extract_capture_meta(data, client_meta)
    stored = _reencode_jpeg(data)
    if attachment_bytes_total() + len(stored) > attachment_budget_bytes():
        raise MediaError("attachment budget exceeded", status=413)
    # The single total media budget is enforced at every write path (§2-18):
    # upload pre-flight here, pack submit pre-flight in the pack job, and
    # prune_data as the cron backstop.
    if media_bytes_total() + len(stored) > media_budget_bytes():
        raise MediaError("media budget exceeded", status=413)

    digest = hashlib.sha256(stored).hexdigest()
    filename = f"{owner_type.lower()}-{owner_id}-{digest[:16]}.jpg"
    attachment = Attachment(
        owner_type=owner_type,
        owner_id=owner_id,
        sha256=digest,
        byte_size=len(stored),
        content_type=C.STORED_IMAGE_CONTENT_TYPE,
        original_content_type=original_type,
        capture_meta=capture_meta,
        uploaded_by=user if getattr(user, "is_authenticated", False) else None,
        is_sample_data=is_sample_data,
    )
    attachment.file.save(filename, io.BytesIO(stored), save=False)
    attachment.save()
    return attachment


def _reencode_jpeg(data: bytes) -> bytes:
    """Re-encode to JPEG ≤ 1600 px, EXIF stripped (the privacy choice)."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        if max(image.size) > C.STORED_IMAGE_MAX_PX:
            image.thumbnail((C.STORED_IMAGE_MAX_PX, C.STORED_IMAGE_MAX_PX))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=False)
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# Sanctioned deletion (admin only; always audited)
# ---------------------------------------------------------------------------
def admin_delete_attachment(attachment: Attachment, *, actor, reason: str = "") -> None:
    """The budget-freeing and error-correction path — writes a MediaAudit row."""
    MediaAudit.objects.create(
        attachment=None,  # the row is about to disappear; keep the scalar reference
        attachment_ref=attachment.pk,
        sha256=attachment.sha256,
        reason=reason or "admin delete",
        actor=actor if getattr(actor, "is_authenticated", False) else None,
    )
    stored = attachment.file
    if stored and stored.name:
        try:
            stored.storage.delete(stored.name)
        except OSError:  # pragma: no cover - already gone
            pass
    Attachment.objects.filter(pk=attachment.pk).delete()


# ---------------------------------------------------------------------------
# In-job pruning (oldest pack files first, then oldest attachments)
# ---------------------------------------------------------------------------
def prune_media_to_budget(target_bytes: int | None = None) -> int:
    """Delete oldest stored pack files (row + files together) to hold budget.

    Returns the media byte total after pruning.  Attachments are never pruned
    automatically — they are deleted only by an admin action.  RENDERING packs
    are never pruned: a job may not delete its own in-flight row.
    """
    from .models import EvidencePack

    target = target_bytes if target_bytes is not None else media_budget_bytes()
    if media_bytes_total() <= target:
        return media_bytes_total()
    for pack in (
        EvidencePack.objects.exclude(status=C.PACK_STATUS_RENDERING).order_by("created_at", "id")
    ):
        if media_bytes_total() <= target:
            break
        delete_pack_files(pack)
    return media_bytes_total()


def delete_pack_files_only(pack) -> None:
    """Delete the pack's stored files (and its pack dir) but KEEP the row —
    the reaper/failure path: a FAILED pack stays visible in history."""
    root = media_root()
    for relative in (pack.zip_path, pack.pdf_path):
        if not relative:
            continue
        path = root / relative
        try:
            if path.exists():
                path.unlink()
        except OSError:  # pragma: no cover
            pass
    pack_dir = root / "packs" / str(pack.pk)
    try:
        if pack_dir.exists():
            for child in pack_dir.iterdir():
                child.unlink(missing_ok=True)
            pack_dir.rmdir()
    except OSError:  # pragma: no cover
        pass


def delete_pack_files(pack) -> None:
    """Delete a pack's row and both stored files together — never file-only."""
    delete_pack_files_only(pack)
    pack.delete()


def timestamp_tag() -> str:
    return timezone.now().strftime("%Y%m%dT%H%M%SZ")
