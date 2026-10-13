# HydrantProof — API Reference

**Audience:** developers and operators scripting against a district's
HydrantProof instance (integrations, bulk loads, county/CAD feeds, automation).

The served OpenAPI 3.0.3 schema is the machine-readable companion to this
document: `GET /openapi.json` (anonymous, schema only). It is generated from
the same route table this document describes.

---

## 1. Conventions

| | |
|---|---|
| **Base URL (local)** | `http://localhost:8000` |
| **API prefix** | `/api/v1/` |
| **Content types** | Requests: `application/json` (or `multipart/form-data` for uploads). Responses: `application/json`, `text/csv`, `application/pdf`, `application/zip`, `application/geo+json`, `image/jpeg` |
| **Browsable API** | Disabled by design — the API answers JSON only |
| **OpenAPI schema** | `GET /openapi.json` — anonymous, `openapi: 3.0.3` |

### Authentication

Two mechanisms:

1. **HTTP Basic** — accepted **only** under `/api/v1/`. Every request carries
   `Authorization: Basic <base64(user:password)>`. CSRF does not apply.
2. **Session cookie** — the browser UI's mechanism; also accepted on
   `/api/v1/` for same-origin JavaScript, where unsafe methods need the
   `X-CSRFToken` header (the UI pages read the token from their forms).

