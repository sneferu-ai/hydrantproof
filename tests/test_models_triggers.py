"""Tier A database triggers, the ORM append-only guard, and the denormalized caches."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from ledger import constants as C
from ledger.models import (
    AppendOnlyViolation,
    DistrictSettings,
    FlowTest,
    Hydrant,
    OosAcknowledgement,
    SettingsAudit,
)
from ledger.services import create_flow_test, create_inspection, create_status_event

TIER_A_TABLES = (
    "ledger_flowtest",
    "ledger_outletreading",
    "ledger_hydrantstatusevent",
    "ledger_hydrantattributechange",
    "ledger_parcelattributechange",
    "ledger_inspection",
    "ledger_oosacknowledgement",
    "ledger_settingsaudit",
)

pytestmark = pytest.mark.django_db


def make_hydrant(asset_tag="H-1", **kwargs) -> Hydrant:
    defaults = dict(asset_tag=asset_tag, lat=38.0, lon=-122.0, source_org="Sample District")
    defaults.update(kwargs)
    hydrant = Hydrant.objects.create(**defaults)
    create_status_event(hydrant=hydrant, to_status=C.STATUS_IN_SERVICE, reason="Initial record",
                        user=None, is_initial=True)
    return hydrant


@pytest.fixture
def tier_a_row(db):
    """Create one real row per Tier A table and hand back (table, pk)."""
    rows = {}

    def seed():
        hydrant = make_hydrant("H-TRIG")
        today = timezone.localdate()
        test = create_flow_test(
            hydrant=hydrant, tested_on=today, static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        inspection = create_inspection(
            hydrant=hydrant, inspected_on=today, result=C.INSPECTION_FAIL,
            deficiency_codes=["CAP"], user=None,
        )
        from ledger.models import HydrantAttributeChange, Parcel, ParcelAttributeChange
        from ledger.services import record_attribute_changes, record_parcel_changes

        record_attribute_changes(hydrant, [("notes", "", "hello")], reason="manual", user=None)
        parcel = Parcel.objects.create(parcel_id="P-TRIG", lat=38.0, lon=-122.0)
        record_parcel_changes(parcel, [("address", "", "1 Main St")], user=None)
        ack = OosAcknowledgement.objects.create(hydrant=hydrant, note="ack")
        audit = SettingsAudit.objects.create(field="recency_days", old_value="365", new_value="400")
        rows.update({
            "ledger_flowtest": test.pk,
            "ledger_outletreading": test.outlets.first().pk,
            "ledger_hydrantstatusevent": hydrant.status_events.first().pk,
            "ledger_hydrantattributechange": HydrantAttributeChange.objects.first().pk,
            "ledger_parcelattributechange": ParcelAttributeChange.objects.first().pk,
            "ledger_inspection": inspection.pk,
            "ledger_oosacknowledgement": ack.pk,
            "ledger_settingsaudit": audit.pk,
        })

    seed()
    return rows


class TestTierATriggers:
    @pytest.mark.parametrize("table", TIER_A_TABLES)
    def test_raw_sql_update_raises(self, tier_a_row, table):
        pk = tier_a_row[table]
        with pytest.raises(Exception) as exc:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET id = id WHERE id = %s", [pk])
        assert "append-only" in str(exc.value)

    @pytest.mark.parametrize("table", TIER_A_TABLES)
    def test_raw_sql_delete_raises(self, tier_a_row, table):
        pk = tier_a_row[table]
        with pytest.raises(Exception) as exc:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f"DELETE FROM {table} WHERE id = %s", [pk])
        assert "append-only" in str(exc.value)

    def test_hydrant_update_succeeds(self):
        hydrant = make_hydrant("H-UPDATE")
        hydrant.location_description = "Main St"
        hydrant.save()
        hydrant.refresh_from_db()
        assert hydrant.location_description == "Main St"

    def test_all_eight_tables_have_a_trigger(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_object_table FROM information_schema.triggers "
                "WHERE trigger_name LIKE '%append_only'"
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert tables == set(TIER_A_TABLES)


class TestOrmAppendOnlyGuard:
    def test_flow_test_update_refused(self):
        hydrant = make_hydrant("H-ORM")
        test = create_flow_test(
            hydrant=hydrant,
            tested_on=timezone.localdate(),
            static_psi="80",
            residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        test.notes = "edited"
        with pytest.raises(AppendOnlyViolation):
            test.save()

    def test_flow_test_delete_refused(self):
        hydrant = make_hydrant("H-ORM2")
        test = create_flow_test(
            hydrant=hydrant,
            tested_on=timezone.localdate(),
            static_psi="80",
            residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        with pytest.raises(AppendOnlyViolation):
            test.delete()

    def test_inspection_delete_refused(self):
        hydrant = make_hydrant("H-ORM3")
        inspection = create_inspection(
            hydrant=hydrant, inspected_on=timezone.localdate(), result=C.INSPECTION_OK,
            deficiency_codes=[], user=None,
        )
        with pytest.raises(AppendOnlyViolation):
            inspection.delete()

    def test_status_event_update_refused(self):
        hydrant = make_hydrant("H-ORM4")
        event = hydrant.status_events.first()
        event.reason = "changed"
        with pytest.raises(AppendOnlyViolation):
            event.save()


class TestCurrentTestPointer:
    def test_pointer_follows_the_canonical_max_rule(self):
        hydrant = make_hydrant("H-PTR")
        today = timezone.localdate()
        first = create_flow_test(
            hydrant=hydrant, tested_on=today - timedelta(days=10), static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        hydrant.refresh_from_db()
        assert hydrant.current_test_ref_id == first.pk

        correction = create_flow_test(
            hydrant=hydrant, tested_on=today - timedelta(days=10), static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
            supersedes=first,
        )
        hydrant.refresh_from_db()
        assert hydrant.current_test_ref_id == correction.pk

        # A correction may carry an earlier tested_on; the pointer follows max().
        earlier = create_flow_test(
            hydrant=hydrant, tested_on=today - timedelta(days=30), static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        hydrant.refresh_from_db()
        assert hydrant.current_test_ref_id == correction.pk


class TestSupersessionIntegrity:
    def test_double_supersession_refused_by_unique_index(self):
        hydrant = make_hydrant("H-SUP")
        today = timezone.localdate()
        original = create_flow_test(
            hydrant=hydrant, tested_on=today, static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        create_flow_test(
            hydrant=hydrant, tested_on=today, static_psi="80", residual_psi="41",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "27"}],
            supersedes=original,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                FlowTest.objects.create(
                    hydrant=hydrant, tested_on=today, static_psi="80", residual_psi="42",
                    computed_total_gpm="800.0", formula_version=C.CURRENT_FORMULA_VERSION,
                    supersedes=original,
                )

    def test_cross_hydrant_supersession_refused(self):
        from ledger.services import LedgerError

        hydrant_a = make_hydrant("H-A")
        hydrant_b = make_hydrant("H-B")
        today = timezone.localdate()
        test_b = create_flow_test(
            hydrant=hydrant_b, tested_on=today, static_psi="80", residual_psi="40",
            outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
        )
        with pytest.raises(LedgerError) as exc:
            create_flow_test(
                hydrant=hydrant_a, tested_on=today, static_psi="80", residual_psi="40",
                outlets=[{"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": "28"}],
                supersedes=test_b,
            )
        assert exc.value.field == "supersedes"


class TestGeneratedColumn:
    def test_normalized_source_org_casefolds_and_collapses_whitespace(self):
        hydrant = make_hydrant("H-NORM", source_org="  North   Fork  Water  District ")
        hydrant.refresh_from_db()
        assert hydrant.normalized_source_org == "north fork water district"

    def test_blank_source_org_normalizes_to_empty(self):
        hydrant = make_hydrant("H-BLANK", source_org="")
        hydrant.refresh_from_db()
        assert hydrant.normalized_source_org == ""


class TestDistrictSettingsSingleton:
    def test_load_always_returns_the_singleton(self):
        district = DistrictSettings.load()
        assert district.pk == 1
        district.name = "North Fork Fire District"
        district.save()
        assert DistrictSettings.load().pk == 1
        assert DistrictSettings.objects.count() == 1

    def test_cross_validation(self):
        from django.core.exceptions import ValidationError

        district = DistrictSettings.load()
        district.due_soon_days = district.recency_days
        with pytest.raises(ValidationError):
            district.save()
