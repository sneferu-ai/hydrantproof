"""Page context builders (server-rendered UI; no frontend build step)."""
from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import constants as C
from .auth import user_can_write, user_is_admin
from .geo import FEET_PER_METER
from .models import Deficiency, DistrictSettings, Hydrant, NotificationOutbox, Parcel
from .services import derive_hydrants, params_from_settings


def dashboard_context(request) -> dict:
    from .models import CoverageRun
    from .staleness import coverage_staleness

    today = timezone.localdate()
    district = DistrictSettings.load()
    hydrants = list(Hydrant.objects.all().order_by("asset_tag"))
    derivations = derive_hydrants(hydrants, params_from_settings(district, today))

    readiness_counts = {state: 0 for state in C.READINESS_STATES}
    for derived in derivations.values():
        readiness_counts[derived.readiness] += 1

    overdue_tests = sorted(
        ((h, derivations[h.pk]) for h in hydrants if derivations[h.pk].days_test_overdue),
        key=lambda item: -item[1].days_test_overdue,
    )
    overdue_inspections = sorted(
        ((h, derivations[h.pk]) for h in hydrants if derivations[h.pk].days_inspection_overdue),
        key=lambda item: -item[1].days_inspection_overdue,
    )
    expired_oos = sorted(
        ((h, derivations[h.pk]) for h in hydrants if derivations[h.pk].oos_expired),
        key=lambda item: -item[1].days_past_expiry,
    )
    degrading = [
        (h, derivations[h.pk])
        for h in hydrants
        if derivations[h.pk].trend in (C.TREND_DEGRADING, C.TREND_RATED_FLOW_LOST)
    ]
    open_deficiencies = (
        Deficiency.objects.filter(status=C.DEFICIENCY_OPEN)
        .select_related("hydrant")
        .order_by("hydrant__asset_tag", "id")
    )
    unsent = NotificationOutbox.objects.filter(sent_at__isnull=True).count()

    latest_run = (
        CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE)
        .order_by("-finished_at", "-id")
        .first()
    )
    staleness = coverage_staleness(latest_run) if latest_run else None

    return {
        "district": district,
        "today": today,
        "hydrant_count": len(hydrants),
        "board": [(h, derivations[h.pk]) for h in hydrants],
        "readiness_counts": readiness_counts,
        "overdue_tests": overdue_tests[:50],
        "overdue_inspections": overdue_inspections[:50],
        "expired_oos": expired_oos[:50],
        "degrading": degrading[:50],
        "open_deficiencies": open_deficiencies[:100],
        "unsent_outbox": unsent,
        "derivations": derivations,
        "latest_run": latest_run,
        "coverage_staleness": staleness,
    }


def hydrant_list_context(request) -> dict:
    today = timezone.localdate()
    query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    readiness_filter = (request.GET.get("readiness") or "").strip()

    hydrants = list(Hydrant.objects.all().order_by("asset_tag"))
    if query:
        hydrants = [h for h in hydrants if query.lower() in (h.asset_tag or "").lower()]
    if status_filter in C.HYDRANT_STATUSES:
        hydrants = [h for h in hydrants if h.status == status_filter]
    else:
        status_filter = ""

    derivations = derive_hydrants(hydrants, params_from_settings(today=today))
    rows = [
        {"hydrant": hydrant, "derived": derivations[hydrant.pk]}
        for hydrant in hydrants
    ]
    if readiness_filter in C.READINESS_STATES:
        rows = [row for row in rows if row["derived"].readiness == readiness_filter]
    else:
        readiness_filter = ""

    return {
        "rows": rows,
        "today": today,
        "total": Hydrant.objects.count(),
        "shown": len(rows),
        "query": query,
        "status_filter": status_filter,
        "readiness_filter": readiness_filter,
        "status_choices": [(value, value.replace("_", " ").title()) for value in C.HYDRANT_STATUSES],
        "target_residual_psi": C.TARGET_RESIDUAL_PSI,
    }


