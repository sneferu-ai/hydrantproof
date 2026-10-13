# HydrantProof — Architecture

**Audience:** engineers who need to understand how the system fits together,
change it safely, or debug it in production. For installing and running the
system see [`OPERATIONS.md`](OPERATIONS.md); for the HTTP contract see
[`API.md`](API.md).

---

## 1. Project shape

HydrantProof is a **self-hosted Django web application** — a hybrid of four
surfaces served by one process:

| Surface | What it is |
|---|---|
| Server-rendered UI | Django templates at `/`, `/dashboard/`, `/hydrants/`, `/hydrants/{id}/`, `/coverage/`, `/packs/`, `/field/`, `/login/` |
| JSON API | Django REST Framework at `/api/v1/…` (39 paths), plus the anonymous OpenAPI schema at `/openapi.json` |
| Asynchronous jobs | Coverage runs and evidence-pack renders, executed on an in-process `ThreadPoolExecutor(max_workers=1)` under a database lease |
| Management CLI | `manage.py` commands for accounts, seeding, imports/exports, notifications, pruning, and recovery |

There is **no frontend build step, no Redis, no Celery, no Node, and no
PostGIS**. One Python process (gunicorn, 2 workers by default) plus PostgreSQL
serves everything; static assets are collected at image build time and served
by WhiteNoise.

The screen inventory with per-screen purpose, the user-flow diagrams, and the
UI component (template) hierarchy live in [`UI.md`](UI.md); because there is
no build step, the UI's component model *is* the template tree plus the two
page scripts.

---

## 2. C4 Context diagram

Who and what surrounds the system. Note what is deliberately *not* connected:
there is no ISO/Verisk integration (no points are computed), and the server
makes no outbound HTTP calls except the customer-hosted `drain_outbox`
command.

```mermaid
C4Context
  title System Context — HydrantProof (one district per installation)

  Person(staff, "District staff", "Fire chief, officers, water-supply clerk")
  Person(operator, "District operator", "Installs and administers the server")

  System(hp, "HydrantProof", "Self-hosted water-supply records: registry, NFPA 291 flow ledger, inspections, readiness, coverage worksheet, evidence packs")

  System_Ext(browser, "Web browser", "Desktop or field phone/tablet")
  System_Ext(pg, "PostgreSQL 16", "The district's database (declared service)")
  System_Ext(media, "Media volume", "Attachments, overlays, stored pack bytes, stored import files")
  System_Ext(webhook, "Alert webhook", "Optional static HTTPS endpoint; contacted only by manage.py drain_outbox on the customer host")
  System_Ext(iso, "ISO / Verisk", "PPC classification authority — intentionally NOT integrated; no scores or point values are computed")

  Rel(staff, browser, "Uses")
  Rel(browser, hp, "HTTP(S): pages, forms, JSON API, downloads")
  Rel(operator, hp, "manage.py commands, admin site")
  Rel(hp, pg, "SQL (psycopg 3)")
  Rel(hp, media, "File reads/writes")
  Rel(hp, webhook, "POST notification rows", "only when ALERT_WEBHOOK_URL is set")
  Rel(staff, iso, "Requests the official classification", "off-system")
```

---

## 3. C4 Container diagram

One application container, one database, one persistent volume. The job
executor lives *inside* the web process (there is no separate worker
container) — this is the deliberate "the row is the queue" design.

```mermaid
C4Container
  title Container diagram — HydrantProof

  Person(staff, "District staff", "Editor / viewer / admin roles")

  Container(web, "HydrantProof application", "Python 3.12 · Django 5.2 · DRF · gunicorn", "Serves HTML pages, the /api/v1 JSON API, printable PDFs, and runs coverage/pack jobs on an in-process leased thread pool")
  ContainerDb(db, "PostgreSQL 16", "PostgreSQL", "Ledger tables (Tier A append-only + lifecycle), settings, job rows, audit rows")
  ContainerDb(mediavol, "Media volume", "Filesystem (/data/hydrantproof_media → /app/media)", "attachments/, overlays/, packs/{id}/pack.zip+pack.pdf, imports/{batch}.csv")

  Rel(staff, web, "HTTPS", "pages · forms · JSON API · downloads")
  Rel(web, db, "SQL", "psycopg 3 · one connection per request · advisory locks for job gates")
  Rel(web, mediavol, "Reads/writes files", "re-encoded JPEGs · GeoJSON · stored pack bytes · stored import CSVs")
```

### Runtime topology (Docker)

