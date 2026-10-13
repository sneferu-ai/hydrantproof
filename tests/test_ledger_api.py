"""The /api/v1/ ledger contract: pinned numbers, supersession, replay, deficiencies."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from ledger import constants as C
from ledger.models import Deficiency, FlowTest

pytestmark = pytest.mark.django_db

FIXTURE_A = {
    "tested_on": None,  # filled per test (must not be in the future)
    "static_psi": "80.00",
    "residual_psi": "40.00",
    "outlets": [
        {"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "28.00"}
    ],
}


def create_hydrant(api, asset_tag="H-100", **extra):
    payload = {"asset_tag": asset_tag, "lat": 38.0, "lon": -122.0}
    payload.update(extra)
    response = api.post("/api/v1/hydrants/", payload, format="json")
    assert response.status_code == 201, response.data
    return response.data


def post_fixture_a(api, hydrant_id, **overrides):
    payload = dict(FIXTURE_A)
    payload["tested_on"] = str(timezone.localdate())
    payload.update(overrides)
    return api.post(f"/api/v1/hydrants/{hydrant_id}/tests/", payload, format="json")


class TestHydrantCreation:
    def test_creation_writes_an_attributed_initial_status_event(self, api):
        body = create_hydrant(api)
        events = api.get(f"/api/v1/hydrants/{body['id']}/status-events/").data
        assert len(events) == 1
        assert events[0]["to_status"] == C.STATUS_IN_SERVICE
        assert events[0]["from_status"] == ""
        assert events[0]["reason"] == "Initial record"
        assert events[0]["changed_by"] is not None

    def test_initially_oos_requires_expected_return_or_indefinite(self, api):
        response = api.post(
            "/api/v1/hydrants/",
            {"asset_tag": "H-OOS", "lat": 38.0, "lon": -122.0, "initial_status": "OUT_OF_SERVICE"},
            format="json",
        )
        assert response.status_code == 400
        assert "expected_return_on" in response.data
        assert "indefinite" in response.data

    def test_initially_oos_with_expected_return(self, api):
        response = api.post(
            "/api/v1/hydrants/",
            {
                "asset_tag": "H-OOS2",
                "lat": 38.0,
                "lon": -122.0,
                "initial_status": "OUT_OF_SERVICE",
                "expected_return_on": str(timezone.localdate() + timedelta(days=10)),
                "initial_status_reason": "Main break",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["status"] == "OUT_OF_SERVICE"
        detail = api.get(f"/api/v1/hydrants/{response.data['id']}/").data
        assert detail["exclusion_code"] == "OUT_OF_SERVICE"
        assert detail["readiness"] == "NOT_READY"

    def test_future_effective_on_rejected(self, api):
        response = api.post(
            "/api/v1/hydrants/",
            {
                "asset_tag": "H-FUTURE",
                "lat": 38.0,
                "lon": -122.0,
                "effective_on": str(timezone.localdate() + timedelta(days=1)),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_out_of_range_coordinates_rejected(self, api):
        response = api.post(
            "/api/v1/hydrants/", {"asset_tag": "H-LAT", "lat": 85.0, "lon": -122.0}, format="json"
        )
        assert response.status_code == 400

    def test_duplicate_asset_tag_rejected(self, api):
        create_hydrant(api, "H-DUP")
        response = api.post(
            "/api/v1/hydrants/", {"asset_tag": "H-DUP", "lat": 38.0, "lon": -122.0}, format="json"
        )
        assert response.status_code == 400

    def test_mixed_patch_keeps_the_ownership_transfer_audit(self, api):
        """§10: an explicit source_org value in an in-app PATCH is audited as
        an ownership_transfer even when the same request changes other
        attributes (those stay 'manual')."""
        from ledger.models import HydrantAttributeChange

        hydrant = create_hydrant(api, "H-OWN", source_org="North Fork Water District")
        response = api.patch(
            f"/api/v1/hydrants/{hydrant['id']}/",
            {"source_org": "South Fork Water District",
             "location_description": "Corner of 1st & Main"},
            format="json",
        )
        assert response.status_code == 200
        rows = {
            row.field: row
            for row in HydrantAttributeChange.objects.filter(hydrant_id=hydrant["id"])
        }
        assert rows["source_org"].change_reason == "ownership_transfer"
        assert rows["source_org"].old_value == "North Fork Water District"
        assert rows["source_org"].new_value == "South Fork Water District"
        assert rows["location_description"].change_reason == "manual"


class TestFlowTestLedger:
    def test_fixture_a_pinned_numbers(self, api):
        hydrant = create_hydrant(api, "H-A")
        response = post_fixture_a(api, hydrant["id"])
        assert response.status_code == 201, response.data
        assert float(response.data["computed_total_gpm"]) == pytest.approx(887.9, abs=0.05)
        assert float(response.data["rated_gpm_20"]) == pytest.approx(1105.2, abs=0.15)
        assert response.data["capacity_class"] == "A"
        assert response.data["formula_version"] == C.CURRENT_FORMULA_VERSION

    def test_impossible_pressures_rejected(self, api):
        hydrant = create_hydrant(api, "H-IMP")
        response = post_fixture_a(api, hydrant["id"], residual_psi="90.00")
        assert response.status_code == 400
        assert "residual_psi" in response.data

    def test_future_tested_on_rejected(self, api):
        hydrant = create_hydrant(api, "H-FT")
        response = post_fixture_a(
            api, hydrant["id"], tested_on=str(timezone.localdate() + timedelta(days=1))
        )
        assert response.status_code == 400

    def test_client_uuid_replay_same_payload_is_idempotent(self, api):
        hydrant = create_hydrant(api, "H-REPLAY")
        client_uuid = "3f1c4a52-0f4b-4f5a-9f0e-3b1f4b8b8f01"
        first = post_fixture_a(api, hydrant["id"], client_uuid=client_uuid)
        assert first.status_code == 201
        second = post_fixture_a(api, hydrant["id"], client_uuid=client_uuid)
        assert second.status_code == 200
        assert second.data["duplicate_client_uuid"] is True
        assert second.data["id"] == first.data["id"]
        assert FlowTest.objects.filter(client_uuid=client_uuid).count() == 1

    def test_client_uuid_divergent_payload_conflicts(self, api):
        hydrant = create_hydrant(api, "H-CONFLICT")
        client_uuid = "9a2e5d2b-6b3a-4c8e-8a2f-0f1a2b3c4d5e"
        assert post_fixture_a(api, hydrant["id"], client_uuid=client_uuid).status_code == 201
        divergent = post_fixture_a(api, hydrant["id"], client_uuid=client_uuid, static_psi="70.00")
        assert divergent.status_code == 409
        assert divergent.data["detail"] == "client_uuid_conflict"
        assert "new client_uuid" in divergent.data["hint"]

    def test_duplicate_reading_is_flagged(self, api):
        hydrant = create_hydrant(api, "H-DUPREAD")
        assert post_fixture_a(api, hydrant["id"]).status_code == 201
        again = post_fixture_a(api, hydrant["id"])
        assert again.status_code == 201
        assert C.WARNING_DUPLICATE_READING in again.data["warnings"]

    def test_supersession_excludes_the_original_from_current(self, api):
        hydrant = create_hydrant(api, "H-SUPAPI")
        original = post_fixture_a(api, hydrant["id"]).data
        correction = post_fixture_a(
            api, hydrant["id"], supersedes=original["id"], residual_psi="41.00"
        )
        assert correction.status_code == 201
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert float(detail["latest_rated_gpm_20"]) == pytest.approx(
            float(correction.data["rated_gpm_20"]), abs=0.1
        )

    def test_cross_hydrant_supersession_rejected(self, api):
        hydrant_a = create_hydrant(api, "H-XA")
        hydrant_b = create_hydrant(api, "H-XB")
        test_b = post_fixture_a(api, hydrant_b["id"]).data
        response = post_fixture_a(api, hydrant_a["id"], supersedes=test_b["id"])
        assert response.status_code == 400
        assert "supersedes" in response.data

    def test_test_detail_is_read_only(self, api):
        hydrant = create_hydrant(api, "H-RO")
        test = post_fixture_a(api, hydrant["id"]).data
        url = f"/api/v1/hydrants/{hydrant['id']}/tests/{test['id']}/"
        assert api.get(url).status_code == 200
        assert api.patch(url, {"notes": "x"}, format="json").status_code == 405
        assert api.delete(url).status_code == 405

    def test_tested_on_future_rejected_by_serializer_or_service(self, api):
        hydrant = create_hydrant(api, "H-FUT")
        response = post_fixture_a(
            api, hydrant["id"], tested_on=str(timezone.localdate() + timedelta(days=3))
        )
        assert response.status_code == 400


class TestInspectionsAndDeficiencies:
    def test_fail_opens_one_gating_row_per_code(self, api):
        hydrant = create_hydrant(api, "H-FAIL")
        assert post_fixture_a(api, hydrant["id"]).status_code == 201
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL",
             "deficiency_codes": ["CAP", "LEAK"]},
            format="json",
        )
        assert response.status_code == 201
        rows = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data
        assert len(rows) == 2
        assert all(row["gates_credit"] for row in rows)
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert detail["exclusion_code"] == "INSPECTION_FAIL"
        assert detail["readiness"] == "NOT_READY"
        assert "INSPECTION_FAIL" in detail["gating_flags"]

    def test_deficiency_result_opens_non_gating_rows(self, api):
        hydrant = create_hydrant(api, "H-DEF")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "DEFICIENCY",
             "deficiency_codes": ["PAINT"]},
            format="json",
        )
        rows = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data
        assert len(rows) == 1
        assert rows[0]["gates_credit"] is False

    def test_ok_inspection_auto_closes_open_rows(self, api):
        hydrant = create_hydrant(api, "H-OK")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": ["CAP"]},
            format="json",
        )
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "OK", "deficiency_codes": []},
            format="json",
        )
        rows = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data
        assert rows[0]["status"] == "RESOLVED"
        assert rows[0]["resolution_kind"] == "auto_ok"
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert detail["open_deficiency"] is False

    def test_editor_resolve_clears_credit_exclusion(self, api):
        """EARS 7: the Deficiency table is the single source of truth — the
        editor resolution clears the gating row, so the INSPECTION_FAIL credit
        exclusion clears with it (a later OK inspection is the other route)."""
        hydrant = create_hydrant(api, "H-RESOLVE")
        assert post_fixture_a(api, hydrant["id"]).status_code == 201
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert detail["exclusion_code"] is None
        assert detail["district_credit_eligible"] is True
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": ["CAP"]},
            format="json",
        )
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert detail["exclusion_code"] == "INSPECTION_FAIL"
        assert detail["district_credit_eligible"] is False
        deficiency = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/deficiencies/{deficiency['id']}/resolve/",
            {"resolution_note": "Cap replaced and pressure-tested"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == "RESOLVED"
        assert response.data["resolution_kind"] == "manual"
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        # The gating row is gone, so the credit exclusion is gone with it.
        assert detail["exclusion_code"] is None
        assert detail["district_credit_eligible"] is True
        assert detail["open_deficiency"] is False
        # The FAIL inspection itself stays on the record, so readiness still
        # gates on it (§6: READY requires latest inspection result ≠ FAIL)
        # until a later OK inspection replaces it.
        assert detail["readiness"] == "NOT_READY"
        assert "INSPECTION_FAIL" in detail["gating_flags"]

    def test_resolution_note_required(self, api):
        hydrant = create_hydrant(api, "H-NOTE")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": ["CAP"]},
            format="json",
        )
        deficiency = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/deficiencies/{deficiency['id']}/resolve/",
            {}, format="json",
        )
        assert response.status_code == 400

    def test_resolve_persists_assignment_and_due_date(self, api):
        """§3/§5: the resolve route is the one non-admin write path for
        ``assigned_to``/``due_on`` — the stored row, not just the 200 response,
        must carry what the editor supplied."""
        hydrant = create_hydrant(api, "H-ASSIGN")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": ["CAP"]},
            format="json",
        )
        deficiency = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        due = timezone.localdate() + timedelta(days=30)
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/deficiencies/{deficiency['id']}/resolve/",
            {"resolution_note": "Cap ordered from the yard",
             "assigned_to": "Crew 3", "due_on": str(due)},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["assigned_to"] == "Crew 3"
        assert response.data["due_on"] == str(due)
        # The assignment survives a re-read through the API ...
        stored = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        assert stored["assigned_to"] == "Crew 3"
        assert stored["due_on"] == str(due)
        assert stored["status"] == "RESOLVED"
        # ... and is on the row itself, not only in the response echo.
        row = Deficiency.objects.get(pk=deficiency["id"])
        assert row.assigned_to == "Crew 3"
        assert row.due_on == due

    def test_resolve_without_assignment_keeps_the_fields_empty(self, api):
        """Omitting the optional fields leaves them untouched (they are not
        overwritten with blanks by the resolve write)."""
        hydrant = create_hydrant(api, "H-PLAIN")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "DEFICIENCY",
             "deficiency_codes": ["PAINT"]},
            format="json",
        )
        deficiency = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/deficiencies/{deficiency['id']}/resolve/",
            {"resolution_note": "Touched up"}, format="json",
        )
        assert response.status_code == 200
        row = Deficiency.objects.get(pk=deficiency["id"])
        assert row.assigned_to == ""
        assert row.due_on is None

    def test_resolve_rejects_due_on_beyond_a_year(self, api):
        """§5: ``due_on`` is bounded at today+365 — the resolve route is the
        only place the bound can be enforced."""
        hydrant = create_hydrant(api, "H-DUE")
        api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": ["CAP"]},
            format="json",
        )
        deficiency = api.get(f"/api/v1/hydrants/{hydrant['id']}/deficiencies/").data[0]
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/deficiencies/{deficiency['id']}/resolve/",
            {"resolution_note": "Too far out",
             "due_on": str(timezone.localdate() + timedelta(days=366))},
            format="json",
        )
        assert response.status_code == 400
        assert "due_on" in response.data
        # The row is untouched by the refused resolve.
        row = Deficiency.objects.get(pk=deficiency["id"])
        assert row.status == "OPEN"
        assert row.due_on is None

    def test_fail_without_codes_rejected(self, api):
        hydrant = create_hydrant(api, "H-NOCODE")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate()), "result": "FAIL", "deficiency_codes": []},
            format="json",
        )
        assert response.status_code == 400

    def test_future_inspection_rejected(self, api):
        hydrant = create_hydrant(api, "H-FUTINSP")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/inspections/",
            {"inspected_on": str(timezone.localdate() + timedelta(days=1)), "result": "OK"},
            format="json",
        )
        assert response.status_code == 400


class TestStatusLifecycle:
    def test_six_transitions_and_required_fields(self, api):
        hydrant = create_hydrant(api, "H-LIFE")
        url = f"/api/v1/hydrants/{hydrant['id']}/status-events/"

        # IN_SERVICE -> OUT_OF_SERVICE requires expected_return_on or indefinite
        missing = api.post(url, {"to_status": "OUT_OF_SERVICE", "reason": "Main break"}, format="json")
        assert missing.status_code == 400

        oos = api.post(
            url,
            {"to_status": "OUT_OF_SERVICE", "reason": "Main break",
             "expected_return_on": str(timezone.localdate() + timedelta(days=7))},
            format="json",
        )
        assert oos.status_code == 201
        assert api.get(f"/api/v1/hydrants/{hydrant['id']}/").data["status"] == "OUT_OF_SERVICE"

        back = api.post(url, {"to_status": "IN_SERVICE", "reason": "Repaired"}, format="json")
        assert back.status_code == 201

        retired = api.post(url, {"to_status": "RETIRED", "reason": "Removed with main replacement"},
                           format="json")
        assert retired.status_code == 201

        # RETIRED -> IN_SERVICE requires a reason
        no_reason = api.post(url, {"to_status": "IN_SERVICE"}, format="json")
        assert no_reason.status_code == 400

        revive = api.post(url, {"to_status": "IN_SERVICE", "reason": "Recorded in error"},
                          format="json")
        assert revive.status_code == 201

    def test_illegal_transition_rejected(self, api):
        hydrant = create_hydrant(api, "H-ILLEGAL")
        response = api.post(
            f"/api/v1/hydrants/{hydrant['id']}/status-events/",
            {"to_status": "IN_SERVICE", "reason": "no-op"}, format="json",
        )
        assert response.status_code == 400

    def test_reversal_pins_effective_on_and_derives_statuses(self, api):
        hydrant = create_hydrant(api, "H-REV")
        url = f"/api/v1/hydrants/{hydrant['id']}/status-events/"
        effective = str(timezone.localdate())
        event = api.post(
            url,
            {"to_status": "OUT_OF_SERVICE", "reason": "Mistaken record", "indefinite": True,
             "effective_on": effective},
            format="json",
        ).data
        reversal = api.post(url, {"reversal_of": event["id"], "reason": "Recorded in error"},
                            format="json")
        assert reversal.status_code == 201
        assert reversal.data["effective_on"] == effective
        assert reversal.data["from_status"] == "OUT_OF_SERVICE"
        assert reversal.data["to_status"] == "IN_SERVICE"

    def test_backdated_event_never_becomes_the_current_status(self, api):
        hydrant = create_hydrant(api, "H-BACKDATE")
        url = f"/api/v1/hydrants/{hydrant['id']}/status-events/"
        response = api.post(
            url,
            {"to_status": "OUT_OF_SERVICE", "reason": "Recorded late", "indefinite": True,
             "effective_on": str(timezone.localdate() - timedelta(days=3))},
            format="json",
        )
        assert response.status_code == 201
        # max (effective_on, changed_at, id): the initial event (today) still wins.
        assert api.get(f"/api/v1/hydrants/{hydrant['id']}/").data["status"] == "IN_SERVICE"

    def test_reversal_targets_only_the_current_event(self, api):
        hydrant = create_hydrant(api, "H-REV2")
        url = f"/api/v1/hydrants/{hydrant['id']}/status-events/"
        first = api.post(url, {"to_status": "OUT_OF_SERVICE", "reason": "a", "indefinite": True},
                         format="json").data
        api.post(url, {"to_status": "IN_SERVICE", "reason": "b"}, format="json")
        response = api.post(url, {"reversal_of": first["id"], "reason": "c"}, format="json")
        assert response.status_code == 400


class TestComputePreview:
    def test_fixture_a_numbers(self, api):
        response = api.post(
            "/api/v1/compute-preview/",
            {"tested_on": str(timezone.localdate()), "static_psi": "80.00", "residual_psi": "40.00",
             "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                          "pitot_psi": "28.00"}]},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["computed_total_gpm"] == pytest.approx(887.9, abs=0.05)
        assert response.data["rated_gpm_20"] == pytest.approx(1105.2, abs=0.15)
        assert response.data["capacity_class"] == "A"

    def test_invalid_pressures_answer_400_with_field(self, api):
        response = api.post(
            "/api/v1/compute-preview/",
            {"tested_on": str(timezone.localdate()), "static_psi": "80.00", "residual_psi": "80.00",
             "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                          "pitot_psi": "28.00"}]},
            format="json",
        )
        assert response.status_code == 400
        assert "residual_psi" in response.data


class TestAuthorization:
    def test_viewer_write_is_403(self, viewer_api, api):
        hydrant = create_hydrant(api, "H-VIEWER")
        response = viewer_api.post(
            f"/api/v1/hydrants/{hydrant['id']}/tests/",
            {**FIXTURE_A, "tested_on": str(timezone.localdate())}, format="json",
        )
        assert response.status_code == 403

    def test_viewer_reads_everything(self, viewer_api, api):
        create_hydrant(api, "H-READ")
        assert viewer_api.get("/api/v1/hydrants/").status_code == 200

    def test_anonymous_json_is_401_with_the_pinned_detail(self, anonymous_api):
        response = anonymous_api.get("/api/v1/hydrants/")
        assert response.status_code == 401
        assert response.data["detail"] == "Authentication required."

    def test_anonymous_html_redirects_to_login(self, anonymous_api):
        response = anonymous_api.get("/dashboard/")
        assert response.status_code == 302
        assert "/login/" in response["Location"]


# ---------------------------------------------------------------------------
# Repair-round regressions (native static review BUG-001/005/006/012/015)
# ---------------------------------------------------------------------------
class TestDatabasePaging:
    """List endpoints page at the database, never by materializing the table."""

    def test_parcel_list_pages_at_the_database(self, api, monkeypatch):
        from ledger import api as api_module
        from ledger.models import Parcel

        Parcel.objects.bulk_create(
            [Parcel(parcel_id=f"PG-{index:03d}", lat=38.0, lon=-122.0) for index in range(60)]
        )
        captured = {}
        original = api_module._paginated

        def spy(request, rows, serializer_class, context):
            captured["rows"] = rows
            return original(request, rows, serializer_class, context)

        monkeypatch.setattr(api_module, "_paginated", spy)
        response = api.get("/api/v1/parcels/")
        assert response.data["count"] == 60
        assert len(response.data["results"]) == 50
        # A lazy queryset (COUNT + LIMIT in SQL), not a materialized list.
        assert hasattr(captured["rows"], "filter")

    def test_hydrant_test_list_pages_at_the_database(self, api, monkeypatch):
        from ledger import api as api_module

        hydrant = create_hydrant(api, "H-PAGE")
        for _ in range(3):
            assert post_fixture_a(api, hydrant["id"]).status_code == 201
        captured = {}
        original = api_module._paginated

        def spy(request, rows, serializer_class, context):
            captured["rows"] = rows
            return original(request, rows, serializer_class, context)

        monkeypatch.setattr(api_module, "_paginated", spy)
        response = api.get(f"/api/v1/hydrants/{hydrant['id']}/tests/")
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3
        assert hasattr(captured["rows"], "filter")

    def test_inspection_list_is_stably_ordered(self, api):
        hydrant = create_hydrant(api, "H-ORD")
        today = timezone.localdate()
        for offset in range(3):
            response = api.post(
                f"/api/v1/hydrants/{hydrant['id']}/inspections/",
                {"inspected_on": str(today - timedelta(days=offset)), "result": "OK"},
                format="json",
            )
            assert response.status_code == 201
        page = api.get(f"/api/v1/hydrants/{hydrant['id']}/inspections/").data
        # Newest first — a stable key, so pagination cannot reshuffle rows.
        assert [row["inspected_on"] for row in page["results"]] == [
            str(today), str(today - timedelta(days=1)), str(today - timedelta(days=2))
        ]


class TestLostCreateRaces:
    """A concurrent duplicate create answers the documented 400, not a 500."""

    def test_hydrant_create_survives_a_lost_race(self, api, monkeypatch):
        from django.db import IntegrityError

        import ledger.api as api_module

        def collide(*args, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(api_module, "_create_hydrant_with_initial_event", collide)
        response = api.post(
            "/api/v1/hydrants/", {"asset_tag": "H-RACE", "lat": 38.0, "lon": -122.0},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["asset_tag"] == ["a hydrant with this asset_tag already exists"]

    def test_parcel_create_survives_a_lost_race(self, api, monkeypatch):
        from django.db import IntegrityError

        from ledger.models import Parcel

        def collide(**kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(Parcel.objects, "create", collide)
        response = api.post(
            "/api/v1/parcels/", {"parcel_id": "PAR-RACE", "lat": 38.0, "lon": -122.0},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["parcel_id"] == ["a parcel with this parcel_id already exists"]


class TestOosAcknowledgement:
    """Acknowledgements apply to expired OOS hydrants only, and cannot restart
    their own amber window (the append-only table's rows are permanent)."""

    def _to_expired_oos(self, api, hydrant_id, days_past=2):
        response = api.post(
            f"/api/v1/hydrants/{hydrant_id}/status-events/",
            {"to_status": "OUT_OF_SERVICE", "reason": "main break",
             "expected_return_on": str(timezone.localdate() - timedelta(days=days_past))},
            format="json",
        )
        assert response.status_code == 201

    def test_acknowledgement_requires_an_expired_oos_hydrant(self, api):
        hydrant = create_hydrant(api, "ACK-INSERVICE")
        response = api.post(f"/api/v1/hydrants/{hydrant['id']}/oos-acknowledgements/",
                            {}, format="json")
        assert response.status_code == 400
        # OOS with a future expected return is not expired yet.
        future = create_hydrant(api, "ACK-FUTURE")
        api.post(
            f"/api/v1/hydrants/{future['id']}/status-events/",
            {"to_status": "OUT_OF_SERVICE", "reason": "repair",
             "expected_return_on": str(timezone.localdate() + timedelta(days=7))},
            format="json",
        )
        assert api.post(f"/api/v1/hydrants/{future['id']}/oos-acknowledgements/",
                        {}, format="json").status_code == 400
        # Retired hydrants are never acknowledgeable either.
        retired = create_hydrant(api, "ACK-RETIRED")
        api.post(f"/api/v1/hydrants/{retired['id']}/status-events/",
                 {"to_status": "RETIRED", "reason": "removed"}, format="json")
        assert api.post(f"/api/v1/hydrants/{retired['id']}/oos-acknowledgements/",
                        {}, format="json").status_code == 400

    def test_expired_oos_is_acknowledgeable(self, api):
        from ledger.models import OosAcknowledgement

        hydrant = create_hydrant(api, "ACK-OK")
        self._to_expired_oos(api, hydrant["id"])
        response = api.post(f"/api/v1/hydrants/{hydrant['id']}/oos-acknowledgements/",
                            {"note": "crew notified"}, format="json")
        assert response.status_code == 201
        assert response.data["note"] == "crew notified"
        assert OosAcknowledgement.objects.filter(hydrant_id=hydrant["id"]).count() == 1
        detail = api.get(f"/api/v1/hydrants/{hydrant['id']}/").data
        assert detail["oos_expired"] is True
        assert detail["oos_acknowledged_at"] is not None  # inside the amber window

    def test_repeat_acknowledgement_does_not_restart_the_window(self, api):
        from ledger.models import OosAcknowledgement

        hydrant = create_hydrant(api, "ACK-REPEAT")
        self._to_expired_oos(api, hydrant["id"])
        first = api.post(f"/api/v1/hydrants/{hydrant['id']}/oos-acknowledgements/",
                         {"note": "first"}, format="json")
        assert first.status_code == 201
        second = api.post(f"/api/v1/hydrants/{hydrant['id']}/oos-acknowledgements/",
                          {"note": "again"}, format="json")
        # Idempotent inside the amber window: no new append-only row.
        assert second.status_code == 200
        assert second.data["id"] == first.data["id"]
        assert OosAcknowledgement.objects.filter(hydrant_id=hydrant["id"]).count() == 1

    def test_a_new_acknowledgement_lands_after_the_amber_window(self, api):
        from unittest.mock import patch

        from ledger.models import OosAcknowledgement

        hydrant = create_hydrant(api, "ACK-REOPEN")
        self._to_expired_oos(api, hydrant["id"])
        stale = timezone.now() - timedelta(days=31)
        with patch("django.utils.timezone.now", return_value=stale):
            OosAcknowledgement.objects.create(hydrant_id=hydrant["id"], note="month-old")
        response = api.post(f"/api/v1/hydrants/{hydrant['id']}/oos-acknowledgements/",
                            {"note": "fresh"}, format="json")
        assert response.status_code == 201
        assert OosAcknowledgement.objects.filter(hydrant_id=hydrant["id"]).count() == 2


class TestPatchChangeDetection:
    """A semantic no-op PATCH writes no row to the append-only audit tables."""

    def test_equivalent_numeric_patch_writes_no_audit_row(self, api):
        from ledger.models import HydrantAttributeChange

        hydrant = create_hydrant(api, "H-NOOP", lat=38.0)
        response = api.patch(f"/api/v1/hydrants/{hydrant['id']}/",
                             {"lat": "38.00"}, format="json")
        assert response.status_code == 200
        assert not HydrantAttributeChange.objects.filter(
            hydrant_id=hydrant["id"], field="lat"
        ).exists()

    def test_a_real_change_still_writes_the_audit_row(self, api):
        from ledger.models import HydrantAttributeChange

        hydrant = create_hydrant(api, "H-REAL", lat=38.0)
        assert api.patch(f"/api/v1/hydrants/{hydrant['id']}/",
                         {"lat": "38.5"}, format="json").status_code == 200
        assert HydrantAttributeChange.objects.filter(
            hydrant_id=hydrant["id"], field="lat"
        ).exists()

    def test_equivalent_parcel_patch_writes_no_audit_row(self, api):
        from ledger.models import Parcel, ParcelAttributeChange

        parcel = Parcel.objects.create(parcel_id="PAR-NOOP", lat=38.0, lon=-122.0)
        response = api.patch(f"/api/v1/parcels/{parcel.pk}/", {"lat": "38.00"}, format="json")
        assert response.status_code == 200
        assert not ParcelAttributeChange.objects.filter(parcel=parcel, field="lat").exists()

    def test_string_fields_still_compare_as_strings(self, api):
        from ledger.models import Parcel, ParcelAttributeChange

        parcel = Parcel.objects.create(parcel_id="PAR-STR", lat=38.0, lon=-122.0, address="7 Main")
        assert api.patch(f"/api/v1/parcels/{parcel.pk}/",
                         {"address": "007 Main"}, format="json").status_code == 200
        assert ParcelAttributeChange.objects.filter(
            parcel=parcel, field="address", new_value="007 Main"
        ).exists()