def hydrant_detail_context(request, hydrant: Hydrant) -> dict:
    today = timezone.localdate()
    derived = derive_hydrants([hydrant], params_from_settings(today=today))[hydrant.pk]

    # The service-status door: only the transitions the state machine allows
    # from the CURRENT status, each carrying whether a reason is required.
    current_status = hydrant.status
    options = []
    for (from_status, to_status) in sorted(C.STATUS_TRANSITIONS):
        if from_status != current_status:
            continue
        options.append({
            "value": to_status,
            "label": to_status.replace("_", " ").title(),
            "reason_required": (from_status, to_status) in C.TRANSITIONS_REQUIRING_REASON,
        })

    latest_event = hydrant.status_events.order_by("-effective_on", "-changed_at", "-id").first()
    reversal_event_id = None
    if latest_event is not None and latest_event.reversal_of_id is None:
        reversal_event_id = latest_event.pk

    current_test_id = None
    from .services import current_test_for, superseded_ids

    current_test = current_test_for(hydrant.pk)
    if current_test is not None:
        current_test_id = current_test.pk

    # The trend view: rated flow over time, oldest to newest, with the change
    # against the previous included test. Superseded rows are excluded — they
    # are corrections, not observations.
    superseded = superseded_ids([hydrant.pk])
    included = [
        test for test in reversed(list(hydrant.tests.order_by("-tested_on", "-created_at", "-id")[:50]))
        if test.pk not in superseded
    ]
    trend_rows = []
    previous = None
    for test in included:
        delta = None
        if (previous is not None and test.rated_gpm_20 is not None
                and previous.rated_gpm_20 is not None):
            delta = float(test.rated_gpm_20) - float(previous.rated_gpm_20)
        trend_rows.append({"test": test, "delta": delta})
        previous = test
    trend_rows = trend_rows[-8:]

    district = DistrictSettings.load()

    return {
        "hydrant": hydrant,
        "derived": derived,
        "tests": hydrant.tests.order_by("-tested_on", "-created_at", "-id")[:50],
        "trend_rows": trend_rows,
        "inspections": hydrant.inspections.all()[:50],
        "status_events": hydrant.status_events.order_by("-effective_on", "-changed_at", "-id")[:50],
        "attribute_changes": hydrant.attribute_changes.order_by("-changed_at", "-id")[:50],
        "deficiencies": hydrant.deficiencies.order_by("status", "id"),
        "today": today,
        "map_tile_url": getattr(settings, "MAP_TILE_URL", ""),
        "can_write": user_can_write(request.user),
        "status_options": options,
        "default_status_target": options[0]["value"] if options else "",
        "current_event_id": latest_event.pk if latest_event is not None else None,
        "reversal_event_id": reversal_event_id,
        "current_test_id": current_test_id,
        "target_residual_psi": C.TARGET_RESIDUAL_PSI,
        # The map's credit ring draws the district's own radius — the same
        # number the caption states. The conversion runs through the one
        # feet-per-metre constant (geo.FEET_PER_METER) so the drawn ring can
        # never disagree with the caption or with coverage's measurement.
        "credit_radius_ft": district.credit_radius_ft,
        "credit_radius_m": float(district.credit_radius_ft) / FEET_PER_METER,
    }


# ---------------------------------------------------------------------------
# Record surfaces: the forms and registers the spec puts on a screen
# ---------------------------------------------------------------------------

def hydrant_form_context(request, hydrant: Hydrant | None = None) -> dict:
    return {
        "hydrant": hydrant,
        "editing": hydrant is not None,
        "can_write": user_can_write(request.user),
        "statuses": C.HYDRANT_STATUSES,
        "coefficient_presets": C.COEFFICIENT_PRESETS,
    }


def flow_test_form_context(request, hydrant: Hydrant) -> dict:
    from .services import superseded_ids

    superseded = superseded_ids([hydrant.pk])
    candidates = [
        test for test in hydrant.tests.order_by("-tested_on", "-created_at", "-id")[:20]
        if test.pk not in superseded
    ]
    return {
        "hydrant": hydrant,
        "today": timezone.localdate(),
        "can_write": user_can_write(request.user),
        "target_residual_psi": C.TARGET_RESIDUAL_PSI,
        "max_outlets": C.MAX_OUTLETS_PER_TEST,
        "max_static_psi": C.MAX_STATIC_PSI,
        "max_pitot_psi": C.MAX_PITOT_PSI,
        "coefficient_presets": C.COEFFICIENT_PRESETS,
        "supersede_candidates": candidates[:8],
        "formula_version": C.CURRENT_FORMULA_VERSION,
        "warnings": list(C.WARNING_ORDER),
    }


def inspection_form_context(request, hydrant: Hydrant) -> dict:
    today = timezone.localdate()
    derived = derive_hydrants([hydrant], params_from_settings(today=today))[hydrant.pk]
    return {
        "hydrant": hydrant,
        "today": today,
        "derived": derived,
        "can_write": user_can_write(request.user),
        "deficiency_codes": C.DEFICIENCY_CODES,
        "open_deficiencies": hydrant.deficiencies.filter(status=C.DEFICIENCY_OPEN).order_by("id"),
        "recent_inspections": hydrant.inspections.all()[:10],
    }


def parcel_list_context(request) -> dict:
    query = (request.GET.get("q") or "").strip()
    queryset = Parcel.objects.all().order_by("parcel_id")
    if query:
        queryset = queryset.filter(Q(parcel_id__icontains=query) | Q(address__icontains=query))
    parcels = list(queryset[:500])
    return {
        "parcels": parcels,
        "total": Parcel.objects.count(),
        "protected": Parcel.objects.filter(is_protected_structure=True).count(),
        "overridden": Parcel.objects.filter(structure_lat__isnull=False).count(),
        "query": query,
        "truncated": queryset.count() > len(parcels),
        "centroid_note": C.CENTROID_PROXY_NOTE,
    }


