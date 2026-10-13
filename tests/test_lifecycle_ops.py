"""Scenario 9's lifecycle half and the §10 demo-deactivation mechanism.

The spec's acceptance criterion requires all nine scenario groups green in the
gate command; the demo/lifecycle half of scenario 9 lives here:

* ``seed_demo`` — content, idempotency, non-empty refusal, purge-marker block;
* ``deactivate_demo_users_on_first_real_row`` — the named §10 mechanism, proven
  through the API exactly as the runtime smoke plan's final step proves it
  (the published password dies on contact with the first real row);
* ``purge_demo`` — demo-only delete (rows + files + demo users together),
  refusal on real data that still deactivates, idempotency;
* ``prune_data`` — pack rows and files deleted together, budgets, age
  retention, in-flight RENDERING rows never pruned, dry-run;
* ``export_ledger`` → ``import_ledger`` — the documented backup/restore lane,
  including the user/group restore and the reconciled deactivation invariant
  (bulk_create cannot fire the post_save signal, so the restore reconciles it).
"""
from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from ledger import constants as C
from ledger.export_document import SCHEMA_VERSION, json_default, serialize_row
from ledger.executor import run_coverage_job
from ledger.models import (
    AppSetting,
    Attachment,
    CoverageRun,
    EvidencePack,
    FlowTest,
    Hydrant,
    Inspection,
    LoginAttempt,
    MapOverlay,
    Parcel,
)
from ledger.signals import DEMO_DEACTIVATED_MARKER, DEMO_USERNAMES

from test_coverage_jobs import make_hydrant, make_parcel, make_run
from test_packs import render_sync

pytestmark = pytest.mark.django_db

DEMO_PASSWORD = "hydrantproof-demo"


def basic(username: str, password: str) -> APIClient:
    client = APIClient()
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
    return client


class TestSeedDemo:
    def test_seed_creates_the_demo_district(self):
        call_command("seed_demo")
        assert Hydrant.objects.count() == 24
        assert Parcel.objects.count() == 60
        assert not Hydrant.objects.filter(is_sample_data=False).exists()
        assert not Parcel.objects.filter(is_sample_data=False).exists()
        assert MapOverlay.objects.filter(is_sample_data=True).exists()
        editor = User.objects.get(username="demo-editor")
        assert editor.is_active
        assert editor.check_password(DEMO_PASSWORD)
        assert editor.groups.filter(name="editor").exists()
        viewer = User.objects.get(username="demo-viewer")
        assert viewer.groups.filter(name="viewer").exists()
        admin = User.objects.get(username="demo-admin")
        # demo-admin gets a random one-time password, never the published one.
        assert admin.is_active
        assert not admin.check_password(DEMO_PASSWORD)

    def test_seed_is_idempotent(self):
        call_command("seed_demo")
        call_command("seed_demo")
        assert Hydrant.objects.count() == 24
        assert Parcel.objects.count() == 60

    def test_seed_refuses_a_non_empty_ledger(self):
        Hydrant.objects.create(asset_tag="REAL-0", lat=38.0, lon=-122.0)
        call_command("seed_demo")
        assert not Hydrant.objects.filter(is_sample_data=True).exists()
        assert not User.objects.filter(username="demo-editor").exists()


