"""Pure hydraulics: golden fixtures A–D and the §6 edge matrix. No database."""
from __future__ import annotations

import pytest

from ledger.constants import (
    CURRENT_FORMULA_VERSION,
    MAX_TOTAL_GPM,
    WARNING_DUPLICATE_READING,
    WARNING_EXCESSIVE_EXTRAPOLATION,
    WARNING_METER_AND_PITOT_PRESENT,
    WARNING_METER_PITOT_DIVERGENT,
    WARNING_RESIDUAL_BELOW_TARGET,
    WARNING_STATIC_BELOW_TARGET,
)
from ledger.hydraulics import (
    HydraulicsError,
    OutletReadingInput,
    capacity_class_for,
    compute_flow_test,
    outlet_triples_key,
    round_gpm,
)

STANDARD_OUTLET = OutletReadingInput(diameter_in=2.5, coefficient=0.90, pitot_psi=28.0)


def _outlet(pitot: float, diameter: float = 2.5, coefficient: float = 0.90) -> OutletReadingInput:
    return OutletReadingInput(diameter_in=diameter, coefficient=coefficient, pitot_psi=pitot)


# ---------------------------------------------------------------------------
# Golden fixtures (specification §6)
# ---------------------------------------------------------------------------
class TestGoldenFixtures:
    def test_fixture_a(self):
        result = compute_flow_test(static_psi=80, residual_psi=40, outlets=[STANDARD_OUTLET])
        assert result.computed_total_gpm == pytest.approx(887.9, abs=0.05)
        assert result.rated_gpm_20 == pytest.approx(1105.2, abs=0.15)
        assert result.capacity_class == "A"
        assert result.warnings == ()
        assert result.formula_version == CURRENT_FORMULA_VERSION
        assert result.pitot_sum_gpm is None  # no meter present

    def test_fixture_b_meterless_two_outlets(self):
        result = compute_flow_test(static_psi=80, residual_psi=40, outlets=[_outlet(20), _outlet(20)])
        assert result.computed_total_gpm == pytest.approx(1500.8, abs=0.05)
        assert result.rated_gpm_20 == pytest.approx(1868.1, abs=0.15)
        assert result.capacity_class == "AA"

    def test_fixture_c_sub_target_residual_is_credited_by_default(self):
        result = compute_flow_test(static_psi=80, residual_psi=15, outlets=[_outlet(10)])
        assert result.computed_total_gpm == pytest.approx(530.6, abs=0.05)
        assert result.rated_gpm_20 == pytest.approx(508.1, abs=0.15)
        assert result.rated_gpm_20 < result.computed_total_gpm  # conservative factor < 1
        assert result.capacity_class == "B"
        assert WARNING_RESIDUAL_BELOW_TARGET in result.warnings
        # Credited by default: rated flow is present (exclusion is an opt-in policy).
        assert result.rated_gpm_20 is not None

    def test_fixture_d_excessive_extrapolation(self):
        result = compute_flow_test(static_psi=80, residual_psi=79.5, outlets=[_outlet(15)])
        assert result.extrapolation_factor == pytest.approx(120.0)
        assert result.rated_gpm_20 is None
        assert result.capacity_class is None
        assert result.warnings == (WARNING_EXCESSIVE_EXTRAPOLATION,)

    def test_applied_multiplier_is_capped_at_about_2_1x(self):
        """4^0.54 ≈ 2.1 — the factor caps multiplier inflation."""
        result = compute_flow_test(static_psi=80, residual_psi=41.0, outlets=[_outlet(28)])
        # factor = 60/39 = 1.538…; multiplier 1.538^0.54 ≈ 1.26
        assert result.rated_gpm_20 / result.computed_total_gpm == pytest.approx(1.5385 ** 0.54, abs=0.01)
        capped = compute_flow_test(static_psi=80, residual_psi=40.1, outlets=[_outlet(28)])
        # factor = 60/39.9 ≈ 1.5038 → allowed; the cap only rejects > 4.0
        assert capped.rated_gpm_20 is not None


# ---------------------------------------------------------------------------
# Meter / pitot precedence
# ---------------------------------------------------------------------------
class TestMeterWins:
    def test_meter_is_authoritative_and_pitot_sum_is_stored(self):
        result = compute_flow_test(
            static_psi=80, residual_psi=40, outlets=[_outlet(28)], measured_total_gpm=900.0
        )
        assert result.computed_total_gpm == pytest.approx(900.0, abs=0.05)
        assert result.pitot_sum_gpm == pytest.approx(887.9, abs=0.05)
        assert WARNING_METER_AND_PITOT_PRESENT in result.warnings
        assert WARNING_METER_PITOT_DIVERGENT not in result.warnings  # 1.3 % apart

    def test_divergence_beyond_10_percent_flags(self):
        result = compute_flow_test(
            static_psi=80, residual_psi=40, outlets=[_outlet(28)], measured_total_gpm=1200.0
        )
        assert result.computed_total_gpm == pytest.approx(1200.0, abs=0.05)
        assert WARNING_METER_PITOT_DIVERGENT in result.warnings

    def test_meter_only_is_legal(self):
        result = compute_flow_test(static_psi=80, residual_psi=40, outlets=[], measured_total_gpm=900.0)
        assert result.computed_total_gpm == pytest.approx(900.0)
        assert result.pitot_sum_gpm is None
        assert WARNING_METER_AND_PITOT_PRESENT not in result.warnings


