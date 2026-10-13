"""Transactional ledger operations and the derivation service layer.

Serializers and views stay thin: every write that must hold an invariant
(initial status events, the current-test pointer, deficiency rows, the status
cache) lives here, inside one transaction, under ``select_for_update`` where
the specification requires it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from . import constants as C
from .derive import (
    DerivationParams,
    FlowTestView,
    HydrantView,
    InspectionView,
    derive_hydrant,
)
from .hydraulics import HydraulicsError, compute_flow_test, outlet_triples_key
from .models import (
    Deficiency,
    DistrictSettings,
    FlowTest,
    Hydrant,
    HydrantAttributeChange,
    HydrantStatusEvent,
    Inspection,
    OosAcknowledgement,
    OutletReading,
    Parcel,
    ParcelAttributeChange,
)


class LedgerError(Exception):
    """A domain-rule rejection; ``field`` names the offending input."""

    def __init__(self, detail: str, field: str | None = None, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.field = field
        self.status = status


# ---------------------------------------------------------------------------
# Derivation service
# ---------------------------------------------------------------------------
def params_from_settings(district: DistrictSettings | None = None, today: date | None = None) -> DerivationParams:
    district = district or DistrictSettings.load()
    return district.derivation_params(today)


def superseded_ids(hydrant_ids=None):
    qs = FlowTest.objects.filter(supersedes__isnull=False).values_list("supersedes_id", flat=True)
    return set(qs)


def current_test_for(hydrant_id: int) -> FlowTest | None:
    """max (tested_on, created_at, id) among rows not superseded by another."""
    superseded = superseded_ids()
    return (
        FlowTest.objects.filter(hydrant_id=hydrant_id)
        .exclude(pk__in=superseded)
        .order_by("-tested_on", "-created_at", "-id")
        .first()
    )


def previous_test_for(hydrant_id: int, current: FlowTest | None) -> FlowTest | None:
    if current is None:
        return None
    return (
        FlowTest.objects.filter(hydrant_id=hydrant_id, pk__lt=current.pk)
        .exclude(pk__in=superseded_ids())
        .order_by("-tested_on", "-created_at", "-id")
        .first()
    )


def _test_view(test: FlowTest | None) -> FlowTestView | None:
    if test is None:
        return None
    return FlowTestView(
        tested_on=test.tested_on,
        rated_gpm_20=float(test.rated_gpm_20) if test.rated_gpm_20 is not None else None,
        warnings=tuple(test.warnings or ()),
        formula_version=test.formula_version,
        computed_total_gpm=float(test.computed_total_gpm),
        created_at=test.created_at,
        pk=test.pk,
    )


def _inspection_view(inspection: Inspection | None) -> InspectionView | None:
    if inspection is None:
        return None
    return InspectionView(
        inspected_on=inspection.inspected_on,
        result=inspection.result,
        created_at=inspection.created_at,
        pk=inspection.pk,
    )


def load_hydrant_views(hydrants) -> dict[int, HydrantView]:
    """Bulk-load everything the derivation needs for a set of hydrants."""
    hydrants = list(hydrants)
    ids = [h.pk for h in hydrants]
    superseded = superseded_ids()

    tests_by_hydrant: dict[int, list[FlowTest]] = {hid: [] for hid in ids}
    for test in FlowTest.objects.filter(hydrant_id__in=ids).order_by("tested_on", "created_at", "id"):
        tests_by_hydrant.setdefault(test.hydrant_id, []).append(test)

    inspections_by_hydrant: dict[int, list[Inspection]] = {hid: [] for hid in ids}
    for inspection in Inspection.objects.filter(hydrant_id__in=ids).order_by(
        "inspected_on", "created_at", "id"
    ):
        inspections_by_hydrant.setdefault(inspection.hydrant_id, []).append(inspection)

    open_deficiencies: dict[int, list[Deficiency]] = {hid: [] for hid in ids}
    for deficiency in Deficiency.objects.filter(hydrant_id__in=ids, status=C.DEFICIENCY_OPEN):
        open_deficiencies.setdefault(deficiency.hydrant_id, []).append(deficiency)

    acks: dict[int, OosAcknowledgement] = {}
    for ack in OosAcknowledgement.objects.filter(hydrant_id__in=ids).order_by("acknowledged_at", "id"):
        acks[ack.hydrant_id] = ack  # last one wins (latest)

    latest_events: dict[int, HydrantStatusEvent] = {}
    for event in HydrantStatusEvent.objects.filter(hydrant_id__in=ids).order_by(
        "effective_on", "changed_at", "id"
    ):
        latest_events[event.hydrant_id] = event

    views: dict[int, HydrantView] = {}
    for hydrant in hydrants:
        tests = [t for t in tests_by_hydrant.get(hydrant.pk, []) if t.pk not in superseded]
        tests.sort(key=lambda t: (t.tested_on, t.created_at, t.pk), reverse=True)
        current = tests[0] if tests else None
        previous = tests[1] if len(tests) > 1 else None
        inspections = inspections_by_hydrant.get(hydrant.pk, [])
        current_inspection = inspections[-1] if inspections else None
        deficiencies = open_deficiencies.get(hydrant.pk, [])
        event = latest_events.get(hydrant.pk)
        views[hydrant.pk] = HydrantView(
            status=event.to_status if event else hydrant.status,
            current_test=_test_view(current),
            previous_test=_test_view(previous),
            current_inspection=_inspection_view(current_inspection),
            open_deficiency_codes=tuple(sorted({d.category for d in deficiencies})),
            gates_credit_deficiency=any(d.gates_credit for d in deficiencies),
            expected_return_on=event.expected_return_on if event else None,
            indefinite=bool(event.indefinite) if event else False,
            oos_acknowledged_at=acks[hydrant.pk].acknowledged_at if hydrant.pk in acks else None,
            status_reason=event.reason if event else "",
        )
    return views


def derive_hydrants(hydrants, params: DerivationParams | None = None):
    params = params or params_from_settings()
    views = load_hydrant_views(hydrants)
    return {pk: derive_hydrant(view, params) for pk, view in views.items()}


# ---------------------------------------------------------------------------
# Status events
# ---------------------------------------------------------------------------
def current_status_event(hydrant: Hydrant) -> HydrantStatusEvent | None:
    return hydrant.status_events.order_by("-effective_on", "-changed_at", "-id").first()


def current_status(hydrant: Hydrant) -> str:
    event = current_status_event(hydrant)
    return event.to_status if event else hydrant.status


def create_status_event(
    *,
    hydrant: Hydrant,
    to_status: str,
    reason: str,
    user,
    expected_return_on: date | None = None,
    indefinite: bool = False,
    effective_on: date | None = None,
    reversal_of: HydrantStatusEvent | None = None,
    is_initial: bool = False,
) -> HydrantStatusEvent:
    """Apply the six-transition state machine with required reasons/fields."""
    today = timezone.localdate()
    effective_on = effective_on or today
    if effective_on > today:
        raise LedgerError("effective_on may not be in the future", field="effective_on")

    with transaction.atomic():
        locked = Hydrant.objects.select_for_update().get(pk=hydrant.pk)
        current_event = current_status_event(locked)
        from_status = current_event.to_status if current_event else ""

        if reversal_of is not None:
            if current_event is None or reversal_of.pk != current_event.pk:
                raise LedgerError(
                    "a reversal may target only the current (latest) status event",
                    field="reversal_of",
                )
            if reversal_of.reversal_of_id is not None:
                raise LedgerError(
                    "a reversal event may not itself be reversed; record a further forward event",
                    field="reversal_of",
                )
            to_status = reversal_of.from_status
            expected_return_on = reversal_of.expected_return_on
            indefinite = reversal_of.indefinite
            effective_on = reversal_of.effective_on
            if not reason:
                raise LedgerError("a reversal requires a reason", field="reason")
        elif not is_initial:
            if from_status == to_status:
                raise LedgerError(f"status is already {to_status}", field="to_status")
            if (from_status, to_status) not in C.STATUS_TRANSITIONS:
                raise LedgerError(
                    f"illegal transition {from_status or 'UNKNOWN'} -> {to_status}", field="to_status"
                )

        if to_status == C.STATUS_OUT_OF_SERVICE:
            if not expected_return_on and not indefinite:
                raise LedgerError(
                    "entering OUT_OF_SERVICE requires expected_return_on or indefinite=true",
                    field="expected_return_on",
                )
            if expected_return_on and indefinite:
                raise LedgerError(
                    "provide expected_return_on or indefinite=true, not both",
                    field="indefinite",
                )
        else:
            expected_return_on = None
            indefinite = False

        if (from_status, to_status) in C.TRANSITIONS_REQUIRING_REASON and not reason:
            raise LedgerError("this transition requires a reason", field="reason")
        if not reason:
            reason = "Initial record" if is_initial else ""

        event = HydrantStatusEvent.objects.create(
            hydrant=locked,
            from_status=from_status,
            to_status=to_status,
            reason=reason or "Status change",
            expected_return_on=expected_return_on,
            indefinite=indefinite,
            effective_on=effective_on,
            reversal_of=reversal_of,
            changed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        # Current status = event with max (effective_on, changed_at, id): a
        # backdated event never silently becomes the current status.
        winner = locked.status_events.order_by("-effective_on", "-changed_at", "-id").first()
        current = winner.to_status if winner else to_status
        locked.status = current
        locked.updated_by = user if getattr(user, "is_authenticated", False) else None
        locked.save(update_fields=["status", "updated_by", "updated_at"])
        hydrant.status = current
    return event


def record_attribute_changes(hydrant, changes: list[tuple[str, str, str]], *, reason: str, user, batch=None):
    """Append HydrantAttributeChange rows for a dict of field -> (old, new)."""
    for field, old_value, new_value in changes:
        HydrantAttributeChange.objects.create(
            hydrant=hydrant,
            field=field,
            old_value="" if old_value is None else str(old_value),
            new_value="" if new_value is None else str(new_value),
            change_reason=reason,
            changed_by=user if getattr(user, "is_authenticated", False) else None,
            import_batch=batch,
        )


def values_differ(old, new) -> bool:
    """Typed change detection for the PATCH audit paths.

    ``str(old) != str(new)`` writes a permanent audit row for a semantic no-op
    (``Decimal('38.0')`` vs ``'38.00'``, ``None`` vs ``''``) into the Tier A
    append-only tables, where it can never be removed.  Numbers compare
    numerically; strings compare as strings (so ``'007'`` vs ``'7'`` is still
    a real change); ``None``/``''`` are the same "unset" value.
    """
    from decimal import Decimal, InvalidOperation

    if old is None and new in ("", None):
        return False
    if new is None and old in ("", None):
        return False
    if old is None or new is None:
        return True
    for candidate, other in ((old, new), (new, old)):
        if isinstance(candidate, (int, float, Decimal)) and not isinstance(candidate, bool):
            try:
                return Decimal(str(candidate)) != Decimal(str(other))
            except (InvalidOperation, ValueError, TypeError):
                return True
    return str(old) != str(new)


# ---------------------------------------------------------------------------
# Flow tests
# ---------------------------------------------------------------------------
def canonical_payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def exact_duplicate_exists(hydrant: Hydrant, tested_on: date, static_psi, residual_psi,
                           outlet_key, measured_total_gpm) -> bool:
    """Same hydrant, same tested_on, identical stored values (specification §5)."""
    candidates = FlowTest.objects.filter(hydrant=hydrant, tested_on=tested_on)
    for candidate in candidates:
        if abs(float(candidate.static_psi) - float(static_psi)) > 0.005:
            continue
        if abs(float(candidate.residual_psi) - float(residual_psi)) > 0.005:
            continue
        candidate_meter = candidate.measured_total_gpm
        if (candidate_meter is None) != (measured_total_gpm is None):
            continue
        if candidate_meter is not None and abs(float(candidate_meter) - float(measured_total_gpm)) > 0.05:
            continue
        candidate_key = outlet_triples_key(candidate.outlets.all())
        if candidate_key == outlet_key:
            return True
    return False


def create_flow_test(
    *,
    hydrant: Hydrant,
    tested_on: date,
    static_psi,
    residual_psi,
    outlets: list[dict],
    measured_total_gpm=None,
    crew_names: str = "",
    notes: str = "",
    supersedes: FlowTest | None = None,
    client_uuid=None,
    client_payload_hash: str = "",
    user=None,
    is_sample_data: bool = False,
) -> FlowTest:
    """Compute the snapshot once, enforce supersession integrity, persist."""
    today = timezone.localdate()
    if tested_on > today:
        raise LedgerError("tested_on may not be in the future", field="tested_on")

    from .hydraulics import OutletReadingInput

    outlet_inputs = [
        OutletReadingInput(
            diameter_in=float(o["outlet_diameter_in"]),
            coefficient=float(o["outlet_coefficient"]),
            pitot_psi=float(o["pitot_psi"]),
            label=o.get("outlet_label", "") or "",
            flow_asset_tag=o.get("flow_asset_tag"),
        )
        for o in outlets
    ]
    outlet_key = outlet_triples_key(
        [
            {
                "outlet_diameter_in": o.diameter_in,
                "outlet_coefficient": o.coefficient,
                "pitot_psi": o.pitot_psi,
            }
            for o in outlet_inputs
        ]
    )
    duplicate = exact_duplicate_exists(
        hydrant, tested_on, static_psi, residual_psi, outlet_key, measured_total_gpm
    )
    try:
        computation = compute_flow_test(
            static_psi=float(static_psi),
            residual_psi=float(residual_psi),
            outlets=outlet_inputs,
            measured_total_gpm=float(measured_total_gpm) if measured_total_gpm is not None else None,
            duplicate=duplicate,
        )
    except HydraulicsError as exc:
        raise LedgerError(exc.message, field=exc.field_name) from exc

    with transaction.atomic():
        locked = Hydrant.objects.select_for_update().get(pk=hydrant.pk)
        if supersedes is not None:
            if supersedes.hydrant_id != locked.pk:
                raise LedgerError("supersedes must reference a test of the same hydrant",
                                  field="supersedes")
            if FlowTest.objects.filter(supersedes=supersedes).exists():
                raise LedgerError("supersedes target is already superseded", field="supersedes")

        test = FlowTest.objects.create(
            hydrant=locked,
            tested_on=tested_on,
            static_psi=static_psi,
            residual_psi=residual_psi,
            measured_total_gpm=measured_total_gpm,
            client_uuid=client_uuid,
            client_payload_hash=client_payload_hash,
            computed_total_gpm=computation.computed_total_gpm,
            pitot_sum_gpm=computation.pitot_sum_gpm,
            rated_gpm_20=computation.rated_gpm_20,
            capacity_class=computation.capacity_class,
            warnings=list(computation.warnings),
            formula_version=computation.formula_version,
            crew_names=crew_names or "",
            notes=notes or "",
            supersedes=supersedes,
            tested_by=user if getattr(user, "is_authenticated", False) else None,
            is_sample_data=is_sample_data,
        )
        # Resolve flowed-hydrant tags to hydrant FKs (attribution stays visible).
        flowed_by_tag = {}
        tags = [o.flow_asset_tag for o in outlet_inputs if o.flow_asset_tag]
        if tags:
            flowed_by_tag = {h.asset_tag: h for h in Hydrant.objects.filter(asset_tag__in=tags)}

        for index, (outlet, gpm) in enumerate(zip(outlet_inputs, computation.outlet_gpm)):
            flow_hydrant = locked
            if outlet.flow_asset_tag:
                flow_hydrant = flowed_by_tag.get(outlet.flow_asset_tag, locked)
            OutletReading.objects.create(
                flow_test=test,
                flow_hydrant=flow_hydrant,
                outlet_label=outlet.label or f"outlet-{index + 1}",
                outlet_diameter_in=outlet.diameter_in,
                outlet_coefficient=outlet.coefficient,
                pitot_psi=outlet.pitot_psi,
                computed_gpm=gpm,
            )
        refresh_current_test_ref(locked)
    return test


def refresh_current_test_ref(hydrant: Hydrant) -> FlowTest | None:
    """Maintain the denormalized pointer under the caller's row lock."""
    current = current_test_for(hydrant.pk)
    Hydrant.objects.filter(pk=hydrant.pk).update(
        current_test_ref=current, updated_at=timezone.now()
    )
    hydrant.current_test_ref = current
    return current