Anonymous requests to `/api/v1/` answer:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"detail": "Authentication required."}
```

### Roles

| Role | Group | Rights |
|---|---|---|
| viewer | `viewer` | Read everything, including exports and pack downloads |
| editor | `editor` | viewer + all writes (create/update hydrants and parcels, tests, inspections, deficiencies, status events, acknowledgements, imports, overlays, coverage/pack submission) |
| admin | `admin` (or superuser) | editor + attachment deletion + ledger export |

A viewer's write attempt answers **403** `{"detail": "Your role is read-only."}`.

### Pagination

List endpoints that paginate use DRF `PageNumberPagination` with page size 50:

```json
{"count": 123, "next": "http://host/api/v1/hydrants/?page=2", "previous": null, "results": [ … ]}
```

Use `?page=N`. A few small lists are unpaginated and shaped
`{"results": [ … ]}` (overlays, import history, notifications) or a bare JSON
array (deficiencies, status events, acknowledgements, attachments) — each
endpoint below states its shape.

### Error envelope

| Status | Shape | Meaning |
|---|---|---|
| 400 | `{"field": ["message"]}` or `{"detail": "…"}` | Validation failure; the field name is the key |
| 401 | `{"detail": "Authentication required."}` | Missing/invalid credentials |
| 403 | `{"detail": "Your role is read-only."}` / `"Administrator role required."` | Role too low |
| 404 | `{"detail": "Not found."}` or a specific message | Object does not exist |
| 405 | `{"detail": "Method \"PUT\" not allowed."}` | No PUT anywhere; PATCH/DELETE only where documented |
| 409 | `{"detail": "…"}` | Job single-flight conflict, or `client_uuid_conflict` |
| 413 | `{"detail": "…"}` | A size cap (upload, overlay, parcel-id list, attachment limits) |
| 429 | `{"detail": "…"}` + `Retry-After` | Auth throttle, or the pack rate limit (10/minute/user) |
| 500 | `{"detail": "…"}` | Server error — check the server log |

### Throttling

* 10 failed logins for one username within 15 minutes → **429** with
  `Retry-After: 900`.
* Valid credentials are accepted during a throttle window up to **20
  verifications per username per 15 minutes**; beyond that, 429 for that
  username (bounded residual DoS window, 15 minutes).
* A successful login resets the window.
* Per-IP dimension (50 failures/15 min) activates **only** when
  `TRUST_PROXY_HEADERS=true`.
* Evidence-pack creation is limited to **10 per minute per user** (429 +
  `Retry-After: 60`).

### The two async jobs

Coverage runs and evidence packs are asynchronous. Both share one creation
gate (PostgreSQL advisory lock + single-flight row check):

* `POST` returns **202** `{"id": N, "status": "RUNNING"|"RENDERING", "poll_url": "…"}`.
* A conflicting submission answers **409** (another job in flight, or the lock
  is held during creation).
* Poll the `poll_url` until `status` is `COMPLETE` or `FAILED`. **Job failures
  are not HTTP errors** — the poll answers 200 with `status: "FAILED"`,
  `failure_reason`, and a `remediation` hint.
* A server restart marks in-flight jobs `FAILED (orphaned)`; start a new job.

---

## 2. Endpoint index

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET, POST | `/api/v1/hydrants/` | viewer / editor | List (filters) / create |
| GET, PATCH | `/api/v1/hydrants/{id}/` | viewer / editor | Read / update attributes |
| GET, POST | `/api/v1/hydrants/{id}/tests/` | viewer / editor | List / record flow tests |
| GET | `/api/v1/hydrants/{id}/tests/{test_id}/` | viewer | One test (read-only) |
| GET, POST | `/api/v1/hydrants/{id}/tests/{owner_id}/attachments/` | viewer / editor | List / upload photos |
| GET, DELETE | `/api/v1/hydrants/{id}/tests/{owner_id}/attachments/{att_id}/` | viewer / admin | Serve bytes / audited delete |
| GET, POST | `/api/v1/hydrants/{id}/inspections/` | viewer / editor | List / record inspections |
| GET, POST | `/api/v1/hydrants/{id}/inspections/{owner_id}/attachments/` | viewer / editor | List / upload photos |
| GET, DELETE | `/api/v1/hydrants/{id}/inspections/{owner_id}/attachments/{att_id}/` | viewer / admin | Serve bytes / audited delete |
| GET, POST | `/api/v1/hydrants/{id}/status-events/` | viewer / editor | Status history / apply transition or reversal |
| GET, POST | `/api/v1/hydrants/{id}/oos-acknowledgements/` | viewer / editor | List / acknowledge expired OOS |
| GET | `/api/v1/hydrants/{id}/deficiencies/` | viewer | Deficiency rows |
| POST | `/api/v1/hydrants/{id}/deficiencies/{def_id}/resolve/` | editor | Resolve a deficiency |
| GET | `/api/v1/hydrants/{id}/coverage/` | viewer | Parcels this hydrant serves (latest COMPLETE run) |
| POST | `/api/v1/compute-preview/` | editor | Authoritative hydraulics preview |
| GET | `/api/v1/overdue/` | viewer | Ranked overdue list (`kind=test\|inspection`) |
| GET | `/api/v1/notifications/` | viewer | Ranked live notification list |
| GET, POST | `/api/v1/parcels/` | viewer / editor | List / create |
| GET, PATCH | `/api/v1/parcels/{id}/` | viewer / editor | Read / update |
| GET | `/api/v1/parcels/{id}/coverage/` | viewer | This parcel's rows (latest COMPLETE run) |
| GET | `/api/v1/settings/` | viewer | District settings (read-only over the API) |
| GET, POST | `/api/v1/coverage-runs/` | viewer / editor | List / start a coverage run |
| GET | `/api/v1/coverage-runs/{id}/` | viewer | Poll run status (+ staleness) |
| GET | `/api/v1/coverage-runs/{id}/results/` | viewer | Worksheet rows |
| GET, POST | `/api/v1/evidence-packs/` | viewer / editor | List / generate a pack |
| GET | `/api/v1/evidence-packs/{id}/` | viewer | Poll pack status |
| GET | `/api/v1/evidence-packs/{id}/zip/` | viewer | Download stored zip |
| GET | `/api/v1/evidence-packs/{id}/pdf/` | viewer | Download stored PDF |
| GET | `/api/v1/overlays/` | viewer | List overlays |
| POST | `/api/v1/overlays/` | editor | Upload/replace the active overlay |
| GET | `/api/v1/overlays/active/` | viewer | Active overlay bytes (`application/geo+json`) |
| GET, DELETE | `/api/v1/overlays/{id}/` | viewer / editor | Metadata / delete |
| GET | `/api/v1/imports/` | viewer | Import batch history |
| POST | `/api/v1/imports/hydrants/` | editor | Two-phase hydrant CSV import |
| POST | `/api/v1/imports/parcels/` | editor | Two-phase parcel CSV import |
| POST | `/api/v1/imports/flow-tests/` | editor | Two-phase historical-test CSV import |
| GET | `/api/v1/exports/dispatch/` | viewer | Dispatch CSV download |
| GET | `/api/v1/exports/forms/flow-test/` | viewer | Printable blank flow-test sheet (PDF) |
| GET | `/api/v1/exports/forms/inventory/` | viewer | Printable blank inventory sheet (PDF) |
| GET | `/api/v1/exports/ledger/` | **admin** | Versioned JSON ledger export |

Non-API routes are listed in §12.

---

## 3. Hydrants

### GET `/api/v1/hydrants/` — list

Query parameters (all optional):

| Param | Values | Effect |
|---|---|---|
| `status` | `IN_SERVICE` / `OUT_OF_SERVICE` / `RETIRED` | Filter on the service lifecycle |
| `q` | text | Case-insensitive contains on `asset_tag`, `location_description`, `ownership`, `source_org` |
| `readiness` | `READY` / `WATCH` / `NOT_READY` | Derived filter (evaluates every row) |
| `class` | `AA` / `A` / `B` / `C` | Derived capacity-class filter |
| `recency` | `CURRENT` / `DUE_SOON` / `OVERDUE` / `NEVER_TESTED` | Derived test-recency filter |
| `page` | integer | Pagination |

Each result carries the full hydrant record **plus the canonical derivation**:
`readiness`, `gating_flags`, `attention_flags`, `trend`, `capacity_class`,
`test_recency_flag`, `inspection_recency_flag`, `exclusion_code`,
`district_credit_eligible`, `open_deficiency`, `open_deficiency_codes`,
`oos_expired`, `oos_acknowledged_at`, `latest_tested_on`,
`latest_rated_gpm_20`, `latest_computed_total_gpm`, `test_warnings`,
`days_test_overdue`, `days_inspection_overdue`.

```bash
curl -u "$USER:$PASS" "http://localhost:8000/api/v1/hydrants/?status=IN_SERVICE&readiness=NOT_READY"
```

```json
{
  "count": 23,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1, "asset_tag": "HP-001", "lat": 38.0, "lon": -122.0,
      "location_description": "HP-001 sample location",
      "status": "IN_SERVICE", "source": "MANUAL", "source_org": "Sample District",
      "is_sample_data": true,
      "readiness": "READY", "gating_flags": [], "attention_flags": [],
      "trend": "INSUFFICIENT_HISTORY", "capacity_class": "AA",
      "test_recency_flag": "CURRENT", "inspection_recency_flag": "CURRENT",
      "exclusion_code": null, "district_credit_eligible": true,
      "latest_tested_on": "2026-09-04", "latest_rated_gpm_20": 2642.0,
      "test_warnings": []
    }
  ]
}
```

### POST `/api/v1/hydrants/` — create

Body (required: `asset_tag`, `lat`, `lon`):

| Field | Type | Notes |
|---|---|---|
| `asset_tag` | string ≤ 64 | Unique; duplicates answer 400 |
| `lat`, `lon` | number | \|lat\| ≤ 80, \|lon\| ≤ 180 |
| `location_description`, `manufacturer`, `model`, `ownership`, `source_org`, `notes` | string | Optional |
| `year_installed` | int | Optional |
| `main_size_inches` | decimal | Optional |
| `outlet_diameter_in`, `discharge_coefficient` | decimal | Defaults `2.50` / `0.90` |
| `initial_status` | enum | `IN_SERVICE` (default) / `OUT_OF_SERVICE` / `RETIRED` |
| `initial_status_reason` | string | Required for `RETIRED` |
| `expected_return_on` | date | Required for `OUT_OF_SERVICE` unless `indefinite=true` |
| `indefinite` | bool | For an out-of-service hydrant with no return date |
| `effective_on` | date | Defaults to today; future dates rejected |

Every creation writes an **attributed initial status event** in the same
transaction. `201` returns the hydrant with its derivation.

```bash
curl -u "$USER:$PASS" -H "Content-Type: application/json" -d '{
  "asset_tag": "HP-101", "lat": 38.01, "lon": -122.02,
  "location_description": "Main St & 1st Ave", "source_org": "Sample District"
}' http://localhost:8000/api/v1/hydrants/
```

### GET / PATCH `/api/v1/hydrants/{id}/`

PATCH accepts only the attribute fields: `location_description`,
`manufacturer`, `model`, `year_installed`, `main_size_inches`,
`outlet_diameter_in`, `discharge_coefficient`, `ownership`, `notes`,
`source_org`, `lat`, `lon`. `asset_tag` is immutable (400). **Status is never
changed here** — use status events.

Every changed field appends a `HydrantAttributeChange` row with reason
`manual` — except `source_org`, which is always recorded as
`ownership_transfer` when explicitly set (ownership never transfers
implicitly).

### GET / POST `/api/v1/hydrants/{id}/tests/` — flow tests

GET is paginated, newest first.

POST body:

| Field | Type | Notes |
|---|---|---|
| `tested_on` | date | Required; future dates rejected |
| `static_psi` | decimal | > 0, ≤ 500 |
| `residual_psi` | decimal | ≥ 0, < static |
| `outlets` | array | 0–4 rows: `outlet_diameter_in` (0.5–8.0), `outlet_coefficient` (0.5–1.0), `pitot_psi` (> 0, ≤ 200), optional `outlet_label`, optional `flow_asset_tag` (tag of the flowed hydrant) |
| `measured_total_gpm` | decimal | Optional; 1–100,000. When present it is authoritative (`Q_F`) |
| `crew_names`, `notes` | string | Optional |
| `supersedes` | int | Optional id of a test of the same hydrant being corrected (a target may be superseded once) |
| `client_uuid` | UUID | Optional idempotency key (field capture uses it) |

At least one outlet reading or a meter total is required. The server computes
`computed_total_gpm` (Q_F), `rated_gpm_20` (Q_R), `capacity_class`,
`warnings`, and stamps `formula_version`.

**Idempotency:** re-POSTing the same `client_uuid` with an identical payload
returns **200** with the existing test and `"duplicate_client_uuid": true`; a
different payload under the same `client_uuid` answers **409**
`client_uuid_conflict`.

```bash
curl -u "$USER:$PASS" -H "Content-Type: application/json" -d '{
  "tested_on": "2026-10-01", "static_psi": "80.00", "residual_psi": "40.00",
  "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "28.00"}],
  "crew_names": "A shift"
}' http://localhost:8000/api/v1/hydrants/1/tests/
```

```json
{
  "id": 41, "hydrant": 1, "tested_on": "2026-10-01",
  "static_psi": "80.00", "residual_psi": "40.00",
  "computed_total_gpm": "887.9", "rated_gpm_20": "1105.2",
  "capacity_class": "A", "warnings": [],
  "formula_version": "nfpa291-pct-drop-v1",
  "outlets": [
    {"id": 77, "outlet_label": "outlet-1", "outlet_diameter_in": "2.50",
     "outlet_coefficient": "0.90", "pitot_psi": "28.00",
     "computed_gpm": "887.9", "flow_hydrant": 1, "flow_asset_tag": "HP-001"}
  ]
}
```

Warnings you may see in `warnings` (fixed order): `STATIC_BELOW_TARGET`,
`RESIDUAL_BELOW_TARGET`, `EXCESSIVE_EXTRAPOLATION`, `METER_AND_PITOT_PRESENT`,
`METER_PITOT_DIVERGENT`, `DUPLICATE_READING`.

### GET `/api/v1/hydrants/{id}/tests/{test_id}/`

Read-only (PATCH/DELETE answer 405). Returns the test with its outlets.

### POST `/api/v1/compute-preview/` — hydraulics preview

The single authoritative preview: same inputs as a flow test, same engine —
so a UI preview can never disagree with what a save would compute. Creates
nothing.

```bash
curl -u "$USER:$PASS" -H "Content-Type: application/json" -d '{
  "tested_on": "2026-10-03", "static_psi": "80.00", "residual_psi": "40.00",
  "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "28.00"}]
}' http://localhost:8000/api/v1/compute-preview/
```

```json
{
  "computed_total_gpm": 887.9, "pitot_sum_gpm": null, "rated_gpm_20": 1105.2,
  "capacity_class": "A", "warnings": [], "formula_version": "nfpa291-pct-drop-v1",
  "extrapolation_factor": 1.5, "outlet_gpm": [887.9]
}
```

---

## 4. Inspections, deficiencies, status, acknowledgements

### GET / POST `/api/v1/hydrants/{id}/inspections/`

POST body: `inspected_on` (date, required, no future), `result`
(`OK`/`DEFICIENCY`/`FAIL`), `deficiency_codes` (array from `CAP`, `CHAIN`,
`NOZZLE`, `VALVE`, `LEAK`, `PAINT`, `ACCESS`, `OBSTRUCTION`, `OTHER`;
required when result ≠ `OK`), `inspector_names`, `notes`.

Effects, transactionally:

* `FAIL` → opens one **gating** deficiency row per code (`gates_credit=true`).
* `DEFICIENCY` → opens one **non-gating** row per code (`gates_credit=false`).
* `OK` → auto-closes **every** open deficiency row for the hydrant
  (`resolution_kind=auto_ok`).

### GET `/api/v1/hydrants/{id}/deficiencies/`

Bare array. Optional `?status=OPEN|RESOLVED`. Each row: `id`, `hydrant`,
`inspection`, `category`, `gates_credit`, `note`, `assigned_to`, `due_on`,
`status`, `opened_by`, `opened_at`, `resolved_by`, `resolved_at`,
`resolution_note`, `resolution_kind`.

### POST `/api/v1/hydrants/{id}/deficiencies/{def_id}/resolve/`

Body: `resolution_note` (required ≤ 255), optional `resolved_at` (no future),
optional `assigned_to`, optional `due_on` (≤ today + 365). Resolving an
already-resolved row answers 400.

### GET / POST `/api/v1/hydrants/{id}/status-events/`

GET returns a bare array ordered by `(effective_on, changed_at, id)`. Fields:
`id`, `hydrant`, `from_status`, `to_status`, `reason`, `expected_return_on`,
`indefinite`, `effective_on`, `reversal_of`, `changed_by`, `changed_at`.

POST applies one of the six legal transitions
(`IN_SERVICE ↔ OUT_OF_SERVICE`, `IN_SERVICE/OUT_OF_SERVICE → RETIRED`,
`RETIRED → IN_SERVICE/OUT_OF_SERVICE`). Rules enforced:

* Entering `OUT_OF_SERVICE` requires `expected_return_on` **or**
  `indefinite=true` (not both).
* `RETIRED → *` transitions require a `reason`.
* `effective_on` defaults to today; future dates are rejected.
* A backdated event never silently becomes current status (current status is
  max `(effective_on, changed_at, id)`).

**Reversal:** pass `reversal_of` = the id of the current (latest) event to
undo it; `to_status` is then inferred from that event's `from_status`, and a
`reason` is required. A reversal event may not itself be reversed.

### GET / POST `/api/v1/hydrants/{id}/oos-acknowledgements/`

GET: bare array, newest first. POST body `note` (optional). Records who
acknowledged an expired out-of-service hydrant and when — POST is 400 on a
hydrant that is not currently expired-OOS (in-service, retired, or not yet
past its expected return). The acknowledgement surfaces the hydrant amber for
30 days; after that it re-surfaces red and the dispatch CSV blanks
`oos_acknowledged_at`. A repeat POST inside the 30-day window answers 200 with
the existing row (idempotent — repeats never restart the window). **An expired
out-of-service hydrant is never auto-returned to service** — acknowledgement
changes presentation, not status.

### GET `/api/v1/overdue/`

`?kind=test` (default) or `?kind=inspection`; anything else is 400.

```json
{
  "kind": "test", "count": 2,
  "results": [
    {"hydrant": 22, "asset_tag": "HP-022", "status": "RETIRED",
     "days_overdue": 335, "readiness": "NOT_READY",
     "gating_flags": ["TEST_STALE", "NEVER_INSPECTED"],
     "last_tested_on": "2024-11-03", "last_inspected_on": null}
  ]
}
```

### GET `/api/v1/notifications/`

Ranked live list (`{"results": [...]}`), expired OOS first (rank 0), then
overdue tests (rank 1), then overdue inspections (rank 2); each row carries
`rank`, `kind`, `hydrant`, `asset_tag`, `detail`, `days`, and (OOS rows)
`acknowledged`.

---

## 5. Parcels and settings

### GET / POST `/api/v1/parcels/`

GET is paginated; filters `?q=` (parcel_id/address contains) and
`?protected=true`.

POST body: `parcel_id` (required, unique), `lat`, `lon`, `address`,
`structure_lat`, `structure_lon`, `is_protected_structure`, `structure_type`,
`source_org`. When **both** `structure_lat` and `structure_lon` are set, the
override is the point used for coverage; a one-sided pair is stored but
ignored at coverage time (the centroid is used). The PATCH route and the CSV
import reject one-sided pairs; the create route stores them.

Response adds `point_lat`/`point_lon` — the point coverage will use (override
when both are present, else the centroid).

### GET / PATCH `/api/v1/parcels/{id}/`

PATCH accepts `address`, `lat`, `lon`, `structure_lat`, `structure_lon`,
`is_protected_structure`, `structure_type`. Changes append
`ParcelAttributeChange` rows (reason `manual`).

### GET `/api/v1/settings/` — district settings

Read-only over the API (mutations go through the Django admin, which writes a
`SettingsAudit` row per changed field). Fields: `name`, `iso_community_id`,
`state`, `time_zone`, `recency_days` (30–1095), `due_soon_days` (1–364, <
recency), `inspection_recency_days` (30–1095), `inspection_due_soon_days`
(1–364), `credit_radius_ft` (250–2640), `exclude_residual_below_target`,
`max_media_mb`, `max_attachment_total_mb`.

---

## 6. Coverage runs

### POST `/api/v1/coverage-runs/` — start a run

Body: `{"parcel_filter": null}` or one of:

* `{"parcel_filter": {"parcel_ids": [1, 2, 3]}}` — at most 50,000 ids
  (more → **413**); ids are sorted+deduplicated before hashing.
* `{"parcel_filter": {"bbox": [minlon, minlat, maxlon, maxlat]}}` — requires
  `minlon < maxlon`, `minlat < maxlat`, longitudes within ±180, latitudes
  within ±80.

Returns **202**:

```json
{"id": 7, "status": "RUNNING", "poll_url": "/api/v1/coverage-runs/7/"}
```

**409** while any run is RUNNING or any pack is RENDERING, or while another
creation holds the advisory lock.

The run **freezes** the district parameters it computed under (settings echo +
constants + filter, hashed as `params_sha256`). The worksheet's credit rule is
evaluated under those frozen parameters, so a later settings change cannot
rewrite history — it only marks the run stale.

### GET `/api/v1/coverage-runs/{id}/` — poll

Returns the run record. On `COMPLETE` it adds a `staleness` object
(`{stale, reasons[]}`); on `FAILED` it adds a `remediation` string. Key fields:

`id`, `status`, `recency_days`, `inspection_recency_days`, `credit_radius_ft`,
`exclude_residual_below_target`, `settings_echo`, `parcel_filter`,
`params_sha256`, `core_params_sha256`, `snapshot_ts`,
`formula_versions_present`, `hydrant_count`, `protected_parcel_count`,
`pair_count`, `in_radius_count`, `covered_count`, `credited_band_counts`,
`started_at`, `finished_at`, `failure_reason`.

`failure_reason` values on a run: `pair_count_exceeded` (narrow with
`parcel_filter`), `orphaned` (server restart — start a new run), or
`error:<ExceptionType>` (check the server log). A *pack* can additionally fail
with `media_budget_exceeded` (§7).

### GET `/api/v1/coverage-runs/{id}/results/` — worksheet rows

Paginated. Optional `?parcel=<parcel_id>` filters to one parcel. Each row:
`id`, `run`, `parcel`, `parcel_id`, `hydrant` (nullable — null rows are
`NO_HYDRANT_IN_RADIUS`), `asset_tag`, `distance_ft` (unrounded; display
rounds to 0.1), `district_credited`, `district_credited_gpm`,
`exclusion_code`, `expired`.

Exclusion codes, in precedence order (first match wins): `RETIRED`,
`OUT_OF_SERVICE`, `UNTESTED`, `TEST_STALE`, `INSPECTION_FAIL`,
`STATIC_BELOW_TARGET`, `EXCESSIVE_EXTRAPOLATION`,
`FORMULA_VERSION_MISMATCH`, `RESIDUAL_BELOW_TARGET`,
`NO_HYDRANT_IN_RADIUS`.

### GET `/api/v1/hydrants/{id}/coverage/` and `/api/v1/parcels/{id}/coverage/`

The same rows, scoped to one hydrant or parcel, from the **latest COMPLETE
run**. Answers 404 `{"detail": "No completed coverage run", "hint": "POST /api/v1/coverage-runs/"}` when no run has completed.

---

## 7. Evidence packs

### POST `/api/v1/evidence-packs/` — generate

Body: `{"run_id": N}` (must reference a COMPLETE run — **404** otherwise; a
run covering zero hydrants or zero protected parcels → **400**) or `{}` (a
fresh coverage run is created and executed first; **400** when the ledger has
no hydrants or no protected parcels). `include_attachments` (bool, default
false) adds every attachment of the run's hydrants to the zip; it counts
against the media budget.

Rate limit: 10/minute/user → **429** + `Retry-After: 60`.

Returns **202** `{"id": N, "run_id": M, "status": "RENDERING", "poll_url": "…"}`.

The pack is rendered **once** and stored under `media/packs/{id}/`.

### GET `/api/v1/evidence-packs/{id}/` — poll

Fields: `id`, `run`, `status`, `byte_size`, `pdf_sha256`, `csv_zip_sha256`,
`page_count`, `row_counts`, `render_stack_version`, `formula_versions_present`,
`include_attachments`, `failure_reason`, `generated_by`, `generated_at`,
`created_at`, `is_sample_data`. When `COMPLETE`, adds `zip_url` and `pdf_url`;
when `FAILED`, adds `remediation`.

### GET `/api/v1/evidence-packs/{id}/zip/` and `/pdf/` — downloads

Serve the **stored bytes** — byte-identical on every download (verified in the
runtime smoke: two downloads hash identically). Responses:

* **200** with `Content-Disposition: attachment` and `nosniff`.
* **409** while the pack is still rendering.
* **404** `{"detail": "pack pruned", "hint": "generate a new pack"}` when the
  row was pruned.
* **410** `{"detail": "stored file missing", "hint": "generate a new pack"}`
  when the row exists but the file is gone.

Zip contents (fixed order): `pack.pdf`, `hydrants.csv`,
`coverage_worksheet.csv`, `parcel_summary.csv`, `oos_register.csv`,
`manifest.txt`, then `attachments/{id}.jpg` when included. `manifest.txt`
carries the run id, timestamps, both params hashes, formula versions, the
render-stack version, the frozen parameters, and the SHA-256 of every other
entry — plus the artifact label, disclaimer, and provisional banner.

---

## 8. Overlays

### GET `/api/v1/overlays/` — list (first 50, `{"results": [...]}`)

Fields: `id`, `name`, `feature_count`, `byte_size`, `uploaded_by`,
`uploaded_at`.

### POST `/api/v1/overlays/` — upload/replace (editor+)

`multipart/form-data` with `file` (required) and optional `name`. Validates a
GeoJSON **FeatureCollection**:

* ≤ 5 MB (413), ≤ 20,000 features (413), valid UTF-8 JSON, geometry types
  `Point`/`LineString`/`Polygon`/`Multi*` only, coordinates within ±180 lon /
  ±80 lat. Errors name the first offending feature index.
* Counts against the single media budget (413 when exceeded).

Upload **replaces** the active overlay (one active overlay; the old file is
deleted after the new bytes land).

### GET `/api/v1/overlays/active/` — the active overlay

`application/geo+json`; 404 when none; 410 when the stored file is missing.

### GET / DELETE `/api/v1/overlays/{id}/`

GET returns metadata; DELETE (editor+) removes row + file. 204 on success.

---

## 9. Imports (two-phase CSV)

All three kinds share the protocol:

1. `POST` with `mode=dry_run`, `file=@your.csv`, optional `supplier`,
   optional `allow_overwrite`. The whole file is validated; **nothing is
   applied**. The file is stored under `media/imports/{batch}.csv`. The
   response carries the batch, `rows_ok`, `rows_rejected`, `errors`
   (`{line, field, message}`), `warnings`, and a one-shot `commit_token`
   (stored only as SHA-256, bound to your user, 24 h expiry).
2. `POST` with `mode=commit` and `commit_token`. The stored dry-run file is
   reused; alternatively attach the file again — its SHA-256 must match the
   dry run (`file_hash_mismatch` → 400). The commit applies exactly what the
   dry run previewed: a different `supplier` or an `allow_overwrite` value
   that flips the dry run's flag is refused (`commit_context_mismatch` → 400,
   token left unspent — run a new dry run under the new flags). Commit
   **re-validates everything**, then applies passing rows in one transaction.
   Invalid or conflicting rows are skipped with errors — never silently
   applied.

File-level rules: UTF-8 (BOM tolerated), header row required, unknown columns
rejected, required columns enforced, ≤ 50,000 rows (413), ≤ 10 MB upload.
Duplicate upsert keys within one file: the **last row wins** (earlier ones
become warnings). Blank optional cells leave existing values unchanged.
Upsert keys: hydrants `asset_tag`, parcels `parcel_id`. **Status is never
silently upserted** — an `initial_status` cell on an existing hydrant is a row
error; use status events.

Conflict policy: a row whose record belongs to a different supplier (compared
on the canonicalized `source_org`) is skipped with `CONFLICT` unless the batch
sets `allow_overwrite=true` (recorded on the batch; every overwritten field is
audited as `import_overwrite_conflict`).

### `/api/v1/imports/hydrants/`

Columns (allowed; required in bold): **asset_tag**, lat, lon, latitude,
longitude, location_description, manufacturer, model, year_installed,
main_size_inches, outlet_diameter_in, discharge_coefficient, ownership,
source_org, notes, initial_status, initial_status_reason, expected_return_on,
indefinite. Use `lat`/`lon` **or** `latitude`/`longitude`, never both.
Template: `/static/ledger/import_templates/hydrants.csv`.

> **Import caveat:** in the CSV import, `expected_return_on` must not be in
> the future — a future date is a row error. To load an out-of-service hydrant
> whose expected return is a future date, either set `indefinite=true` in the
> import and record the return date through a status event afterwards, or
> create the hydrant through `POST /api/v1/hydrants/`, which accepts a future
> `expected_return_on`.

### `/api/v1/imports/parcels/`

Columns: **parcel_id**, latitude, longitude, address, structure_type,
is_protected_structure, structure_lat, structure_lon, source_org. Template:
`/static/ledger/import_templates/parcels.csv`.

### `/api/v1/imports/flow-tests/`

Columns: **asset_tag**, **tested_on**, **static_psi**, **residual_psi**,
pitot_readings, measured_total_gpm, crew_names, notes, client_uuid.
`pitot_readings` is a semicolon-separated list of
`diameter:coefficient:pitot[:label[:flowed_asset_tag]]` (≤ 4). The full
hydraulics edge matrix runs at import time. A `client_uuid` already recorded is
skipped with a warning (idempotent replay). Template:
`/static/ledger/import_templates/flow-tests.csv`.

```bash
# dry run
curl -u "$USER:$PASS" -F mode=dry_run -F file=@hydrants.csv \
  http://localhost:8000/api/v1/imports/hydrants/
