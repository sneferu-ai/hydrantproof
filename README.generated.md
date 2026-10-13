# HydrantProof

**A district-owned water-supply records system of record.** Self-hosted, one
district per installation, MIT licensed.

A small or rural fire district owes two answers about every hydrant it depends
on: *can crews rely on it today*, and *can the district show a surveyor a
legible water-supply worksheet*. HydrantProof consolidates both into one
system of record.

**Product guide:** [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — start here if
you are a district user. Operators: [install & run](docs/OPERATIONS.md) ·
[architecture](docs/ARCHITECTURE.md) · [HTTP API](docs/API.md) ·
[UI reference](docs/UI.md) · [runnable examples](docs/examples/README.md).

## What it does

1. **Registry** — import a hydrant inventory from CSV (including historical
   flow tests) or drop pins on a map (with optional browser-geolocation snap).
   Attributes, provenance (`source_org` — the asset-owning organization per
   district records), and a three-state service lifecycle (`IN_SERVICE` /
   `OUT_OF_SERVICE` / `RETIRED`) with an append-only, fully attributed event
   log — including an attributed initial status event on every creation path.
2. **Ledger** — NFPA 291 flow tests (static, residual, per-outlet pitot
   readings, optional meter total, optional photos). The server computes the
   discharge (`Q_F`) and the rated available flow at 20 psi residual (`Q_R`),
   snapshots the result with a `formula_version`, keeps full history with
   supersession-based corrections under database-enforced integrity, and
   surfaces per-hydrant flow trending (including `RATED_FLOW_LOST`).
3. **Inspections and deficiencies** — append-only inspections (`OK` /
   `DEFICIENCY` / `FAIL`, deficiency codes, inspector, date, optional photos)
   with their own recency window. `FAIL` opens tracked deficiency rows per code
   that gate district credit; `DEFICIENCY` opens non-gating rows; a later `OK`
   transactionally auto-closes every open row; editors may resolve earlier.
   The `Deficiency` table is the single source of truth.
4. **Readiness** — three-state readiness (`READY` / `WATCH` / `NOT_READY` with
   ordered gating flags) plus information-level attention flags
   (`TEST_DUE_SOON`, `INSPECTION_DUE_SOON`, `FLOW_DEGRADING`,
   `RATED_FLOW_LOST`), recency flags for tests and inspections, a ranked
   overdue list, out-of-service tracking with expiry/escalation/
   acknowledgement, a notification outbox, and a dispatch CSV carrying
   field-verify and disclaimer text on every row.
5. **Water-supply worksheet** — import county assessor parcel centroids (with
   optional per-parcel structure overrides); for each protected parcel,
   enumerate every hydrant inside the credit radius with its district-credited
   contribution or its explicit exclusion reason. The headline metric is
   **distribution**: credited-hydrant count bands (3+ / 2 / 1 / 0) per
   protected parcel. A summed credited-flow band is printed as an explicitly
   labelled **proxy column** ("district-computed flow-band proxy — not the
   FSRS distribution criterion").

## What it refuses

**HydrantProof does not predict a Public Protection Classification and does
not compute FSRS point values.** ISO (Verisk) assigns classifications under a
licensed, copyrighted schedule this product cannot quote or encode. The
district gets complete worksheet inputs, ranked gaps, and a neutral readiness
profile organised the way the public FSRS water-supply section is structured —
**no points are computed anywhere.**

## Proof posture — stated plainly

HydrantProof's proof is **proof of records** — attributed, append-only,
versioned, reproducible — not proof of physical presence. The `/field/` page
captures browser geolocation (with accuracy), client capture timestamps, and
EXIF-derived capture metadata, but nothing in v0 attests presence (no QR/NFC
scan, no device attestation, no cryptographic signature). The `client_uuid` +
payload-hash replay mechanism makes queued field submissions idempotent, not
signed.

> Proof of records, not proof of presence.

Every artifact is labelled: *"District-prepared from district-maintained
records. Integrity self-attested by SHA-256 manifest — not cryptographically
signed."*

## Positioning

A volunteer fire district can produce a surveyor-ready, internally consistent
water-supply gap package — inventory, per-structure distribution, credited
flows, recency, deficiencies, ranked gaps — from its own records, in about an
hour, without a consultant and without a GIS department. That combination plus
the deterministic, disclaimed, district-owned pack is the wedge. This is a
positioning claim about the field, not a patent-style novelty defence.

### Prior art, by named class

| Prior-art class | Concrete exemplars | What it lacks that HydrantProof provides |
|---|---|---|
| Municipal/utility asset-management platforms | Cityworks / Cartegraph class | NFPA 291 rated-flow computation; eligibility-gated coverage; FSRS-organised pack; READY/WATCH readiness |
| County GIS viewers / hydrant map layers | ArcGIS Online, QGIS | Flow ledger; supersession integrity; per-structure 1000-ft worksheet; reproducible pack |
| NFPA 291 engineering spreadsheets | the hydraulics arithmetic per test | Recency/OOS readiness; per-structure coverage; audit trail; evidence pack |
| PPC preparation consultants | assemble the worksheet per engagement | Cost/recurrence; self-maintained district system; deterministic reproducible pack |

## Provisional constants — verify before submitting

Two constant classes are labelled distinctly in `ledger/constants.py` and the
label travels into every artifact:

* **Grounded arithmetic** — `DISCHARGE_CONSTANT = 29.83`, `TARGET_RESIDUAL_PSI
  = 20.0`, `DROP_EXPONENT = 0.54`.
* **Provisional, verification-gated district policy** — coefficient presets,
  the extrapolation factor (`MAX_EXTRAPOLATION_FACTOR = 4.0`), the trend
  threshold (15 %), the distribution bands, the flow-band proxy thresholds,
  and the capacity colours.

Every PDF page footer and `manifest.txt` carries:

Computed with provisional constants (coefficient presets, extrapolation factor, attribution rule, distribution bands, flow-band proxy, trend threshold). Verify against a licensed current NFPA 291 edition and current FSRS documentation before submitting to a surveyor.

### Mandatory disclaimer

This worksheet is prepared by the district using HydrantProof from district-maintained records. It is not issued by, endorsed by, or affiliated with ISO (Verisk) or NFPA. Public Protection Classifications are determined solely by ISO. Flow-test frequency and credit criteria vary — confirm with your ISO field representative and your authority having jurisdiction.

### Coverage semantics caveats (printed beside the headline numbers)

* Credited counts and flow sums are **filtered** (eligibility rules are
  printed; exclusion-code counts ride the summary line).
* Coverage is computed from **parcel centroids or optional structure
  overrides** — a centroid is a proxy whose error on large rural parcels can
  exceed the credit radius.
* The worksheet **does not model main capacity, duration, or demand**. A rated
  flow at 20 psi residual is a snapshot of one test, not a capacity model.
* **Sub-target-residual tests are credited by default**; the percentage-drop
  extrapolation is conservative there (`Q_R < Q_F`). Any band or proxy fed by
  such tests carries an asterisk footnoted with that risk posture.

## Configuration — every value, no secrets

Configuration comes from the environment. The names below are the complete
deployment contract (the `env_var_names` of `packaging.json`); none of them
contains a secret value in this repository. Five process-tuning variables the
code also reads — `HYDRANTPROOF_MEDIA_ROOT`, `DJANGO_LOG_LEVEL`,
`GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `STATIC_ROOT` — are documented in the
[operations guide](docs/OPERATIONS.md#2-environment-variables--the-complete-list)
and rarely need to change.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | (required) | PostgreSQL connection URL. The declared `db` service provides it. |
| `DJANGO_SECRET_KEY` | generated (see below) | Django secret key. **Required in production.** |
| `PORT` | `8000` | Port gunicorn binds. |
| `ALLOWED_HOSTS` | `*` (demo only) | Comma-separated host list. Restrict in production. |
| `DJANGO_DEBUG` | `false` | Never enable in production. |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated origins (e.g. `https://hydrants.example.gov`). |
| `SEED_DEMO` | `true` | Seed the demo district on first boot. Set `false` in production. |
| `MAP_TILE_URL` | empty | Browser-side basemap tile template (see "Basemap"). |
| `MAP_TILE_ATTRIBUTION` | empty | Attribution string for the basemap. |
| `COOKIE_SECURE` | `false` | Sets `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`. `true` in production. |
| `TRUST_PROXY_HEADERS` | `false` | Trust `X-Forwarded-For` and enable the per-IP throttle dimension. |
| `ALLOW_INSECURE_LOOPBACK` | empty | The air-gapped, loopback-only escape (see "Refusal gates"). |
| `ALERT_WEBHOOK_URL` | empty | Static endpoint for `manage.py drain_outbox`. No CLI URL flag exists. |

### The secret key

Resolution chain: `DJANGO_SECRET_KEY` env → a generated key persisted to the
`AppSetting` table by `manage.py ensure_secret` (advisory-lock guarded, runs
after `migrate`) → an ephemeral key used only for build-time `collectstatic`.
The DB-persisted fallback is **demo posture** and logs a startup warning. The
production checklist requires the environment key.

A process that **serves traffic never runs on the build-time key**: the WSGI
entry point adopts the DB-persisted key when the environment does not provide
one, and the entrypoint's `check_posture` gate (which runs after
`ensure_secret`) refuses to boot when neither a real key nor a persisted one
exists. `manage.py collectstatic` and `bake_render_stack` are the only
commands that legitimately use the ephemeral constant.

`pg_dump` includes `AppSetting` and therefore the DB-persisted key: **treat any
`pg_dump` output as sensitive** and rotate `DJANGO_SECRET_KEY` after a leak.
`manage.py export_ledger` deliberately **excludes** `AppSetting`.

## Quick start — running it

**Prerequisites:** Python 3.12 (or Docker) and PostgreSQL 16. Nothing else —
no Node build step, no Redis, no Celery.

**The URL:** the product serves at <http://localhost:8000/> (the container maps
`-p 8000:8000`; the bare-metal path uses `PORT=8000`). Sign in at
<http://localhost:8000/login/>; `/dashboard/` is the readiness board and
`/guide/` is the product reference.