# ---------------------------------------------------------------------------
# Inspections and deficiencies
# ---------------------------------------------------------------------------
def create_inspection(
    *,
    hydrant: Hydrant,
    inspected_on: date,
    result: str,
    deficiency_codes: list[str],
    inspector_names: str = "",
    notes: str = "",
    user=None,
    is_sample_data: bool = False,
) -> Inspection:
    today = timezone.localdate()
    if inspected_on > today:
        raise LedgerError("inspected_on may not be in the future", field="inspected_on")
    codes = [c for c in (deficiency_codes or []) if c]
    if result != C.INSPECTION_OK and not codes:
        raise LedgerError("deficiency_codes are required when result is not OK", field="deficiency_codes")
    unknown = [c for c in codes if c not in C.DEFICIENCY_CODES]
    if unknown:
        raise LedgerError(f"unknown deficiency code(s): {', '.join(unknown)}", field="deficiency_codes")

    with transaction.atomic():
        locked = Hydrant.objects.select_for_update().get(pk=hydrant.pk)
        inspection = Inspection.objects.create(
            hydrant=locked,
            inspected_on=inspected_on,
            result=result,
            deficiency_codes=codes,
            inspector_names=inspector_names or "",
            notes=notes or "",
            inspected_by=user if getattr(user, "is_authenticated", False) else None,
            is_sample_data=is_sample_data,
        )
        if result == C.INSPECTION_FAIL:
            for code in dict.fromkeys(codes):  # idempotent per (inspection, category)
                Deficiency.objects.get_or_create(
                    inspection=inspection,
                    category=code,
                    defaults=dict(
                        hydrant=locked,
                        gates_credit=True,
                        opened_by=user if getattr(user, "is_authenticated", False) else None,
                        is_sample_data=is_sample_data,
                    ),
                )
        elif result == C.INSPECTION_DEFICIENCY:
            for code in dict.fromkeys(codes):
                Deficiency.objects.get_or_create(
                    inspection=inspection,
                    category=code,
                    defaults=dict(
                        hydrant=locked,
                        gates_credit=False,
                        opened_by=user if getattr(user, "is_authenticated", False) else None,
                        is_sample_data=is_sample_data,
                    ),
                )
        else:  # OK transactionally auto-closes every OPEN row
            _auto_close_deficiencies(locked, user)
    return inspection


