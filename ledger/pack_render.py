"""The evidence-pack renderer (specification §9).

``render_pack`` is the job body the executor drives.  It computes every byte
and every row field but **never writes the pack row** — the executor installs
status + fields in one conditional UPDATE, so a reaped render never survives.

One source of truth per datum: the snapshot JSONB carries identity, frozen
parameters and per-hydrant derived rows; ``CoverageResult`` supplies all
coverage math.  Determinism mechanisms (all named): ``rl_config.invariant=1``,
fixed PDF CreationDate from ``generated_at``, fixed ``ZipInfo.date_time``,
fixed entry order, canonical numeric serialization, CRLF CSVs, every query
ordered by a total key.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from django.utils import timezone

from . import constants as C
from .coverage import _nearest_within, constants_snapshot
from .csvio import format_coord, format_date, format_flow, neutralize_cell, write_csv
from .geo import latitude_window_deg
from .media import media_bytes_total, media_budget_bytes, media_root, prune_media_to_budget
from .models import Attachment, CoverageResult, DistrictSettings
from .render_stack import render_stack_version
from .services import derive_hydrant, load_hydrant_views


class MediaBudgetExceeded(Exception):
    """The pre-render media projection exceeds the budget after pruning."""


# Fixed CSV column orders (artifacts are contracts).
WORKSHEET_COLUMNS = (
    "parcel_id", "asset_tag", "distance_ft", "district_credited",
    "district_credited_gpm", "exclusion_code", "expired",
)
PARCEL_SUMMARY_COLUMNS = (
    "parcel_id", "address", "point_mode", "in_radius_count", "credited_count",
    "district_credited_gpm", "distribution_band", "flow_band_proxy", "covered",
    "nearest_within_2x_radius_ft", "exclusion_codes",
)
HYDRANT_CSV_COLUMNS = (
    "asset_tag", "latitude", "longitude", "location_description", "manufacturer",
    "model", "year_installed", "main_size_inches", "outlet_diameter_in",
    "discharge_coefficient", "ownership", "source_org", "status",
    "oos_expected_return_on", "oos_expired", "open_deficiency_codes",
    "latest_tested_on", "computed_total_gpm", "rated_gpm_20", "capacity_class",
    "test_recency_flag", "inspection_recency_flag", "readiness", "trend",
    "exclusion_code", "district_credit_eligible", "test_warnings", "formula_version",
)
OOS_REGISTER_COLUMNS = (
    "asset_tag", "location_description", "oos_reason", "oos_expected_return_on",
    "indefinite", "oos_expired", "days_past_expiry", "oos_acknowledged_at",
)

# Estimation constants for the pre-render media check (bytes, pessimistic).
_EST_WORKSHEET_ROW = 96
_EST_HYDRANT_ROW = 240
_EST_PARCEL_ROW = 150
_EST_OOS_ROW = 160
_EST_PDF_BASE = 120_000
_EST_PDF_PAGE = 3_000

GAP_CONFIRM_NOTE = "confirm criteria with your ISO field representative."
EXCEPTIONS_CAP = 2_000
GAP_ROWS_PRINTED = 100


# ---------------------------------------------------------------------------
# Snapshot (identity + frozen parameters + per-hydrant derived rows)
# ---------------------------------------------------------------------------
def build_snapshot(pack, run, generated_at) -> dict:
    """Freeze the pack's snapshot: identity and derived rows, no pair math."""
    from .coverage_runner import params_for_run
    from .models import Hydrant, Parcel

    params = params_for_run(run)
    hydrants = list(Hydrant.objects.all().order_by("asset_tag", "id"))
    views = load_hydrant_views(hydrants)
    hydrant_rows = []
    for hydrant in hydrants:
        derived = derive_hydrant(views[hydrant.pk], params)
        previous = views[hydrant.pk].previous_test
        decline_pct = None
        if (
            derived.trend == C.TREND_DEGRADING
            and previous is not None and previous.rated_gpm_20
        ):
            decline_pct = round(
                (float(previous.rated_gpm_20) - float(derived.rated_gpm_20 or 0.0))
                / float(previous.rated_gpm_20) * 100.0,
                1,
            )
        hydrant_rows.append({
            "id": hydrant.pk,
            "asset_tag": hydrant.asset_tag,
            "lat": float(hydrant.lat),
            "lon": float(hydrant.lon),
            "location_description": hydrant.location_description,
            "manufacturer": hydrant.manufacturer,
            "model": hydrant.model,
            "year_installed": hydrant.year_installed,
            "main_size_inches": str(hydrant.main_size_inches or ""),
            "outlet_diameter_in": str(hydrant.outlet_diameter_in),
            "discharge_coefficient": str(hydrant.discharge_coefficient),
            "ownership": hydrant.ownership,
            "source_org": hydrant.source_org,
            "status": views[hydrant.pk].status,
            "readiness": derived.readiness,
            "gating_flags": list(derived.gating_flags),
            "attention_flags": list(derived.attention_flags),
            "trend": derived.trend,
            "trend_decline_pct": decline_pct,
            "capacity_class": derived.capacity_class,
            "test_recency_flag": derived.test_recency_flag,
            "inspection_recency_flag": derived.inspection_recency_flag,
            "exclusion_code": derived.exclusion_code,
            "district_credit_eligible": derived.district_credit_eligible,
            "rated_gpm_20": derived.rated_gpm_20,
            "computed_total_gpm": derived.computed_total_gpm,
            "latest_tested_on": format_date(derived.latest_tested_on),
            "latest_inspected_on": format_date(derived.latest_inspected_on),
            "days_test_overdue": derived.days_test_overdue,
            "days_inspection_overdue": derived.days_inspection_overdue,
            "warnings": list(derived.warnings),
            "formula_version": (
                views[hydrant.pk].current_test.formula_version
                if views[hydrant.pk].current_test else None
            ),
            "open_deficiency_codes": list(derived.open_deficiency_codes),
            "oos": {
                "expected_return_on": format_date(derived.expected_return_on),
                "indefinite": bool(views[hydrant.pk].indefinite),
                "expired": derived.oos_expired,
                "days_past_expiry": derived.days_past_expiry,
                "reason": derived.oos_reason or "",
                "acknowledged_at": (
                    derived.oos_acknowledged_at.isoformat() if derived.oos_acknowledged_at else None
                ),
            },
            "is_sample_data": hydrant.is_sample_data,
        })

    # Parcel identity for exactly the parcels the run's result rows name (the
    # run's scope is the math of record; identity is read at pack time).
    parcel_ids = (
        CoverageResult.objects.filter(run=run)
        .values_list("parcel_id", flat=True)
        .distinct()
    )
    parcels = (
        Parcel.objects.filter(pk__in=list(parcel_ids))
        .order_by("parcel_id", "id")
    )
    parcel_rows = []
    for parcel in parcels:
        point_mode = "structure" if parcel.structure_lat is not None else "centroid"
        parcel_rows.append({
            "id": parcel.pk,
            "parcel_id": parcel.parcel_id,
            "address": parcel.address,
            "point_lat": float(parcel.point[0]),
            "point_lon": float(parcel.point[1]),
            "point_mode": point_mode,
        })

    district = DistrictSettings.load()
    return {
        "schema": "hydrantproof-pack-snapshot-v1",
        "generated_at": generated_at.isoformat(),
        "district": {
            "name": district.name,
            "iso_community_id": district.iso_community_id,
            "state": district.state,
        },
        "run": {
            "id": run.pk,
            "snapshot_ts": run.snapshot_ts.isoformat() if run.snapshot_ts else None,
            "parcel_filter": run.parcel_filter,
            "params_sha256": run.params_sha256,
            "core_params_sha256": run.core_params_sha256,
            "recency_days": run.recency_days,
            "inspection_recency_days": run.inspection_recency_days,
            "credit_radius_ft": run.credit_radius_ft,
            "exclude_residual_below_target": run.exclude_residual_below_target,
        },
        "settings_echo": run.settings_echo,
        "constants": constants_snapshot(),
        "hydrants": hydrant_rows,
        "parcels": parcel_rows,
    }