```mermaid
flowchart LR
  accTitle: Runtime topology (Docker)
  accDescr: One application container runs the entrypoint, gunicorn, WhiteNoise and the in-process job executor; a PostgreSQL container and a media volume sit beside it; the browser talks HTTP to gunicorn.
  subgraph host["District host (air-gap capable)"]
    subgraph appc["container: hydrantproof"]
      EP["entrypoint.sh<br/>gates → migrate → ensure_secret<br/>→ recover_jobs → seed → gunicorn"]
      G["gunicorn (no --preload)<br/>2 workers default"]
      EX["ThreadPoolExecutor(max_workers=1)<br/>coverage + pack jobs"]
      WN["WhiteNoise<br/>static files"]
    end
    DB[("container: postgres:16-alpine<br/>volume: pgdata")]
    MV[("volume: hydrantproof_media<br/>/data/hydrantproof_media")]
  end
  B["Browser"] -- ":8000 HTTP" --> G
  G --> WN
  G --> DB
  G --> MV
  EX --> DB
  EX --> MV
```

---

## 4. Component (module) diagram

The `ledger` Django app is the whole product. Modules, with their actual
responsibilities:

```mermaid
flowchart TB
  accTitle: Component map of the ledger Django app
  accDescr: Entry points route into the API layer, the pure domain core, the job plane, and the data and auth modules; arrows show the real import dependencies.
  subgraph entry["Entry points"]
    UR["hydrantproof/urls.py<br/>root routing"]
    URA["ledger/urls_api.py<br/>/api/v1 route table"]
    URH["ledger/urls.py + views.py<br/>HTML routes + login/logout/sw.js"]
    MG["ledger/management/commands/*<br/>14 manage.py commands"]
  end

  subgraph api["API layer"]
    API["api.py<br/>hydrants, tests, inspections,<br/>deficiencies, status events, parcels"]
    APJ["api_jobs.py<br/>coverage runs + evidence packs<br/>+ single-flight gate"]
    APF["api_files.py<br/>overlays + two-phase CSV imports<br/>+ ledger export"]
    EXP["exports.py<br/>dispatch CSV + printable PDF forms"]
  end

  subgraph domain["Domain core (no Django imports where marked)"]
    HYD["hydraulics.py ★ pure<br/>NFPA 291 arithmetic + edge matrix"]
    DER["derive.py ★ pure<br/>the ONE canonical derivation"]
    COV["coverage.py ★ pure<br/>credit rule, prefilter, bands, params hash"]
    GEO["geo.py ★ pure<br/>haversine, windows, coordinate limits"]
    SRV["services.py<br/>transactional writes + invariants"]
    IMP["imports.py<br/>CSV parse/validate/apply"]
    MED["media.py<br/>sniff/re-encode/budget/audit"]
    NOT["notifications.py<br/>outbox enqueue/drain/ranked list"]
    STALE["staleness.py<br/>snapshot_ts staleness"]
  end

  subgraph jobs["Job plane"]
    EXE["executor.py<br/>claim → snapshot → compute → conditional complete"]
    JOB["jobs.py<br/>leases, reaper, boot sweep"]
    RUN["coverage_runner.py<br/>snapshot inputs (REPEATABLE READ)"]
    PACK["pack_render.py<br/>PDF + CSV zip + manifest + stored bytes"]
    RS["render_stack.py<br/>baked render-stack facts"]
  end

  subgraph data["Data & auth"]
    MOD["models.py<br/>three-tier model + triggers (migration 0003)"]
    AUTH["auth.py<br/>throttle, roles, JSON error contract"]
    SIG["signals.py<br/>demo-deactivation invariant"]
    MID["middleware.py<br/>12 h absolute session cap"]
  end

  URA --> API & APJ & APF & EXP
  URH --> SRV
  MG --> SRV & NOT & JOB & MED & IMP
  API --> SRV & HYD & DER
  APJ --> COV & EXE & JOB
  APF --> IMP & MED
  EXP --> DER
  SRV --> DER & HYD & MOD
  COV --> DER & GEO
  DER --> HYD
  EXE --> RUN & PACK & JOB
  RUN --> COV
  PACK --> COV & RS & MED
  AUTH --> MOD
  SIG --> MOD
  STALE --> COV
```

★ = pure Python modules, unit-tested without a database.

---

## 5. Primary flow — record a test, run coverage, ship a pack

One sequence covering the two asynchronous jobs and the derivation that every
surface shares.