# commit with the returned token
curl -u "$USER:$PASS" -F mode=commit -F commit_token=TOKEN \
  http://localhost:8000/api/v1/imports/hydrants/
```

### GET `/api/v1/imports/` — batch history

`{"results": [...]}` (newest 50). Fields: `id`, `kind`, `filename`,
`file_sha256`, `supplier`, `mode`, `allow_overwrite`, `rows_ok`,
`rows_rejected`, `errors`, `created_by`, `created_at`, `committed_at`,
`is_sample_data`. Commit tokens are never echoed.

---

## 10. Attachments

Uploads are `multipart/form-data` with `file` (required) plus optional
`gps_accuracy_m` and `captured_at` (field-capture metadata). Constraints:
content-sniffed JPEG/PNG/WebP only (extension ignored), ≤ 5 MB (413), ≤ 4 per
record (413), counted against the attachment sub-budget and the total media
budget (413). The stored file is re-encoded to JPEG ≤ 1600 px with **EXIF
stripped**; `capture_meta` keeps the pre-strip EXIF facts.

`GET …/attachments/{att_id}/` serves the stored bytes (`image/jpeg`,
`nosniff`, `inline`); when the row exists but its stored file is gone (media
volume loss) it answers **410** `{"detail": "stored file missing", "hint":
"upload the attachment again"}` — the same honesty as the pack downloads.
**DELETE is admin-only** and writes a `MediaAudit` row
(actor, attachment ref, sha256, reason) — the budget-freeing and
error-correction path. The attachment must belong to the named test/inspection
on the named hydrant, or 404.

```bash
curl -u "$USER:$PASS" -F file=@gauge.jpg -F gps_accuracy_m=12 \
  http://localhost:8000/api/v1/hydrants/1/tests/41/attachments/
