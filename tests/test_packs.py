"""Evidence packs: render pipeline, stored bytes, 6+N zip, manifest,
404/410/409 download semantics, rate limit, and the media-budget failure."""
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from django.conf import settings

from ledger import constants as C
from ledger.executor import run_coverage_job, run_pack_job
from ledger.models import CoverageRun, EvidencePack, ExportAudit

from test_coverage_jobs import make_hydrant, make_parcel, make_run

pytestmark = pytest.mark.django_db


def completed_run():
    make_hydrant()
    make_parcel()
    run = make_run()
    run_coverage_job(run.pk)
    run.refresh_from_db()
    assert run.status == C.RUN_STATUS_COMPLETE
    return run


def render_sync(run, *, include_attachments=False, user=None) -> EvidencePack:
    pack = EvidencePack.objects.create(
        run=run, status=C.PACK_STATUS_RENDERING, include_attachments=include_attachments,
        generated_by=user,
    )
    run_pack_job(pack.pk)
    pack.refresh_from_db()
    return pack


class TestRender:
    def test_pack_completes_with_six_entries_and_digests(self):
        run = completed_run()
        pack = render_sync(run)
        assert pack.status == C.PACK_STATUS_COMPLETE
        assert pack.page_count >= 2
        zip_path = settings.MEDIA_ROOT / pack.zip_path
        pdf_path = settings.MEDIA_ROOT / pack.pdf_path
        assert zip_path.exists() and pdf_path.exists()
        assert pack.byte_size == zip_path.stat().st_size + pdf_path.stat().st_size
        assert pack.csv_zip_sha256 == hashlib.sha256(zip_path.read_bytes()).hexdigest()
        assert pack.pdf_sha256 == hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
            assert zf.namelist() == [
                "pack.pdf", "hydrants.csv", "coverage_worksheet.csv",
                "parcel_summary.csv", "oos_register.csv", "manifest.txt",
            ]  # exactly 6 + N with N=0, in the fixed order
            manifest = zf.read("manifest.txt").decode()
        assert f"run_id: {run.pk}" in manifest
        assert run.params_sha256 in manifest
        assert run.core_params_sha256 in manifest
        assert C.PROVISIONAL_BANNER.split(".")[0] in manifest
        # manifest self-exclusion: the manifest's own hash never appears
        assert hashlib.sha256(manifest.encode()).hexdigest() not in manifest
        # every other entry is hashed
        for name in ("pack.pdf", "hydrants.csv", "coverage_worksheet.csv",
                     "parcel_summary.csv", "oos_register.csv"):
            assert name in manifest
        assert pack.snapshot["schema"] == "hydrantproof-pack-snapshot-v1"
        assert pack.row_counts["worksheet_rows"] == 1

    def test_sub_target_residual_included_count_is_printed(self):
        """Fixture C rides a pack: credited by default, and counted."""
        from datetime import timedelta

        from django.utils import timezone

        from ledger.services import create_flow_test

        hydrant = make_hydrant("H-SUB", with_test=False)
        create_flow_test(
            hydrant=hydrant,
            tested_on=timezone.localdate() - timedelta(days=5),
            static_psi="80.00", residual_psi="15.00",  # fixture C: factor 60/65 < 1
            outlets=[{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                      "pitot_psi": "10.00"}],
        )
        make_parcel()
        run = make_run()
        run_coverage_job(run.pk)
        run.refresh_from_db()
        pack = render_sync(run)
        assert pack.status == C.PACK_STATUS_COMPLETE
        assert pack.snapshot["sub_target_residual_included_count"] == 1

    def test_pdf_is_deterministic_for_fixed_inputs(self):
        """The named determinism mechanisms: same inputs + same generated_at →
        byte-identical PDF."""
        run = completed_run()
        pack = render_sync(run)
        assert pack.status == C.PACK_STATUS_COMPLETE
        from ledger.pack_render import _parcel_aggregates, _render_pdf

        snapshot = pack.snapshot
        generated_at = pack.generated_at
        summaries = _parcel_aggregates(run, snapshot)
        pdf_a, _ = _render_pdf(snapshot, summaries, [], generated_at)
        pdf_b, _ = _render_pdf(snapshot, summaries, [], generated_at)
        assert hashlib.sha256(pdf_a).hexdigest() == hashlib.sha256(pdf_b).hexdigest()
        assert pdf_a[:8] == b"%PDF-1.4"

    def test_pack_csvs_and_manifest_pass_the_forbidden_phrase_scrub(self):
        run = completed_run()
        pack = render_sync(run)
        zip_path = settings.MEDIA_ROOT / pack.zip_path
        with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
            for name in zf.namelist():
                if not (name.endswith(".csv") or name == "manifest.txt"):
                    continue
                text = zf.read(name).decode("utf-8")
                stripped = text.replace(C.DISCLAIMER, "")
                for phrase in C.FORBIDDEN_PHRASES:
                    assert phrase.lower() not in stripped.lower(), f"{name} contains {phrase!r}"

    def test_attachments_make_it_6_plus_n(self, api):
        from test_attachments import png_bytes  # the suite's fixture image

        run = completed_run()
        hydrant = run.results.first().hydrant
        test = hydrant.tests.first()
        response = api.post(
            f"/api/v1/hydrants/{hydrant.pk}/tests/{test.pk}/attachments/",
            {"file": io.BytesIO(png_bytes())},
            format="multipart",
        )
        assert response.status_code == 201, response.data
        pack = render_sync(run, include_attachments=True)
        assert pack.status == C.PACK_STATUS_COMPLETE
        zip_path = settings.MEDIA_ROOT / pack.zip_path
        with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
            names = zf.namelist()
        assert len(names) == 6 + 1
        assert any(name.startswith("attachments/") for name in names)
        assert pack.row_counts["attachments"] == 1