def parcel_detail_context(request, parcel: Parcel) -> dict:
    from .models import CoverageResult, CoverageRun

    latest_run = (
        CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).order_by("-finished_at", "-id").first()
    )
    results = []
    if latest_run is not None:
        results = list(
            CoverageResult.objects.filter(run=latest_run, parcel=parcel)
            .select_related("hydrant")
            .order_by("-district_credited", "distance_ft", "id")
        )
    return {
        "parcel": parcel,
        "results": results,
        "latest_run": latest_run,
        "centroid_note": C.CENTROID_PROXY_NOTE,
    }


def parcel_form_context(request, parcel: Parcel) -> dict:
    return {
        "parcel": parcel,
        "editing": True,
        "can_write": user_can_write(request.user),
        "district": DistrictSettings.load(),
    }


def exports_context(request) -> dict:
    return {
        "verify_in_field": C.VERIFY_IN_FIELD,
        "disclaimer": C.DISCLAIMER,
        "is_admin": user_is_admin(request.user),
    }


def guide_context(request) -> dict:
    district = DistrictSettings.load()
    return {
        "district": district,
        "target_residual_psi": C.TARGET_RESIDUAL_PSI,
        "credit_radius_ft": district.credit_radius_ft,
        "gating_flags": list(C.GATING_FLAG_ORDER),
        "attention_flags": list(C.ATTENTION_FLAGS),
        "deficiency_codes": list(C.DEFICIENCY_CODES),
        "exclusion_precedence": list(C.EXCLUSION_PRECEDENCE),
        "flow_band_label": C.FLOW_BAND_PROXY_LABEL,
        "gap_disclaimer": C.GAP_SUMMARY_DISCLAIMER,
        "centroid_note": C.CENTROID_PROXY_NOTE,
        "subtarget_note": C.SUBTARGET_RISK_NOTE,
        "disclaimer": C.DISCLAIMER,
    }


# (key, label, unit, low, high, note) — read by the read-only settings page.
SETTINGS_WINDOWS = (
    ("recency_days", "Flow-test recency", "days", 30, 1095,
     "A flow test older than this is overdue; a hydrant tested inside it is current."),
    ("due_soon_days", "Flow-test due-soon", "days", 1, 364,
     "Inside this margin a test is due soon — attention only, never gating."),
    ("inspection_recency_days", "Inspection recency", "days", 30, 1095,
     "Inspections age on their own window, independent of the flow-test window."),
    ("inspection_due_soon_days", "Inspection due-soon", "days", 1, 364,
     "The inspection counterpart of the due-soon margin."),
)

SETTINGS_POLICY = (
    ("credit_radius_ft", "Credit radius", "ft", 250, 2640,
     "How far from the parcel's measuring point a hydrant can still be credited."),
    ("max_media_mb", "Media budget", "MB", 1, 100000,
     "The stored-photo budget per pack when attachments are included."),
    ("max_attachment_total_mb", "Attachment total", "MB", 1, 100000,
     "The cap on the summed attachment bytes a pack may carry."),
)


def settings_context(request) -> dict:
    return {
        "district": DistrictSettings.load(),
        "windows": SETTINGS_WINDOWS,
        "policy": SETTINGS_POLICY,
        "is_admin": user_is_admin(request.user),
    }


def coverage_context(request) -> dict:
    from .models import CoverageRun
    from .staleness import coverage_staleness

    latest = CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).order_by("-finished_at", "-id").first()
    recent = CoverageRun.objects.order_by("-created_at", "-id")[:10]
    in_flight = CoverageRun.objects.filter(status=C.RUN_STATUS_RUNNING).first()
    return {
        "latest_run": latest,
        "staleness": coverage_staleness(latest) if latest else None,
        "recent_runs": recent,
        "in_flight": in_flight,
        "district": DistrictSettings.load(),
    }


def pack_context(request) -> dict:
    from .models import CoverageRun, EvidencePack
    from .render_stack import render_stack_version

    packs = EvidencePack.objects.order_by("-created_at", "-id")[:20]
    complete_runs = CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).order_by("-finished_at", "-id")[:10]
    in_flight = EvidencePack.objects.filter(status=C.PACK_STATUS_RENDERING).first()

    # §9(c): the pack page is the one surface that shows the render-stack check.
    # A pack is frozen against the stack that rendered it; a live stack that no
    # longer matches means a re-render would not be byte-identical.
    live_stack = render_stack_version()
    newest = packs[0] if packs else None
    stack_stale = bool(
        newest is not None
        and newest.render_stack_version
        and newest.render_stack_version != live_stack
    )

    return {
        "packs": packs,
        "complete_runs": complete_runs,
        "in_flight": in_flight,
        "district": DistrictSettings.load(),
        "live_render_stack": live_stack,
        "stack_stale": stack_stale,
        "newest_pack": newest,
    }
