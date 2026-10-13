"""The two-phase CSV import engine (specification §10).

``validate_import`` parses and validates the whole file (header discipline,
per-row field validation, in-file duplicate last-wins, conflict policy
computed against live state) and returns a parsed-row report without writing
anything.  ``apply_import`` re-checks conflicts inside the commit transaction
and applies passing rows; invalid or conflicting rows are skipped with
``{line, field, message}`` errors.

Upsert keys: hydrants ``asset_tag``, parcels ``parcel_id``.  Status is never
silently upserted — creates write the attributed initial status event
(EARS 1), and an ``initial_status`` cell on an existing hydrant is a row
error.  Blank optional cells leave existing values unchanged.
"""
from __future__ import annotations

import csv
import io
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from . import constants as C
from .geo import valid_latitude, valid_longitude
from .hydraulics import HydraulicsError, compute_flow_test
from .models import Hydrant, ImportBatch, Parcel
from .services import LedgerError, create_flow_test, create_status_event


class ImportError_(Exception):
    """A file-level rejection (413 caps, unknown column, unparseable CSV)."""

    def __init__(self, detail: str, status_code: int = 400, **extra):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.extra = extra


@dataclass
class ParsedRow:
    line: int
    record: dict
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    create: bool = True
    existing_pk: int | None = None

    @property
    def valid(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# CSV frame
# ---------------------------------------------------------------------------
def _parse_csv(data: bytes, kind: str, allowed_columns: set[str], required: set[str]):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_("file is not valid UTF-8") from None
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportError_("file is empty; a header row is required") from None
    columns = [c.strip() for c in header]
    if not any(columns):
        raise ImportError_("a header row is required")
    unknown = [c for c in columns if c and c not in allowed_columns]
    if unknown:
        raise ImportError_(f"unknown column: {unknown[0]}", column=unknown[0])
    missing = [c for c in required if c not in columns]
    if missing:
        raise ImportError_(f"missing required column: {missing[0]}", column=missing[0])

    rows = []
    for line_no, cells in enumerate(reader, start=2):
        if not cells or all(not c.strip() for c in cells):
            continue
        if len(cells) > len(columns):
            raise ImportError_(
                f"line {line_no}: more cells than header columns", line=line_no
            )
        record = {columns[i]: cells[i].strip() for i in range(len(cells)) if columns[i]}
        rows.append((line_no, record))
        if len(rows) > C.MAX_IMPORT_ROWS:
            raise ImportError_(
                f"imports are capped at {C.MAX_IMPORT_ROWS:,} rows",
                status_code=413, max_rows=C.MAX_IMPORT_ROWS,
            )
    return rows


def _canonical_org(value: str) -> str:
    return " ".join((value or "").split()).lower()


# ---------------------------------------------------------------------------
# Field parsers (row-level errors as (field, message) tuples)
# ---------------------------------------------------------------------------
def _parse_float(record, column, *, required=False, minimum=None, maximum=None):
    raw = record.get(column, "")
    if raw == "":
        if required:
            return None, (column, "required")
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, (column, f"must be a number, got {raw!r}")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        return None, (column, f"must be between {minimum} and {maximum}")
    return value, None


def _parse_date(record, column, *, required=False, allow_future=False):
    raw = record.get(column, "")
    if raw == "":
        if required:
            return None, (column, "required")
        return None, None
    try:
        value = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None, (column, f"must be YYYY-MM-DD, got {raw!r}")
    if not allow_future and value > timezone.localdate():
        return None, (column, "may not be in the future")
    return value, None


def _parse_bool(record, column):
    raw = record.get(column, "").lower()
    if raw in ("",):
        return None, None
    if raw in ("1", "true", "yes", "y"):
        return True, None
    if raw in ("0", "false", "no", "n"):
        return False, None
    return None, (column, f"must be true/false, got {raw!r}")


# ---------------------------------------------------------------------------
# Hydrants
# ---------------------------------------------------------------------------
HYDRANT_COLUMNS = {
    "asset_tag", "lat", "lon", "latitude", "longitude", "location_description",
    "manufacturer", "model", "year_installed", "main_size_inches",
    "outlet_diameter_in", "discharge_coefficient", "ownership", "source_org",
    "notes", "initial_status", "initial_status_reason", "expected_return_on",
    "indefinite",
}


def _validate_hydrant_row(record, line_no, existing_by_tag) -> ParsedRow:
    row = ParsedRow(line=line_no, record=record)
    tag = record.get("asset_tag", "")
    if not tag:
        row.errors.append({"line": line_no, "field": "asset_tag", "message": "required"})
        return row
    if len(tag) > 64:
        row.errors.append({"line": line_no, "field": "asset_tag", "message": "at most 64 characters"})

    lat, err = _parse_float(record, "lat" if "lat" in record else "latitude")
    if "lat" in record and "latitude" in record:
        row.errors.append({"line": line_no, "field": "lat", "message": "use lat or latitude, not both"})
    lon, err_lon = _parse_float(record, "lon" if "lon" in record else "longitude")
    if "lon" in record and "longitude" in record:
        row.errors.append({"line": line_no, "field": "lon", "message": "use lon or longitude, not both"})
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    if err_lon:
        row.errors.append({"line": line_no, "field": err_lon[0], "message": err_lon[1]})

    existing = existing_by_tag.get(tag)
    is_create = existing is None
    if is_create and (lat is None or lon is None):
        if lat is None and not any(e["field"] == "lat" for e in row.errors):
            row.errors.append({"line": line_no, "field": "lat", "message": "required on create"})
        if lon is None and not any(e["field"] == "lon" for e in row.errors):
            row.errors.append({"line": line_no, "field": "lon", "message": "required on create"})
    if lat is not None and not valid_latitude(lat):
        row.errors.append({"line": line_no, "field": "lat", "message": "latitude must be between -80 and 80"})
    if lon is not None and not valid_longitude(lon):
        row.errors.append({"line": line_no, "field": "lon", "message": "longitude must be between -180 and 180"})

    initial_status = record.get("initial_status", "").upper()
    if initial_status and initial_status not in C.HYDRANT_STATUSES:
        row.errors.append({"line": line_no, "field": "initial_status",
                           "message": f"must be one of {', '.join(C.HYDRANT_STATUSES)}"})
    if not is_create and initial_status:
        # Status never silently upserted: the event log owns status.
        row.errors.append({"line": line_no, "field": "initial_status",
                           "message": "status changes only through status events on existing hydrants"})
    expected_return_on, err = _parse_date(record, "expected_return_on")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    indefinite, err = _parse_bool(record, "indefinite")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    if is_create and initial_status == C.STATUS_OUT_OF_SERVICE:
        if expected_return_on is None and not indefinite:
            row.errors.append({"line": line_no, "field": "expected_return_on",
                               "message": "required (or indefinite=true) when initial_status is OUT_OF_SERVICE"})
        if expected_return_on is not None and indefinite:
            row.errors.append({"line": line_no, "field": "indefinite",
                               "message": "provide expected_return_on or indefinite=true, not both"})

    year, err = _parse_float(record, "year_installed", minimum=1800, maximum=2100)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    main_size, err = _parse_float(record, "main_size_inches", minimum=0.1, maximum=96.0)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    outlet_d, err = _parse_float(record, "outlet_diameter_in", minimum=0.5, maximum=8.0)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    coeff, err = _parse_float(record, "discharge_coefficient", minimum=0.5, maximum=1.0)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})

    row.create = is_create
    row.existing_pk = existing.pk if existing else None
    row.data = {
        "asset_tag": tag, "lat": lat, "lon": lon,
        "location_description": record.get("location_description", ""),
        "manufacturer": record.get("manufacturer", ""),
        "model": record.get("model", ""),
        "year_installed": int(year) if year is not None else None,
        "main_size_inches": main_size,
        "outlet_diameter_in": outlet_d,
        "discharge_coefficient": coeff,
        "ownership": record.get("ownership", ""),
        "source_org": record.get("source_org", ""),
        "notes": record.get("notes", ""),
        "initial_status": initial_status or C.STATUS_IN_SERVICE,
        "initial_status_reason": record.get("initial_status_reason", ""),
        "expected_return_on": expected_return_on,
        "indefinite": bool(indefinite),
    }
    return row