# ---------------------------------------------------------------------------
# The three-way ceiling — each input validated independently
# ---------------------------------------------------------------------------
class TestCeiling:
    def test_meter_over_ceiling_rejected(self):
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[], measured_total_gpm=MAX_TOTAL_GPM + 1)
        assert exc.value.field_name == "measured_total_gpm"

    def test_pitot_sum_over_ceiling_rejected_even_when_meter_under(self):
        # 4 outlets at 200 psi with 8-inch smooth: far above the cap
        big = [_outlet(200.0, diameter=8.0, coefficient=1.0) for _ in range(4)]
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=80, residual_psi=40, outlets=big, measured_total_gpm=100.0)
        assert exc.value.field_name == "outlets"

    def test_meter_under_ceiling_does_not_rescue_pitot_sum(self):
        big = [_outlet(200.0, diameter=8.0, coefficient=1.0) for _ in range(4)]
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=40, outlets=big, measured_total_gpm=500.0)


# ---------------------------------------------------------------------------
# Edge matrix rejections
# ---------------------------------------------------------------------------
class TestRejections:
    @pytest.mark.parametrize("static", [0, -1, 500.01])
    def test_static_out_of_range(self, static):
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=static, residual_psi=0, outlets=[_outlet(20)])
        assert exc.value.field_name == "static_psi"

    def test_residual_negative(self):
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=80, residual_psi=-0.5, outlets=[_outlet(20)])
        assert exc.value.field_name == "residual_psi"

    def test_residual_ge_static(self):
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=80, outlets=[_outlet(20)])

    def test_no_outlets_and_no_meter(self):
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[])

    @pytest.mark.parametrize("pitot", [0, -3, 200.5])
    def test_pitot_out_of_range(self, pitot):
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[_outlet(pitot)])
        assert "pitot_psi" in exc.value.field_name

    @pytest.mark.parametrize("diameter", [0.4, 8.1])
    def test_diameter_out_of_range(self, diameter):
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[_outlet(20, diameter=diameter)])

    @pytest.mark.parametrize("coefficient", [0.49, 1.01])
    def test_coefficient_out_of_range(self, coefficient):
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[_outlet(20, coefficient=coefficient)])

    def test_five_outlets_rejected(self):
        with pytest.raises(HydraulicsError) as exc:
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[_outlet(20) for _ in range(5)])
        assert exc.value.field_name == "outlets"

    def test_meter_out_of_low_range(self):
        with pytest.raises(HydraulicsError):
            compute_flow_test(static_psi=80, residual_psi=40, outlets=[], measured_total_gpm=0.5)


# ---------------------------------------------------------------------------
# Static at or below target
# ---------------------------------------------------------------------------
class TestStaticBelowTarget:
    def test_static_below_target_nulls_rating(self):
        result = compute_flow_test(static_psi=19.5, residual_psi=10, outlets=[_outlet(20)])
        assert result.rated_gpm_20 is None
        assert result.capacity_class is None
        assert WARNING_STATIC_BELOW_TARGET in result.warnings
        assert result.computed_total_gpm > 0

    def test_static_exactly_at_target(self):
        result = compute_flow_test(static_psi=20, residual_psi=10, outlets=[_outlet(20)])
        assert result.rated_gpm_20 is None
        assert WARNING_STATIC_BELOW_TARGET in result.warnings


# ---------------------------------------------------------------------------
# Warning order and helpers
# ---------------------------------------------------------------------------
class TestWarningOrder:
    def test_warning_order_is_canonical(self):
        result = compute_flow_test(
            static_psi=80,
            residual_psi=15,
            outlets=[_outlet(10)],
            measured_total_gpm=700.0,
            duplicate=True,
        )
        assert result.warnings == (
            WARNING_RESIDUAL_BELOW_TARGET,
            WARNING_METER_AND_PITOT_PRESENT,
            WARNING_METER_PITOT_DIVERGENT,
            WARNING_DUPLICATE_READING,
        )

    def test_rounding_is_half_up(self):
        assert round_gpm(887.85) == 887.9
        assert round_gpm(887.84) == 887.8

    @pytest.mark.parametrize(
        "rated,expected",
        [(1500.0, "AA"), (1499.9, "A"), (1000.0, "A"), (999.9, "B"), (500.0, "B"), (499.9, "C"), (None, None)],
    )
    def test_capacity_classes(self, rated, expected):
        assert capacity_class_for(rated) == expected

    def test_outlet_multiset_key_is_order_insensitive(self):
        one = [_outlet(20), _outlet(30)]
        two = [_outlet(30), _outlet(20)]
        assert outlet_triples_key(one) == outlet_triples_key(two)
        assert outlet_triples_key(one) != outlet_triples_key([_outlet(20), _outlet(31)])