```mermaid
sequenceDiagram
  accTitle: Primary flow — record a test, run coverage, ship a pack
  accDescr: A synchronous flow-test write, then two asynchronous database-leased jobs — a coverage run and a one-time evidence-pack render whose stored bytes are served on download.
  autonumber
  actor Staff as District staff
  participant UI as Django UI/API
  participant Svc as services.py
  participant DB as PostgreSQL
  participant Job as Job executor (thread)
  participant FS as Media volume

  Note over Staff,FS: Record a flow test (synchronous)
  Staff->>UI: POST /api/v1/hydrants/{id}/tests/ (static, residual, outlets)
  UI->>Svc: create_flow_test(...)
  Svc->>Svc: compute_flow_test() → Q_F, Q_R, class, warnings, formula_version
  Svc->>DB: INSERT FlowTest + OutletReading rows (append-only) + refresh current_test_ref
  UI-->>Staff: 201 with the computed snapshot

  Note over Staff,FS: Run coverage (asynchronous, DB-leased)
  Staff->>UI: POST /api/v1/coverage-runs/ {parcel_filter}
  UI->>DB: pg_try_advisory_xact_lock(4242) + refuse while a job is in flight
  UI->>DB: INSERT CoverageRun (RUNNING, frozen params, params_sha256)
  UI-->>Staff: 202 {id, poll_url}
  Job->>DB: claim row (worker_id + 120 s lease)
  Job->>DB: read inputs in one REPEATABLE READ txn (snapshot_ts)
  Job->>Job: iter_coverage() per parcel — credit rule under FROZEN params
  Job->>DB: bulk_create CoverageResult (10k batches) + heartbeat every 60 s
  Job->>DB: conditional UPDATE → COMPLETE (zero rows ⇒ reaped ⇒ rollback)
  Staff->>UI: GET poll_url → status COMPLETE + counts + staleness

  Note over Staff,FS: Generate the evidence pack (asynchronous, stored once)
  Staff->>UI: POST /api/v1/evidence-packs/ {run_id, include_attachments}
  UI->>DB: single-flight gate → INSERT EvidencePack (RENDERING)
  UI-->>Staff: 202 {id, poll_url}
  Job->>Job: render_pack() → PDF + 4 CSVs + manifest.txt → zip
  Job->>FS: write media/packs/{id}/pack.zip + pack.pdf
  Job->>DB: conditional UPDATE → COMPLETE (+ sha256, page_count, byte_size)
  Staff->>UI: GET /evidence-packs/{id}/zip/ → stored bytes (never regenerated)
```

---

## 6. Data model

Three tiers with different mutation rules, enforced in three places (ORM,
routes, and — for Tier A — database triggers):

* **Tier A — append-only truth tables (8).** `FlowTest`, `OutletReading`,
  `HydrantStatusEvent`, `HydrantAttributeChange`, `ParcelAttributeChange`,
  `Inspection`, `OosAcknowledgement`, `SettingsAudit`. A PostgreSQL
  `BEFORE UPDATE OR DELETE` trigger (migration `0003_tier_a_triggers`) refuses
  mutation; the ORM `AppendOnlyModel.save()/delete()` raises too. Corrections
  are new rows (supersession, reversal, acknowledgement).
* **Tier B — lifecycle tables.** `Deficiency`, `Attachment`, `Parcel`,
  `CoverageRun`, `CoverageResult`, `EvidencePack`, `MapOverlay`,
  `ImportBatch`, `NotificationOutbox`, `AppSetting`, `LoginAttempt`,
  `ExportAudit`, `MediaAudit`. No DB trigger; only the enumerated route
  mutations exist, and they are tested.
* **Tier C — mutable core.** `Hydrant`, `DistrictSettings`, users.

```mermaid
erDiagram
  accTitle: Data model — three mutation tiers
  accDescr: Append-only ledger tables, lifecycle tables, and the mutable core, drawn with the relationships every query and derivation relies on.
  DistrictSettings ||--o{ CoverageRun : "frozen into (settings_echo)"
  Hydrant ||--o{ FlowTest : "graded hydrant"
  Hydrant ||--o{ OutletReading : "flowed hydrant (nullable tag)"
  FlowTest ||--o{ OutletReading : "1..4 outlets"
  FlowTest ||--o| FlowTest : "supersedes (correction chain)"
  Hydrant ||--o{ Inspection : "append-only"
  Inspection ||--o{ Deficiency : "opens rows (SET_NULL)"
  Hydrant ||--o{ Deficiency : "single source of truth"
  Hydrant ||--o{ HydrantStatusEvent : "append-only lifecycle"
  Hydrant ||--o{ OosAcknowledgement : "expired-OOS ack"
  FlowTest ||--o{ Attachment : "photos (owner_type)"
  Inspection ||--o{ Attachment : "photos (owner_type)"
  Parcel ||--o{ CoverageResult : "per-parcel rows"
  Hydrant ||--o{ CoverageResult : "per-pair rows (nullable)"
  CoverageRun ||--o{ CoverageResult : "one worksheet"
  CoverageRun ||--o{ EvidencePack : "rendered from (PROTECT)"
  ImportBatch ||--o{ Hydrant : "creates/updates (audited)"
```