# ---------------------------------------------------------------------------
# Parcels
# ---------------------------------------------------------------------------
PARCEL_COLUMNS = {
    "parcel_id", "latitude", "longitude", "address", "structure_type",
    "is_protected_structure", "structure_lat", "structure_lon", "source_org",
}


def _validate_parcel_row(record, line_no, existing_by_pid) -> ParsedRow:
    row = ParsedRow(line=line_no, record=record)
    pid = record.get("parcel_id", "")
    if not pid:
        row.errors.append({"line": line_no, "field": "parcel_id", "message": "required"})
        return row
    if len(pid) > 64:
        row.errors.append({"line": line_no, "field": "parcel_id", "message": "at most 64 characters"})

    lat, err = _parse_float(record, "latitude")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    lon, err = _parse_float(record, "longitude")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    existing = existing_by_pid.get(pid)
    is_create = existing is None
    if is_create and lat is None and not any(e["field"] == "latitude" for e in row.errors):
        row.errors.append({"line": line_no, "field": "latitude", "message": "required on create"})
    if is_create and lon is None and not any(e["field"] == "longitude" for e in row.errors):
        row.errors.append({"line": line_no, "field": "longitude", "message": "required on create"})
    if lat is not None and not valid_latitude(lat):
        row.errors.append({"line": line_no, "field": "latitude", "message": "latitude must be between -80 and 80"})
    if lon is not None and not valid_longitude(lon):
        row.errors.append({"line": line_no, "field": "longitude", "message": "longitude must be between -180 and 180"})

    slat, err = _parse_float(record, "structure_lat")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    slon, err = _parse_float(record, "structure_lon")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    if (slat is None) != (slon is None):
        row.errors.append({"line": line_no, "field": "structure_lat",
                           "message": "structure_lat and structure_lon are set together"})
    if slat is not None and (not valid_latitude(slat) or not valid_longitude(slon)):
        row.errors.append({"line": line_no, "field": "structure_lat",
                           "message": "structure override out of range"})
    protected, err = _parse_bool(record, "is_protected_structure")
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})

    row.create = is_create
    row.existing_pk = existing.pk if existing else None
    row.data = {
        "parcel_id": pid, "lat": lat, "lon": lon,
        "address": record.get("address", ""),
        "structure_type": record.get("structure_type", ""),
        "is_protected_structure": protected,
        "structure_lat": slat, "structure_lon": slon,
        "source_org": record.get("source_org", ""),
    }
    return row


