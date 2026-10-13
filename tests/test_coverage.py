"""The coverage engine (pure core) — fixture E boundary, credit rule, bands,
null rows, nearest-2x, the pair guard, and the frozen-parameter hashes."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from ledger import constants as C
from ledger.coverage import (
    CoverageHydrant,
    CoverageParcel,
    PairGuardExceeded,
    ParcelIdsExceeded,
    compute_coverage,
    distribution_band,
    flow_band_proxy,
    normalize_parcel_filter,
    params_hash,
)
from ledger.derive import DerivationParams, FlowTestView, HydrantView, InspectionView
from ledger.geo import degrees_for_feet

TODAY = date(2026, 10, 3)
PARAMS = DerivationParams(today=TODAY)


def hydrant(pk, lat=38.0, lon=-122.0, *, status=C.STATUS_IN_SERVICE, rated=1200.0,
            warnings=(), formula=C.CURRENT_FORMULA_VERSION, inspected=True,
            deficiency=False, gating=False, expected_return_on=None, indefinite=False):
    view = HydrantView(
        status=status,
        current_test=FlowTestView(
            tested_on=TODAY - timedelta(days=30), rated_gpm_20=rated,
            warnings=tuple(warnings), formula_version=formula, computed_total_gpm=rated,
        ),
        previous_test=None,
        current_inspection=InspectionView(inspected_on=TODAY - timedelta(days=30), result=C.INSPECTION_OK)
        if inspected else None,
        open_deficiency_codes=("VALVE",) if deficiency else (),
        gates_credit_deficiency=gating,
        expected_return_on=expected_return_on,
        indefinite=indefinite,
    )
    return CoverageHydrant(pk=pk, lat=lat, lon=lon, view=view)


def parcel(pk, lat=38.0, lon=-122.0):
    return CoverageParcel(pk=pk, lat=lat, lon=lon)


class TestFixtureEBoundary:
    """Engineered 999.5 / 1000.5 ft pairs — inclusion on the unrounded value."""

    def test_inside_999_5_included(self):
        p = parcel(1)
        h = hydrant(7, lat=38.0 + degrees_for_feet(999.5))
        result = compute_coverage([h], [p], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].hydrant_pk == 7
        assert result.rows[0].distance_ft == pytest.approx(999.5, abs=0.1)
        assert result.rows[0].credited is True
        assert result.rows[0].credited_gpm == pytest.approx(1200.0)

    def test_outside_1000_5_excluded_to_null_row(self):
        p = parcel(1)
        h = hydrant(7, lat=38.0 + degrees_for_feet(1000.5))
        result = compute_coverage([h], [p], params=PARAMS, credit_radius_ft=1000.0)
        assert len(result.rows) == 1
        assert result.rows[0].hydrant_pk is None
        assert result.rows[0].exclusion_code == C.EXCLUSION_NO_HYDRANT_IN_RADIUS
        # …but the gap-locating metric still sees it (within 2× radius).
        assert result.parcels[0].nearest_within_2x_ft == pytest.approx(1000.5, abs=0.2)


class TestCreditRule:
    def test_credited_when_eligible(self):
        result = compute_coverage([hydrant(1)], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].credited is True
        assert result.covered_count == 1
        assert result.credited_band_counts == {"3+": 0, "2": 0, "1": 1, "0": 0}

    def test_out_of_service_excluded_with_expired_flag(self):
        h = hydrant(1, status=C.STATUS_OUT_OF_SERVICE,
                    expected_return_on=TODAY - timedelta(days=5))
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].credited is False
        assert result.rows[0].exclusion_code == C.EXCLUSION_OUT_OF_SERVICE
        assert result.rows[0].expired is True

    def test_retired_excluded(self):
        h = hydrant(1, status=C.STATUS_RETIRED)
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].exclusion_code == C.EXCLUSION_RETIRED

    def test_untested_excluded(self):
        h = hydrant(1)
        h = CoverageHydrant(pk=1, lat=h.lat, lon=h.lon,
                            view=HydrantView(status=C.STATUS_IN_SERVICE,
                                             current_inspection=h.view.current_inspection))
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].exclusion_code == C.EXCLUSION_UNTESTED

    def test_formula_version_mismatch_excluded(self):
        h = hydrant(1, formula="nfpa291-pct-drop-v0")
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].exclusion_code == C.EXCLUSION_FORMULA_VERSION_MISMATCH

    def test_gating_deficiency_excluded_via_deficiency_table(self):
        h = hydrant(1, deficiency=True, gating=True)
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].credited is False
        assert result.rows[0].exclusion_code == C.EXCLUSION_INSPECTION_FAIL

    def test_sub_target_residual_credited_by_default_counted(self):
        h = hydrant(1, rated=508.1, warnings=(C.WARNING_RESIDUAL_BELOW_TARGET,))
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].credited is True
        assert result.sub_target_residual_included_count == 1

    def test_sub_target_residual_excluded_when_policy_true(self):
        h = hydrant(1, rated=508.1, warnings=(C.WARNING_RESIDUAL_BELOW_TARGET,))
        strict = DerivationParams(today=TODAY, exclude_residual_below_target=True)
        result = compute_coverage([h], [parcel(1)], params=strict, credit_radius_ft=1000.0)
        assert result.rows[0].credited is False
        assert result.rows[0].exclusion_code == C.EXCLUSION_RESIDUAL_BELOW_TARGET

    def test_exclusion_precedence_oos_beats_untested(self):
        h = CoverageHydrant(pk=1, lat=38.0, lon=-122.0,
                            view=HydrantView(status=C.STATUS_OUT_OF_SERVICE, indefinite=True))
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.rows[0].exclusion_code == C.EXCLUSION_OUT_OF_SERVICE


class TestBands:
    def test_distribution_bands(self):
        assert distribution_band(5) == "3+"
        assert distribution_band(3) == "3+"
        assert distribution_band(2) == "2"
        assert distribution_band(1) == "1"
        assert distribution_band(0) == "0"

    def test_flow_band_proxy(self):
        assert flow_band_proxy(1500.0) == "1000+"
        assert flow_band_proxy(1000.0) == "1000+"
        assert flow_band_proxy(700.0) == "500-999"
        assert flow_band_proxy(250.0) == "1-499"
        assert flow_band_proxy(0.0) == "NONE"

    def test_band_counts_across_parcels(self):
        hydrants = [hydrant(i, lat=38.0, lon=-122.0) for i in range(1, 4)]
        parcels = [parcel(1), parcel(2, lat=39.0, lon=-121.0)]  # second is alone
        result = compute_coverage(hydrants, parcels, params=PARAMS, credit_radius_ft=1000.0)
        assert result.credited_band_counts["3+"] == 1
        assert result.credited_band_counts["0"] == 1
        assert result.covered_count == 1
        assert result.protected_parcel_count == 2

    def test_uncredited_sum_is_uncapped(self):
        hydrants = [hydrant(i, rated=9000.0) for i in range(1, 5)]
        result = compute_coverage(hydrants, [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert result.parcels[0].credited_gpm_sum == pytest.approx(36000.0)
        assert result.parcels[0].flow_band_proxy == "1000+"


class TestNullRows:
    def test_null_row_exactly_once_per_empty_parcel(self):
        result = compute_coverage([], [parcel(1), parcel(2, lat=39.0)],
                                  params=PARAMS, credit_radius_ft=1000.0)
        assert len(result.rows) == 2
        assert all(r.hydrant_pk is None for r in result.rows)
        assert result.parcels[0].nearest_within_2x_ft is None

    def test_excluded_only_parcel_has_no_null_row(self):
        h = hydrant(1, status=C.STATUS_RETIRED)
        result = compute_coverage([h], [parcel(1)], params=PARAMS, credit_radius_ft=1000.0)
        assert len(result.rows) == 1  # the excluded pair row, never doubled
        assert result.rows[0].exclusion_code == C.EXCLUSION_RETIRED
        assert result.parcels[0].distribution_band == "0"


class TestPairGuard:
    def test_guard_trips_incrementally(self):
        hydrants = [hydrant(i) for i in range(1, 8)]  # 7 in-radius per parcel
        parcels = [parcel(i, lat=38.0 + i * 1e-9) for i in range(1, 400)]
        with pytest.raises(PairGuardExceeded):
            compute_coverage(hydrants, parcels, params=PARAMS,
                             credit_radius_ft=1000.0, max_pairs=1000)

    def test_guard_counts_null_rows_too(self):
        parcels = [parcel(i, lat=30.0 + i * 0.01) for i in range(1, 50)]
        with pytest.raises(PairGuardExceeded):
            compute_coverage([], parcels, params=PARAMS, credit_radius_ft=1000.0, max_pairs=10)


class TestParcelFilter:
    def test_none_passthrough(self):
        assert normalize_parcel_filter(None) is None

    def test_parcel_ids_normalized_sorted_deduped(self):
        assert normalize_parcel_filter({"parcel_ids": [3, 1, 3]}) == {"parcel_ids": [1, 3]}

    def test_parcel_ids_cap_is_413_class(self):
        with pytest.raises(ParcelIdsExceeded):
            normalize_parcel_filter({"parcel_ids": list(range(50_001))})

    def test_parcel_ids_type_checked(self):
        with pytest.raises(ValueError):
            normalize_parcel_filter({"parcel_ids": ["1", 2]})

    def test_bbox_validated(self):
        assert normalize_parcel_filter({"bbox": [-122.5, 37.5, -121.5, 38.5]}) == {
            "bbox": [-122.5, 37.5, -121.5, 38.5]
        }
        with pytest.raises(ValueError):
            normalize_parcel_filter({"bbox": [-121.5, 37.5, -122.5, 38.5]})  # minlon > maxlon
        with pytest.raises(ValueError):
            normalize_parcel_filter({"bbox": [-122.5, 85.0, -121.5, 86.0]})  # lat out of range

    def test_unknown_shape_rejected(self):
        with pytest.raises(ValueError):
            normalize_parcel_filter({"bbox": [0, 0, 1, 1], "parcel_ids": []})
        with pytest.raises(ValueError):
            normalize_parcel_filter("bbox")


class TestParamsHash:
    def test_deterministic_and_filter_sensitive(self):
        settings = {"recency_days": 365, "credit_radius_ft": 1000.0}
        a = params_hash(settings, None)
        assert a == params_hash({"credit_radius_ft": 1000.0, "recency_days": 365}, None)
        assert a != params_hash(settings, {"bbox": [0, 0, 1, 1]})
        # The core hash ignores the filter (a filtered run is not permanently
        # stale by construction).
        core = params_hash(settings, {"bbox": [0, 0, 1, 1]}, core=True)
        assert core == params_hash(settings, None, core=True)
        assert core == params_hash(settings, None, core=False)  # null filter = core payload

    def test_constants_change_moves_the_hash(self):
        assert params_hash({"recency_days": 365}, None) != params_hash({"recency_days": 366}, None)