```

---

## 11. Exports

### GET `/api/v1/exports/dispatch/` — dispatch CSV

One row per non-retired hydrant, fixed column order (`asset_tag`, `latitude`,
`longitude`, `location_description`, `status`, `oos_reason`,
`oos_expected_return_on`, `oos_expired`, `oos_acknowledged_at`,
`open_deficiency`, `latest_tested_on`, `latest_rated_gpm_20`, `nfpa_class`,
`recency_flag`, `inspection_recency_flag`, `test_warnings`, `readiness`,
`trend`, `verify_in_field`, `escalation`, `disclaimer`, `data_as_of_utc`).
Every row carries the field-verify literal and the full disclaimer.
`Content-Disposition: attachment; filename="dispatch.csv"`. Text cells are
CSV-injection neutralized (leading `=`, `+`, `-`, `@`, tab, CR are quoted).
RETIRED hydrants are excluded. Audited (`ExportAudit`).

### GET `/api/v1/exports/forms/flow-test/` — blank field sheet (PDF)

A printable district flow-test sheet (per-outlet grid up to 4 rows, meter
total, photo note, signature line) drawn with ReportLab base-14 fonts; footer
carries the provisional banner and disclaimer. Audited.

### GET `/api/v1/exports/forms/inventory/` — blank inventory sheet (PDF)

Printable hydrant inventory sheet with the same footer treatment. Audited.

### GET `/api/v1/exports/ledger/` — versioned JSON export (**admin only**)

The full ledger + user tables (password hashes are required for restore) as
`ledger-export.json`; **excludes `AppSetting`** (the DB-persisted secret key
never leaves in a JSON backup). Same document as
`manage.py export_ledger`. Audited. Admin-only because it carries credential
material.

---

## 12. Non-API routes (for completeness)

| Route | Auth | Purpose |
|---|---|---|
| `GET /healthz` | anonymous | 200 `{"status":"ok","checks":{"database":"reachable","migrations":"applied"}}` only when the DB answers and migrations are applied; otherwise 503 naming the failing check. Cached 5 s. |
| `GET /openapi.json` | anonymous | OpenAPI 3.0.3 schema |
| `GET /login/` | anonymous | Sign-in page (CSRF token in the form) |
| `POST /logout/` | session | Sign out (the UI's POST form is CSRF-protected), redirects to `/login/`; anonymous GET redirects to `/login/` |
| `GET /` , `/dashboard/` | session | Dashboard (anonymous → 302 to `/login/?next=…`) |
| `GET /hydrants/` , `/hydrants/{id}/` | session | Registry list / hydrant detail |
| `GET /coverage/` , `/packs/` | session | Coverage worksheet / evidence packs pages |
| `GET /field/` | session | Field capture page (offline queue) |
| `GET /sw.js` | anonymous | Service worker for `/field/` (no-cache) |
| `GET /static/…` | anonymous | Collected static assets (WhiteNoise) |
| `/admin/…` | staff session | Django admin (users, district settings, read-only ledger tables) |

---

## 13. Worked example — full cycle with curl

```bash
BASE=http://localhost:8000
AUTH="-u demo-editor:hydrantproof-demo"

