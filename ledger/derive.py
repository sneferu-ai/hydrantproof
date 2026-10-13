"""The one canonical derivation — pure Python, no Django imports.

Every consumer (map badges, list filters, dispatch CSV, worksheet, evidence
pack, trend view) reads a hydrant's readiness, capacity class, recency flags,
district-credit eligibility, exclusion code, warnings and trend from
:func:`derive_hydrant` and from nothing else.  The module is unit-tested
without a database.

Definitions pinned here (specification §6):

* ``test_recency_flag`` — age = today − tested_on.  ``CURRENT`` while the test
  is more than ``due_soon_days`` inside the window (age ≤ recency − due_soon),
  ``DUE_SOON`` inside the trailing early-warning band, ``OVERDUE`` past the
  window, ``NEVER_TESTED`` when there is no current test.  The flag is based on
  the current test regardless of qualification.
* ``readiness`` — three states.  ``WATCH`` carries degrading/due-soon concerns
  without flipping a serviceable hydrant to ``NOT_READY``.
* ``exclusion_codes`` — ordered precedence, first match wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .constants import (
    ATTENTION_FLAGS,
    CURRENT_FORMULA_VERSION,
    EXCLUSION_FORMULA_VERSION_MISMATCH,
    EXCLUSION_INSPECTION_FAIL,
    EXCLUSION_OUT_OF_SERVICE,
    EXCLUSION_RESIDUAL_BELOW_TARGET,
    EXCLUSION_RETIRED,
    EXCLUSION_STATIC_BELOW_TARGET,
    EXCLUSION_TEST_STALE,
    EXCLUSION_UNTESTED,
    EXCLUSION_EXCESSIVE_EXTRAPOLATION,
    GATING_FLAG_ORDER,
    INSPECTION_FAIL,
    OOS_ACK_AMBER_DAYS,
    READINESS_NOT_READY,
    READINESS_READY,
    READINESS_WATCH,
    RECENCY_CURRENT,
    RECENCY_DUE_SOON,
    RECENCY_NEVER_INSPECTED,
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
    TREND_DECLINE_PCT,
    WARNING_EXCESSIVE_EXTRAPOLATION,
    WARNING_RESIDUAL_BELOW_TARGET,
    WARNING_STATIC_BELOW_TARGET,
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FlowTestView:
    """One flow test as the derivation sees it (already ordered)."""

    tested_on: date
    rated_gpm_20: float | None
    warnings: tuple[str, ...] = ()
    formula_version: str = CURRENT_FORMULA_VERSION
    computed_total_gpm: float | None = None
    created_at: datetime | None = None
    pk: int | None = None

    def has_warning(self, code: str) -> bool:
        return code in (self.warnings or ())


@dataclass(frozen=True)
class InspectionView:
    inspected_on: date
    result: str
    created_at: datetime | None = None
    pk: int | None = None


@dataclass(frozen=True)
class HydrantView:
    status: str = STATUS_IN_SERVICE
    current_test: FlowTestView | None = None
    previous_test: FlowTestView | None = None
    current_inspection: InspectionView | None = None
    open_deficiency_codes: tuple[str, ...] = ()
    gates_credit_deficiency: bool = False
    expected_return_on: date | None = None
    indefinite: bool = False
    oos_acknowledged_at: datetime | None = None
    status_reason: str = ""  # the current status event's reason (dispatch CSV)


@dataclass(frozen=True)
class DerivationParams:
    """The frozen parameters a derivation runs under (never live settings)."""

    today: date
    recency_days: int = 365
    due_soon_days: int = 90
    inspection_recency_days: int = 365
    inspection_due_soon_days: int = 90
    exclude_residual_below_target: bool = False
    current_formula_version: str = CURRENT_FORMULA_VERSION


@dataclass(frozen=True)
class HydrantDerivation:
    test_recency_flag: str
    inspection_recency_flag: str
    district_credit_eligible: bool
    capacity_class: str | None
    trend: str
    readiness: str
    gating_flags: tuple[str, ...] = field(default=())
    attention_flags: tuple[str, ...] = field(default=())
    exclusion_code: str | None = None
    warnings: tuple[str, ...] = field(default=())
    open_deficiency_codes: tuple[str, ...] = field(default=())
    open_deficiency: bool = False
    gates_credit_deficiency: bool = False
    oos_expired: bool = False
    days_past_expiry: int | None = None
    expected_return_on: date | None = None
    oos_reason: str | None = None
    oos_acknowledged_at: datetime | None = None
    oos_acknowledged_active: bool = False
    days_test_overdue: int | None = None
    days_inspection_overdue: int | None = None
    latest_tested_on: date | None = None
    latest_inspected_on: date | None = None
    rated_gpm_20: float | None = None
    computed_total_gpm: float | None = None


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------
def _recency_flag(
    occurred_on: date | None,
    today: date,
    window_days: int,
    due_soon_days: int,
    never_label: str,
) -> str:
    if occurred_on is None:
        return never_label
    age_days = (today - occurred_on).days
    if age_days < 0:
        age_days = 0  # future dates are rejected at the API boundary; never mislabel
    if age_days <= max(0, window_days - due_soon_days):
        return RECENCY_CURRENT
    if age_days <= window_days:
        return RECENCY_DUE_SOON
    return RECENCY_OVERDUE


def test_recency_flag(tested_on: date | None, params: DerivationParams) -> str:
    return _recency_flag(tested_on, params.today, params.recency_days, params.due_soon_days, RECENCY_NEVER_TESTED)


def inspection_recency_flag(inspected_on: date | None, params: DerivationParams) -> str:
    return _recency_flag(
        inspected_on,
        params.today,
        params.inspection_recency_days,
        params.inspection_due_soon_days,
        RECENCY_NEVER_INSPECTED,
    )


def days_overdue(occurred_on: date | None, today: date, window_days: int) -> int | None:
    if occurred_on is None:
        return None
    delta = (today - occurred_on).days - window_days
    return delta if delta > 0 else None


def compute_trend(current: FlowTestView | None, previous: FlowTestView | None) -> str:
    """Four-valued trend (specification §6).

    ``RATED_FLOW_LOST`` is checked before plain ``UNRATED``: it is the specific
    case of a rated test becoming excluded (previous rated non-null, current
    rated NULL).  ``UNRATED`` covers a current test that never had a rating and
    has no rated predecessor.
    """
    current_rated = current.rated_gpm_20 if current is not None else None
    previous_rated = previous.rated_gpm_20 if previous is not None else None
    if current_rated is None:
        if previous is not None and previous_rated is not None:
            return TREND_RATED_FLOW_LOST
        return TREND_UNRATED
    if previous is None or previous_rated is None:
        return TREND_INSUFFICIENT_HISTORY
    if previous_rated <= 0:
        return TREND_STABLE
    decline_pct = (previous_rated - current_rated) / previous_rated * 100.0
    if decline_pct >= TREND_DECLINE_PCT:
        return TREND_DEGRADING
    return TREND_STABLE


def oos_expiry(view: HydrantView, today: date) -> tuple[bool, int | None]:
    """(expired, days past expiry) — a date guess never restores service."""
    if view.status != STATUS_OUT_OF_SERVICE or view.indefinite or view.expected_return_on is None:
        return False, None
    delta = (today - view.expected_return_on).days
    if delta > 0:
        return True, delta
    return False, None


def exclusion_code_for(
    view: HydrantView,
    params: DerivationParams,
    *,
    test_flag: str,
    expired: bool,
) -> str | None:
    """Ordered precedence, first match wins (specification §6)."""
    if view.status == STATUS_RETIRED:
        return EXCLUSION_RETIRED
    if view.status == STATUS_OUT_OF_SERVICE:
        return EXCLUSION_OUT_OF_SERVICE
    if view.current_test is None:
        return EXCLUSION_UNTESTED
    if test_flag == RECENCY_OVERDUE:
        return EXCLUSION_TEST_STALE
    if view.gates_credit_deficiency:
        # EARS 7: the Deficiency table is the single source of truth read by
        # readiness, credit, dispatch, dashboard and pack.  A FAIL inspection
        # opens one gating row per code and excludes the hydrant from district
        # credit with INSPECTION_FAIL; the exclusion is cleared by the same
        # two routes that clear the rows — a later OK inspection (which
        # transactionally auto-closes all OPEN rows) or an editor resolution
        # (POST /resolve/).  Keying on the inspection RESULT instead would
        # leave the hydrant excluded after every row was resolved, which
        # EARS 7 forbids.  Gating rows are opened only by FAIL results, so the
        # code still names the origin, and every non-credited row keeps an
        # explicit reason.
        return EXCLUSION_INSPECTION_FAIL
    if view.current_test.has_warning(WARNING_STATIC_BELOW_TARGET):
        return EXCLUSION_STATIC_BELOW_TARGET
    if view.current_test.has_warning(WARNING_EXCESSIVE_EXTRAPOLATION):
        return EXCLUSION_EXCESSIVE_EXTRAPOLATION
    if view.current_test.formula_version != params.current_formula_version:
        return EXCLUSION_FORMULA_VERSION_MISMATCH
    if params.exclude_residual_below_target and view.current_test.has_warning(WARNING_RESIDUAL_BELOW_TARGET):
        return EXCLUSION_RESIDUAL_BELOW_TARGET
    return None


def gating_flags_for(
    view: HydrantView,
    *,
    test_flag: str,
    inspection_flag: str,
    expired: bool,
) -> tuple[str, ...]:
    applicable = {
        "OUT_OF_SERVICE": view.status == STATUS_OUT_OF_SERVICE,
        "EXPIRED_OOS": expired,
        "UNTESTED": view.current_test is None,
        "TEST_STALE": test_flag == RECENCY_OVERDUE,
        "INSPECTION_FAIL": view.current_inspection is not None and view.current_inspection.result == INSPECTION_FAIL,
        "NEVER_INSPECTED": view.current_inspection is None,
        "INSPECTION_OVERDUE": inspection_flag == RECENCY_OVERDUE,
        "OPEN_DEFICIENCY": bool(view.open_deficiency_codes),
    }
    return tuple(flag for flag in GATING_FLAG_ORDER if applicable.get(flag))


def derive_hydrant(view: HydrantView, params: DerivationParams) -> HydrantDerivation:
    """The canonical derivation for one hydrant under frozen parameters."""
    test_flag = test_recency_flag(view.current_test.tested_on if view.current_test else None, params)
    inspection_flag = inspection_recency_flag(
        view.current_inspection.inspected_on if view.current_inspection else None, params
    )
    expired, days_past = oos_expiry(view, params.today)

    trend = compute_trend(view.current_test, view.previous_test)
    exclusion = exclusion_code_for(view, params, test_flag=test_flag, expired=expired)
    # Specification §6: district_credit_eligible requires a current test, a
    # non-null rated flow, no exclusion, and no gating deficiency.  The first
    # three collapse into "no exclusion code" (a NULL rating always carries
    # STATIC_BELOW_TARGET / EXCESSIVE_EXTRAPOLATION); the rated check stays
    # explicit so the contract is pinned even if the warning set evolves.
    rated_raw = view.current_test.rated_gpm_20 if view.current_test else None
    eligible = exclusion is None and rated_raw is not None
    rated = rated_raw if eligible else None

    inspection_failed = view.current_inspection is not None and view.current_inspection.result == INSPECTION_FAIL
    has_open_deficiency = bool(view.open_deficiency_codes)
    serviceable = view.status == STATUS_IN_SERVICE
    test_ok = test_flag in (RECENCY_CURRENT, RECENCY_DUE_SOON)
    inspection_ok = inspection_flag in (RECENCY_CURRENT, RECENCY_DUE_SOON)

    ready_conditions = serviceable and test_ok and inspection_ok and not inspection_failed and not has_open_deficiency
    if ready_conditions:
        watch_conditions = (
            trend in (TREND_DEGRADING, TREND_RATED_FLOW_LOST)
            or test_flag == RECENCY_DUE_SOON
            or inspection_flag == RECENCY_DUE_SOON
        )
        readiness = READINESS_WATCH if watch_conditions else READINESS_READY
        gating: tuple[str, ...] = ()
    else:
        readiness = READINESS_NOT_READY
        gating = gating_flags_for(view, test_flag=test_flag, inspection_flag=inspection_flag, expired=expired)

    attention = {
        "TEST_DUE_SOON": test_flag == RECENCY_DUE_SOON,
        "INSPECTION_DUE_SOON": inspection_flag == RECENCY_DUE_SOON,
        "FLOW_DEGRADING": trend == TREND_DEGRADING,
        "RATED_FLOW_LOST": trend == TREND_RATED_FLOW_LOST,
    }
    attention_flags = tuple(flag for flag in ATTENTION_FLAGS if attention.get(flag))

    acknowledged_at = view.oos_acknowledged_at
    acknowledged_active = False
    if expired and acknowledged_at is not None:
        ack_age = (params.today - _as_date(acknowledged_at)).days
        acknowledged_active = ack_age <= OOS_ACK_AMBER_DAYS
        if not acknowledged_active:
            acknowledged_at = None  # amber window closed: the row re-surfaces red

    warnings = tuple(view.current_test.warnings) if view.current_test else ()

    return HydrantDerivation(
        test_recency_flag=test_flag,
        inspection_recency_flag=inspection_flag,
        district_credit_eligible=eligible,
        capacity_class=_capacity_class(rated),
        trend=trend,
        readiness=readiness,
        gating_flags=gating,
        attention_flags=attention_flags,
        exclusion_code=exclusion,
        warnings=warnings,
        open_deficiency_codes=tuple(view.open_deficiency_codes),
        open_deficiency=has_open_deficiency,
        gates_credit_deficiency=bool(view.gates_credit_deficiency),
        oos_expired=expired,
        days_past_expiry=days_past,
        expected_return_on=view.expected_return_on if view.status == STATUS_OUT_OF_SERVICE else None,
        oos_reason=view.status_reason if view.status == STATUS_OUT_OF_SERVICE else None,
        oos_acknowledged_at=acknowledged_at,
        oos_acknowledged_active=acknowledged_active,
        days_test_overdue=days_overdue(
            view.current_test.tested_on if view.current_test else None, params.today, params.recency_days
        ),
        days_inspection_overdue=days_overdue(
            view.current_inspection.inspected_on if view.current_inspection else None,
            params.today,
            params.inspection_recency_days,
        ),
        latest_tested_on=view.current_test.tested_on if view.current_test else None,
        latest_inspected_on=view.current_inspection.inspected_on if view.current_inspection else None,
        rated_gpm_20=rated,
        computed_total_gpm=view.current_test.computed_total_gpm if view.current_test else None,
    )


def _capacity_class(rated_gpm_20: float | None) -> str | None:
    from .hydraulics import capacity_class_for

    return capacity_class_for(rated_gpm_20)


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


# ---------------------------------------------------------------------------
# Ordering helpers (one canonical order for "current" and "previous")
# ---------------------------------------------------------------------------
def current_test_key(test) -> tuple:
    """max (tested_on, created_at, id) — the canonical current-test rule."""
    created = getattr(test, "created_at", None)
    if isinstance(created, datetime):
        created_key = created.isoformat()
    else:
        created_key = "" if created is None else str(created)
    return (test.tested_on, created_key, getattr(test, "pk", None) or 0)


def order_tests(tests: list) -> list:
    """Return tests sorted so index 0 is the current test, 1 the previous."""
    return sorted(tests, key=current_test_key, reverse=True)


def current_inspection_key(inspection) -> tuple:
    created = getattr(inspection, "created_at", None)
    if isinstance(created, datetime):
        created_key = created.isoformat()
    else:
        created_key = "" if created is None else str(created)
    return (inspection.inspected_on, created_key, getattr(inspection, "pk", None) or 0)


def order_inspections(inspections: list) -> list:
    return sorted(inspections, key=current_inspection_key, reverse=True)


def days_until_due(occurred_on: date | None, today: date, window_days: int) -> int | None:
    if occurred_on is None:
        return None
    return window_days - (today - occurred_on).days


def amber_deadline(acknowledged_at: date | datetime) -> date:
    return _as_date(acknowledged_at) + timedelta(days=OOS_ACK_AMBER_DAYS)
