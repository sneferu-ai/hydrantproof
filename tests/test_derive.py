"""The canonical derivation: three-state readiness, recency bands, trend,
exclusion precedence. No database."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from ledger.constants import (
    EXCLUSION_EXCESSIVE_EXTRAPOLATION,
    EXCLUSION_FORMULA_VERSION_MISMATCH,
    EXCLUSION_INSPECTION_FAIL,
    EXCLUSION_OUT_OF_SERVICE,
    EXCLUSION_RESIDUAL_BELOW_TARGET,
    EXCLUSION_RETIRED,
    EXCLUSION_STATIC_BELOW_TARGET,
    EXCLUSION_TEST_STALE,
    EXCLUSION_UNTESTED,
    INSPECTION_FAIL,
    INSPECTION_OK,
    READINESS_NOT_READY,
    READINESS_READY,
    READINESS_WATCH,
    RECENCY_CURRENT,
    RECENCY_DUE_SOON,
    RECENCY_NEVER_TESTED,
    RECENCY_OVERDUE,
    STATUS_IN_SERVICE,
    STATUS_OUT_OF_SERVICE,
    STATUS_RETIRED,
    TREND_DEGRADING,
    TREND_INSUFFICIENT_HISTORY,
    TREND_RATED_FLOW_LOST,
    TREND_STABLE,
    TREND_UNRATED,
    WARNING_EXCESSIVE_EXTRAPOLATION,
    WARNING_RESIDUAL_BELOW_TARGET,
    WARNING_STATIC_BELOW_TARGET,
)
from ledger.derive import (
    DerivationParams,
    FlowTestView,
    HydrantView,
    InspectionView,
    compute_trend,
    derive_hydrant,
)
from ledger.derive import test_recency_flag as recency_flag

TODAY = date(2026, 10, 3)
PARAMS = DerivationParams(today=TODAY)


def make_test(**kwargs) -> FlowTestView:
    defaults = dict(tested_on=TODAY - timedelta(days=10), rated_gpm_20=1200.0)
    defaults.update(kwargs)
    return FlowTestView(**defaults)


def make_inspection(**kwargs) -> InspectionView:
    defaults = dict(inspected_on=TODAY - timedelta(days=10), result=INSPECTION_OK)
    defaults.update(kwargs)
    return InspectionView(**defaults)


def make_view(**kwargs) -> HydrantView:
    defaults = dict(status=STATUS_IN_SERVICE, current_test=make_test(), current_inspection=make_inspection())
    defaults.update(kwargs)
    return HydrantView(**defaults)


# ---------------------------------------------------------------------------
# Recency bands
# ---------------------------------------------------------------------------
class TestRecency:
    @pytest.mark.parametrize(
        "age_days,expected",
        [
            (0, RECENCY_CURRENT),
            (275, RECENCY_CURRENT),   # 365 − 90: still inside the window's body
            (276, RECENCY_DUE_SOON),  # trailing early-warning band
            (365, RECENCY_DUE_SOON),
            (366, RECENCY_OVERDUE),
        ],
    )
    def test_test_recency_bands(self, age_days, expected):
        assert recency_flag(TODAY - timedelta(days=age_days), PARAMS) == expected

    def test_never_tested(self):
        assert recency_flag(None, PARAMS) == RECENCY_NEVER_TESTED

    def test_inspection_window_is_independent(self):
        params = DerivationParams(today=TODAY, inspection_recency_days=180, inspection_due_soon_days=30)
        derived = derive_hydrant(
            make_view(current_inspection=make_inspection(inspected_on=TODAY - timedelta(days=160))), params
        )
        assert derived.inspection_recency_flag == RECENCY_DUE_SOON
        assert derived.test_recency_flag == RECENCY_CURRENT


# ---------------------------------------------------------------------------
# Trend (four-valued)
# ---------------------------------------------------------------------------
class TestTrend:
    def test_unrated_when_current_null_and_no_rated_predecessor(self):
        assert compute_trend(make_test(rated_gpm_20=None), None) == TREND_UNRATED

    def test_rated_flow_lost_beats_plain_unrated(self):
        assert compute_trend(make_test(rated_gpm_20=None), make_test(rated_gpm_20=1200.0)) == TREND_RATED_FLOW_LOST

    def test_insufficient_history_when_no_previous(self):
        assert compute_trend(make_test(rated_gpm_20=1200.0), None) == TREND_INSUFFICIENT_HISTORY

    def test_insufficient_history_when_previous_unrated(self):
        assert compute_trend(make_test(rated_gpm_20=1200.0), make_test(rated_gpm_20=None)) == TREND_INSUFFICIENT_HISTORY

    def test_degrading_at_or_beyond_15_percent(self):
        assert compute_trend(make_test(rated_gpm_20=850.0), make_test(rated_gpm_20=1000.0)) == TREND_DEGRADING
        assert compute_trend(make_test(rated_gpm_20=851.0), make_test(rated_gpm_20=1000.0)) == TREND_STABLE

    def test_stable_improvement(self):
        assert compute_trend(make_test(rated_gpm_20=1300.0), make_test(rated_gpm_20=1000.0)) == TREND_STABLE


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
class TestReadiness:
    def test_ready(self):
        derived = derive_hydrant(make_view(), PARAMS)
        assert derived.readiness == READINESS_READY
        assert derived.gating_flags == ()
        assert derived.attention_flags == ()

    def test_watch_on_due_soon_test(self):
        derived = derive_hydrant(make_view(current_test=make_test(tested_on=TODAY - timedelta(days=300))), PARAMS)
        assert derived.readiness == READINESS_WATCH
        assert derived.test_recency_flag == RECENCY_DUE_SOON
        assert derived.attention_flags == ("TEST_DUE_SOON",)

    def test_watch_on_due_soon_inspection(self):
        derived = derive_hydrant(
            make_view(current_inspection=make_inspection(inspected_on=TODAY - timedelta(days=300))), PARAMS
        )
        assert derived.readiness == READINESS_WATCH
        assert derived.attention_flags == ("INSPECTION_DUE_SOON",)

    def test_watch_on_degrading_flow(self):
        derived = derive_hydrant(
            make_view(current_test=make_test(rated_gpm_20=800.0), previous_test=make_test(rated_gpm_20=1000.0)), PARAMS
        )
        assert derived.readiness == READINESS_WATCH
        assert derived.trend == TREND_DEGRADING
        assert derived.attention_flags == ("FLOW_DEGRADING",)

    def test_watch_on_rated_flow_lost(self):
        derived = derive_hydrant(
            make_view(current_test=make_test(rated_gpm_20=None), previous_test=make_test(rated_gpm_20=1000.0)), PARAMS
        )
        assert derived.readiness == READINESS_WATCH
        assert derived.attention_flags == ("RATED_FLOW_LOST",)

    def test_due_soon_never_produces_not_ready(self):
        derived = derive_hydrant(
            make_view(
                current_test=make_test(tested_on=TODAY - timedelta(days=300)),
                current_inspection=make_inspection(inspected_on=TODAY - timedelta(days=300)),
            ),
            PARAMS,
        )
        assert derived.readiness == READINESS_WATCH
        assert "TEST_STALE" not in derived.gating_flags
        assert "INSPECTION_OVERDUE" not in derived.gating_flags

    def test_not_ready_untested(self):
        derived = derive_hydrant(make_view(current_test=None), PARAMS)
        assert derived.readiness == READINESS_NOT_READY
        assert derived.gating_flags == ("UNTESTED",)

    def test_not_ready_test_stale_and_never_inspected(self):
        derived = derive_hydrant(
            make_view(current_test=make_test(tested_on=TODAY - timedelta(days=400)), current_inspection=None), PARAMS
        )
        assert derived.readiness == READINESS_NOT_READY
        assert derived.gating_flags == ("TEST_STALE", "NEVER_INSPECTED")

    def test_gating_flag_order_is_canonical(self):
        derived = derive_hydrant(
            make_view(
                status=STATUS_OUT_OF_SERVICE,
                expected_return_on=TODAY - timedelta(days=5),
                current_test=None,
                current_inspection=make_inspection(result=INSPECTION_FAIL),
                open_deficiency_codes=("CAP",),
            ),
            PARAMS,
        )
        assert derived.gating_flags == (
            "OUT_OF_SERVICE",
            "EXPIRED_OOS",
            "UNTESTED",
            "INSPECTION_FAIL",
            "OPEN_DEFICIENCY",
        )

    def test_inspection_fail_blocks_ready(self):
        derived = derive_hydrant(make_view(current_inspection=make_inspection(result=INSPECTION_FAIL)), PARAMS)
        assert derived.readiness == READINESS_NOT_READY
        assert derived.gating_flags == ("INSPECTION_FAIL",)

    def test_open_deficiency_blocks_ready(self):
        derived = derive_hydrant(make_view(open_deficiency_codes=("CAP",)), PARAMS)
        assert derived.readiness == READINESS_NOT_READY
        assert derived.gating_flags == ("OPEN_DEFICIENCY",)

    def test_retired_is_not_ready_out_of_service_flag(self):
        derived = derive_hydrant(make_view(status=STATUS_RETIRED), PARAMS)
        assert derived.readiness == READINESS_NOT_READY
        assert derived.gating_flags == ()


# ---------------------------------------------------------------------------
# District credit / exclusion precedence
# ---------------------------------------------------------------------------
class TestExclusion:
    def test_eligible_when_everything_holds(self):
        derived = derive_hydrant(make_view(), PARAMS)
        assert derived.district_credit_eligible is True
        assert derived.exclusion_code is None
        assert derived.rated_gpm_20 == pytest.approx(1200.0)
        assert derived.capacity_class == "A"

    def test_retired_beats_out_of_service(self):
        derived = derive_hydrant(make_view(status=STATUS_RETIRED), PARAMS)
        assert derived.exclusion_code == EXCLUSION_RETIRED

    def test_out_of_service_excludes_even_with_perfect_records(self):
        derived = derive_hydrant(make_view(status=STATUS_OUT_OF_SERVICE), PARAMS)
        assert derived.exclusion_code == EXCLUSION_OUT_OF_SERVICE
        assert derived.capacity_class is None

    def test_untested(self):
        assert derive_hydrant(make_view(current_test=None), PARAMS).exclusion_code == EXCLUSION_UNTESTED

    def test_test_stale(self):
        derived = derive_hydrant(make_view(current_test=make_test(tested_on=TODAY - timedelta(days=400))), PARAMS)
        assert derived.exclusion_code == EXCLUSION_TEST_STALE

    def test_inspection_fail_exclusion_beats_static_below_target(self):
        # Precedence (§6): INSPECTION_FAIL outranks STATIC_BELOW_TARGET.  The
        # exclusion is carried by the OPEN gating row a FAIL inspection opens
        # (EARS 7) — the current-inspection RESULT alone is not the gate.
        derived = derive_hydrant(
            make_view(
                current_test=make_test(warnings=(WARNING_STATIC_BELOW_TARGET,), rated_gpm_20=None),
                current_inspection=make_inspection(result=INSPECTION_FAIL),
                gates_credit_deficiency=True,
            ),
            PARAMS,
        )
        assert derived.exclusion_code == EXCLUSION_INSPECTION_FAIL

    def test_resolved_gating_rows_clear_the_inspection_fail_exclusion(self):
        # EARS 7: the exclusion clears via the same two routes that clear the
        # gating rows (an editor resolution or a later OK inspection).  A FAIL
        # inspection whose rows are all RESOLVED must not keep the hydrant
        # excluded from district credit.
        derived = derive_hydrant(
            make_view(
                current_inspection=make_inspection(result=INSPECTION_FAIL),
                gates_credit_deficiency=False,
            ),
            PARAMS,
        )
        assert derived.exclusion_code is None
        assert derived.district_credit_eligible is True
        # Readiness still gates on the FAIL inspection itself (§6: READY
        # requires latest inspection result ≠ FAIL).
        assert derived.readiness == READINESS_NOT_READY
        assert "INSPECTION_FAIL" in derived.gating_flags

    def test_static_below_target(self):
        derived = derive_hydrant(
            make_view(current_test=make_test(warnings=(WARNING_STATIC_BELOW_TARGET,), rated_gpm_20=None)), PARAMS
        )
        assert derived.exclusion_code == EXCLUSION_STATIC_BELOW_TARGET

    def test_excessive_extrapolation(self):
        derived = derive_hydrant(
            make_view(current_test=make_test(warnings=(WARNING_EXCESSIVE_EXTRAPOLATION,), rated_gpm_20=None)), PARAMS
        )
        assert derived.exclusion_code == EXCLUSION_EXCESSIVE_EXTRAPOLATION

    def test_formula_version_mismatch(self):
        derived = derive_hydrant(make_view(current_test=make_test(formula_version="nfpa291-pct-drop-v0")), PARAMS)
        assert derived.exclusion_code == EXCLUSION_FORMULA_VERSION_MISMATCH
        assert derived.district_credit_eligible is False

    def test_residual_below_target_only_excluded_when_policy_true(self):
        sub_target = make_test(warnings=(WARNING_RESIDUAL_BELOW_TARGET,), rated_gpm_20=508.1)
        assert derive_hydrant(make_view(current_test=sub_target), PARAMS).exclusion_code is None
        strict = DerivationParams(today=TODAY, exclude_residual_below_target=True)
        assert derive_hydrant(make_view(current_test=sub_target), strict).exclusion_code == EXCLUSION_RESIDUAL_BELOW_TARGET

    def test_capacity_class_follows_eligibility_not_the_raw_rating(self):
        # An OOS hydrant keeps its stored rating but is Unclassified for credit.
        derived = derive_hydrant(make_view(status=STATUS_OUT_OF_SERVICE), PARAMS)
        assert derived.rated_gpm_20 is None
        assert derived.capacity_class is None

    def test_gating_deficiency_gates_credit_even_after_a_later_deficiency_inspection(self):
        # EARS 7: the Deficiency table is the single source of truth read by
        # credit.  A FAIL opens gating rows; a later DEFICIENCY-result
        # inspection becomes current without closing them (only OK auto-closes),
        # so the current-inspection check alone cannot see the gate.
        from ledger.constants import INSPECTION_DEFICIENCY

        derived = derive_hydrant(
            make_view(
                current_inspection=make_inspection(result=INSPECTION_DEFICIENCY),
                open_deficiency_codes=("VALVE",),
                gates_credit_deficiency=True,
            ),
            PARAMS,
        )
        assert derived.district_credit_eligible is False
        # Every non-credited row keeps an explicit reason; the code names the
        # origin (gating rows are opened only by FAIL inspections).
        assert derived.exclusion_code == EXCLUSION_INSPECTION_FAIL
        assert derived.rated_gpm_20 is None  # never credited through a gate
        assert derived.capacity_class is None

    def test_non_gating_open_deficiency_does_not_gate_credit(self):
        # A DEFICIENCY-result row (gates_credit=false) affects readiness but
        # never district credit.
        from ledger.constants import INSPECTION_DEFICIENCY

        derived = derive_hydrant(
            make_view(
                current_inspection=make_inspection(result=INSPECTION_DEFICIENCY),
                open_deficiency_codes=("PAINT",),
                gates_credit_deficiency=False,
            ),
            PARAMS,
        )
        assert derived.district_credit_eligible is True
        assert derived.exclusion_code is None
        assert derived.readiness == READINESS_NOT_READY  # OPEN_DEFICIENCY gates readiness


# ---------------------------------------------------------------------------
# OOS expiry and acknowledgement window
# ---------------------------------------------------------------------------
class TestOosExpiry:
    def test_expired_when_expected_return_passed(self):
        derived = derive_hydrant(
            make_view(status=STATUS_OUT_OF_SERVICE, expected_return_on=TODAY - timedelta(days=12)), PARAMS
        )
        assert derived.oos_expired is True
        assert derived.days_past_expiry == 12
        assert "EXPIRED_OOS" in derived.gating_flags

    def test_indefinite_is_never_expired(self):
        derived = derive_hydrant(
            make_view(status=STATUS_OUT_OF_SERVICE, indefinite=True, expected_return_on=None), PARAMS
        )
        assert derived.oos_expired is False
        assert derived.days_past_expiry is None

    def test_future_expected_return_is_not_expired(self):
        derived = derive_hydrant(
            make_view(status=STATUS_OUT_OF_SERVICE, expected_return_on=TODAY + timedelta(days=3)), PARAMS
        )
        assert derived.oos_expired is False

    def test_acknowledgement_ambers_for_30_days_then_blanks(self):
        ack = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)  # 13 days ago
        derived = derive_hydrant(
            make_view(
                status=STATUS_OUT_OF_SERVICE,
                expected_return_on=TODAY - timedelta(days=20),
                oos_acknowledged_at=ack,
            ),
            PARAMS,
        )
        assert derived.oos_acknowledged_active is True
        assert derived.oos_acknowledged_at == ack

        stale_ack = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)  # 63 days ago
        derived = derive_hydrant(
            make_view(
                status=STATUS_OUT_OF_SERVICE,
                expected_return_on=TODAY - timedelta(days=70),
                oos_acknowledged_at=stale_ack,
            ),
            PARAMS,
        )
        assert derived.oos_acknowledged_active is False
        assert derived.oos_acknowledged_at is None  # re-surfaces red
