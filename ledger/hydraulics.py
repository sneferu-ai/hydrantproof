"""NFPA 291 flow-test arithmetic — pure Python, no Django imports.

Every stored flow-test snapshot is computed here exactly once (at save time)
and never recomputed lazily; ``formula_version`` is stamped on the result.

    q_i        = 29.83 · c_i · d_i² · √p_i            (per outlet, gpm)
    pitot_sum  = Σ q_i
    Q_F        = measured_total_gpm when present, else pitot_sum
    Q_R        = Q_F · ((S − T) / (S − R))^0.54       (rated flow at 20 psi residual)

The ``MAX_TOTAL_GPM`` ceiling is validated independently on the meter total,
the pitot sum, and the authoritative ``Q_F``: a meter under the cap does not
rescue a pitot sum over it, and vice versa.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .constants import (
    CURRENT_FORMULA_VERSION,
    DISCHARGE_CONSTANT,
    DROP_EXPONENT,
    MAX_EXTRAPOLATION_FACTOR,
    MAX_OUTLETS_PER_TEST,
    MAX_PITOT_PSI,
    MAX_STATIC_PSI,
    MAX_TOTAL_GPM,
    METER_PITOT_DIVERGENCE_PCT,
    TARGET_RESIDUAL_PSI,
    WARNING_DUPLICATE_READING,
    WARNING_EXCESSIVE_EXTRAPOLATION,
    WARNING_METER_AND_PITOT_PRESENT,
    WARNING_METER_PITOT_DIVERGENT,
    WARNING_ORDER,
    WARNING_RESIDUAL_BELOW_TARGET,
    WARNING_STATIC_BELOW_TARGET,
)


class HydraulicsError(ValueError):
    """A validation failure carrying the offending field name."""

    def __init__(self, field_name: str, message: str):
        super().__init__(f"{field_name}: {message}")
        self.field_name = field_name
        self.message = message


@dataclass(frozen=True)
class OutletReadingInput:
    """One outlet's raw reading (pre-computation)."""

    diameter_in: float
    coefficient: float
    pitot_psi: float
    label: str = ""
    flow_asset_tag: str | None = None


@dataclass(frozen=True)
class FlowComputation:
    """The stored snapshot produced by one flow test."""

    computed_total_gpm: float                 # authoritative Q_F
    rated_gpm_20: float | None                # Q_R (None when excluded)
    capacity_class: str | None
    warnings: tuple[str, ...]
    formula_version: str
    pitot_sum_gpm: float | None = None        # stored when both meter and pitot present
    extrapolation_factor: float | None = None
    outlet_gpm: tuple[float, ...] = field(default=())

    def has_warning(self, code: str) -> bool:
        return code in self.warnings