# ---------------------------------------------------------------------------
# Historical flow tests
# ---------------------------------------------------------------------------
FLOW_TEST_COLUMNS = {
    "asset_tag", "tested_on", "static_psi", "residual_psi", "pitot_readings",
    "measured_total_gpm", "crew_names", "notes", "client_uuid",
}


def _parse_pitot_readings(raw: str, line_no, known_tags: set[str]) -> tuple[list, list]:
    """``d:c:pitot[:label[:flowed_asset_tag]]`` triples, semicolon-separated."""
    outlets = []
    errors = []
    if not raw.strip():
        return outlets, errors
    triples = [piece.strip() for piece in raw.split(";") if piece.strip()]
    if len(triples) > C.MAX_OUTLETS_PER_TEST:
        errors.append({"line": line_no, "field": "pitot_readings",
                       "message": f"at most {C.MAX_OUTLETS_PER_TEST} outlet readings"})
        return outlets, errors
    for index, piece in enumerate(triples):
        parts = piece.split(":")
        if len(parts) < 3:
            errors.append({"line": line_no, "field": "pitot_readings",
                           "message": f"reading {index + 1} must be diameter:coefficient:pitot"})
            continue
        try:
            diameter, coefficient, pitot = (float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            errors.append({"line": line_no, "field": "pitot_readings",
                           "message": f"reading {index + 1} has a non-numeric value"})
            continue
        label = parts[3] if len(parts) >= 4 else ""
        flowed_tag = parts[4] if len(parts) >= 5 else ""
        if len(parts) > 5:
            errors.append({"line": line_no, "field": "pitot_readings",
                           "message": f"reading {index + 1} has too many ':'-separated parts"})
            continue
        if flowed_tag and flowed_tag not in known_tags:
            errors.append({"line": line_no, "field": "pitot_readings",
                           "message": f"unknown flowed_asset_tag {flowed_tag!r}"})
            continue
        outlets.append({
            "outlet_diameter_in": diameter, "outlet_coefficient": coefficient,
            "pitot_psi": pitot, "outlet_label": label, "flow_asset_tag": flowed_tag or None,
        })
    return outlets, errors


def _validate_flow_test_row(record, line_no, hydrant_by_tag, taken_uuids) -> ParsedRow:
    row = ParsedRow(line=line_no, record=record)
    tag = record.get("asset_tag", "")
    if not tag:
        row.errors.append({"line": line_no, "field": "asset_tag", "message": "required"})
        return row
    hydrant = hydrant_by_tag.get(tag)
    if hydrant is None:
        row.errors.append({"line": line_no, "field": "asset_tag",
                           "message": f"unknown hydrant {tag!r}"})
        return row

    tested_on, err = _parse_date(record, "tested_on", required=True)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    static_psi, err = _parse_float(record, "static_psi", required=True)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    residual_psi, err = _parse_float(record, "residual_psi", required=True)
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})
    meter, err = _parse_float(record, "measured_total_gpm", minimum=1.0, maximum=float(C.MAX_TOTAL_GPM))
    if err:
        row.errors.append({"line": line_no, "field": err[0], "message": err[1]})

    known_tags = set(hydrant_by_tag)
    outlets, outlet_errors = _parse_pitot_readings(
        record.get("pitot_readings", ""), line_no, known_tags
    )
    row.errors.extend(outlet_errors)

    client_uuid = None
    raw_uuid = record.get("client_uuid", "")
    if raw_uuid:
        try:
            client_uuid = str(uuid_module.UUID(raw_uuid))
        except ValueError:
            row.errors.append({"line": line_no, "field": "client_uuid",
                               "message": f"not a UUID: {raw_uuid!r}"})
        else:
            if client_uuid in taken_uuids:
                row.warnings.append({"line": line_no,
                                     "message": "duplicate_client_uuid — already recorded"})
                row.data["skip"] = True

    if row.errors:
        return row
    # The full §6 edge matrix runs at import time, exactly as at the API.
    from .hydraulics import OutletReadingInput

    try:
        compute_flow_test(
            static_psi=static_psi, residual_psi=residual_psi,
            outlets=[
                OutletReadingInput(
                    diameter_in=o["outlet_diameter_in"],
                    coefficient=o["outlet_coefficient"],
                    pitot_psi=o["pitot_psi"],
                )
                for o in outlets
            ],
            measured_total_gpm=meter,
        )
    except HydraulicsError as exc:
        row.errors.append({"line": line_no, "field": exc.field_name, "message": exc.message})
        return row

    row.existing_pk = hydrant.pk
    row.data = {
        "hydrant": hydrant, "tested_on": tested_on, "static_psi": static_psi,
        "residual_psi": residual_psi, "outlets": outlets, "measured_total_gpm": meter,
        "crew_names": record.get("crew_names", ""), "notes": record.get("notes", ""),
        "client_uuid": client_uuid, "skip": row.data.get("skip", False),
    }
    return row