class TestDemoDeactivation:
    """The published-password accounts cannot survive contact with real data."""

    def test_first_real_row_via_api_kills_the_published_password(self):
        call_command("seed_demo")
        client = basic("demo-editor", DEMO_PASSWORD)
        assert client.get("/api/v1/hydrants/").status_code == 200
        response = client.post(
            "/api/v1/hydrants/", {"asset_tag": "REAL-1", "lat": 38.0, "lon": -122.0},
            format="json",
        )
        assert response.status_code == 201
        assert AppSetting.marker_set(DEMO_DEACTIVATED_MARKER)
        for username in DEMO_USERNAMES:
            assert not User.objects.get(username=username).is_active, username
        # The runtime smoke plan's final step: the same credentials now 401.
        assert client.get("/api/v1/hydrants/").status_code == 401

    def test_sample_rows_do_not_fire_the_trigger(self):
        call_command("seed_demo")  # 24 sample hydrants + 60 sample parcels
        assert not AppSetting.marker_set(DEMO_DEACTIVATED_MARKER)
        assert User.objects.get(username="demo-editor").is_active

    # seed→purge in one test needs autocommit: purge_demo/import_ledger run
    # ALTER TABLE … DISABLE TRIGGER, which PostgreSQL refuses inside an ambient
    # transaction holding pending trigger events on a Tier A table.
    @pytest.mark.django_db(transaction=True)
    def test_reseed_after_purge_rearms_the_trigger(self):
        """A prior purge leaves the fired marker behind; the next demo
        generation must re-arm the trigger or its accounts would survive the
        first real row."""
        call_command("seed_demo")
        call_command("purge_demo")
        assert AppSetting.marker_set(DEMO_DEACTIVATED_MARKER)
        call_command("clear_purge_marker")
        call_command("seed_demo")
        assert User.objects.get(username="demo-editor").is_active
        assert not AppSetting.marker_set(DEMO_DEACTIVATED_MARKER)
        Hydrant.objects.create(asset_tag="REAL-9", lat=38.0, lon=-122.0)
        assert not User.objects.get(username="demo-editor").is_active


@pytest.mark.django_db(transaction=True)  # purge_demo ALTERs Tier A trigger tables
class TestPurgeDemo:
    def test_demo_only_purge_removes_rows_files_and_users_and_blocks_reseed(self):
        call_command("seed_demo")
        overlay = MapOverlay.objects.get()
        overlay_file = Path(settings.MEDIA_ROOT) / overlay.geojson_path
        assert overlay_file.exists()
        call_command("purge_demo")
        for model in (Hydrant, Parcel, FlowTest, Inspection, MapOverlay, Attachment,
                      CoverageRun, EvidencePack):
            assert model.objects.count() == 0, model.__name__
        assert not overlay_file.exists()
        assert not User.objects.filter(username__in=DEMO_USERNAMES).exists()
        assert AppSetting.marker_set("demo_purged")
        # Reseeding stays off until the marker is cleared.
        call_command("seed_demo")
        assert Hydrant.objects.count() == 0
        call_command("clear_purge_marker")
        call_command("seed_demo")
        assert Hydrant.objects.count() == 24

    def test_purge_refuses_on_real_data_but_still_deactivates(self):
        call_command("seed_demo")
        Hydrant.objects.create(asset_tag="REAL-1", lat=38.0, lon=-122.0)
        # The signal already fired; an admin re-activating an account in the
        # Django admin must not survive the refusal path either (§10).
        User.objects.filter(username="demo-editor").update(is_active=True)
        with pytest.raises(SystemExit):
            call_command("purge_demo")
        assert Hydrant.objects.filter(asset_tag="REAL-1").exists()
        assert Hydrant.objects.filter(is_sample_data=True).count() == 24
        assert not User.objects.get(username="demo-editor").is_active

    def test_purge_twice_is_idempotent(self):
        call_command("seed_demo")
        call_command("purge_demo")
        call_command("purge_demo")  # an empty demo ledger purges cleanly
        assert Hydrant.objects.count() == 0
        assert AppSetting.marker_set("demo_purged")


def _complete_pack():
    """One real COMPLETE coverage run with its rendered pack (stored files)."""
    run = make_run()
    run_coverage_job(run.pk)
    run.refresh_from_db()
    assert run.status == C.RUN_STATUS_COMPLETE
    pack = render_sync(run)
    assert pack.status == C.PACK_STATUS_COMPLETE
    return run, pack