def round_gpm(value: float) -> float:
    """Round a flow to 0.1 gpm, half-up (the persistence convention)."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def discharge_gpm(coefficient: float, diameter_in: float, pitot_psi: float) -> float:
    """q = 29.83 · c · d² · √p  (gpm)."""
    return DISCHARGE_CONSTANT * coefficient * (diameter_in ** 2) * math.sqrt(pitot_psi)


def capacity_class_for(rated_gpm_20: float | None) -> str | None:
    """Capacity class from a rated flow; ``None`` renders "Unclassified"."""
    if rated_gpm_20 is None:
        return None
    if rated_gpm_20 >= 1500:
        return "AA"
    if rated_gpm_20 >= 1000:
        return "A"
    if rated_gpm_20 >= 500:
        return "B"
    return "C"


def extrapolation_factor(static_psi: float, residual_psi: float) -> float | None:
    """(S − T) / (S − R); ``None`` when the denominator is not positive."""
    denominator = static_psi - residual_psi
    if denominator <= 0:
        return None
    return (static_psi - TARGET_RESIDUAL_PSI) / denominator


def _validate_outlets(outlets: list[OutletReadingInput]) -> None:
    if len(outlets) > MAX_OUTLETS_PER_TEST:
        raise HydraulicsError("outlets", f"at most {MAX_OUTLETS_PER_TEST} outlet readings per test")
    for index, outlet in enumerate(outlets):
        prefix = f"outlets[{index}]"
        if not (0.5 <= float(outlet.diameter_in) <= 8.0):
            raise HydraulicsError(f"{prefix}.outlet_diameter_in", "must be between 0.5 and 8.0 inches")
        if not (0.5 <= float(outlet.coefficient) <= 1.0):
            raise HydraulicsError(f"{prefix}.outlet_coefficient", "must be between 0.5 and 1.0")
        if not (0.0 < float(outlet.pitot_psi) <= MAX_PITOT_PSI):
            raise HydraulicsError(f"{prefix}.pitot_psi", f"must be greater than 0 and at most {MAX_PITOT_PSI:g}")


def compute_flow_test(
    *,
    static_psi: float,
    residual_psi: float,
    outlets: list[OutletReadingInput] | tuple[OutletReadingInput, ...] = (),
    measured_total_gpm: float | None = None,
    formula_version: str = CURRENT_FORMULA_VERSION,
    duplicate: bool = False,
) -> FlowComputation:
    """Compute and validate one flow test's stored snapshot.

    Raises :class:`HydraulicsError` with the offending field name on any
    rejected input; the edge matrix of the specification §6 is enforced here
    and nowhere else.
    """
    static_psi = float(static_psi)
    residual_psi = float(residual_psi)
    outlets = list(outlets)

    if not (0.0 < static_psi <= MAX_STATIC_PSI):
        raise HydraulicsError("static_psi", f"must be greater than 0 and at most {MAX_STATIC_PSI:g}")
    if residual_psi < 0:
        raise HydraulicsError("residual_psi", "must be zero or greater")
    if residual_psi >= static_psi:
        raise HydraulicsError("residual_psi", "must be less than static_psi")

    _validate_outlets(outlets)

    if measured_total_gpm is not None:
        measured_total_gpm = float(measured_total_gpm)
        if not (1.0 <= measured_total_gpm <= MAX_TOTAL_GPM):
            raise HydraulicsError("measured_total_gpm", f"must be between 1 and {MAX_TOTAL_GPM:,} gpm")
    if not outlets and measured_total_gpm is None:
        raise HydraulicsError("outlets", "provide at least one outlet reading or a meter total")

    outlet_gpm = tuple(discharge_gpm(o.coefficient, o.diameter_in, o.pitot_psi) for o in outlets)
    pitot_sum = sum(outlet_gpm) if outlet_gpm else None
    if pitot_sum is not None and pitot_sum > MAX_TOTAL_GPM:
        raise HydraulicsError("outlets", f"pitot discharge sum exceeds {MAX_TOTAL_GPM:,} gpm")

    if measured_total_gpm is not None:
        authoritative = measured_total_gpm
    else:
        authoritative = pitot_sum
    if authoritative > MAX_TOTAL_GPM:
        raise HydraulicsError("measured_total_gpm", f"authoritative flow exceeds {MAX_TOTAL_GPM:,} gpm")

    warnings: set[str] = set()
    if measured_total_gpm is not None and pitot_sum is not None:
        warnings.add(WARNING_METER_AND_PITOT_PRESENT)
        if measured_total_gpm > 0 and abs(measured_total_gpm - pitot_sum) / measured_total_gpm * 100.0 > METER_PITOT_DIVERGENCE_PCT:
            warnings.add(WARNING_METER_PITOT_DIVERGENT)
    if duplicate:
        warnings.add(WARNING_DUPLICATE_READING)

    factor = extrapolation_factor(static_psi, residual_psi)
    rated: float | None
    if static_psi <= TARGET_RESIDUAL_PSI:
        # A static pressure at or below the target cannot be extrapolated up;
        # the factor is not meaningful and is not stored.
        rated = None
        factor = None
        warnings.add(WARNING_STATIC_BELOW_TARGET)
    elif factor is not None and factor > MAX_EXTRAPOLATION_FACTOR:
        # MAX_EXTRAPOLATION_FACTOR — provisional, verification-gated (constants.py).
        rated = None
        warnings.add(WARNING_EXCESSIVE_EXTRAPOLATION)
    else:
        rated = authoritative * (factor ** DROP_EXPONENT) if factor is not None else None
        if residual_psi < TARGET_RESIDUAL_PSI:
            # Conservative interpolation: (S−T)/(S−R) < 1 ⇒ Q_R < Q_F.
            warnings.add(WARNING_RESIDUAL_BELOW_TARGET)

    ordered_warnings = tuple(code for code in WARNING_ORDER if code in warnings)
    rated_rounded = round_gpm(rated) if rated is not None else None
    return FlowComputation(
        computed_total_gpm=round_gpm(authoritative),
        rated_gpm_20=rated_rounded,
        capacity_class=capacity_class_for(rated_rounded),
        warnings=ordered_warnings,
        formula_version=formula_version,
        # Stored only when both a meter total and pitot readings are present
        # (specification §5: the pitot sum is context for the meter-wins rule).
        pitot_sum_gpm=(
            round_gpm(pitot_sum)
            if (pitot_sum is not None and measured_total_gpm is not None)
            else None
        ),
        extrapolation_factor=factor,
        outlet_gpm=tuple(round_gpm(value) for value in outlet_gpm),
    )


def outlet_triples_key(outlets) -> tuple:
    """The sorted multiset key used by exact duplicate detection.

    ``outlets`` is any iterable of objects/dicts carrying ``outlet_diameter_in``
    (or ``diameter_in``), ``outlet_coefficient`` (or ``coefficient``) and
    ``pitot_psi``.  Values are rounded to the comparison precision pinned by
    the specification (pressures 0.01).
    """
    key = []
    for outlet in outlets:
        if isinstance(outlet, dict):
            diameter = outlet["outlet_diameter_in"]
            coefficient = outlet["outlet_coefficient"]
            pitot = outlet["pitot_psi"]
        else:
            diameter = getattr(outlet, "outlet_diameter_in", getattr(outlet, "diameter_in", None))
            coefficient = getattr(outlet, "outlet_coefficient", getattr(outlet, "coefficient", None))
            pitot = outlet.pitot_psi
        key.append((
            round(float(diameter), 4),
            round(float(coefficient), 4),
            round(float(pitot), 2),
        ))
    return tuple(sorted(key))