def _dedupe_in_file(parsed: list[ParsedRow], key_field: str) -> list[ParsedRow]:
    """Duplicate upsert keys in one file: the LAST row wins; every earlier
    occurrence is demoted to a warning-only row (never applied, never counted
    as a rejection)."""
    winner: dict[str, int] = {}
    out: list[ParsedRow] = []
    for row in parsed:
        key = row.record.get(key_field, "")
        if key and key in winner:
            prior_index = winner[key]
            prior = out[prior_index]
            out[prior_index] = None  # type: ignore[assignment]
            row.warnings.append({
                "line": row.line,
                "message": f"duplicate {key_field} {key!r} in file; this later row wins "
                           f"(line {prior.line} ignored)",
            })
        if key:
            winner[key] = len(out)
        out.append(row)
    return [row for row in out if row is not None]


# ---------------------------------------------------------------------------
# The two phases
# ---------------------------------------------------------------------------
def validate_import(kind: str, data: bytes) -> dict:
    """Parse and validate the whole file; apply nothing."""
    if kind == ImportBatch.KIND_HYDRANTS:
        allowed, required = HYDRANT_COLUMNS, {"asset_tag"}
    elif kind == ImportBatch.KIND_PARCELS:
        allowed, required = PARCEL_COLUMNS, {"parcel_id"}
    elif kind == ImportBatch.KIND_FLOW_TESTS:
        allowed, required = FLOW_TEST_COLUMNS, {"asset_tag", "tested_on", "static_psi", "residual_psi"}
    else:  # pragma: no cover - the route table constrains kind
        raise ImportError_(f"unknown import kind {kind!r}")

    rows = _parse_csv(data, kind, allowed, required)

    from .models import FlowTest

    if kind == ImportBatch.KIND_HYDRANTS:
        existing_by_tag = {h.asset_tag: h for h in Hydrant.objects.all()}
        parsed = [_validate_hydrant_row(record, line_no, existing_by_tag) for line_no, record in rows]
        rows_out = _dedupe_in_file(parsed, "asset_tag")
    elif kind == ImportBatch.KIND_PARCELS:
        existing_by_pid = {p.parcel_id: p for p in Parcel.objects.all()}
        parsed = [_validate_parcel_row(record, line_no, existing_by_pid) for line_no, record in rows]
        rows_out = _dedupe_in_file(parsed, "parcel_id")
    else:
        taken = {str(u) for u in FlowTest.objects.exclude(client_uuid=None)
                 .values_list("client_uuid", flat=True)}
        hydrant_by_tag = {h.asset_tag: h for h in Hydrant.objects.all()}
        in_file_uuids: set[str] = set()
        parsed = []
        for line_no, record in rows:
            parsed_row = _validate_flow_test_row(record, line_no, hydrant_by_tag,
                                                 taken | in_file_uuids)
            if parsed_row.data.get("client_uuid"):
                in_file_uuids.add(parsed_row.data["client_uuid"])
            parsed.append(parsed_row)
        rows_out = parsed

    errors = [error for row in rows_out for error in row.errors]
    warnings = [warning for row in rows_out for warning in row.warnings]
    skipped = sum(1 for row in rows_out if row.data.get("skip"))
    rows_ok = sum(1 for row in rows_out if row.valid and not row.data.get("skip"))
    return {
        "rows": rows_out,
        "rows_ok": rows_ok,
        "rows_rejected": sum(1 for row in rows_out if not row.valid),
        "errors": errors,
        "warnings": warnings,
        "skipped_duplicates": skipped,
    }