class TestPruneData:
    def test_keep_packs_deletes_the_oldest_row_with_both_files(self):
        make_hydrant(tag="H-PR")
        make_parcel(pid="P-PR")
        _, older = _complete_pack()
        _, newer = _complete_pack()
        older_zip = Path(settings.MEDIA_ROOT) / older.zip_path
        older_pdf = Path(settings.MEDIA_ROOT) / older.pdf_path
        assert older_zip.exists() and older_pdf.exists()
        call_command("prune_data", "--keep-packs", "1")
        assert not EvidencePack.objects.filter(pk=older.pk).exists()
        assert not older_zip.exists() and not older_pdf.exists()
        assert EvidencePack.objects.filter(pk=newer.pk).exists()
        assert (Path(settings.MEDIA_ROOT) / newer.zip_path).exists()

    def test_a_rendering_pack_is_never_pruned(self):
        make_hydrant(tag="H-PR")
        make_parcel(pid="P-PR")
        run, older = _complete_pack()
        _, _ = _complete_pack()
        inflight = EvidencePack.objects.create(run=run, status=C.PACK_STATUS_RENDERING)
        call_command("prune_data", "--keep-packs", "1")
        assert not EvidencePack.objects.filter(pk=older.pk).exists()
        assert EvidencePack.objects.filter(pk=inflight.pk, status=C.PACK_STATUS_RENDERING).exists()

    def test_the_media_budget_prunes_oldest_first_until_met(self):
        make_hydrant(tag="H-PR")
        make_parcel(pid="P-PR")
        _, pack_a = _complete_pack()
        _, pack_b = _complete_pack()
        call_command("prune_data", "--keep-packs", "10", "--max-media-mb", "0")
        assert not EvidencePack.objects.filter(pk__in=(pack_a.pk, pack_b.pk)).exists()
        # The runs themselves are not the media budget's business.
        assert CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).count() == 2

    def test_failed_runs_are_pruned_only_after_90_days(self):
        old = make_run()
        CoverageRun.objects.filter(pk=old.pk).update(
            status=C.RUN_STATUS_FAILED, created_at=timezone.now() - timedelta(days=100)
        )
        recent = make_run()
        CoverageRun.objects.filter(pk=recent.pk).update(status=C.RUN_STATUS_FAILED)
        call_command("prune_data")
        assert not CoverageRun.objects.filter(pk=old.pk).exists()
        assert CoverageRun.objects.filter(pk=recent.pk).exists()

    def test_login_attempts_prune_by_age(self):
        LoginAttempt.objects.create(username="old", surface="basic", success=False)
        LoginAttempt.objects.create(username="new", surface="basic", success=False)
        LoginAttempt.objects.filter(username="old").update(
            at=timezone.now() - timedelta(days=40)
        )
        call_command("prune_data")
        assert not LoginAttempt.objects.filter(username="old").exists()
        assert LoginAttempt.objects.filter(username="new").exists()

    def test_dry_run_changes_nothing(self):
        make_hydrant(tag="H-PR")
        make_parcel(pid="P-PR")
        _, pack = _complete_pack()
        call_command("prune_data", "--keep-packs", "0", "--dry-run")
        assert EvidencePack.objects.filter(pk=pack.pk).exists()
        assert (Path(settings.MEDIA_ROOT) / pack.zip_path).exists()