**Stopping it:** `docker stop hydrantproof` (and `docker stop hydrantproof-db`),
or `Ctrl-C` in the foreground shell that is running `./entrypoint.sh`.

**Where the logs are:** the container's stdout (`docker logs -f hydrantproof`)
— the entrypoint prints one dated line per boot step (`posture: static gate
passed`, `database reachable`, `migrations applied`, `job sweep complete`), then
gunicorn's access and error log. On bare metal the same lines go to the
terminal; gunicorn writes access lines to stdout and errors to stderr. The
three common startup failures and what they mean:

| Symptom | Meaning |
|---|---|
| `FATAL: database did not become reachable within 150 s` | `DATABASE_URL` is wrong, or Postgres is not up yet |
| `posture:` gate exits non-zero | a refusal gate found a published demo password or an unset secret in a posture that forbids it — the message names the check |
| `DisallowedHost` in the log | `ALLOWED_HOSTS` does not include the host you opened; set it, or use `ALLOW_INSECURE_LOOPBACK=true` for loopback only |

### Docker (recommended)

```bash
docker network create hydrantproof || true
docker run -d --name hydrantproof-db --network hydrantproof \
  -e POSTGRES_USER=hydrantproof -e POSTGRES_PASSWORD=hydrantproof \
  -e POSTGRES_DB=hydrantproof -v hydrantproof_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine

docker build -t hydrantproof .
docker run -d --name hydrantproof --network hydrantproof -p 8000:8000 \
  -e DATABASE_URL=postgres://hydrantproof:hydrantproof@hydrantproof-db:5432/hydrantproof \
  -e SEED_DEMO=true -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e ALLOW_INSECURE_LOOPBACK=true \
  -v hydrantproof_media:/app/media \
  hydrantproof
```