def _auto_close_deficiencies(hydrant: Hydrant, user) -> int:
    now = timezone.now()
    return Deficiency.objects.filter(hydrant=hydrant, status=C.DEFICIENCY_OPEN).update(
        status=C.DEFICIENCY_RESOLVED,
        resolved_at=now,
        resolution_kind=C.RESOLUTION_AUTO_OK,
        resolution_note="Auto-closed by an OK inspection",
        resolved_by=user if getattr(user, "is_authenticated", False) else None,
    )


def resolve_deficiency(
    *,
    deficiency: Deficiency,
    resolution_note: str,
    resolved_at,
    user,
    assigned_to: str | None = None,
    due_on: date | None = None,
) -> Deficiency:
    if deficiency.status != C.DEFICIENCY_OPEN:
        raise LedgerError("deficiency is already resolved", field="status")
    if not resolution_note:
        raise LedgerError("resolution_note is required", field="resolution_note")
    today = timezone.localdate()
    if resolved_at is None:
        resolved_at = today
    if resolved_at > today:
        raise LedgerError("resolved_at may not be in the future", field="resolved_at")
    if due_on is not None and due_on > today + timedelta(days=365):
        raise LedgerError("due_on may not be more than 365 days out", field="due_on")
    deficiency.status = C.DEFICIENCY_RESOLVED
    deficiency.resolved_at = timezone.make_aware(
        datetime.combine(resolved_at, datetime.min.time())
    ) if not isinstance(resolved_at, datetime) else resolved_at
    deficiency.resolution_note = resolution_note
    deficiency.resolution_kind = C.RESOLUTION_MANUAL
    deficiency.resolved_by = user if getattr(user, "is_authenticated", False) else None
    update_fields = ["status", "resolved_at", "resolution_note", "resolution_kind", "resolved_by"]
    # §3/§5: the resolve route is the one non-admin path that assigns the row —
    # persist what it was given; ``update_fields`` must carry every field it
    # touches or the assignment is silently dropped from the stored row while
    # the response echoes it.
    if assigned_to is not None:
        deficiency.assigned_to = assigned_to
        update_fields.append("assigned_to")
    if due_on is not None:
        deficiency.due_on = due_on
        update_fields.append("due_on")
    deficiency.save(update_fields=update_fields)
    return deficiency