# ---------------------------------------------------------------------------
# Pre-render media check (the named failure path — before any byte is written)
# ---------------------------------------------------------------------------
def preflight_media_budget(*, run, include_attachments: bool) -> list:
    """Project the render's bytes; prune oldest packs; raise when still over."""
    pair_rows = CoverageResult.objects.filter(run=run).count()
    district = DistrictSettings.load()
    attachments = []
    if include_attachments:
        attachments = list(Attachment.objects.order_by("id"))
    attachment_bytes = sum(a.byte_size for a in attachments)
    estimated_output = (
        pair_rows * _EST_WORKSHEET_ROW
        + run.hydrant_count * _EST_HYDRANT_ROW
        + run.protected_parcel_count * _EST_PARCEL_ROW
        + _EST_PDF_BASE
        + _EST_PDF_PAGE * (run.protected_parcel_count // 40 + run.hydrant_count // 40 + 12)
    )
    budget = media_budget_bytes(district.max_media_mb)
    projected = media_bytes_total() + attachment_bytes + estimated_output
    if projected > budget:
        # In-job prune of oldest pack files (row + file together), then re-check.
        prune_media_to_budget(budget - attachment_bytes - estimated_output)
        projected = media_bytes_total() + attachment_bytes + estimated_output
    if projected > budget:
        raise MediaBudgetExceeded(
            f"projected media {projected} bytes exceeds the {budget}-byte budget"
        )
    return attachments


# ---------------------------------------------------------------------------
# CSV artifacts
# ---------------------------------------------------------------------------
def _hydrants_csv(snapshot) -> str:
    rows = []
    for h in snapshot["hydrants"]:
        rows.append({
            "asset_tag": neutralize_cell(h["asset_tag"]),
            "latitude": format_coord(h["lat"]),
            "longitude": format_coord(h["lon"]),
            "location_description": neutralize_cell(h["location_description"]),
            "manufacturer": neutralize_cell(h["manufacturer"]),
            "model": neutralize_cell(h["model"]),
            "year_installed": h["year_installed"] if h["year_installed"] is not None else "",
            "main_size_inches": h["main_size_inches"],
            "outlet_diameter_in": h["outlet_diameter_in"],
            "discharge_coefficient": h["discharge_coefficient"],
            "ownership": neutralize_cell(h["ownership"]),
            "source_org": neutralize_cell(h["source_org"]),
            "status": h["status"],
            "oos_expected_return_on": h["oos"]["expected_return_on"],
            "oos_expired": "true" if h["oos"]["expired"] else "false",
            "open_deficiency_codes": neutralize_cell(", ".join(h["open_deficiency_codes"])),
            "latest_tested_on": h["latest_tested_on"],
            "computed_total_gpm": format_flow(h["computed_total_gpm"]),
            "rated_gpm_20": format_flow(h["rated_gpm_20"]),
            "capacity_class": h["capacity_class"] or "",
            "test_recency_flag": h["test_recency_flag"],
            "inspection_recency_flag": h["inspection_recency_flag"],
            "readiness": h["readiness"],
            "trend": h["trend"],
            "exclusion_code": h["exclusion_code"] or "",
            "district_credit_eligible": "true" if h["district_credit_eligible"] else "false",
            "test_warnings": neutralize_cell(", ".join(h["warnings"])),
            "formula_version": h["formula_version"] or "",
        })
    return write_csv(HYDRANT_CSV_COLUMNS, rows)


def _worksheet_rows(run, snapshot):
    """Stream the worksheet rows from CoverageResult (the math of record)."""
    hydrant_tags = {h["id"]: h["asset_tag"] for h in snapshot["hydrants"]}
    parcel_tags = {p["id"]: p["parcel_id"] for p in snapshot["parcels"]}
    results = (
        CoverageResult.objects.filter(run=run)
        .order_by("parcel_id", "id")
        .iterator(chunk_size=5000)
    )
    for result in results:
        yield {
            "parcel_id": neutralize_cell(parcel_tags.get(result.parcel_id, str(result.parcel_id))),
            "asset_tag": neutralize_cell(hydrant_tags.get(result.hydrant_id, "")) if result.hydrant_id else "",
            "distance_ft": format_flow(result.distance_ft),
            "district_credited": "true" if result.district_credited else "false",
            "district_credited_gpm": format_flow(result.district_credited_gpm),
            "exclusion_code": result.exclusion_code or "",
            "expired": "true" if result.expired else "false",
        }


class _SnapshotPoint:
    """Attribute adapter over a snapshot hydrant row for the geo helpers."""

    __slots__ = ("pk", "lat", "lon")

    def __init__(self, row: dict):
        self.pk = row["id"]
        self.lat = row["lat"]
        self.lon = row["lon"]


def _parcel_aggregates(run, snapshot):
    """Per-parcel sums from the stored result rows (one source for the math),
    plus the nearest-within-2x gap metric computed from snapshot positions."""
    from .coverage import distribution_band, flow_band_proxy

    points = {p["id"]: p for p in snapshot["parcels"]}
    ordered_h = [
        _SnapshotPoint(h)
        for h in sorted(snapshot["hydrants"], key=lambda h: (h["lat"], h["lon"], h["id"]))
    ]
    lats = [h.lat for h in ordered_h]
    radius = float(snapshot["run"]["credit_radius_ft"])
    lat_half_2x = latitude_window_deg(radius * 2.0)

    aggregates: dict[int, dict] = {}
    results = (
        CoverageResult.objects.filter(run=run)
        .order_by("parcel_id", "id")
        .iterator(chunk_size=5000)
    )
    for result in results:
        agg = aggregates.setdefault(result.parcel_id, {
            "in_radius": 0, "credited": 0, "credited_sum": 0.0, "exclusions": {},
        })
        if result.hydrant_id is not None:
            agg["in_radius"] += 1
        if result.district_credited:
            agg["credited"] += 1
            agg["credited_sum"] += float(result.district_credited_gpm or 0.0)
        elif result.exclusion_code and result.exclusion_code != C.EXCLUSION_NO_HYDRANT_IN_RADIUS:
            agg["exclusions"][result.exclusion_code] = agg["exclusions"].get(result.exclusion_code, 0) + 1

    summaries = []
    for parcel_pk, agg in aggregates.items():
        point = points.get(parcel_pk)
        nearest = None
        if point is not None and ordered_h:
            nearest = _nearest_within(
                ordered_h, lats, point["point_lat"], point["point_lon"], radius * 2.0, lat_half_2x
            )
        summaries.append({
            "parcel_pk": parcel_pk,
            "parcel": point,
            "in_radius_count": agg["in_radius"],
            "credited_count": agg["credited"],
            "credited_gpm_sum": agg["credited_sum"],
            "distribution_band": distribution_band(agg["credited"]),
            "flow_band_proxy": flow_band_proxy(agg["credited_sum"]),
            "covered": agg["credited"] >= 1,
            "nearest_within_2x_ft": nearest,
            "exclusion_code_counts": agg["exclusions"],
        })
    summaries.sort(key=lambda s: (s["parcel"]["parcel_id"] if s["parcel"] else "", s["parcel_pk"]))
    return summaries


def _parcel_summary_csv(summaries) -> str:
    rows = []
    for summary in summaries:
        parcel = summary["parcel"] or {}
        exclusions = ";".join(
            f"{code}:{count}" for code, count in sorted(summary["exclusion_code_counts"].items())
        )
        rows.append({
            "parcel_id": neutralize_cell(parcel.get("parcel_id", summary["parcel_pk"])),
            "address": neutralize_cell(parcel.get("address", "")),
            "point_mode": parcel.get("point_mode", ""),
            "in_radius_count": summary["in_radius_count"],
            "credited_count": summary["credited_count"],
            "district_credited_gpm": format_flow(summary["credited_gpm_sum"]),
            "distribution_band": summary["distribution_band"],
            "flow_band_proxy": summary["flow_band_proxy"],
            "covered": "true" if summary["covered"] else "false",
            "nearest_within_2x_radius_ft": format_flow(summary["nearest_within_2x_ft"]),
            "exclusion_codes": neutralize_cell(exclusions),
        })
    return write_csv(PARCEL_SUMMARY_COLUMNS, rows)


def _oos_register_rows(snapshot) -> list[dict]:
    rows = []
    for h in snapshot["hydrants"]:
        if h["status"] != C.STATUS_OUT_OF_SERVICE:
            continue
        rows.append({
            "asset_tag": neutralize_cell(h["asset_tag"]),
            "location_description": neutralize_cell(h["location_description"]),
            "oos_reason": neutralize_cell(h["oos"]["reason"]),
            "oos_expected_return_on": h["oos"]["expected_return_on"],
            "indefinite": "true" if h["oos"]["indefinite"] else "false",
            "oos_expired": "true" if h["oos"]["expired"] else "false",
            "days_past_expiry": h["oos"]["days_past_expiry"] or "",
            "oos_acknowledged_at": h["oos"]["acknowledged_at"] or "",
        })
    return rows


def _oos_register_csv(snapshot) -> str:
    return write_csv(OOS_REGISTER_COLUMNS, _oos_register_rows(snapshot))


# ---------------------------------------------------------------------------
# The PDF
# ---------------------------------------------------------------------------
def _wrap(text: str, width_chars: int = 106) -> list[str]:
    """Deterministic word wrap (fixed-pitch approximation is not acceptable;
    use a conservative character budget for 8pt Helvetica on letter)."""
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width_chars:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


# Footer geometry: 6pt Helvetica on letter.  The mandatory strings are drawn
# in full, wrapped to the measured width — never sliced mid-sentence (§3: the
# disclaimer and the provisional banner are mandatory, not decorative).
FOOTER_FONT_SIZE = 6
FOOTER_LINE_HEIGHT = 7
FOOTER_MAX_WIDTH = 500.0


def _footer_lines(text: str) -> list[str]:
    """Word-wrap a footer string to the page's text width at 6pt Helvetica.

    Uses the font's own metrics (``stringWidth``), so no line can overflow the
    page; the result is deterministic for a fixed font and size.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, "Helvetica", FOOTER_FONT_SIZE) > FOOTER_MAX_WIDTH:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


class _PdfWriter:
    """Minimal letter-size PDF writer: headings, wrapped body, simple tables."""

    def __init__(self, buffer, generated_at):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as pdf_canvas

        self.page_size = letter
        self.canvas = pdf_canvas.Canvas(buffer, pagesize=letter, invariant=1)
        self.canvas.setTitle("HydrantProof evidence pack")
        self.canvas.setCreator("HydrantProof")
        stamp = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.canvas.setDateFormatter(lambda *args: stamp)  # fixed CreationDate
        self.width, self.height = letter
        self.y = self.height - 48
        self.page_count = 0
        self._new_page()

    def _new_page(self):
        if self.page_count:
            self._footer()
            self.canvas.showPage()
        self.page_count += 1
        self.y = self.height - 48

    def _footer(self):
        self.canvas.saveState()
        self.canvas.setFont("Helvetica", FOOTER_FONT_SIZE)
        # Banner above the disclaimer, both drawn in reading order (top-down),
        # so the PDF text stream carries the canonical sentences verbatim.
        banner_lines = _footer_lines(C.PROVISIONAL_BANNER)
        disclaimer_lines = _footer_lines(C.DISCLAIMER)
        y = 16 + (len(banner_lines) + len(disclaimer_lines) - 1) * FOOTER_LINE_HEIGHT
        for line in banner_lines + disclaimer_lines:
            self.canvas.drawString(36, y, line)
            y -= FOOTER_LINE_HEIGHT
        self.canvas.drawRightString(self.width - 36, 16, f"Page {self.page_count}")
        self.canvas.restoreState()

    def _need(self, points: float):
        if self.y - points < 56:
            self._new_page()

    def heading(self, text: str):
        self._need(30)
        self.canvas.setFont("Helvetica-Bold", 13)
        self.canvas.drawString(36, self.y, text)
        self.y -= 20

    def subheading(self, text: str):
        self._need(24)
        self.canvas.setFont("Helvetica-Bold", 10)
        self.canvas.drawString(36, self.y, text)
        self.y -= 15

    def para(self, text: str, size: int = 8):
        self.canvas.setFont("Helvetica", size)
        for line in _wrap(text):
            self._need(size + 5)
            self.canvas.drawString(36, self.y, line)
            self.y -= size + 4
        self.y -= 3

    def table(self, headers, rows, size: int = 7, col_width: int = 100):
        self._need(size + 12)
        self.canvas.setFont("Helvetica-Bold", size)
        for i, header in enumerate(headers):
            self.canvas.drawString(36 + i * col_width, self.y, str(header)[: col_width // 5])
        self.y -= size + 6
        self.canvas.setFont("Helvetica", size)
        for row in rows:
            self._need(size + 8)
            for i, cell in enumerate(row):
                self.canvas.drawString(36 + i * col_width, self.y, str(cell)[: col_width // 5])
            self.y -= size + 4
        self.y -= 4

    def save(self):
        self._footer()
        self.canvas.save()


def _render_pdf(snapshot, summaries, attachments, generated_at) -> tuple[bytes, int]:
    from reportlab import rl_config

    run = snapshot["run"]
    district = snapshot["district"]
    buffer = io.BytesIO()
    previous_invariant = rl_config.invariant
    rl_config.invariant = 1
    try:
        pdf = _PdfWriter(buffer, generated_at)

        # 1 — Cover
        pdf.heading(f"{district['name']} — water-supply evidence pack")
        pdf.para(f"Generated: {snapshot['generated_at']} (UTC) · coverage run #{run['id']} "
                 f"(snapshot {run['snapshot_ts']})")
        pdf.para(f"ISO community id: {district['iso_community_id'] or '—'} · State: {district['state'] or '—'}")
        pdf.subheading("Frozen parameters")
        pdf.para(json.dumps({
            "recency_days": run["recency_days"],
            "inspection_recency_days": run["inspection_recency_days"],
            "credit_radius_ft": run["credit_radius_ft"],
            "exclude_residual_below_target": run["exclude_residual_below_target"],
            "parcel_filter": run["parcel_filter"],
        }, sort_keys=True))
        if snapshot.get("sub_target_residual_included_count"):
            pdf.para("Sub-target-residual tests included in credited sums: "
                     f"{snapshot['sub_target_residual_included_count']}.")
        pdf.para(C.PROVISIONAL_BANNER)
        pdf.para(C.SINGLE_TEST_SNAPSHOT_NOTE)
        pdf.para(C.SUBTARGET_RISK_NOTE)
        pdf.para(C.DISCLAIMER)
        pdf.para(C.ARTIFACT_LABEL)

        # 2 — Method statement
        pdf.heading("Method statement")
        constants = snapshot["constants"]
        pdf.para(
            "Grounded constants (the pinned arithmetic): discharge constant "
            f"{constants['DISCHARGE_CONSTANT']} (q = 29.83·c·d²·√p), target residual "
            f"{constants['TARGET_RESIDUAL_PSI']} psi, drop exponent {constants['DROP_EXPONENT']}."
        )
        pdf.para(
            "Provisional, verification-gated constants (district policy; verify against a "
            f"licensed current NFPA 291 edition): coefficient presets {constants['COEFFICIENT_PRESETS']}, "
            f"maximum extrapolation factor {constants['MAX_EXTRAPOLATION_FACTOR']}, "
            f"distribution bands {[b[0] for b in constants['DISTRIBUTION_BANDS']]}, "
            f"flow-band proxy {[b[0] for b in constants['FLOW_BAND_PROXY']]}, "
            f"trend decline threshold {constants['TREND_DECLINE_PCT']}%."
        )
        pdf.para(
            f"Formula version: {C.CURRENT_FORMULA_VERSION}. Tests stamped with any other "
            "formula_version are excluded from district credit (FORMULA_VERSION_MISMATCH); "
            "a version change requires a re-attribution migration."
        )
        pdf.para(
            "Exclusion precedence (first match wins): " + " > ".join(C.EXCLUSION_PRECEDENCE) + "."
        )
        pdf.para(C.ATTRIBUTION_RULE_NOTE)
        pdf.para(C.CENTROID_PROXY_NOTE)
        pdf.para(
            "Point modes: " + str(_point_mode_counts(snapshot)) + ". "
            "A rated flow is a single-test snapshot; main capacity, duration and demand are "
            "not modeled. When a meter total and pitot readings are both present, the meter "
            "total is the authoritative flow (meter-wins rule)."
        )

        # 3 — Water-supply summary by public category
        pdf.heading("Water-supply summary (public category structure — no point values)")
        band_counts = _band_counts(summaries)
        pdf.subheading("Hydrant distribution (headline)")
        pdf.para(
            "Protected parcels by credited-hydrant count band "
            f"{C.DISTRIBUTION_BANDS_NOTE}: "
            + ", ".join(f"{label}: {band_counts.get(label, 0)}" for label, _ in C.DISTRIBUTION_BANDS)
            + f". Covered (≥1 credited): {sum(1 for s in summaries if s['covered'])} of "
            f"{len(summaries)} protected parcels."
        )
        nearest_values = [s["nearest_within_2x_ft"] for s in summaries if s["nearest_within_2x_ft"] is not None]
        none_count = sum(1 for s in summaries if s["nearest_within_2x_ft"] is None)
        if nearest_values:
            pdf.para(
                "Nearest-hydrant distance (within 2× radius, any status): "
                f"min {min(nearest_values):.1f} ft, median {sorted(nearest_values)[len(nearest_values)//2]:.1f} ft, "
                f"max {max(nearest_values):.1f} ft."
            )
        pdf.para(f"Parcels with no hydrant within 2× the credit radius: {none_count}.")
        pdf.subheading(f"Flow-band proxy ({C.FLOW_BAND_PROXY_LABEL})")
        flow_counts = _flow_counts(summaries)
        pdf.para(
            ", ".join(f"{label}: {flow_counts.get(label, 0)}" for label, _ in C.FLOW_BAND_PROXY)
            + ". Exclusion-code counts for the sum's filter: "
            + json.dumps(_exclusion_counts(summaries), sort_keys=True)
        )
        pdf.subheading("Flow testing")
        pdf.para(_flow_testing_summary(snapshot))
        pdf.subheading("Inspection")
        pdf.para(_inspection_summary(snapshot))
        pdf.subheading("Readiness profile (counts, never points)")
        pdf.para(_readiness_summary(snapshot))

        # 4 — Ranked gap summary
        pdf.heading("Ranked gap summary (district-computed estimate)")
        pdf.para(C.GAP_SUMMARY_DISCLAIMER)
        gaps = _ranked_gaps(snapshot, summaries)
        for severity, category, subject, detail in gaps[:GAP_ROWS_PRINTED]:
            pdf.para(f"{severity}. [{category}] {subject} — {detail} ({GAP_CONFIRM_NOTE})", size=7)
        if len(gaps) > GAP_ROWS_PRINTED:
            pdf.para(f"…and {len(gaps) - GAP_ROWS_PRINTED} more findings; "
                     "the full detail is computed from parcel_summary.csv and hydrants.csv.")

        # 5 — OOS register
        oos_rows = _oos_register_rows(snapshot)
        pdf.heading("Out-of-service register")
        if oos_rows:
            pdf.table(
                ["Tag", "Expected return", "Expired", "Days past", "Reason"],
                [[r["asset_tag"], r["oos_expected_return_on"] or ("indefinite" if r["indefinite"] == "true" else ""),
                  r["oos_expired"], r["days_past_expiry"], r["oos_reason"][:40]] for r in oos_rows[:EXCEPTIONS_CAP]],
                col_width=110,
            )
            if len(oos_rows) > EXCEPTIONS_CAP:
                pdf.para(f"…and {len(oos_rows) - EXCEPTIONS_CAP} more in oos_register.csv")
        else:
            pdf.para("No hydrants out of service at the snapshot.")

        # 6 — Vector coverage overview
        pdf.heading("Coverage overview (vector, schematic — not a basemap)")
        _draw_overview(pdf, snapshot, summaries)

        # 7 — Exceptions table
        pdf.heading("Exceptions (uncovered and low-band parcels, excluded-pair reasons)")
        exceptions = _exceptions_rows(summaries)
        if exceptions:
            pdf.table(
                ["Parcel", "Band", "Credited gpm", "Nearest 2× ft", "Exclusions"],
                exceptions[:EXCEPTIONS_CAP],
                col_width=110,
            )
            if len(exceptions) > EXCEPTIONS_CAP:
                pdf.para(f"…and {len(exceptions) - EXCEPTIONS_CAP} more in parcel_summary.csv")
        else:
            pdf.para("No uncovered or low-band parcels in this run.")

        pdf.save()
    finally:
        rl_config.invariant = previous_invariant
    return buffer.getvalue(), pdf.page_count


def _point_mode_counts(snapshot) -> dict:
    counts: dict[str, int] = {}
    for parcel in snapshot["parcels"]:
        counts[parcel["point_mode"]] = counts.get(parcel["point_mode"], 0) + 1
    return counts


def _band_counts(summaries) -> dict:
    counts: dict[str, int] = {}
    for summary in summaries:
        counts[summary["distribution_band"]] = counts.get(summary["distribution_band"], 0) + 1
    return counts


def _flow_counts(summaries) -> dict:
    counts: dict[str, int] = {}
    for summary in summaries:
        counts[summary["flow_band_proxy"]] = counts.get(summary["flow_band_proxy"], 0) + 1
    return counts


def _exclusion_counts(summaries) -> dict:
    counts: dict[str, int] = {}
    for summary in summaries:
        for code, count in summary["exclusion_code_counts"].items():
            counts[code] = counts.get(code, 0) + count
    return counts


def _flow_testing_summary(snapshot) -> str:
    hydrants = snapshot["hydrants"]
    total = len(hydrants)
    tested = sum(1 for h in hydrants if h["latest_tested_on"])
    recency: dict[str, int] = {}
    degrading = 0
    versions: dict[str, int] = {}
    for h in hydrants:
        recency[h["test_recency_flag"]] = recency.get(h["test_recency_flag"], 0) + 1
        if h["trend"] in (C.TREND_DEGRADING, C.TREND_RATED_FLOW_LOST):
            degrading += 1
        if h["formula_version"]:
            versions[h["formula_version"]] = versions.get(h["formula_version"], 0) + 1
    pct = (tested / total * 100.0) if total else 0.0
    version_note = ""
    if len(versions) > 1:
        version_note = (" Multiple formula versions present: "
                        + json.dumps(versions, sort_keys=True)
                        + " — district credit requires the current version.")
    return (
        f"Tested within the ledger: {tested} of {total} hydrants ({pct:.1f}%). "
        f"Recency counts: {json.dumps(recency, sort_keys=True)}. "
        f"Degrading-trend count (including RATED_FLOW_LOST): {degrading}."
        f" Formula versions present: {json.dumps(versions, sort_keys=True)}.{version_note}"
    )


def _inspection_summary(snapshot) -> str:
    hydrants = snapshot["hydrants"]
    total = len(hydrants)
    current = sum(
        1 for h in hydrants if h["inspection_recency_flag"] in (C.RECENCY_CURRENT, C.RECENCY_DUE_SOON)
    )
    by_code: dict[str, int] = {}
    for h in hydrants:
        for code in h["open_deficiency_codes"]:
            by_code[code] = by_code.get(code, 0) + 1
    pct = (current / total * 100.0) if total else 0.0
    return (
        f"Inspection currency: {current} of {total} hydrants current or due-soon ({pct:.1f}%). "
        f"Open deficiencies by code: {json.dumps(by_code, sort_keys=True)}."
    )


def _readiness_summary(snapshot) -> str:
    readiness: dict[str, int] = {}
    gating: dict[str, int] = {}
    attention: dict[str, int] = {}
    for h in snapshot["hydrants"]:
        readiness[h["readiness"]] = readiness.get(h["readiness"], 0) + 1
        for flag in h["gating_flags"]:
            gating[flag] = gating.get(flag, 0) + 1
        for flag in h["attention_flags"]:
            attention[flag] = attention.get(flag, 0) + 1
    return (
        f"Readiness: {json.dumps(readiness, sort_keys=True)}. "
        f"Gating flags: {json.dumps(gating, sort_keys=True)}. "
        f"Attention flags: {json.dumps(attention, sort_keys=True)}."
    )


def _ranked_gaps(snapshot, summaries) -> list[tuple[int, str, str, str]]:
    """The mechanically ranked gap summary (§9.4) — six severity classes."""
    gaps: list[tuple[int, str, str, str]] = []
    # (1) uncovered protected parcels: ascending nearest_within_2x (none-within-
    # 2x sorts last and is counted on the cover line), tie by parcel_id.
    uncovered = [s for s in summaries if not s["covered"]]
    uncovered.sort(key=lambda s: (
        s["nearest_within_2x_ft"] is None,
        s["nearest_within_2x_ft"] if s["nearest_within_2x_ft"] is not None else 0.0,
        (s["parcel"] or {}).get("parcel_id", ""),
    ))
    for summary in uncovered:
        parcel = summary["parcel"] or {}
        nearest = summary["nearest_within_2x_ft"]
        detail = (
            "no hydrant within 2× the credit radius"
            if nearest is None else f"nearest hydrant {nearest:.1f} ft (2× window)"
        )
        gaps.append((len(gaps) + 1, "hydrant distribution",
                     parcel.get("parcel_id", str(summary["parcel_pk"])), detail))
    # (2) expired OOS, days past descending
    expired = [h for h in snapshot["hydrants"]
               if h["status"] == C.STATUS_OUT_OF_SERVICE and h["oos"]["expired"]]
    expired.sort(key=lambda h: (-(h["oos"]["days_past_expiry"] or 0), h["asset_tag"]))
    for h in expired:
        gaps.append((len(gaps) + 1, "hydrant distribution", h["asset_tag"],
                     f"out of service {h['oos']['days_past_expiry']} day(s) past expected return"))
    # (3) never-tested, asset_tag
    never = sorted(
        (h for h in snapshot["hydrants"] if h["test_recency_flag"] == C.RECENCY_NEVER_TESTED),
        key=lambda h: h["asset_tag"],
    )
    for h in never:
        gaps.append((len(gaps) + 1, "flow testing", h["asset_tag"], "never tested"))
    # (4) stale tests, days overdue descending
    stale = [h for h in snapshot["hydrants"] if h["test_recency_flag"] == C.RECENCY_OVERDUE]
    stale.sort(key=lambda h: (-(h["days_test_overdue"] or 0), h["asset_tag"]))
    for h in stale:
        gaps.append((len(gaps) + 1, "flow testing", h["asset_tag"],
                     f"test overdue (last tested {h['latest_tested_on'] or 'never'})"))
    # (5) overdue inspections, days overdue descending
    overdue_insp = [h for h in snapshot["hydrants"] if h["inspection_recency_flag"] == C.RECENCY_OVERDUE]
    overdue_insp.sort(key=lambda h: (-(h["days_inspection_overdue"] or 0), h["asset_tag"]))
    for h in overdue_insp:
        gaps.append((len(gaps) + 1, "inspection", h["asset_tag"],
                     f"inspection overdue (last inspected {h['latest_inspected_on'] or 'never'})"))
    # (6) degrading flows, percent decline descending
    degrading = [h for h in snapshot["hydrants"] if h["trend"] == C.TREND_DEGRADING]
    degrading.sort(key=lambda h: (-(h["trend_decline_pct"] or 0.0), h["asset_tag"]))
    for h in degrading:
        gaps.append((len(gaps) + 1, "flow testing", h["asset_tag"],
                     f"rated flow degrading ({h['trend_decline_pct']}% decline)"))
    return gaps


def _draw_overview(pdf, snapshot, summaries) -> None:
    """ReportLab-drawn scatter: class-colored hydrants + parcel squares,
    longitude scaled by cos(mean latitude); deterministic subsampling."""
    hydrants = sorted(snapshot["hydrants"], key=lambda h: h["asset_tag"])
    step = max(1, -(-len(hydrants) // 5000))  # ceil(N/5000)
    plotted = hydrants[::step]
    pdf.para(
        f"Schematic scatter of {len(plotted)} of {len(hydrants)} hydrants "
        f"(sorted by asset_tag, every {step}-th kept) and "
        f"{len(snapshot['parcels'])} protected-parcel points; longitude scaled by "
        "cos(mean latitude). Schematic only — no basemap."
    )
    if not hydrants:
        return
    import math

    mean_lat = sum(h["lat"] for h in hydrants) / len(hydrants)
    cos_lat = math.cos(math.radians(mean_lat)) or 1.0
    xs = [h["lon"] * cos_lat for h in hydrants] + [p["point_lon"] * cos_lat for p in snapshot["parcels"]]
    ys = [h["lat"] for h in hydrants] + [p["point_lat"] for p in snapshot["parcels"]]
    if not xs or not ys:
        return
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = (max_x - min_x) or 1e-9
    span_y = (max_y - min_y) or 1e-9
    box = 240.0
    left, bottom = 36.0, 120.0

    def project(lon, lat):
        x = left + ((lon * cos_lat) - min_x) / span_x * box
        y = bottom + (lat - min_y) / span_y * box
        return x, y

    pdf._need(box + 90)
    canvas = pdf.canvas
    canvas.saveState()
    canvas.rect(left, bottom, box, box)
    from reportlab.lib.colors import HexColor

    for summary in summaries:
        parcel = summary["parcel"]
        if parcel is None:
            continue
        x, y = project(parcel["point_lon"], parcel["point_lat"])
        canvas.setStrokeColor(HexColor("#888888"))
        canvas.rect(x - 1.5, y - 1.5, 3, 3)
    for h in plotted:
        x, y = project(h["lon"], h["lat"])
        color = C.CAPACITY_CLASS_COLORS.get(h["capacity_class"], C.CAPACITY_CLASS_COLORS[None])
        canvas.setFillColor(HexColor(color))
        canvas.circle(x, y, 1.8, stroke=0, fill=1)
    canvas.restoreState()
    pdf.y -= box + 16


def _exceptions_rows(summaries) -> list[list[str]]:
    """Uncovered and band-1 parcels + excluded-pair reasons; deterministic:
    uncovered first by the gap key, then ascending credited gpm, then parcel_id."""
    def key(summary):
        return (
            0 if not summary["covered"] else 1,
            summary["nearest_within_2x_ft"] is None,
            summary["nearest_within_2x_ft"] if summary["nearest_within_2x_ft"] is not None else 0.0,
            summary["credited_gpm_sum"],
            (summary["parcel"] or {}).get("parcel_id", ""),
        )

    selected = [s for s in summaries if not s["covered"] or s["distribution_band"] == "1"]
    selected.sort(key=key)
    rows = []
    for summary in selected:
        parcel = summary["parcel"] or {}
        exclusions = ";".join(
            f"{code}:{count}" for code, count in sorted(summary["exclusion_code_counts"].items())
        )
        rows.append([
            parcel.get("parcel_id", str(summary["parcel_pk"])),
            summary["distribution_band"],
            format_flow(summary["credited_gpm_sum"]),
            format_flow(summary["nearest_within_2x_ft"]) or "none within 2×",
            exclusions[:48],
        ])
    return rows


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def _manifest(entries: dict[str, bytes], *, run, snapshot, render_stack: str) -> str:
    """SHA-256 of every other entry (the manifest never hashes itself)."""
    lines = [
        "HydrantProof evidence pack",
        f"run_id: {run.pk}",
        f"generated_at: {snapshot['generated_at']}",
        f"params_sha256: {run.params_sha256}",
        f"core_params_sha256: {run.core_params_sha256}",
        "formula_versions_present: " + json.dumps(list(run.formula_versions_present or [])),
        f"render_stack_version: {render_stack}",
        "frozen_parameters: " + json.dumps({
            "recency_days": run.recency_days,
            "inspection_recency_days": run.inspection_recency_days,
            "credit_radius_ft": run.credit_radius_ft,
            "exclude_residual_below_target": run.exclude_residual_below_target,
            "parcel_filter": run.parcel_filter,
        }, sort_keys=True),
        "",
        "SHA-256 manifest (every entry except this manifest itself):",
    ]
    for name in sorted(entries):
        lines.append(f"{hashlib.sha256(entries[name]).hexdigest()}  {name}")
    lines += ["", C.ARTIFACT_LABEL, C.DISCLAIMER, C.PROVISIONAL_BANNER, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The render
# ---------------------------------------------------------------------------
def render_pack(pack, *, heartbeat=None) -> dict:
    """Compute the pack's files and row fields; write no row state.

    Returns the field dict for the executor's conditional UPDATE.  Raises
    :class:`MediaBudgetExceeded` before any byte is written; a reap mid-render
    raises ``JobReapedError`` and the executor sweeps the pack directory.
    """
    from .coverage import JobReapedError

    run = pack.run
    generated_at = timezone.now()
    attachments = preflight_media_budget(run=run, include_attachments=pack.include_attachments)

    snapshot = build_snapshot(pack, run, generated_at)
    summaries = _parcel_aggregates(run, snapshot)
    snapshot["sub_target_residual_included_count"] = _sub_target_count(run)
    if heartbeat and heartbeat(0) is False:
        raise JobReapedError()

    pdf_bytes, page_count = _render_pdf(snapshot, summaries, attachments, generated_at)
    csv_parts = {
        "hydrants.csv": _hydrants_csv(snapshot).encode("utf-8"),
        "coverage_worksheet.csv": write_csv(WORKSHEET_COLUMNS, _worksheet_rows(run, snapshot)).encode("utf-8"),
        "parcel_summary.csv": _parcel_summary_csv(summaries).encode("utf-8"),
        "oos_register.csv": _oos_register_csv(snapshot).encode("utf-8"),
    }
    attachment_parts: dict[str, bytes] = {}
    for attachment in attachments:
        with attachment.file.open("rb") as handle:
            attachment_parts[f"attachments/{attachment.pk}.jpg"] = handle.read()

    stack = render_stack_version()
    manifest_input = {"pack.pdf": pdf_bytes, **csv_parts, **attachment_parts}
    manifest_bytes = _manifest(manifest_input, run=run, snapshot=snapshot, render_stack=stack).encode("utf-8")

    # Zip: fixed entry order, fixed date_time, fixed compression — byte-stable.
    zip_buffer = io.BytesIO()
    date_time = generated_at.utctimetuple()[:6]
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in ["pack.pdf", *csv_parts.keys(), "manifest.txt", *sorted(attachment_parts)]:
            payload = {"pack.pdf": pdf_bytes, **csv_parts, "manifest.txt": manifest_bytes,
                       **attachment_parts}[name]
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, payload)
    zip_bytes = zip_buffer.getvalue()

    pack_dir = media_root() / "packs" / str(pack.pk)
    pack_dir.mkdir(parents=True, exist_ok=True)
    zip_path = pack_dir / C.PACK_ZIP_NAME
    pdf_path = pack_dir / C.PACK_PDF_NAME
    zip_path.write_bytes(zip_bytes)
    pdf_path.write_bytes(pdf_bytes)

    row_counts = {
        "hydrants": len(snapshot["hydrants"]),
        "worksheet_rows": int(run.pair_count),
        "parcel_summary": len(summaries),
        "oos_register": len(_oos_register_rows(snapshot)),
        "attachments": len(attachment_parts),
    }
    fields = {
        "snapshot": snapshot,
        "zip_path": str(Path("packs") / str(pack.pk) / C.PACK_ZIP_NAME),
        "pdf_path": str(Path("packs") / str(pack.pk) / C.PACK_PDF_NAME),
        "byte_size": len(zip_bytes) + len(pdf_bytes),
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "csv_zip_sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "page_count": page_count,
        "row_counts": row_counts,
        "render_stack_version": stack,
        "formula_versions_present": list(run.formula_versions_present or []),
        "generated_at": generated_at,
    }
    return fields


def _sub_target_count(run) -> int:
    """Credited rows fed by sub-target-residual tests (the printed risk count)."""
    return CoverageResult.objects.filter(
        run=run, district_credited=True, hydrant__current_test_ref__warnings__contains=[
            C.WARNING_RESIDUAL_BELOW_TARGET
        ]
    ).count()
