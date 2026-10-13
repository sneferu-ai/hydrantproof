"""The attachment pipeline: sniffing, re-encode, capture metadata, budget, admin delete."""
from __future__ import annotations

import base64
import io
import pathlib
import struct
import zlib
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from ledger import constants as C
from ledger.media import sniff_image_type
from ledger.models import Attachment, DistrictSettings, MediaAudit

pytestmark = pytest.mark.django_db


def png_bytes(width=8, height=8, color=(200, 30, 30)) -> bytes:
    """A tiny valid PNG (no Pillow needed to build the fixture)."""

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def hydrant_with_test(api):
    hydrant = api.post(
        "/api/v1/hydrants/", {"asset_tag": "A-1", "lat": 38.0, "lon": -122.0}, format="json"
    ).data
    test = api.post(
        f"/api/v1/hydrants/{hydrant['id']}/tests/",
        {
            "tested_on": str(timezone.localdate()),
            "static_psi": "80.00",
            "residual_psi": "40.00",
            "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                         "pitot_psi": "28.00"}],
        },
        format="json",
    ).data
    return hydrant, test


class TestSniffing:
    def test_png_is_recognized(self):
        assert sniff_image_type(png_bytes()[:32]) == "image/png"

    def test_jpeg_is_recognized(self):
        assert sniff_image_type(jpeg_bytes()[:32]) == "image/jpeg"

    def test_other_bytes_are_rejected(self):
        assert sniff_image_type(b"GIF89a" + b"\x00" * 26) is None

    def test_extension_is_ignored(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("not-an-image.txt", png_bytes(), content_type="text/plain")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        )
        assert response.status_code == 201
        assert response.data["original_content_type"] == "image/png"
        assert response.data["content_type"] == C.STORED_IMAGE_CONTENT_TYPE


class TestUploadContract:
    def test_stored_bytes_are_jpeg_with_digest_and_size(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("gauge.png", png_bytes(64, 64), content_type="image/png")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload, "gps_accuracy_m": "8", "captured_at": "2026-10-03T10:00:00Z"},
            format="multipart",
        )
        assert response.status_code == 201
        attachment = Attachment.objects.get(pk=response.data["id"])
        stored = attachment.file.read()
        import hashlib

        assert hashlib.sha256(stored).hexdigest() == attachment.sha256
        assert len(stored) == attachment.byte_size
        assert stored.startswith(b"\xff\xd8\xff")  # re-encoded to JPEG
        assert attachment.capture_meta["gps_accuracy_m"] == "8"
        assert attachment.capture_meta["captured_at"] == "2026-10-03T10:00:00Z"

    def test_oversize_upload_is_413(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        big = SimpleUploadedFile("big.png", png_bytes() + b"\x00" * (C.MAX_ATTACHMENT_BYTES + 1))
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": big}, format="multipart",
        )
        assert response.status_code == 413

    def test_at_most_four_attachments_per_record(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        url = f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/"
        for index in range(C.MAX_ATTACHMENTS_PER_RECORD):
            upload = SimpleUploadedFile(f"p{index}.png", png_bytes(), content_type="image/png")
            assert api.post(url, {"file": upload}, format="multipart").status_code == 201
        extra = SimpleUploadedFile("p5.png", png_bytes(), content_type="image/png")
        assert api.post(url, {"file": extra}, format="multipart").status_code == 413

    def test_attachment_sub_budget_is_enforced_at_upload(self, api, hydrant_with_test, settings):
        hydrant, test = hydrant_with_test
        district = DistrictSettings.load()
        district.max_attachment_total_mb = 1
        district.save()
        # Fill the sub-budget with a fake existing attachment file.
        root = pathlib.Path(settings.MEDIA_ROOT) / "attachments"
        root.mkdir(parents=True, exist_ok=True)
        (root / "filler.jpg").write_bytes(b"\x00" * (1024 * 1024))
        upload = SimpleUploadedFile("p.png", png_bytes(), content_type="image/png")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        )
        assert response.status_code == 413


class TestServing:
    def test_attachment_serves_with_nosniff(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = api.get(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == C.STORED_IMAGE_CONTENT_TYPE
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_anonymous_attachment_access_is_refused(self, anonymous_api, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = anonymous_api.get(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 401

    def test_viewer_reads_attachment_bytes(self, viewer_api, api, hydrant_with_test):
        """§4: viewers read everything; §5: the FileResponse is authenticated,
        not editor-gated."""
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = viewer_api.get(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == C.STORED_IMAGE_CONTENT_TYPE

    def test_viewer_cannot_delete_an_attachment(self, viewer_api, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = viewer_api.delete(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 403

    def test_attachment_from_another_record_is_404(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        other = api.post(
            "/api/v1/hydrants/", {"asset_tag": "A-2", "lat": 38.1, "lon": -122.1}, format="json"
        ).data
        other_test = api.post(
            f"/api/v1/hydrants/{other['id']}/tests/",
            {"tested_on": str(timezone.localdate()), "static_psi": "80.00", "residual_psi": "40.00",
             "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                          "pitot_psi": "28.00"}]},
            format="json",
        ).data
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = api.get(
            f"/api/v1/hydrants/{other['id']}/tests/{other_test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 404


class TestDeletion:
    def test_editor_cannot_delete_an_attachment(self, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        response = api.delete(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 403

    def test_admin_delete_removes_the_file_and_writes_media_audit(self, admin_user, api, hydrant_with_test):
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        attachment = Attachment.objects.get(pk=created["id"])
        stored_path = attachment.file.path

        token = base64.b64encode(f"{admin_user.username}:hydrantproof-test-pass".encode()).decode()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
        response = client.delete(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 204
        assert not Attachment.objects.filter(pk=created["id"]).exists()
        audit = MediaAudit.objects.get()
        assert audit.sha256 == attachment.sha256
        assert audit.actor_id == admin_user.pk
        assert not pathlib.Path(stored_path).exists()


# ---------------------------------------------------------------------------
# Repair-round regressions (native static review BUG-002/007)
# ---------------------------------------------------------------------------
class TestMissingStoredFile:
    def test_missing_stored_file_is_410_with_hint(self, api, hydrant_with_test):
        """A media-volume loss must answer the honest 410, never a 500."""
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")
        created = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart",
        ).data
        stored_path = pathlib.Path(Attachment.objects.get(pk=created["id"]).file.path)
        stored_path.unlink()
        response = api.get(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/{created['id']}/"
        )
        assert response.status_code == 410
        assert response.data["detail"] == "stored file missing"
        assert "upload" in response.data["hint"]


class TestUploadSizeGuard:
    def test_declared_oversize_body_is_rejected_before_parsing(self, api, hydrant_with_test):
        """The Content-Length guard answers 413 without reading/spooling the
        body: no Attachment row is created and the tiny payload is untouched."""
        hydrant, test = hydrant_with_test
        upload = SimpleUploadedFile("small.png", png_bytes(), content_type="image/png")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": upload}, format="multipart", CONTENT_LENGTH=str(4 * 1024 * 1024 * 1024),
        )
        assert response.status_code == 413
        assert not Attachment.objects.exists()

    def test_spooled_upload_over_the_cap_is_413(self, api, hydrant_with_test):
        """The exact per-file size guard fires before ``upload.read()``."""
        hydrant, test = hydrant_with_test
        big = SimpleUploadedFile("big.png", png_bytes() + b"\x00" * (C.MAX_ATTACHMENT_BYTES + 1))
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/attachments/",
            {"file": big}, format="multipart",
        )
        assert response.status_code == 413
        assert not Attachment.objects.exists()