def _conflicts(existing_org: str, supplier: str) -> bool:
    """Canonical-key conflict: any difference conflicts, blank vs non-blank
    included ('blank source_org conflicts with any non-blank supplier')."""
    return _canonical_org(existing_org) != _canonical_org(supplier)


def apply_import(kind: str, rows: list[ParsedRow], *, batch: ImportBatch, user) -> dict:
    """Apply the valid rows inside the caller's transaction; skip the rest."""
    from .services import record_attribute_changes, record_parcel_changes

    ok = 0
    rejected = 0
    errors: list[dict] = []
    warnings: list[dict] = []
    supplier = batch.supplier

    for row in rows:
        if not row.valid:
            rejected += 1
            errors.extend(row.errors)
            continue
        warnings.extend(row.warnings)
        if row.data.get("skip"):
            continue  # idempotent replay (duplicate client_uuid)
        try:
            if kind == ImportBatch.KIND_HYDRANTS:
                applied = _apply_hydrant_row(row, batch=batch, supplier=supplier, user=user,
                                             record_changes=record_attribute_changes)
            elif kind == ImportBatch.KIND_PARCELS:
                applied = _apply_parcel_row(row, batch=batch, supplier=supplier, user=user,
                                            record_changes=record_parcel_changes)
            else:
                applied = _apply_flow_test_row(row, user=user)
        except LedgerError as exc:
            rejected += 1
            errors.append({"line": row.line, "field": exc.field or "", "message": exc.detail})
            continue
        if applied == "conflict":
            rejected += 1
            errors.append({
                "line": row.line, "field": "source_org",
                "message": "CONFLICT: the record's source_org differs from the batch "
                           "supplier; resubmit with allow_overwrite=true to take ownership of "
                           "the fields (audited import_overwrite_conflict)",
            })
            continue
        ok += 1
    return {"rows_ok": ok, "rows_rejected": rejected, "errors": errors, "warnings": warnings}