class TestExportImportRestore:
    """The §10 backup/restore lane: versioned JSON out, pk-preserved restore."""

    @pytest.mark.django_db(transaction=True)  # seed→purge→restore ALTERs Tier A tables
    def test_round_trip_restores_rows_users_and_groups(self, tmp_path):
        call_command("seed_demo")
        out = tmp_path / "export.json"
        call_command("export_ledger", "--output", str(out))
        payload = json.loads(out.read_text())
        assert payload["schema_version"] == SCHEMA_VERSION
        assert "AppSetting" not in payload["tables"]  # the secret key never exports
        assert payload["tables"]["auth.User"]          # users (hashes) do

        call_command("purge_demo")
        assert Hydrant.objects.count() == 0
        call_command("import_ledger", str(out), "--confirm")

        assert Hydrant.objects.count() == 24
        assert Parcel.objects.count() == 60
        editor = User.objects.get(username="demo-editor")
        assert editor.is_active
        assert editor.check_password(DEMO_PASSWORD)
        assert editor.groups.filter(name="editor").exists()
        # A sample-only restore re-arms the first-real-row trigger, and the
        # trigger then fires on the next real row.
        assert not AppSetting.marker_set(DEMO_DEACTIVATED_MARKER)
        Hydrant.objects.create(asset_tag="REAL-7", lat=38.0, lon=-122.0)
        assert not User.objects.get(username="demo-editor").is_active

    def test_restore_with_real_rows_deactivates_the_demo_accounts(self, tmp_path):
        """bulk_create never fires the post_save signal, so the restore
        reconciles the §10 invariant itself: real rows land ⇒ demo accounts
        deactivate in the same transaction."""
        real = Hydrant.objects.create(asset_tag="R-1", lat=38.0, lon=-122.0)
        row = serialize_row(real)
        Hydrant.objects.filter(pk=real.pk).delete()  # Tier C: the restore is the only writer
        payload = {
            "schema_version": SCHEMA_VERSION,
            "tables": {
                "Hydrant": [row],
                "auth.User": [{
                    "id": 9001,
                    "username": "demo-editor",
                    "password": "pbkdf2_sha256$0$restored$hash",
                    "is_active": True,
                    "is_staff": False,
                    "is_superuser": False,
                    "date_joined": "2026-01-01T00:00:00+00:00",
                    "groups": ["editor"],
                }],
            },
        }
        out = tmp_path / "restore.json"
        out.write_text(json.dumps(payload, default=json_default))
        call_command("import_ledger", str(out), "--confirm")
        restored = User.objects.get(username="demo-editor")
        assert not restored.is_active          # the invariant held across restore
        assert restored.groups.filter(name="editor").exists()  # memberships restored
        assert Hydrant.objects.filter(asset_tag="R-1").exists()

    def test_version_mismatch_refuses(self, tmp_path):
        from django.core.management.base import CommandError

        out = tmp_path / "bad.json"
        out.write_text(json.dumps({"schema_version": 999, "tables": {}}))
        with pytest.raises(CommandError):
            call_command("import_ledger", str(out), "--confirm")

    def test_confirm_is_required(self, tmp_path):
        from django.core.management.base import CommandError

        out = tmp_path / "x.json"
        out.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "tables": {}}))
        with pytest.raises(CommandError):
            call_command("import_ledger", str(out))


# ---------------------------------------------------------------------------
# Repair-round regressions (native static review BUG-010)
# ---------------------------------------------------------------------------
class TestSecretKeyPosture:
    """A serving process never signs sessions/CSRF with the public build-time key."""

    def test_check_posture_refuses_the_build_time_key(self, monkeypatch):
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        assert not AppSetting.objects.filter(key="django_secret_key").exists()
        with pytest.raises(SystemExit) as excinfo:
            call_command("check_posture")
        assert excinfo.value.code == 1

    def test_check_posture_passes_with_a_persisted_key(self, monkeypatch):
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        AppSetting.objects.create(key="django_secret_key", value="a-real-persisted-key")
        call_command("check_posture")  # no SystemExit

    def test_check_posture_passes_with_an_environment_key(self, monkeypatch):
        monkeypatch.setenv("DJANGO_SECRET_KEY", "an-env-provided-key")
        call_command("check_posture")

    def test_static_gate_never_requires_the_key(self, monkeypatch):
        # The pre-DB gate runs before ensure_secret can persist one.
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        call_command("check_posture", "--static")


class TestRuntimeSecretResolution:
    def test_adopts_the_persisted_key(self, settings, monkeypatch):
        from ledger.secret_key import ensure_runtime_secret

        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        AppSetting.objects.create(key="django_secret_key", value="persisted-value")
        settings.SECRET_KEY = settings.EPHEMERAL_SECRET_KEY
        settings.USING_EPHEMERAL_SECRET_KEY = True
        assert ensure_runtime_secret() == "persisted-value"
        assert settings.SECRET_KEY == "persisted-value"
        assert settings.USING_EPHEMERAL_SECRET_KEY is False

    def test_environment_key_wins_over_the_persisted_one(self, settings, monkeypatch):
        from ledger.secret_key import ensure_runtime_secret

        monkeypatch.setenv("DJANGO_SECRET_KEY", "env-key")
        AppSetting.objects.create(key="django_secret_key", value="persisted-value")
        assert ensure_runtime_secret() == "env-key"

    def test_no_key_anywhere_is_loud(self, settings, monkeypatch, caplog):
        import logging

        from ledger.secret_key import ensure_runtime_secret

        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        settings.SECRET_KEY = settings.EPHEMERAL_SECRET_KEY
        settings.USING_EPHEMERAL_SECRET_KEY = True
        with caplog.at_level(logging.CRITICAL, logger="ledger.secret_key"):
            assert ensure_runtime_secret() == ""
        assert any("build-time constant" in record.message for record in caplog.records)
