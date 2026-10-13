"""Every server-rendered page renders for a signed-in editor."""
from __future__ import annotations

import pytest
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db

PAGES = ("/dashboard/", "/hydrants/", "/coverage/", "/packs/", "/field/")


@pytest.fixture
def signed_in(editor_user):
    client = Client()
    client.force_login(editor_user)
    return client


class TestPageRendering:
    @pytest.mark.parametrize("path", PAGES)
    def test_page_renders(self, signed_in, path):
        response = signed_in.get(path)
        assert response.status_code == 200, path
        assert b"<html" in response.content

    def test_dashboard_shows_the_readiness_states(self, signed_in):
        body = signed_in.get("/dashboard/").content.decode()
        for state in ("READY", "WATCH", "NOT_READY"):
            assert state in body

    def test_dashboard_carries_the_artifact_label_and_banner(self, signed_in):
        from ledger import constants as C

        body = signed_in.get("/dashboard/").content.decode()
        assert C.ARTIFACT_LABEL in body
        assert C.PROVISIONAL_BANNER in body

    def test_hydrant_detail_renders_with_history(self, signed_in, api):
        hydrant = api.post(
            "/api/v1/hydrants/", {"asset_tag": "P-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/",
            {"tested_on": str(timezone.localdate()), "static_psi": "80.00", "residual_psi": "40.00",
             "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                          "pitot_psi": "28.00"}]},
            format="json",
        )
        response = signed_in.get(f"/hydrants/{hydrant['id']}/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "P-1" in body
        assert "887.9" in body          # the stored Q_F, not a recomputation
        assert "nfpa291-pct-drop-v1" in body

    def test_coverage_page_names_the_proxy_label(self, signed_in):
        body = signed_in.get("/coverage/").content.decode()
        assert "distribution" in body
        assert "proxy" in body

    def test_field_page_states_the_photo_limitation(self, signed_in):
        body = signed_in.get("/field/").content.decode()
        assert "Photos do not survive the CSV path" in body
        assert "proof of records, not proof of presence" in body

    def test_field_page_carries_the_capture_contract(self, signed_in, api):
        """§11: the hydrant select carries the human asset_tag (the queued CSV
        needs it, not the pk), the photo input exists, and the sync's inline
        re-login form is present for a 302-to-login response."""
        api.post(
            "/api/v1/hydrants/", {"asset_tag": "F-1", "lat": 38.0, "lon": -122.0}, format="json"
        )
        body = signed_in.get("/field/").content.decode()
        assert 'data-asset-tag="F-1"' in body
        assert 'id="photo"' in body
        assert 'id="relogin"' in body
        assert 'id="relogin-submit"' in body

    def test_field_queue_empty_state_names_the_next_action(self):
        """The queue table is never headless: when IndexedDB holds nothing, the
        empty row says so AND names the control that fills it — the design
        system's law that an empty state tells the operator what to do, not
        what is missing."""
        from pathlib import Path

        js = (Path(__file__).resolve().parent.parent / "ledger" / "static" / "ledger" / "field.js").read_text()
        assert "Queue is empty" in js, "the queue lost its empty state"
        assert "Queue for later" in js.split("Queue is empty", 1)[1][:200], (
            "the queue's empty state does not name the action that fills it"
        )

    def test_field_queue_door_refuses_a_reading_the_server_must_refuse(self):
        """A reading without its date, pressures, or any flow figure can never
        sync — the server 400s it — so the queue door must refuse it BEFORE
        anything is stored. "Queued." said over an undeliverable row is fake
        success: the firefighter walks away believing the record is safe, and
        every later sync fails it forever."""
        from pathlib import Path

        js = (Path(__file__).resolve().parent.parent / "ledger" / "static" / "ledger" / "field.js").read_text()
        assert "function validateEntry(entry)" in js, "the queue door lost its validation"
        # Both entry points pass through the gate — not only the online one.
        assert js.count("var refusal = validateEntry(entry);") == 2, (
            "validateEntry must guard BOTH the online submit and the offline queue"
        )
        for needle in ("Static and residual pressures are both required",
                       "at least one pitot reading, or a meter total",
                       "outside the range the ledger accepts"):
            assert needle in js, f"the door lost the refusal: {needle}"

    def test_a_server_refusal_is_never_queued_as_if_it_were_connectivity(self):
        """A 400/409 is a verdict on the reading as entered, not a network
        failure: queueing it would store a row that fails every sync forever.
        Only genuine connectivity failures (no status, 5xx, session expiry)
        fall back to the queue."""
        from pathlib import Path

        js = (Path(__file__).resolve().parent.parent / "ledger" / "static" / "ledger" / "field.js").read_text()
        assert "error.status === 400 || error.status === 409" in js, (
            "the online submit no longer distinguishes a refusal from a connectivity failure"
        )
        assert "Not queued — the server refused this reading" in js, (
            "a refused reading must be named as refused, not queued"
        )
        # And the refusal marks the fields the server named (never a bare toast).
        assert 'HP.markInvalidFromBody(document.getElementById("capture-form")' in js

    def test_a_failed_local_write_is_never_reported_as_queued(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parent.parent / "ledger" / "static" / "ledger" / "field.js").read_text()
        assert "NOT queued — this browser refused the local write" in js, (
            "queuePut has no failure path — a failed IndexedDB write would report 'Queued.'"
        )

    def test_field_inputs_carry_the_engine_bounds(self, signed_in):
        """The offline door reads the engine's numeric bounds from the markup
        (the office sheet declares the same constants), so a mistyped pressure
        is refused at the field, not at the first sync."""
        from ledger import constants as C

        body = signed_in.get("/field/").content.decode()
        assert f'max="{C.MAX_STATIC_PSI}"' in body, "the pressure inputs lost the engine's ceiling"
        assert f'max="{C.MAX_PITOT_PSI}"' in body, "the pitot inputs lost the engine's ceiling"

    def test_poll_exhaustion_is_named_not_silent(self):
        """The panel notes promise the page watches a job "until it completes
        or fails, naming which one happened" — a poll that hits its attempt
        cap must say it stopped (and that the job is unaffected), never leave
        "running…" on screen forever."""
        from pathlib import Path

        static = Path(__file__).resolve().parent.parent / "ledger" / "static" / "ledger"
        coverage = (static / "coverage.js").read_text()
        packs = (static / "packs.js").read_text()
        for name, js in (("coverage.js", coverage), ("packs.js", packs)):
            assert "has stopped polling" in js, f"{name} stops polling silently"
            assert "reload to keep watching" in js, f"{name} does not name the remedy"
            assert "is unaffected" in js, f"{name} does not say the job itself is unaffected"

    def test_hydrants_page_shows_the_single_test_snapshot_note(self, signed_in):
        body = signed_in.get("/hydrants/").content.decode()
        assert "not a main-capacity model" in body


class TestInFlightJobPages:
    """A job started in another tab (or by another operator) parks the form
    AND hands the page the poll URL to watch — the panel note's promise ("the
    page polls the run until it completes or fails") must be true for a page
    loaded mid-job, not only for the page that started it."""

    def test_coverage_page_carries_the_resume_url_only_while_in_flight(self, signed_in, editor_user):
        from ledger.api_jobs import create_coverage_run

        body = signed_in.get("/coverage/").content.decode()
        assert "data-resume-poll" not in body, "no run in flight — nothing to resume"

        run = create_coverage_run(parcel_filter=None, user=editor_user)
        body = signed_in.get("/coverage/").content.decode()
        assert f'data-resume-poll="/api/v1/coverage-runs/{run.pk}/"' in body
        assert "Run coverage now" in body
        assert "disabled" in body

    def test_pack_page_carries_the_resume_url_only_while_rendering(self, signed_in, editor_user):
        from ledger import constants as C
        from ledger.api_jobs import create_coverage_run
        from ledger.models import EvidencePack

        body = signed_in.get("/packs/").content.decode()
        assert "data-resume-poll" not in body, "no pack rendering — nothing to resume"

        run = create_coverage_run(parcel_filter=None, user=editor_user)
        pack = EvidencePack.objects.create(run=run, status=C.PACK_STATUS_RENDERING)
        body = signed_in.get("/packs/").content.decode()
        assert f'data-resume-poll="/api/v1/evidence-packs/{pack.pk}/"' in body
        assert "disabled" in body