def _apply_hydrant_row(row: ParsedRow, *, batch, supplier, user, record_changes) -> str:
    data = row.data
    if row.create:
        hydrant = Hydrant.objects.create(
            asset_tag=data["asset_tag"],
            lat=data["lat"], lon=data["lon"],
            location_description=data["location_description"],
            manufacturer=data["manufacturer"], model=data["model"],
            year_installed=data["year_installed"],
            main_size_inches=data["main_size_inches"],
            outlet_diameter_in=data["outlet_diameter_in"] if data["outlet_diameter_in"] is not None else "2.50",
            discharge_coefficient=data["discharge_coefficient"] if data["discharge_coefficient"] is not None else "0.90",
            ownership=data["ownership"],
            source=Hydrant.SOURCE_CSV,
            source_org=data["source_org"] or supplier,
            notes=data["notes"],
            created_by=user if getattr(user, "is_authenticated", False) else None,
            updated_by=user if getattr(user, "is_authenticated", False) else None,
        )
        # Every creation path writes the attributed initial status event.
        create_status_event(
            hydrant=hydrant,
            to_status=data["initial_status"],
            reason=data["initial_status_reason"] or f"Import {batch.filename}",
            expected_return_on=data["expected_return_on"],
            indefinite=data["indefinite"],
            user=user,
            is_initial=True,
        )
        return "created"

    hydrant = Hydrant.objects.select_for_update().get(pk=row.existing_pk)
    conflict = _conflicts(hydrant.source_org, supplier)
    if conflict and not batch.allow_overwrite:
        return "conflict"
    changes = []
    for field_name in ("lat", "lon", "location_description", "manufacturer", "model",
                       "year_installed", "main_size_inches", "outlet_diameter_in",
                       "discharge_coefficient", "ownership", "notes"):
        new_value = data[field_name]
        if new_value in (None, ""):
            continue  # blank optional cells leave values unchanged
        old_value = getattr(hydrant, field_name)
        if str(old_value if old_value is not None else "") != str(new_value):
            changes.append((field_name, old_value, new_value))
            setattr(hydrant, field_name, new_value)
    if changes:
        hydrant.source = Hydrant.SOURCE_CSV
        hydrant.updated_by = user if getattr(user, "is_authenticated", False) else None
        hydrant.save()
        record_changes(
            hydrant, changes,
            reason="import_overwrite_conflict" if conflict else "import",
            user=user, batch=batch,
        )
    return "updated"


def _apply_parcel_row(row: ParsedRow, *, batch, supplier, user, record_changes) -> str:
    data = row.data
    if row.create:
        Parcel.objects.create(
            parcel_id=data["parcel_id"], lat=data["lat"], lon=data["lon"],
            address=data["address"], structure_type=data["structure_type"],
            is_protected_structure=(
                data["is_protected_structure"] if data["is_protected_structure"] is not None else True
            ),
            structure_lat=data["structure_lat"], structure_lon=data["structure_lon"],
            source=Parcel.SOURCE_CSV,
            source_org=data["source_org"] or supplier,
        )
        return "created"

    parcel = Parcel.objects.select_for_update().get(pk=row.existing_pk)
    conflict = _conflicts(parcel.source_org, supplier)
    if conflict and not batch.allow_overwrite:
        return "conflict"
    changes = []
    for field_name in ("lat", "lon", "address", "structure_type",
                       "is_protected_structure", "structure_lat", "structure_lon"):
        new_value = data[field_name]
        if new_value in (None, ""):
            continue
        old_value = getattr(parcel, field_name)
        if str(old_value if old_value is not None else "") != str(new_value):
            changes.append((field_name, old_value, new_value))
            setattr(parcel, field_name, new_value)
    if changes:
        parcel.source = Parcel.SOURCE_CSV
        parcel.save()
        record_changes(parcel, changes, user=user)
    return "updated"


def _apply_flow_test_row(row: ParsedRow, *, user) -> str:
    data = row.data
    create_flow_test(
        hydrant=data["hydrant"],
        tested_on=data["tested_on"],
        static_psi=data["static_psi"],
        residual_psi=data["residual_psi"],
        outlets=data["outlets"],
        measured_total_gpm=data["measured_total_gpm"],
        crew_names=data["crew_names"],
        notes=data["notes"],
        client_uuid=data["client_uuid"],
        user=user,
    )
    return "created"
