#!/usr/bin/env python3
"""Example 1 — NFPA 291 flow arithmetic with the product's own engine.

Runs completely offline: no server, no database. It imports the exact module
the server uses (`ledger.hydraulics`) so the numbers you see here are the
numbers the API would compute and store.

Setup:
    Run from the repository root with the project's Python environment
    (the app's dependencies installed; `python manage.py` must work).

Run:
    python docs/examples/01_compute_flow.py

Expected output: the two worked fixtures, a warning case, and two exclusion
cases — all with the values asserted in the product's own test suite
(tests/test_hydraulics.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repository root importable no matter where the script is run from.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ledger.hydraulics import HydraulicsError, OutletReadingInput, compute_flow_test  # noqa: E402


def show(title: str, **kwargs) -> None:
    result = compute_flow_test(**kwargs)
    print(f"--- {title} ---")
    print(f"  Q_F (computed_total_gpm): {result.computed_total_gpm}")
    print(f"  Q_R (rated_gpm_20):       {result.rated_gpm_20}")
    print(f"  capacity class:           {result.capacity_class}")
    print(f"  warnings:                 {list(result.warnings) or '[]'}")
    print(f"  formula_version:          {result.formula_version}")
    print()


def main() -> int:
    # Fixture A — one 2.5" outlet at coefficient 0.90, 28 psi pitot.
    # q = 29.83 * 0.90 * 2.50^2 * sqrt(28) = 887.9 gpm
    show(
        "Fixture A — one outlet (pitot sum is Q_F)",
        static_psi=80.0,
        residual_psi=40.0,
        outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.9, pitot_psi=28.0)],
    )

    # The same physical test with a meter total: the meter wins as Q_F and the
    # pitot sum is stored beside it as context (pitot_sum_gpm is not None).
    meter = compute_flow_test(
        static_psi=80.0,
        residual_psi=40.0,
        outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.9, pitot_psi=28.0)],
        measured_total_gpm=900.0,
    )
    print("--- Meter total present (meter wins) ---")
    print(f"  Q_F: {meter.computed_total_gpm}  pitot_sum_gpm: {meter.pitot_sum_gpm}")
    print(f"  warnings: {list(meter.warnings)}")
    print()

    # A sub-target residual test is credited by default; the percentage-drop
    # extrapolation is conservative there (Q_R < Q_F).
    show(
        "Sub-target residual (conservative interpolation)",
        static_psi=60.0,
        residual_psi=15.0,
        outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.9, pitot_psi=20.0)],
    )

    # Static at or below the 20 psi target: cannot be extrapolated up.
    show(
        "Static below target (rated flow is NULL)",
        static_psi=18.0,
        residual_psi=10.0,
        outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.9, pitot_psi=20.0)],
    )

    # Excessive extrapolation (factor > 4.0): excluded, rated flow NULL.
    show(
        "Excessive extrapolation (excluded)",
        static_psi=100.0,
        residual_psi=97.0,
        outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.9, pitot_psi=20.0)],
    )

    # A rejected input shows the API's exact error shape (field name + message).
    try:
        compute_flow_test(static_psi=80.0, residual_psi=80.0)
    except HydraulicsError as exc:
        print("--- Rejected input (residual == static) ---")
        print(f"  field: {exc.field_name}  message: {exc.message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