class TestPackApi:
    def test_post_with_run_id_202_poll_download_twice_byte_identical(self, api):
        run = completed_run()
        response = api.post("/api/v1/evidence-packs/", {"run_id": run.pk}, format="json")
        assert response.status_code == 202
        pack_id = response.data["id"]
        run_pack_job(pack_id)
        poll = api.get(f"/api/v1/evidence-packs/{pack_id}/")
        assert poll.data["status"] == "COMPLETE"
        first = api.get(f"/api/v1/evidence-packs/{pack_id}/zip/")
        second = api.get(f"/api/v1/evidence-packs/{pack_id}/zip/")
        assert first.status_code == 200 and second.status_code == 200
        body_a = b"".join(first.streaming_content)
        body_b = b"".join(second.streaming_content)
        assert hashlib.sha256(body_a).hexdigest() == hashlib.sha256(body_b).hexdigest()
        pdf = api.get(f"/api/v1/evidence-packs/{pack_id}/pdf/")
        assert pdf.status_code == 200
        assert pdf["Content-Type"] == "application/pdf"
        assert pdf["X-Content-Type-Options"] == "nosniff"
        # Downloads are audited exports.
        assert ExportAudit.objects.filter(kind="pack_zip_path").count() == 2
        assert ExportAudit.objects.filter(kind="pack_pdf_path").count() == 1

    def test_post_without_run_id_creates_a_fresh_run(self, api):
        make_hydrant()
        make_parcel()
        response = api.post("/api/v1/evidence-packs/", {"include_attachments": False}, format="json")
        assert response.status_code == 202
        pack_id = response.data["id"]
        run_pack_job(pack_id)  # drives the fresh coverage run, then the render
        poll = api.get(f"/api/v1/evidence-packs/{pack_id}/")
        assert poll.data["status"] == "COMPLETE"
        run = CoverageRun.objects.get(pk=poll.data["run"])
        assert run.status == C.RUN_STATUS_COMPLETE

    def test_unknown_run_id_is_404(self, api):
        response = api.post("/api/v1/evidence-packs/", {"run_id": 999999}, format="json")
        assert response.status_code == 404

    def test_empty_data_is_400(self, api):
        # No hydrants at all → 400, no rows created.
        response = api.post("/api/v1/evidence-packs/", {}, format="json")
        assert response.status_code == 400
        assert EvidencePack.objects.count() == 0
        assert CoverageRun.objects.count() == 0

    def test_409_while_rendering(self, api):
        run = completed_run()
        EvidencePack.objects.create(run=run, status=C.PACK_STATUS_RENDERING)
        response = api.post("/api/v1/evidence-packs/", {"run_id": run.pk}, format="json")
        assert response.status_code == 409

    def test_rate_limit_10_per_minute(self, api, editor_user):
        run = completed_run()
        for _ in range(10):
            EvidencePack.objects.create(run=run, status=C.PACK_STATUS_COMPLETE,
                                        generated_by=editor_user)
        response = api.post("/api/v1/evidence-packs/", {"run_id": run.pk}, format="json")
        assert response.status_code == 429
        assert response["Retry-After"] == "60"

    def test_missing_stored_file_is_410_with_hint(self, api):
        run = completed_run()
        pack = render_sync(run)
        (settings.MEDIA_ROOT / pack.pdf_path).unlink()
        response = api.get(f"/api/v1/evidence-packs/{pack.pk}/pdf/")
        assert response.status_code == 410
        assert response.data["detail"] == "stored file missing"
        assert response.data["hint"] == "generate a new pack"
        # The zip still serves — the semantics are per stored file.
        assert api.get(f"/api/v1/evidence-packs/{pack.pk}/zip/").status_code == 200

    def test_pruned_pack_is_404(self, api):
        run = completed_run()
        pack = render_sync(run)
        pack_id = pack.pk
        pack.delete()  # what prune_data does (row + files together)
        assert api.get(f"/api/v1/evidence-packs/{pack_id}/zip/").status_code == 404

    def test_rendering_pack_download_is_409(self, api):
        run = completed_run()
        pack = EvidencePack.objects.create(run=run, status=C.PACK_STATUS_RENDERING)
        assert api.get(f"/api/v1/evidence-packs/{pack.pk}/zip/").status_code == 409

    def test_viewer_reads_and_downloads(self, viewer_api):
        run = completed_run()
        pack = render_sync(run)
        assert viewer_api.get("/api/v1/evidence-packs/").status_code == 200
        assert viewer_api.get(f"/api/v1/evidence-packs/{pack.pk}/zip/").status_code == 200

    def test_viewer_cannot_generate(self, viewer_api):
        assert viewer_api.post("/api/v1/evidence-packs/", {}, format="json").status_code == 403


