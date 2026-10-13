"""CSV serialization: RFC 4180, UTF-8, CRLF, canonical numeric formats.

CSV-injection neutralization applies to **text-typed columns only** in every
CSV export (dispatch and all pack CSVs): a first character in ``=``, ``+``,
``-``, ``@``, tab or CR is prefixed with a single quote.  Numeric and enum
columns are serialized from typed values and are never prefixed.
"""
from __future__ import annotations

import csv
import io

from . import constants as C

NEUTRALIZE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_cell(value) -> str:
    """Text-typed cell neutralization (the single implementation)."""
    text = "" if value is None else str(value)
    if text[:1] in NEUTRALIZE_PREFIXES:
        return "'" + text
    return text


def format_coord(value) -> str:
    return "" if value is None else f"{float(value):.7f}"


def format_flow(value) -> str:
    return "" if value is None else f"{float(value):.1f}"


def format_date(value) -> str:
    return "" if value is None else str(value)


def write_csv(columns, rows) -> str:
    """RFC 4180 CSV text with CRLF line endings (no trailing blank row)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return buffer.getvalue()


def dispatch_rows(hydrants, derivations, *, data_as_of=None) -> list[dict]:
    """The dispatch CSV rows (fixed column order, one row per hydrant)."""
    data_as_of = data_as_of or ""
    rows = []
    for hydrant in hydrants:
        derived = derivations[hydrant.pk]
        if hydrant.status == C.STATUS_RETIRED:
            continue  # RETIRED rows are excluded from dispatch
        open_codes = ", ".join(derived.open_deficiency_codes)
        escalation = C.ESCALATION_EXPIRED_OOS if derived.oos_expired else ""
        rows.append({
            "asset_tag": neutralize_cell(hydrant.asset_tag),
            "latitude": format_coord(hydrant.lat),
            "longitude": format_coord(hydrant.lon),
            "location_description": neutralize_cell(hydrant.location_description),
            "status": hydrant.status,
            "oos_reason": neutralize_cell(derived.oos_reason or ""),
            "oos_expected_return_on": format_date(derived.expected_return_on),
            "oos_expired": "true" if derived.oos_expired else "false",
            "oos_acknowledged_at": format_date(derived.oos_acknowledged_at.date() if derived.oos_acknowledged_at else None),
            "open_deficiency": neutralize_cell(open_codes),
            "latest_tested_on": format_date(derived.latest_tested_on),
            "latest_rated_gpm_20": format_flow(derived.rated_gpm_20),
            "nfpa_class": derived.capacity_class or "",
            "recency_flag": derived.test_recency_flag,
            "inspection_recency_flag": derived.inspection_recency_flag,
            "test_warnings": neutralize_cell(", ".join(derived.warnings)),
            "readiness": derived.readiness,
            "trend": derived.trend,
            "verify_in_field": C.VERIFY_IN_FIELD,
            "escalation": escalation,
            "disclaimer": C.DISCLAIMER,
            "data_as_of_utc": data_as_of,
        })
    return rows


def dispatch_csv(hydrants, derivations, *, data_as_of=None) -> str:
    return write_csv(C.DISPATCH_COLUMNS, dispatch_rows(hydrants, derivations, data_as_of=data_as_of))