Then open <http://localhost:8000/> and sign in with the demo credentials
(below). The container's entrypoint runs the full fixed order: refusal gates →
database wait → `migrate` → `ensure_secret` → data gate → `recover_jobs` →
`collectstatic` (only if the image's assets are absent) → `seed_demo` → media
ownership hand-off → gunicorn.

### Bare metal

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof
export ALLOWED_HOSTS=localhost,127.0.0.1 ALLOW_INSECURE_LOOPBACK=true
python manage.py migrate
python manage.py ensure_secret
python manage.py recover_jobs
python manage.py seed_demo          # demo data only
PORT=8000 ./entrypoint.sh
```

### Tests

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof_test \
  python -m pytest tests/ -q --tb=short -m "not scale"
```

The suite fails loudly when `DATABASE_URL` is unset — the database groups are
the point of the gate, not an optional extra.

The scale group runs separately, inside the proof container's enforced
envelope (a missing cgroup envelope is a failure under the flag):

```bash
DATABASE_URL=... HYDRANTPROOF_SCALE_TIMING=1 HYDRANTPROOF_SCALE_ENFORCE=1 \
  python -m pytest tests/test_scale.py -q --tb=short
```

## Coverage runs, evidence packs, and imports

**Coverage runs** are asynchronous: `POST /api/v1/coverage-runs/` with an
optional `{"parcel_filter": {"parcel_ids": [...]} | {"bbox": [minlon, minlat,
maxlon, maxlat]}}` returns `202` with a `poll_url`; `GET` the poll URL until
`status` is `COMPLETE` or `FAILED`. A second job while one is running — or two
simultaneous submissions — is a `409`; the single-flight rule rests on a
PostgreSQL advisory lock plus the row state, and the worker holds a 120 s
lease it heartbeats every 60 s, so a crashed worker is reaped (never a
phantom RUNNING row) and a reaped job's late writes are discarded. A run
freezes the district parameters it computed under (`params_sha256`); the
coverage page and dashboard banner flag a run as stale when the ledger or the
settings change after its snapshot.

**Evidence packs**: `POST /api/v1/evidence-packs/` with `{"run_id": N}` (a
COMPLETE run) or `{}` (a fresh run is created first). The pack renders once —
`pack.pdf` (cover, method statement, water-supply summary by the publicly
described FSRS category structure with distribution as the headline and the
flow-band proxy labelled as a proxy, a mechanically ranked gap summary, the
OOS register, a vector coverage overview, and an exceptions table) plus a zip
(`pack.pdf`, `hydrants.csv`, `coverage_worksheet.csv`, `parcel_summary.csv`,
`oos_register.csv`, `manifest.txt`, optional `attachments/`) — stores both
files, and serves exactly those bytes on download. A pruned pack is `404`; a
missing stored file is `410`; nothing is ever regenerated under an existing
id. Downloads are audited (`ExportAudit`).

**Two-phase imports**: `POST /api/v1/imports/{hydrants|parcels|flow-tests}/`
with `mode=dry_run` and a CSV `file` validates the whole file, stores it, and
returns the row report plus a one-shot `commit_token` (24 h, bound to your
user). Re-POST with `mode=commit` and the token to apply — optionally with
the file attached again, whose SHA-256 must match the dry-run's
(`file_hash_mismatch` otherwise). Blank optional cells leave existing values
unchanged; a row whose record belongs to a different supplier is skipped with
`CONFLICT` unless the batch sets `allow_overwrite=true` (recorded, and every
overwritten field is audited as `import_overwrite_conflict`). Header templates
ship at `/static/ledger/import_templates/`. The `/field/` page's
queued-readings CSV export imports through `flow-tests` unchanged.

**Map overlay**: `POST /api/v1/overlays/` (multipart `file`, `editor`+)
validates a GeoJSON FeatureCollection (≤ 5 MB, ≤ 20,000 features, coordinates
in range, Point/LineString/Polygon/Multi* geometries only) and replaces the
active overlay; `GET /api/v1/overlays/active/` serves it as
`application/geo+json`; `DELETE /api/v1/overlays/{id}/` removes it.

## Demo mode

`SEED_DEMO=true` (the proof/preview default) seeds 24 hydrants, ~60 parcels,
~40 flow tests, inspections, and one sample overlay. Every seeded row carries
`is_sample_data=true` and a persistent banner shows while sample rows exist.

* `demo-editor` / `demo-viewer` — password `hydrantproof-demo` (**demo mode
  only; the password is published, so it must never guard real data**).
* `demo-admin` — a random one-time password printed once to the container log.

**Named deactivation hook.** `ledger/signals.py::deactivate_demo_users_on_first_real_row`
is a `post_save` receiver on `Hydrant`, `FlowTest`, `OutletReading`,
`Inspection` and `Parcel`. The moment a non-sample row is committed, all demo
users are deactivated **in the same transaction** and the `demo_deactivated_at`
marker is written. The published-password account cannot survive contact with
real data.

**Purge.** `python manage.py purge_demo` deletes every sample row and file and
the demo users. It refuses (exit 1, no data changes) if any non-sample row
exists — but even when refusing it deactivates the demo users. Seeding stays
off until `python manage.py clear_purge_marker`.

## Production posture checklist

1. **Secret key** — set `DJANGO_SECRET_KEY` (e.g.
   `python -c 'import secrets; print(secrets.token_urlsafe(64))'`). Never ship
   the DB-persisted demo key.
2. **Hosts** — set `ALLOWED_HOSTS` to the exact hostnames the district uses.
3. **Demo off** — `SEED_DEMO=false` (and `purge_demo` if demo data was ever
   loaded).
4. **Cookies** — `COOKIE_SECURE=true`, and terminate TLS at a reverse proxy.
   HSTS belongs at the proxy layer.
5. **Proxy headers** — `TRUST_PROXY_HEADERS=true` **only** behind a
   header-stripping TLS proxy that overwrites `X-Forwarded-For`. This is the
   switch that activates the per-IP throttle dimension. With the default
   `false` the per-IP dimension is off and **the username-keyed throttle is
   the only spray defence** — that is the documented trade-off.
6. **Second admin before go-live** — create a real account
   (`python manage.py createuser <name> --role admin`) and stop using the demo
   accounts.
7. **CSRF origins** — set `CSRF_TRUSTED_ORIGINS` to the public origin.

### Refusal gates (enforced, not warned)

The entrypoint refuses to start when `ALLOWED_HOSTS=*` or
`COOKIE_SECURE=false` while `SEED_DEMO=false` **or** any non-sample ledger row
exists. The documented air-gapped, loopback-only escape is
`ALLOW_INSECURE_LOOPBACK=true`, which waives **both** gates and prints a loud
banner naming them. The entrypoint also warns whenever
`TRUST_PROXY_HEADERS=false` while non-demo data exists.

### Reverse proxy sketch (nginx)

```nginx
server {
  listen 443 ssl;
  server_name hydrants.example.gov;
  ssl_certificate     /etc/ssl/hydrantproof.crt;
  ssl_certificate_key /etc/ssl/hydrantproof.key;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;   # OVERWRITE, never append
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

## Backup and restore

Two objects must be backed up together:

1. **The database** — `pg_dump -Fc hydrantproof > hydrantproof.dump`.
   **Treat dumps as sensitive**: they include `AppSetting`, which may hold the
   DB-persisted secret key.
2. **The media volume** (`hydrantproof_media`: attachments, overlays, stored
   pack bytes, stored import files). A `pg_dump`-only restore leaves
   `Attachment`, `MapOverlay` and `EvidencePack` rows pointing at missing
   files; attachment files are unrecoverable, and pack files can only be
   regenerated as *new* packs from retained runs.

A redacted, human-readable JSON export is available for migration between
districts: `python manage.py export_ledger --output ledger.json` (excludes
`AppSetting`; includes user tables because password hashes are required for
restore). Restore with `python manage.py import_ledger ledger.json --confirm`.

## Scheduled jobs (cron)

```cron
# Nightly: enqueue notification rows (idempotent under (hydrant, kind, as_of_date))
15 2 * * *  cd /app && python manage.py enqueue_notifications
# Nightly: deliver unsent rows to ALERT_WEBHOOK_URL (10 s timeout, ≤5 attempts)
30 2 * * *  cd /app && python manage.py drain_outbox
# Monthly: budgets, the expired-lease reaper, FAILED-run retention, and recover_jobs
0 3 1 * *   cd /app && python manage.py prune_data && python manage.py recover_jobs
```

`prune_data` doubles as the job reaper: it marks jobs whose lease expired
`FAILED (orphaned)` and cleans their partial rows and files. A job with a live
heartbeat is never reaped.

## Basemap

The container is offline by contract. The map ships with **vendored Leaflet**
(see `ledger/static/vendor/leaflet/VENDOR.md` for provenance), a CSS-grid
graticule, class-coloured markers, protected-parcel squares, a 1000-ft ring
and a scale control. To add a basemap, set `MAP_TILE_URL` (and
`MAP_TILE_ATTRIBUTION`) on a connected host:

```bash
MAP_TILE_URL="https://tile.example.gov/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION="© Example Tile Service"
```

Tiles are fetched **by the browser**, never by the server. When the URL is
empty the page shows a visible notice and the graticule remains usable. An
`editor`+ can also upload a validated GeoJSON overlay (≤ 5 MB, ≤ 20,000
features) as an offline reference layer.

## Operational notes and limitations

* **Hydrant locations are sensitive infrastructure data.** Do not expose this
  service without TLS, and do not publish exports.
* **Latitude/antimeridian limits** — coordinates are validated to |lat| ≤ 80
  and |lon| ≤ 180; a district crossing the antimeridian is not supported by
  the coverage prefilter and is out of scope for v0.
* **Attachment metadata** — uploads are content-sniffed (JPEG/PNG/WebP), then
  re-encoded to JPEG ≤ 1600 px with EXIF **stripped** as a privacy choice.
  Capture metadata (EXIF `DateTimeOriginal`, GPS when present, plus the
  client's `gps_accuracy_m` and `captured_at`) is preserved in the database
  **before** the strip.
* **One district per installation.** `export_ledger` is the inter-district
  path; multi-district tenancy is a named roadmap item.
* **No runtime outbound HTTP from the server process** — the single exception
  is the customer-host `drain_outbox` command, which posts to the static
  `ALERT_WEBHOOK_URL` you configure. It is never exercised in proof or
  preview.

## Documentation

| Guide | Audience | Contents |
|---|---|---|
| [Product guide](docs/USER_GUIDE.md) | District staff | Sign-in, the full records-to-pack journey, field capture, restart/recovery, every common error with its fix |
| [Operations guide](docs/OPERATIONS.md) | Operator | Install, configure, run, deploy, monitor, debug |
| [Architecture](docs/ARCHITECTURE.md) | Engineer | C4 diagrams, module map, data model, design decisions |
| [API reference](docs/API.md) | Integrator | Every `/api/v1/` endpoint with request/response shapes and curl examples |
| [UI reference](docs/UI.md) | Designer / operator | Screens, flows, loading/empty/error states, API-to-UI mapping |
| [Runnable examples](docs/examples/README.md) | Everyone | Five examples: offline arithmetic, curl walkthrough, two-phase import, ledger export, Python client |

## Licence

MIT — © 2026 the Sneferu Open Builds program; see `LICENSE`. The vendored
Leaflet distribution is BSD 2-Clause (see
`ledger/static/vendor/leaflet/LICENSE`).