# ---------------------------------------------------------------------------
# Parcels
# ---------------------------------------------------------------------------
def record_parcel_changes(parcel: Parcel, changes: list[tuple[str, str, str]], *, user):
    for field, old_value, new_value in changes:
        ParcelAttributeChange.objects.create(
            parcel=parcel,
            field=field,
            old_value="" if old_value is None else str(old_value),
            new_value="" if new_value is None else str(new_value),
            change_reason="manual",
            changed_by=user if getattr(user, "is_authenticated", False) else None,
        )


# ---------------------------------------------------------------------------
# Overdue ranking
# ---------------------------------------------------------------------------
def overdue_rows(kind: str = "test", today: date | None = None):
    """Ranked overdue list for tests or inspections (bulk-loaded — one bounded
    query set for the whole table, not one load per hydrant)."""
    today = today or timezone.localdate()
    district = DistrictSettings.load()
    params = district.derivation_params(today)
    hydrants = list(Hydrant.objects.all().order_by("asset_tag"))
    views = load_hydrant_views(hydrants)
    rows = []
    for hydrant in hydrants:
        derived = derive_hydrant(views[hydrant.pk], params)
        if kind == "inspection":
            if derived.days_inspection_overdue:
                rows.append((hydrant, derived.days_inspection_overdue, derived))
        else:
            if derived.days_test_overdue:
                rows.append((hydrant, derived.days_test_overdue, derived))
    rows.sort(key=lambda item: (-item[1], item[0].asset_tag))
    return rows