class TestMediaBudgetFailure:
    def test_over_budget_render_fails_before_any_write(self, api):
        run = completed_run()
        hydrant = run.results.first().hydrant
        test = hydrant.tests.first()
        # A ~1 MB attachment (noisy image stays large after JPEG re-encoding);
        # uploaded under the default budgets, then the budgets are tightened.
        big_png = _noisy_png(1200, 1200)
        response = api.post(
            f"/api/v1/hydrants/{hydrant.pk}/tests/{test.pk}/attachments/",
            {"file": io.BytesIO(big_png)},
            format="multipart",
        )
        assert response.status_code == 201, response.data

        from ledger.models import DistrictSettings

        district = DistrictSettings.load()
        district.max_media_mb = 1  # the pack projection cannot fit
        district.max_attachment_total_mb = 1  # cross-validated ≤ total
        district.save()

        pack = EvidencePack.objects.create(
            run=run, status=C.PACK_STATUS_RENDERING, include_attachments=True
        )
        run_pack_job(pack.pk)
        pack.refresh_from_db()
        assert pack.status == C.PACK_STATUS_FAILED
        assert pack.failure_reason == C.FAILURE_MEDIA_BUDGET_EXCEEDED
        # Nothing was written: no files, and the run is untouched.
        assert pack.zip_path == "" and pack.pdf_path == ""
        assert not (settings.MEDIA_ROOT / "packs" / str(pack.pk)).exists()

    def test_failed_pack_poll_reports_remediation(self, api):
        run = completed_run()
        pack = EvidencePack.objects.create(
            run=run, status=C.PACK_STATUS_FAILED, failure_reason=C.FAILURE_MEDIA_BUDGET_EXCEEDED
        )
        poll = api.get(f"/api/v1/evidence-packs/{pack.pk}/")
        assert poll.status_code == 200
        assert "include_attachments" in poll.data["remediation"]


def _noisy_png(width: int, height: int) -> bytes:
    """A PNG that stays large after JPEG re-encoding (deterministic noise)."""
    from PIL import Image

    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            value = (x * 31 + y * 17) % 256
            pixels[x, y] = (value, (value * 7) % 256, (value * 13) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