Key column truths worth knowing before you query:

* `Hydrant.current_test_ref` is a denormalized pointer to the current test —
  **max `(tested_on, created_at, id)` among non-superseded tests** — maintained
  under a row lock in `services.refresh_current_test_ref`.
* `CoverageRun.params_sha256` = SHA-256 of the canonical
  `{constants, settings, parcel_filter}` JSON; `core_params_sha256` is the same
  payload with `parcel_filter: null` (the live staleness comparison key).
  `snapshot_ts` anchors staleness to the run's `REPEATABLE READ` transaction.
* `CoverageResult.distance_ft` is **unrounded**; display rounds to 0.1 ft.
* `EvidencePack` stores `pdf_sha256` / `csv_zip_sha256` and the file paths
  under `media/packs/{id}/`.
* `Attachment.sha256`/`byte_size` describe the **stored** (re-encoded) bytes;
  `original_content_type` is the sniffed upload type; `capture_meta` keeps the
  pre-strip EXIF facts.
* `NotificationOutbox` is unique on `(hydrant, kind, as_of_date)` — the
  idempotency key for nightly enqueueing.

---

## 7. Key design decisions (and why)

1. **One canonical derivation.** Every surface — list badges, dispatch CSV,
   worksheet, pack, trend — reads `derive.derive_hydrant()` and nothing else
   (`ledger/derive.py`). A readiness rule can never drift between two views.
2. **Compute once, at write time.** `hydraulics.compute_flow_test()` runs when
   the test is saved; the result (`Q_F`, `Q_R`, class, warnings,
   `formula_version`) is stored. Nothing recomputes lazily, so a historical
   pack is reproducible from stored rows.
3. **The row is the queue.** Coverage/pack jobs are database rows with a
   worker id and a 120-second lease, heartbeated every 60 seconds; the reaper
   keys on **expired lease**, never wall-clock age. A reaped job's late writes
   are discarded by a conditional final `UPDATE … WHERE worker_id = me`
   (`executor.py`). Single-flight rests on a PostgreSQL advisory lock
   (`pg_try_advisory_xact_lock(4242)`) plus the row state — never on the pool.
4. **Pack bytes are stored, never regenerated.** A download serves exactly the
   bytes rendered once; a pruned pack is 404, a missing file 410.
5. **Append-only truth with database triggers.** Integrity rules live in the
   database (Tier A triggers) *and* the ORM *and* the route table — three
   layers, because the audit trail is the product.
6. **Offline-first by contract.** The container performs no runtime outbound
   HTTP except the customer-hosted `drain_outbox`; map tiles are fetched by
   the **browser** (`MAP_TILE_URL`, default empty), Leaflet is vendored, PDFs
   are drawn with ReportLab base-14 fonts only, and static assets are served
   by WhiteNoise with no CDN.
7. **Two auth surfaces, one throttle.** Session auth for pages, HTTP Basic
   scoped to `/api/v1/` (CSRF-exempt by construction). Three login surfaces
   (form, Basic 401, admin) write `LoginAttempt` rows; the throttle accepts
   valid credentials up to a per-username cap during a window rather than
   locking out, closing the classic lockout-DoS without opening a hash-CPU
   DoS (bounded by a process-wide semaphore of 2, 5 s timeout).
8. **Refusal gates, not warnings.** The entrypoint refuses to boot an insecure
   posture (`ALLOWED_HOSTS=*` or `COOKIE_SECURE=false`) once demo seeding is
   off or real rows exist, unless the operator explicitly waives with
   `ALLOW_INSECURE_LOOPBACK=true` for an air-gapped loopback instance.
9. **Demo data cannot survive real data.** A `post_save` signal deactivates
   all demo accounts in the same transaction as the first non-sample row.
10. **Provisional constants are labeled, not hidden.** Grounded arithmetic
    (29.83, 20 psi, 0.54 exponent) is separated from provisional,
    verification-gated policy (extrapolation factor, trend threshold, bands)
    in `constants.py`; every PDF footer and manifest repeats the banner.