# 1. Health
curl -s $BASE/healthz

# 2. Create a hydrant (editor)
curl -s $AUTH -H 'Content-Type: application/json' -d '{
  "asset_tag":"HP-200","lat":38.0,"lon":-122.0,"source_org":"Sample District"
}' $BASE/api/v1/hydrants/ | python3 -m json.tool

# 3. Record a flow test on it (substitute the id returned by step 2 for 25)
curl -s $AUTH -H 'Content-Type: application/json' -d '{
  "tested_on":"2026-10-01","static_psi":"80.00","residual_psi":"40.00",
  "outlets":[{"outlet_diameter_in":"2.50","outlet_coefficient":"0.90","pitot_psi":"28.00"}]
}' $BASE/api/v1/hydrants/25/tests/

# 4. Start a coverage run, then poll it
RUN=$(curl -s $AUTH -H 'Content-Type: application/json' -d '{"parcel_filter":null}' \
  $BASE/api/v1/coverage-runs/ | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s $AUTH $BASE/api/v1/coverage-runs/$RUN/

# 5. Generate the evidence pack and download the zip
PACK=$(curl -s $AUTH -H 'Content-Type: application/json' -d "{\"run_id\":$RUN}" \
  $BASE/api/v1/evidence-packs/ | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s $AUTH $BASE/api/v1/evidence-packs/$PACK/
curl -s $AUTH -o pack.zip $BASE/api/v1/evidence-packs/$PACK/zip/
```

Runnable versions of this walkthrough (and the other common jobs) live in
[`examples/`](examples/README.md).
