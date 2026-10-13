"""Two-phase CSV imports, GeoJSON overlays, and the ledger export route."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from ledger import constants as C
from ledger.models import (
    FlowTest,
    Hydrant,
    HydrantAttributeChange,
    ImportBatch,
    MapOverlay,
    Parcel,
    ParcelAttributeChange,
)

pytestmark = pytest.mark.django_db

HYDRANT_HEADER = (
    "asset_tag,lat,lon,location_description,manufacturer,model,year_installed,"
    "main_size_inches,outlet_diameter_in,discharge_coefficient,ownership,source_org,"
    "notes,initial_status,initial_status_reason,expected_return_on,indefinite"
)


def upload(client, path, content: bytes, **fields):
    data = {"file": io.BytesIO(content)}
    data.update(fields)
    return client.post(path, data, format="multipart")


def dry_run_hydrants(api, csv_text: str, **fields):
    return upload(api, "/api/v1/imports/hydrants/", csv_text.encode(), mode="dry_run", **fields)


class TestHydrantImport:
    GOOD = (
        HYDRANT_HEADER + "\n"
        'IMP-001,38.0,-122.0,Corner of Main,Sample Works,S-100,1998,6.00,2.50,0.90,'
        'Sample District,Sample Water Co,first import,IN_SERVICE,,\n'
    )

    def test_dry_run_validates_stores_and_returns_a_token(self, api):
        response = dry_run_hydrants(api, self.GOOD, supplier="Sample Water Co")
        assert response.status_code == 200, response.data
        assert response.data["rows_ok"] == 1
        assert response.data["rows_rejected"] == 0
        token = response.data["commit_token"]
        batch = ImportBatch.objects.get(pk=response.data["batch"]["id"])
        # The token is returned once, stored hashed, and the file is stored.
        assert batch.commit_token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert batch.commit_token_hash != token
        from ledger.media import media_root

        stored = media_root() / f"imports/{batch.pk}.csv"
        assert stored.exists()
        assert batch.file_sha256 == hashlib.sha256(self.GOOD.encode()).hexdigest()
        # Dry-run applies nothing.
        assert Hydrant.objects.count() == 0

    def test_commit_creates_with_initial_status_event(self, api):
        token = dry_run_hydrants(api, self.GOOD, supplier="Sample Water Co").data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200, response.data
        assert response.data["rows_ok"] == 1
        hydrant = Hydrant.objects.get(asset_tag="IMP-001")
        assert hydrant.source == Hydrant.SOURCE_CSV
        assert hydrant.source_org == "Sample Water Co"
        events = hydrant.status_events.all()
        assert len(events) == 1
        assert events[0].to_status == "IN_SERVICE"
        assert events[0].changed_by is not None  # attributed

    def test_commit_token_is_single_use_and_user_bound(self, api):
        token = dry_run_hydrants(api, self.GOOD, supplier="Sample Water Co").data["commit_token"]
        # A different user cannot spend it (user-bound).
        import base64

        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        second = User.objects.create_user(username="editor-2", password="hydrantproof-test-pass")
        second.groups.add(Group.objects.get(name="editor"))
        client2 = APIClient()
        client2.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(
            b"editor-2:hydrantproof-test-pass").decode())
        response = client2.post("/api/v1/imports/hydrants/",
                                {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 400
        assert response.data["detail"] == "commit_token invalid"
        # The owner spends it once.
        assert api.post("/api/v1/imports/hydrants/",
                        {"mode": "commit", "commit_token": token},
                        format="multipart").status_code == 200
        # Replay is refused (single-use: the hash is spent at commit).
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 400
        assert response.data["detail"] == "commit_token invalid"

    def test_reupload_with_a_different_file_is_hash_mismatch(self, api):
        token = dry_run_hydrants(api, self.GOOD).data["commit_token"]
        other = self.GOOD.replace("IMP-001", "IMP-001B")
        response = upload(api, "/api/v1/imports/hydrants/", other.encode(),
                          mode="commit", commit_token=token)
        assert response.status_code == 400
        assert response.data["detail"] == "file_hash_mismatch"
        assert response.data["expected"] != response.data["actual"]

    def test_unknown_column_names_the_column(self, api):
        bad = "asset_tag,lat,lon,wat\nX,38,-122,y\n"
        response = dry_run_hydrants(api, bad)
        assert response.status_code == 400
        assert "wat" in response.data["detail"]

    def test_row_level_errors_skip_bad_rows(self, api):
        body = (
            HYDRANT_HEADER + "\n"
            "OK-1,38.0,-122.0,,,,,,,,,,,,\n"
            "BAD-LAT,91.0,-122.0,,,,,,,,,,,,\n"  # |lat| > 80
            ",38.0,-122.0,,,,,,,,,,,,\n"          # missing asset_tag
        )
        token = dry_run_hydrants(api, body).data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200
        assert response.data["rows_ok"] == 1
        assert response.data["rows_rejected"] == 2
        assert {e["field"] for e in response.data["errors"]} == {"lat", "asset_tag"}
        assert Hydrant.objects.filter(asset_tag="OK-1").exists()

    def test_initially_oos_row_needs_expected_return(self, api):
        row = ["OOS-1", "38.0", "-122.0"] + [""] * 10 + ["OUT_OF_SERVICE", "Main broken", "", ""]
        body = HYDRANT_HEADER + "\n" + ",".join(row) + "\n"
        response = dry_run_hydrants(api, body)
        assert response.data["rows_rejected"] == 1
        assert response.data["errors"][0]["field"] == "expected_return_on"

    def test_in_file_duplicate_last_wins_warned(self, api):
        body = (
            HYDRANT_HEADER + "\n"
            "DUP-1,38.0,-122.0,first,,,,,,,,,,,\n"
            "DUP-1,38.5,-121.5,second,,,,,,,,,,,\n"
        )
        token = dry_run_hydrants(api, body).data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200
        assert response.data["rows_ok"] == 1
        assert any("duplicate asset_tag" in w["message"] for w in response.data["warnings"])
        hydrant = Hydrant.objects.get(asset_tag="DUP-1")
        assert hydrant.lat == 38.5  # the later row won

    def test_status_never_silently_upserted(self, api):
        body = HYDRANT_HEADER + "\nUP-1,38.0,-122.0,,,,,,,,,,,,\n"
        token = dry_run_hydrants(api, body).data["commit_token"]
        api.post("/api/v1/imports/hydrants/", {"mode": "commit", "commit_token": token},
                 format="multipart")
        # A second file naming initial_status for the existing hydrant errors.
        row = ["UP-1", "38.0", "-122.0"] + [""] * 10 + ["OUT_OF_SERVICE", "", "2026-12-01", ""]
        body2 = HYDRANT_HEADER + "\n" + ",".join(row) + "\n"
        response = dry_run_hydrants(api, body2)
        assert response.data["rows_rejected"] == 1
        assert "status events" in response.data["errors"][0]["message"]

    def test_upsert_updates_and_audits_changed_fields_only(self, api):
        body = HYDRANT_HEADER + "\nAUD-1,38.0,-122.0,Old location,,,,,,,,,,,\n"
        token = dry_run_hydrants(api, body).data["commit_token"]
        api.post("/api/v1/imports/hydrants/", {"mode": "commit", "commit_token": token},
                 format="multipart")
        body2 = HYDRANT_HEADER + "\nAUD-1,38.0,-122.0,New location,,,,,,,,,,,\n"
        token2 = dry_run_hydrants(api, body2).data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token2}, format="multipart")
        assert response.data["rows_ok"] == 1
        hydrant = Hydrant.objects.get(asset_tag="AUD-1")
        assert hydrant.location_description == "New location"
        change = HydrantAttributeChange.objects.get(hydrant=hydrant, field="location_description")
        assert change.change_reason == "import"
        assert change.import_batch is not None


class TestConflictPolicy:
    def _import(self, api, tag="CF-1", supplier="Alpha Water", location="first"):
        body = (
            HYDRANT_HEADER + "\n"
            f"{tag},38.0,-122.0,{location},,,,,,,Alpha Water,,,,\n"
        )
        token = dry_run_hydrants(api, body, supplier=supplier).data["commit_token"]
        return api.post("/api/v1/imports/hydrants/",
                        {"mode": "commit", "commit_token": token}, format="multipart")

    def test_conflicting_supplier_is_skipped(self, api):
        assert self._import(api).status_code == 200
        # A different supplier tries to modify the same hydrant.
        body = HYDRANT_HEADER + "\nCF-1,38.1,-122.0,claimed,,,,,,,,,,,\n"
        token = dry_run_hydrants(api, body, supplier="Beta Water").data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.data["rows_ok"] == 0
        assert response.data["rows_rejected"] == 1
        assert "CONFLICT" in response.data["errors"][0]["message"]
        hydrant = Hydrant.objects.get(asset_tag="CF-1")
        assert hydrant.lat == 38.0  # untouched

    def test_allow_overwrite_applies_and_audits(self, api):
        self._import(api)
        body = HYDRANT_HEADER + "\nCF-1,38.1,-122.0,claimed,,,,,,,,,,,\n"
        token = dry_run_hydrants(api, body, supplier="Beta Water",
                                 allow_overwrite="true").data["commit_token"]
        batch = ImportBatch.objects.get(commit_token_hash=hashlib.sha256(token.encode()).hexdigest())
        assert batch.allow_overwrite is True  # recorded on the batch
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.data["rows_ok"] == 1
        hydrant = Hydrant.objects.get(asset_tag="CF-1")
        assert hydrant.lat == 38.1
        change = HydrantAttributeChange.objects.get(hydrant=hydrant, field="lat")
        assert change.change_reason == "import_overwrite_conflict"

    def test_blank_source_org_conflicts_with_non_blank_supplier(self, api):
        Hydrant.objects.create(asset_tag="BLANK-ORG", lat=38.0, lon=-122.0, source_org="")
        body = HYDRANT_HEADER + "\nBLANK-ORG,38.2,-122.0,,,,,,,,,,,,\n"
        token = dry_run_hydrants(api, body, supplier="Any Utility").data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.data["rows_rejected"] == 1


class TestParcelImport:
    HEADER = "parcel_id,latitude,longitude,address,structure_type,is_protected_structure,structure_lat,structure_lon,source_org"

    def test_round_trip(self, api):
        body = (
            self.HEADER + "\n"
            "PAR-1,38.0,-122.0,10 Main St,house,true,38.0001,-122.0001,County Assessor\n"
            "PAR-2,38.001,-122.001,12 Main St,barn,false,,,\n"
        )
        token = upload(api, "/api/v1/imports/parcels/", body.encode(), mode="dry_run",
                       supplier="County Assessor").data["commit_token"]
        response = api.post("/api/v1/imports/parcels/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200, response.data
        assert response.data["rows_ok"] == 2
        parcel = Parcel.objects.get(parcel_id="PAR-1")
        assert parcel.is_protected_structure is True
        assert parcel.structure_lat == pytest.approx(38.0001)
        assert Parcel.objects.get(parcel_id="PAR-2").is_protected_structure is False

    def test_structure_override_pairing_enforced(self, api):
        body = self.HEADER + "\nPAR-3,38.0,-122.0,,,true,38.0001,,\n"
        response = upload(api, "/api/v1/imports/parcels/", body.encode(), mode="dry_run")
        assert response.data["rows_rejected"] == 1
        assert response.data["errors"][0]["field"] == "structure_lat"

    def test_parcel_patch_audits_via_attribute_change(self, api):
        body = self.HEADER + "\nPAR-9,38.0,-122.0,Old address,,true,,,\n"
        token = upload(api, "/api/v1/imports/parcels/", body.encode(), mode="dry_run").data["commit_token"]
        api.post("/api/v1/imports/parcels/", {"mode": "commit", "commit_token": token},
                 format="multipart")
        body2 = self.HEADER + "\nPAR-9,38.0,-122.0,New address,,true,,,\n"
        token2 = upload(api, "/api/v1/imports/parcels/", body2.encode(), mode="dry_run").data["commit_token"]
        api.post("/api/v1/imports/parcels/", {"mode": "commit", "commit_token": token2},
                 format="multipart")
        assert ParcelAttributeChange.objects.filter(
            parcel__parcel_id="PAR-9", field="address", new_value="New address"
        ).exists()


class TestFlowTestImport:
    HEADER = ("asset_tag,tested_on,static_psi,residual_psi,pitot_readings,"
              "measured_total_gpm,crew_names,notes,client_uuid")

    def _hydrant(self, api, tag="FT-1"):
        response = api.post("/api/v1/hydrants/", {"asset_tag": tag, "lat": 38.0, "lon": -122.0},
                            format="json")
        assert response.status_code == 201
        return response.data["id"]

    def test_historical_import_computes_the_snapshot(self, api):
        self._hydrant(api)
        today = str(timezone.localdate())
        body = (
            self.HEADER + "\n"
            f"FT-1,{today},80.00,40.00,2.50:0.90:28.00,,Smith;Jones,historical,\n"
        )
        token = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run",
                       ).data["commit_token"]
        response = api.post("/api/v1/imports/flow-tests/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200, response.data
        assert response.data["rows_ok"] == 1
        test = FlowTest.objects.get(hydrant__asset_tag="FT-1")
        # Fixture A's pinned numbers — the import path runs the same engine.
        assert float(test.computed_total_gpm) == pytest.approx(887.9, abs=0.05)
        assert float(test.rated_gpm_20) == pytest.approx(1105.2, abs=0.15)
        assert test.formula_version == C.CURRENT_FORMULA_VERSION

    def test_unknown_flowed_tag_is_a_row_error_naming_the_tag(self, api):
        self._hydrant(api)
        today = str(timezone.localdate())
        body = (
            self.HEADER + "\n"
            f"FT-1,{today},80.00,40.00,2.50:0.90:28.00:steamer:GHOST-9,,,,\n"
        )
        response = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run")
        assert response.data["rows_rejected"] == 1
        assert "GHOST-9" in response.data["errors"][0]["message"]

    def test_known_flowed_tag_is_preserved(self, api):
        self._hydrant(api, "FT-2")
        self._hydrant(api, "FT-3")
        today = str(timezone.localdate())
        body = (
            self.HEADER + "\n"
            f"FT-2,{today},80.00,40.00,2.50:0.90:28.00:steamer:FT-3,,,,\n"
        )
        token = upload(api, "/api/v1/imports/flow-tests/", body.encode(),
                       mode="dry_run").data["commit_token"]
        response = api.post("/api/v1/imports/flow-tests/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.data["rows_ok"] == 1
        outlet = FlowTest.objects.get(hydrant__asset_tag="FT-2").outlets.first()
        assert outlet.flow_hydrant.asset_tag == "FT-3"  # re-attribution preserved

    def test_edge_matrix_runs_at_import(self, api):
        self._hydrant(api)
        today = str(timezone.localdate())
        body = self.HEADER + f"\nFT-1,{today},80.00,95.00,2.50:0.90:28.00,,,,\n"
        response = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run")
        assert response.data["rows_rejected"] == 1
        assert response.data["errors"][0]["field"] == "residual_psi"

    def test_client_uuid_replay_is_idempotent(self, api):
        self._hydrant(api)
        today = str(timezone.localdate())
        uid = "12345678-1234-5678-1234-567812345678"
        body = self.HEADER + f"\nFT-1,{today},80.00,40.00,2.50:0.90:28.00,,,,{uid}\n"
        token = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run").data["commit_token"]
        api.post("/api/v1/imports/flow-tests/", {"mode": "commit", "commit_token": token},
                 format="multipart")
        assert FlowTest.objects.count() == 1
        # Re-importing the same file skips the replayed row (no duplicate).
        token2 = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run").data["commit_token"]
        response = api.post("/api/v1/imports/flow-tests/",
                            {"mode": "commit", "commit_token": token2}, format="multipart")
        assert response.data["rows_ok"] == 0
        assert any("duplicate_client_uuid" in w["message"] for w in response.data["warnings"])
        assert FlowTest.objects.count() == 1

    def test_field_csv_export_format_round_trips(self, api):
        """The /field/ queued-readings CSV is exactly the historical format."""
        self._hydrant(api)
        today = str(timezone.localdate())
        # The field page's exporter shape (field.js): header + d:c:p triples.
        body = (
            "asset_tag,tested_on,static_psi,residual_psi,pitot_readings,measured_total_gpm,crew_names,notes,client_uuid\r\n"
            f"FT-1,{today},80.00,40.00,2.50:0.90:28.00,,,queued 2026-10-03T08:00:00Z,\r\n"
        )
        token = upload(api, "/api/v1/imports/flow-tests/", body.encode(), mode="dry_run").data["commit_token"]
        response = api.post("/api/v1/imports/flow-tests/",
                            {"mode": "commit", "commit_token": token}, format="multipart")
        assert response.status_code == 200
        assert response.data["rows_ok"] == 1


class TestImportHistory:
    def test_history_lists_batches_without_tokens(self, api):
        dry_run_hydrants(api, TestHydrantImport.GOOD)
        response = api.get("/api/v1/imports/")
        assert response.status_code == 200
        batch = response.data["results"][0]
        assert "commit_token" not in batch
        assert batch["file_sha256"]


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------
def geojson_document(features) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def point_feature(lon=-122.0, lat=38.0):
    return {"type": "Feature", "properties": {"name": "tank"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


class TestOverlays:
    def test_upload_serve_delete_cycle(self, api):
        response = upload(api, "/api/v1/overlays/", geojson_document([point_feature()]),
                          name="tanks")
        assert response.status_code == 201, response.data
        overlay = MapOverlay.objects.get()
        assert overlay.feature_count == 1
        active = api.get("/api/v1/overlays/active/")
        assert active.status_code == 200
        assert active["Content-Type"] == "application/geo+json"
        body = b"".join(active.streaming_content)
        assert json.loads(body)["features"][0]["properties"]["name"] == "tank"
        # DELETE (editor+) removes row and file.
        assert api.delete(f"/api/v1/overlays/{overlay.pk}/").status_code == 204
        assert api.get("/api/v1/overlays/active/").status_code == 404

    def test_upload_replaces_the_previous_overlay_and_its_file(self, api):
        upload(api, "/api/v1/overlays/", geojson_document([point_feature()]))
        first = MapOverlay.objects.get()
        from ledger.media import media_root

        first_path = media_root() / first.geojson_path
        assert first_path.exists()
        upload(api, "/api/v1/overlays/", geojson_document([point_feature(), point_feature(-121.0, 38.5)]))
        assert MapOverlay.objects.count() == 1
        assert not first_path.exists()  # the old file went with its row
        assert MapOverlay.objects.get().feature_count == 2

    def test_bad_coordinates_name_the_first_offending_index(self, api):
        bad = geojson_document([point_feature(), point_feature(lon=-200.0, lat=38.0)])
        response = upload(api, "/api/v1/overlays/", bad)
        assert response.status_code == 400
        assert response.data["feature_index"] == 1

    def test_bad_latitude_rejected(self, api):
        response = upload(api, "/api/v1/overlays/", geojson_document([point_feature(lat=85.0)]))
        assert response.status_code == 400
        assert response.data["feature_index"] == 0

    def test_non_feature_collection_rejected(self, api):
        response = upload(api, "/api/v1/overlays/", json.dumps({"type": "Feature"}).encode())
        assert response.status_code == 400

    def test_geometry_type_allowlist(self, api):
        bad = geojson_document([{"type": "Feature", "properties": {},
                                 "geometry": {"type": "GeometryCollection", "geometries": []}}])
        response = upload(api, "/api/v1/overlays/", bad)
        assert response.status_code == 400
        assert "GeometryCollection" in response.data["detail"]

    def test_oversize_is_413(self, api):
        response = upload(api, "/api/v1/overlays/", b" " * (C.MAX_OVERLAY_BYTES + 1))
        assert response.status_code == 413

    def test_viewer_reads_but_cannot_write(self, viewer_api):
        assert viewer_api.get("/api/v1/overlays/").status_code == 200
        response = upload(viewer_api, "/api/v1/overlays/", geojson_document([point_feature()]))
        assert response.status_code == 403
        assert viewer_api.delete("/api/v1/overlays/1/").status_code == 403

    def test_anonymous_cannot_read_the_active_overlay(self, anonymous_api):
        assert anonymous_api.get("/api/v1/overlays/active/").status_code == 401


class TestLedgerExportRoute:
    def test_export_download_is_admin_only_and_audited(self, api, admin_api):
        from ledger.models import ExportAudit

        # The implementer's fixture set has no admin client; build one inline.
        assert api.get("/api/v1/exports/ledger/").status_code == 403
        response = admin_api.get("/api/v1/exports/ledger/")
        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload["schema_version"] == 1
        assert "AppSetting" not in payload["tables"]
        assert "auth.User" in payload["tables"]
        assert ExportAudit.objects.filter(kind="export_ledger").exists()

    def test_viewer_cannot_download_the_ledger_export(self, viewer_api):
        # The document carries password hashes for restore; it rides the
        # admin lane, not the generic viewer-reads-exports rule.
        assert viewer_api.get("/api/v1/exports/ledger/").status_code == 403


class TestOverlayDetailRead:
    def test_overlay_detail_is_viewer_readable(self, api, viewer_api):
        upload(api, "/api/v1/overlays/", geojson_document([point_feature()]))
        overlay = MapOverlay.objects.get()
        response = viewer_api.get(f"/api/v1/overlays/{overlay.pk}/")
        assert response.status_code == 200
        assert response.data["feature_count"] == 1


# ---------------------------------------------------------------------------
# Repair-round regressions (native static review BUG-002/003/008/009)
# ---------------------------------------------------------------------------
def context_hydrant_csv(tag="CTX-1"):
    return (
        HYDRANT_HEADER + "\n"
        f'{tag},38.0,-122.0,Corner of Main,Sample Works,S-100,1998,6.00,2.50,0.90,'
        'Sample District,Sample Water Co,first import,IN_SERVICE,,\n'
    )


class TestImportCommitContext:
    """The commit applies exactly what the dry run validated and reported."""

    def test_commit_cannot_switch_on_allow_overwrite(self, api):
        token = dry_run_hydrants(api, context_hydrant_csv(), supplier="Sample Water Co"
                                 ).data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token, "allow_overwrite": "true"},
                            format="multipart")
        assert response.status_code == 400
        assert response.data["detail"] == "commit_context_mismatch"
        batch = ImportBatch.objects.get(commit_token_hash=hashlib.sha256(token.encode()).hexdigest())
        # The refused attempt neither spent the token nor mutated the flags.
        assert batch.allow_overwrite is False
        assert Hydrant.objects.count() == 0
        ok = api.post("/api/v1/imports/hydrants/",
                      {"mode": "commit", "commit_token": token}, format="multipart")
        assert ok.status_code == 200
        assert ok.data["rows_ok"] == 1

    def test_commit_cannot_swap_the_supplier(self, api):
        token = dry_run_hydrants(api, context_hydrant_csv("CTX-2"), supplier="Alpha Water"
                                 ).data["commit_token"]
        response = api.post("/api/v1/imports/hydrants/",
                            {"mode": "commit", "commit_token": token, "supplier": "Beta Water"},
                            format="multipart")
        assert response.status_code == 400
        assert response.data["detail"] == "commit_context_mismatch"
        batch = ImportBatch.objects.get(commit_token_hash=hashlib.sha256(token.encode()).hexdigest())
        assert batch.supplier == "Alpha Water"
        assert Hydrant.objects.count() == 0

    def test_commit_may_restate_the_dry_run_context(self, api):
        token = dry_run_hydrants(api, context_hydrant_csv("CTX-3"), supplier="Alpha Water",
                                 allow_overwrite="true").data["commit_token"]
        response = api.post(
            "/api/v1/imports/hydrants/",
            {"mode": "commit", "commit_token": token, "supplier": "Alpha Water",
             "allow_overwrite": "true"},
            format="multipart",
        )
        assert response.status_code == 200
        assert response.data["rows_ok"] == 1

    def test_an_empty_allow_overwrite_field_is_treated_as_absent(self, api):
        token = dry_run_hydrants(api, context_hydrant_csv("CTX-4"), supplier="Alpha Water",
                                 allow_overwrite="true").data["commit_token"]
        response = api.post(
            "/api/v1/imports/hydrants/",
            {"mode": "commit", "commit_token": token, "allow_overwrite": ""},
            format="multipart",
        )
        assert response.status_code == 200
        assert response.data["rows_ok"] == 1


class TestImportDryRunAtomicity:
    def test_failed_dry_run_file_write_leaves_no_orphan_batch(self, api, monkeypatch):
        """BUG-008: a failed file write must not leave a token-bearing row."""
        import pathlib

        def explode(self, data):
            raise OSError("no space left on device")

        monkeypatch.setattr(pathlib.Path, "write_bytes", explode)
        with pytest.raises(OSError):
            dry_run_hydrants(api, context_hydrant_csv("ORPHAN-1"), supplier="Sample Water Co")
        assert ImportBatch.objects.count() == 0


class TestOverlayReplaceBudget:
    def test_replacing_an_overlay_near_the_budget_is_allowed(self, api, settings):
        """BUG-009: the replaced overlay's bytes are freed by the same upload."""
        from ledger.models import DistrictSettings
        from ledger.media import media_root

        district = DistrictSettings.load()
        district.max_media_mb = 1
        district.max_attachment_total_mb = 1  # cross-validated ≤ max_media_mb
        district.save()
        payload = json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"pad": "x" * 600_000},
            }],
        }).encode()
        first = upload(api, "/api/v1/overlays/", payload, name="big-1")
        assert first.status_code == 201, first.data
        assert MapOverlay.objects.count() == 1
        first_path = media_root() / MapOverlay.objects.get().geojson_path
        # A same-size replacement fits the post-replacement net; before the
        # fix the outgoing overlay's bytes made this a spurious 413.
        second = upload(api, "/api/v1/overlays/", payload, name="big-2")
        assert second.status_code == 201, second.data
        assert MapOverlay.objects.count() == 1
        assert MapOverlay.objects.get().name == "big-2"
        assert not first_path.exists()

    def test_a_genuinely_over_budget_replace_is_still_413(self, api):
        from ledger.models import DistrictSettings

        district = DistrictSettings.load()
        district.max_media_mb = 1
        district.max_attachment_total_mb = 1  # cross-validated ≤ max_media_mb
        district.save()
        small = geojson_document([point_feature()])
        assert upload(api, "/api/v1/overlays/", small, name="small").status_code == 201
        huge = json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"pad": "x" * (C.MAX_OVERLAY_BYTES - 10_000)},
            }],
        }).encode()
        assert upload(api, "/api/v1/overlays/", huge, name="huge").status_code == 413


class TestUploadBodyGuards:
    def test_declared_oversize_import_body_is_rejected_before_parsing(self, api):
        response = api.post(
            "/api/v1/imports/hydrants/",
            {"file": io.BytesIO(context_hydrant_csv("GUARD-1").encode()), "mode": "dry_run"},
            format="multipart", CONTENT_LENGTH=str(4 * 1024 * 1024 * 1024),
        )
        assert response.status_code == 413
        assert ImportBatch.objects.count() == 0

    def test_declared_oversize_overlay_body_is_rejected_before_parsing(self, api):
        response = api.post(
            "/api/v1/overlays/", {"file": io.BytesIO(b"x")}, format="multipart",
            CONTENT_LENGTH=str(4 * 1024 * 1024 * 1024),
        )
        assert response.status_code == 413
        assert MapOverlay.objects.count() == 0