11. **Coverage determinism.** A conservative latitude-bisect prefilter plus an
    exact haversine on survivors; inclusion compares the **unrounded** distance
    against the radius, so a 999.5 ft pair is in and a 1000.5 ft pair is out,
    identically on every run.
12. **CSV-injection neutralization** applies to text-typed cells in every CSV
    export (`csvio.neutralize_cell`): a leading `=`, `+`, `-`, `@`, tab or CR
    is prefixed with a quote.
13. **One media budget, one attachment sub-budget.** Enforced at every write
    path (upload pre-flight, pack pre-flight, prune backstop) against the
    `DistrictSettings` limits.

---

## 8. Deployment / runtime model

**Declared services** (`runtime-services.json`): a PostgreSQL 16 service
(`db`) and one persistent storage mount `hydrantproof_media` at
`/data/hydrantproof_media`, linked to the application's fixed media path
(`/app/media`). The container image is single-stage, base-pinned by digest,
and runs as a non-root `app` user when started as root.

**Boot order** (`entrypoint.sh`, fixed and idempotent):

1. `manage.py check_posture --static` — static refusal gates (no DB needed).
2. Database wait — up to 150 s (30 × 5 s), then a fatal exit.
3. `manage.py migrate --noinput`.
4. `manage.py ensure_secret` — resolves the secret key; exports it for the
   process if only the DB-persisted key exists (demo posture, logged).
5. `manage.py check_posture` — the data-bearing gate (refuses on a non-sample
   ledger in an insecure posture, and refuses to serve with the public
   build-time secret key).
6. `manage.py recover_jobs` — every RUNNING/RENDERING row becomes
   `FAILED (orphaned)`; partial rows and files are cleaned. No phantom jobs
   survive a restart.
7. `collectstatic` if the manifest is absent (image builds collect it earlier).
8. `manage.py seed_demo` when `SEED_DEMO=true` (idempotent; refuses if the
   ledger is not empty or the purge marker is set).
9. **Media ownership hand-off** — when the entrypoint runs as root, it creates
   the media root (`HYDRANTPROOF_MEDIA_ROOT`, default `/app/media` — the
   container's `MEDIA_ROOT`, which Django derives from `<repo>/media` where
   `<repo>` is `/app`) and `chown -R app:app`'d it, so the unprivileged
   workers can write packs, attachments, overlays and stored import files. A
   fresh named volume is also pre-owned because the image ships `/app/media`
   owned by `app`.
10. `gunicorn hydrantproof.wsgi:application` — **without `--preload`** so the
    lazily-constructed job executor can never be forked into a broken pool.

**Scaling envelope.** The spec's target is a district-scale ledger (the scale
test group documents the timing envelope). The list endpoint pages at the
database and derives only the 50-row page; derived-field filters
(`readiness`/`class`/`recency`) evaluate every row on demand and are the known
worst case. Jobs are single-flight by design.

---

## 9. Security posture (as built)

* Roles: `admin` / `editor` / `viewer` groups; superusers count as admin.
  Route-level permissions are `CanWrite` (editor+) and `IsAdmin`.
* Anonymous surface is exactly enumerated and test-pinned:
  `/healthz`, `/login/`, `/logout/`, `/admin/login/`, `/sw.js`,
  `/openapi.json`, and `/static/…`.
* Coordinates are validated to |lat| ≤ 80, |lon| ≤ 180; overlay GeoJSON is
  geometry-allow-listed and coordinate-checked; attachment uploads are
  content-sniffed (extension ignored), size-capped, re-encoded with EXIF
  stripped, and served with `X-Content-Type-Options: nosniff`.
* Sessions: 2 h idle + 12 h absolute; cookies can be marked `Secure` via
  `COOKIE_SECURE`; CSRF is enforced on all unsafe HTML methods.
* The ledger export (`/api/v1/exports/ledger/`) is **admin-only** because it
  carries password hashes for restore; it excludes `AppSetting` (the
  DB-persisted secret key never leaves in a JSON backup).
* Throttling covers the login form, HTTP Basic 401s, and the Django admin
  login; `TRUST_PROXY_HEADERS=true` (only behind a header-stripping proxy)
  activates the per-IP dimension.
* Known limits are stated, not hidden: field capture is proof of records, not
  proof of presence; the pack manifest is self-attested, not cryptographically
  signed; the v0 file sandbox is the container itself.
