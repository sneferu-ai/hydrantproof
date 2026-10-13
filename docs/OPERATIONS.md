# HydrantProof — Operations Guide

**Audience:** the operator who installs, runs, monitors, and debugs a district's
HydrantProof instance. For product use see [`USER_GUIDE.md`](USER_GUIDE.md);
for architecture see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. System requirements

| Component | Requirement | Notes |
|---|---|---|
| Python | **3.12** | `python:3.12-slim-bookworm` is the pinned container base (by digest, in the `Dockerfile` `FROM` line — the single source of the pin) |
| PostgreSQL | **16** (13+ likely works; 16 is what is tested) | The declared `db` service; no extensions required (no PostGIS) |
| Disk | Database + media volume (attachments, overlays, stored pack bytes, stored import files; default media budget 200 MB) | Budgets configurable in district settings |
| Node / Redis / Celery | **Not required — by design** | Static assets are collected at image build; jobs run in-process |
| Network | Outbound HTTP is **not required at runtime** | The one exception is the operator-run `manage.py drain_outbox` (static `ALERT_WEBHOOK_URL`) |
| Browser | Any current browser | `/field/` is mobile-first; the map degrades gracefully offline |

Runtime dependencies are pinned exactly (`==`) in `requirements.txt`
(Django 5.2.17, DRF 3.18.1, drf-spectacular 0.30.0, psycopg[binary] 3.3.6,
dj-database-url 3.1.2, gunicorn 26.2.0, whitenoise 6.12.0, reportlab 5.0.1,
Pillow 12.3.0). `requirements-dev.txt` adds the test tools.

---

## 2. Environment variables — the complete list

Every configuration value is an environment variable; none of them contains a
secret in this repository. Defaults are shown as `name=default`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **yes** (production) | `postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof` | PostgreSQL connection URL (`postgres://user:pass@host:port/db`). The declared `db` service provides it. |
| `DJANGO_SECRET_KEY` | **yes** (production) | generated fallback (see §3) | Django secret key. Set a long random value; rotate after any leak. |
| `PORT` | no | `8000` | Port gunicorn binds. |
| `ALLOWED_HOSTS` | no (demo default `*`) | `*` | Comma-separated hostnames. **Restrict in production** — the refusal gate blocks `*` once demo mode is off or real rows exist. |
| `DJANGO_DEBUG` | no | `false` | Never enable in production. |
| `CSRF_TRUSTED_ORIGINS` | no | empty | Comma-separated origins, e.g. `https://hydrants.example.gov`. Required when the public origin differs from the request host (HTTPS proxy). |
| `SEED_DEMO` | no | `true` | Seed the demo district on first boot. Set `false` in production. |
| `COOKIE_SECURE` | no | `false` | Sets `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`. `true` in production. |
| `TRUST_PROXY_HEADERS` | no | `false` | Trust `X-Forwarded-For` and activate the per-IP throttle dimension. Enable **only** behind a header-stripping TLS proxy. |
| `ALLOW_INSECURE_LOOPBACK` | no | empty | The documented air-gapped escape: waives **both** refusal gates (`ALLOWED_HOSTS=*`, `COOKIE_SECURE=false`) and prints a loud banner. Never set on a networked host. |
| `MAP_TILE_URL` | no | empty | Browser-side basemap tile template (e.g. `https://tiles.example.gov/{z}/{x}/{y}.png`). Empty = graticule-only map (still usable). |
| `MAP_TILE_ATTRIBUTION` | no | empty | Attribution string rendered with the basemap. |
| `ALERT_WEBHOOK_URL` | no | empty | Static endpoint for `manage.py drain_outbox`. **There is no CLI URL flag by design.** |
| `HYDRANTPROOF_MEDIA_ROOT` | no | `<repo>/media` | Media root override. In the container the managed volume is linked to `/app/media`. |
| `DJANGO_LOG_LEVEL` | no | `INFO` | Log level for the root and `ledger` loggers. |
| `GUNICORN_WORKERS` | no | `2` | gunicorn worker count (entrypoint). |
| `GUNICORN_TIMEOUT` | no | `120` | gunicorn worker timeout in seconds (entrypoint). |
| `STATIC_ROOT` | no | *(unset)* | **Entrypoint probe only.** The entrypoint checks `${STATIC_ROOT:-staticfiles}/staticfiles.json` to decide whether assets were already collected. Django's own `STATIC_ROOT` is fixed to `<repo>/staticfiles` (hardcoded in `settings.py`) — setting the env var does **not** move the collection target, so leave it unset. |

How to obtain values: `DATABASE_URL` from your PostgreSQL service; the secret
key from `python -c 'import secrets; print(secrets.token_urlsafe(64))'`; the
rest are policy choices documented in this table.

---

## 3. The secret key resolution chain

1. `DJANGO_SECRET_KEY` **env** — the production path. The entrypoint exports it
   for the gunicorn process when set.
2. If unset, `manage.py ensure_secret` generates one and persists it to the
   `AppSetting` table (advisory-lock guarded, idempotent) and prints:
   *"secret key: generated and persisted to AppSetting — DEMO POSTURE."*
   The entrypoint then exports it so the web process and the DB agree.
3. Only if both fail (e.g. build-time `collectstatic`), an ephemeral
   build-time key is used so the image can be built.

A process that serves traffic never runs on the ephemeral key: the WSGI entry
point (`hydrantproof/wsgi.py`) adopts the DB-persisted key when the environment
does not provide one, and the entrypoint's data-bearing `check_posture` gate —
which runs **after** `ensure_secret` — refuses to boot when neither exists.

**Consequences of the DB-persisted key:** `pg_dump` output includes it — treat
dumps as sensitive and rotate the key after a leak. `manage.py export_ledger`
deliberately **excludes** `AppSetting`.

---

## 4. Installation

### Docker (recommended, the tested path)

```bash
# 1. Network + database
docker network create hydrantproof || true
docker run -d --name hydrantproof-db --network hydrantproof \
  -e POSTGRES_USER=hydrantproof -e POSTGRES_PASSWORD=hydrantproof \
  -e POSTGRES_DB=hydrantproof -v hydrantproof_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine

# 2. Build the image (base pinned by digest in the Dockerfile)
docker build -t hydrantproof .

# 3. Run (demo/loopback posture shown; see §7 for production)
docker run -d --name hydrantproof --network hydrantproof -p 8000:8000 \
  -e DATABASE_URL=postgres://hydrantproof:hydrantproof@hydrantproof-db:5432/hydrantproof \
  -e SEED_DEMO=true -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e ALLOW_INSECURE_LOOPBACK=true \
  -v hydrantproof_media:/app/media \
  hydrantproof
```

The entrypoint then runs the fixed boot order (gates → database wait up to
150 s → `migrate` → `ensure_secret` → data gate → `recover_jobs` →
`collectstatic` if needed → `seed_demo` if enabled → media ownership hand-off
→ gunicorn without `--preload`).

### Bare metal

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

export DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"

python manage.py migrate
python manage.py ensure_secret
python manage.py recover_jobs
python manage.py collectstatic --noinput        # required before the first page render
python manage.py seed_demo                      # demo data only

PORT=8000 ./entrypoint.sh
```

`./entrypoint.sh` is the supported runtime entry point on bare metal too — it
performs the same ordered boot (including `collectstatic` when the manifest is
missing) and starts gunicorn.

---

## 5. Running locally

### The real entrypoint (closest to production)

```bash
export DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
PORT=8000 ./entrypoint.sh
```

Expected log sequence: `posture: static gate passed` → `database reachable` →
`migrations applied` → `posture: data gate passed` → secret line →
`job sweep complete` → `seed_demo: …` → `media root …/media handed to app`
(only when running as root; `…` is the media root — `/app/media` in the
container) → `starting gunicorn on 0.0.0.0:8000`.

**Success looks like:**

```bash
curl -s http://localhost:8000/healthz
# {"status": "ok", "checks": {"database": "reachable", "migrations": "applied"}}
```

Then open <http://localhost:8000/> and sign in (`demo-editor` /
`hydrantproof-demo` in demo mode).

### Django dev server (`runserver`)

For template/CSS iteration:

```bash
python manage.py migrate
python manage.py ensure_secret
python manage.py collectstatic --noinput      # once; the storage backend needs the manifest
python manage.py runserver
```

> **Note:** `collectstatic` is required before the first page render even in
> dev — the static storage is `CompressedManifestStaticFilesStorage`, and a
> page that references an uncollected asset raises
> `ValueError: Missing staticfiles manifest entry for 'ledger/app.css'`
> (the login page answers 500). Re-run `collectstatic` after changing a file
> under `ledger/static/`.

### Tests

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof_test \
  python -m pytest tests/ -q --tb=short -m "not scale"
```

The suite **fails loudly when `DATABASE_URL` is unset** — the database groups
are the point of the gate, not an optional extra. The pinned gate command is
exactly the one above; on the reference environment (2026-10-05) it ran
**393 passed, 4 deselected (the `scale` group), in 5 min 25 s** — the count
varies with environment-conditional tests. The `scale` group runs separately
under an enforced container envelope:

```bash
DATABASE_URL=… HYDRANTPROOF_SCALE_TIMING=1 HYDRANTPROOF_SCALE_ENFORCE=1 \
  python -m pytest tests/test_scale.py -q --tb=short
```

### HTTP smoke test (boots the real entrypoint and walks the surface)

```bash
DATABASE_URL=… PORT=8123 sh scripts/smoke_runtime.sh
# ends with: smoke: 29 passed, 0 failed
```

It verifies healthz, the OpenAPI schema, the anonymous 401, sign-in, the
dispatch CSV, coverage → results, and pack → byte-identical zip/pdf downloads.
(Requires `gunicorn` — installed by `requirements.txt`.)

---

## 6. Deployment

### Container contract

* **Port:** `8000` (`container_port` in `packaging.json`); bind `PORT` to
  change it.
* **Declared services** (`runtime-services.json`): the `db` PostgreSQL service
  (connection env `DATABASE_URL`) and one persistent storage mount,
  `hydrantproof_media` at `/data/hydrantproof_media`, linked to the
  application's fixed media path `/app/media`.
* **No VOLUME declaration in the image, deliberately:** an image `VOLUME`
  would create an anonymous volume that shadows the managed mount, so media
  bytes would not survive container replacement. Mount a named volume or host
  path at `/app/media` (or set `HYDRANTPROOF_MEDIA_ROOT`).
* **User:** when started as root the entrypoint binds and then drops the
  gunicorn workers to the unprivileged `app` user. Before the workers start
  it creates the media root and hands it to `app` (`chown -R app:app`,
  logged as `media root … handed to app`), so a fresh named volume or a
  root-owned host mount is writable. The image also ships `/app/media` owned
  by `app`, so a new named volume inherits that ownership. A **read-only**
  media mount still fails media writes — that is the one case the entrypoint
  cannot repair.

### Production checklist

1. **Secret key** — set `DJANGO_SECRET_KEY` (never ship the DB-persisted demo
   key).
2. **Hosts** — `ALLOWED_HOSTS` = the exact hostnames the district uses.
3. **Demo off** — `SEED_DEMO=false`; if demo data was ever loaded, run
   `python manage.py purge_demo`.
4. **Cookies** — `COOKIE_SECURE=true`; terminate TLS at a reverse proxy. HSTS
   belongs at the proxy.
5. **Proxy headers** — `TRUST_PROXY_HEADERS=true` **only** behind a
   header-stripping TLS proxy that **overwrites** `X-Forwarded-For`. With the
   default `false`, the per-IP throttle dimension is off and the
   username-keyed throttle is the only password-spray defense (documented
   trade-off).
6. **CSRF origins** — set `CSRF_TRUSTED_ORIGINS` to the public origin.
7. **Real accounts** — `python manage.py createuser <name> --role admin` (and
   editors/viewers as needed); stop using demo accounts.
8. **Restart policy** — restart on failure; the boot sweep reconciles orphaned
   jobs, so a hard restart is safe.

### Refusal gates (enforced, not warned)

The entrypoint refuses to start when `ALLOWED_HOSTS=*` **or**
`COOKIE_SECURE=false` while `SEED_DEMO=false` **or** any non-sample ledger row
exists. The escape is `ALLOW_INSECURE_LOOPBACK=true` for an air-gapped,
loopback-only instance; it prints a banner naming both waived gates. It also
warns when `TRUST_PROXY_HEADERS=false` while non-demo data exists.

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

### Restarting

```bash
docker restart hydrantproof          # or: docker compose restart <service>
```

The restart is safe at any time: `recover_jobs` marks in-flight coverage/pack
jobs `FAILED (orphaned)` and cleans partial rows/files. Users sign back in;
nothing else is lost (database + media volume persist).

---

## 7. Monitoring

| Signal | Where | What it tells you |
|---|---|---|
| Health | `GET /healthz` (anonymous, cached 5 s) | 200 only when the database answers `SELECT 1` **and** migrations are applied; otherwise 503 naming the failing check (`database`, `migrations`) |
| Access log | gunicorn stdout (`--access-logfile -`) | Every request, status, and duration |
| Error log | gunicorn stdout (`--error-logfile -`) + Django `ledger` / `django.request` loggers | Tracebacks at ERROR; job failures logged with the run/pack id (`ledger.jobs`) |
| Job state | `CoverageRun` / `EvidencePack` rows (Django admin; API poll) | `RUNNING`/`RENDERING` rows carry `worker_id`, `lease_expires_at`, `last_heartbeat`; `FAILED` rows carry `failure_reason` |
| Audit trails | Django admin: `ExportAudit` (every export/download), `MediaAudit` (admin attachment deletes), `LoginAttempt` (every login surface), `SettingsAudit` (every settings change) | Who did what, when |
| Boot sequence | entrypoint log | The ordered lines shown in §5; a refusal gate names itself |

There are no metrics endpoints, no tracing, and no external monitoring hooks
in v0 — health + logs + audit tables are the surface. That is a stated
boundary, not an omission to hide.

### Scheduled jobs (cron on the host)

```cron
# Nightly: enqueue notification rows (idempotent under (hydrant, kind, as_of_date))
15 2 * * *  cd /app && python manage.py enqueue_notifications
# Nightly: deliver unsent rows to ALERT_WEBHOOK_URL (10 s timeout, ≤5 attempts)
30 2 * * *  cd /app && python manage.py drain_outbox
# Monthly: budgets, the expired-lease reaper, FAILED-run retention, recover_jobs
0 3 1 * *   cd /app && python manage.py prune_data && python manage.py recover_jobs
```

`prune_data` doubles as the lease reaper (jobs with expired leases →
`FAILED (orphaned)`; a live heartbeat is never reaped) and enforces pack-count,
result-row, media-byte, and age budgets. `--dry-run` previews; see
`python manage.py prune_data --help` for every knob.

---

## 8. Management commands (the operator's CLI)

| Command | Purpose |
|---|---|
| `createuser <name> --role admin\|editor\|viewer [--password] [--org-label]` | Create a district account (password validated: ≥10 chars, not numeric, not common) |
| `ensure_secret [--print-secret] [--print-env-hint]` | Generate/persist the secret key (advisory-lock guarded); `--print-secret` prints only the resolved key (the entrypoint captures it), `--print-env-hint` prints the production export line |
| `check_posture [--static]` | The refusal gate as a standalone command (exit 1 on refusal). The non-static call also refuses the public build-time secret key and defers when the database is not yet reachable |
| `seed_demo [--force]` | Seed the demo district (idempotent; refuses a non-empty ledger or the purge marker) |
| `purge_demo` | Delete every sample row and file + demo users; refuses if non-sample rows exist (but still deactivates demo users); sets the purge marker |
| `clear_purge_marker` | Re-allow demo seeding after `purge_demo` |
| `clear_lockouts --username <name>` | Delete throttle rows so a locked-out username can authenticate |
| `recover_jobs` | Mark orphaned RUNNING/RENDERING jobs FAILED and clean partials (boot does this) |
| `enqueue_notifications [--as-of DATE]` | Enqueue OOS_EXPIRED / TEST_OVERDUE / INSPECTION_OVERDUE rows (idempotent) |
| `drain_outbox [--limit N]` | POST unsent rows to `ALERT_WEBHOOK_URL` (10 s timeout, ≤5 attempts each) |
| `prune_data [--dry-run]` | Reap leases + enforce retention/media budgets |
| `export_ledger [--output FILE] [--user NAME]` | Versioned JSON export (excludes `AppSetting`; includes user tables for restore) |
| `import_ledger <file> --confirm` | Destructive restore (pk-preserving, version-checked) |
| `bake_render_stack [--output PATH]` | Freeze render-stack facts the pack manifest cites (image build does this) |

---

## 9. Backup and restore

Two objects must be backed up **together**:

1. **The database** — `pg_dump -Fc hydrantproof > hydrantproof.dump`.
   **Treat dumps as sensitive** (they may contain the DB-persisted secret key
   in `AppSetting`).
2. **The media volume** (`hydrantproof_media` — attachments, overlays, stored
   pack bytes, stored import files).

A `pg_dump`-only restore leaves `Attachment`, `MapOverlay` and `EvidencePack`
rows pointing at missing files: attachment files are unrecoverable; pack files
can only be regenerated as *new* packs from retained runs.

**Inter-district / migration export:** `python manage.py export_ledger --output
ledger.json` (excludes `AppSetting`; includes user tables because password
hashes are required for restore). Restore with
`python manage.py import_ledger ledger.json --confirm` — schema-version
checked, pk-preserving, and it reconciles the demo-deactivation invariant.

---

## 10. Troubleshooting

### T1 — Login page (or any page) returns 500: `Missing staticfiles manifest entry`

**Diagnosis:** the server log shows
`ValueError: Missing staticfiles manifest entry for 'ledger/app.css'`. This is
the static-storage backend (`CompressedManifestStaticFilesStorage`) telling you
the assets were never collected.

**Fix:**

```bash
python manage.py collectstatic --noinput
# then restart the server (a runserver started before the collect has cached
# the missing manifest — restart it)
```

This is the most common bare-metal/first-run failure. The container entrypoint
handles it automatically.

### T2 — The server refuses to start with `refusing to start: ALLOWED_HOSTS=*…`

**Diagnosis:** the entrypoint log names the exact condition, e.g.
`refusing to start: ALLOWED_HOSTS=*, COOKIE_SECURE=false while SEED_DEMO=false, non-sample ledger rows exist`.

**Fix (production):** set `ALLOWED_HOSTS` to the real hostnames and
`COOKIE_SECURE=true`, then restart.
**Fix (air-gapped loopback only):** set `ALLOW_INSECURE_LOOPBACK=true` — the
banner names both waived gates. Never do this on a networked host.

### T3 — `/healthz` answers 503

**Diagnosis:** the body names the failing check:

* `{"status":"unavailable","checks":{"database":"unreachable: OperationalError",…},"failing":["database"]}`
  → the database is down or `DATABASE_URL` is wrong.
* `"migrations": "pending: N"` → migrations have not been applied.

**Fix:** check the database container/service and `DATABASE_URL`
(`docker logs hydrantproof-db`; `pg_isready -h <host> -p 5432`), then
`python manage.py migrate`. Note the 5-second cache: re-check after a few
seconds.

### T4 — A coverage run or pack is stuck in `RUNNING`/`RENDERING`

**Diagnosis:** in the Django admin (or API), inspect the row's
`lease_expires_at` and `last_heartbeat`. A live job heartbeats every 60 s with
a 120 s lease; a row whose lease is long past belongs to a dead process.

**Fix:** `python manage.py recover_jobs` (or wait — the boot sweep and
`prune_data` both reap expired leases). Then start a new job. A reaped job's
late writes are discarded by design; nothing partial is kept.

### T5 — Users are locked out (429) after failed sign-ins

**Diagnosis:** `LoginAttempt` rows (admin) show ≥10 failures for that username
within 15 minutes.

**Fix:** wait up to 15 minutes, or
`python manage.py clear_lockouts --username <name>`. If it recurs across many
users, check whether a shared proxy IP is being throttled — that dimension
only exists when `TRUST_PROXY_HEADERS=true`; verify the proxy overwrites
`X-Forwarded-For`.

### T6 — The demo accounts suddenly stopped working

**Diagnosis:** this is the **demo-deactivation invariant working as designed** —
the first non-sample hydrant/test/inspection/parcel deactivates all demo
accounts in the same transaction (the `demo_deactivated_at` marker is set).

**Fix:** create real accounts (`python manage.py createuser … --role admin`).
The demo accounts are intentionally unrecoverable once real data exists.

### T7 — `413 media budget exceeded` / `attachment budget exceeded`

**Diagnosis:** `media_bytes_total()` (attachments + overlays + packs +
imports) is over `DistrictSettings.max_media_mb` (default 200), or the
attachment sub-budget (`max_attachment_total_mb`, default 80) is exceeded.

**Fix:** `python manage.py prune_data` (frees old packs; attachments are only
removed by an explicit admin delete), delete unneeded photos through the
admin/API, generate packs without attachments, or raise the budgets in
district settings.

### T8 — Pack download answers 404 or 410

* **404** `"pack pruned"` — the retention job removed the row + files.
  Generate a new pack from the same run.
* **410** `"stored file missing"` — the row exists but the file is gone
  (usually a lost/incorrectly-mounted media volume). Verify the volume is
  mounted at the media path; then generate a new pack.

### T9 — CSRF verification failed on a proxied HTTPS deployment

**Diagnosis:** the browser posts to the public origin while Django sees a
different host/scheme. The login page still renders (GET is not CSRF-checked),
but the POST answers 403 "CSRF verification failed".

**Fix:** set `CSRF_TRUSTED_ORIGINS=https://<public-origin>` (comma-separated)
and make the proxy pass `Host` and `X-Forwarded-Proto`. Also confirm
`ALLOWED_HOSTS` includes the public hostname.

### T10 — Field page cannot capture GPS / the queue never syncs

**Diagnosis:** browser geolocation requires a **secure context** (HTTPS, or
`localhost`). A plain-HTTP LAN address exposes no geolocation and no service
worker. A queue that does not sync is usually an expired session.

**Fix:** serve the field page over HTTPS (the recommended deployment), or use
`localhost` for local testing. For a queue that will not sync, use the **Sign
in to sync** panel on the page; queued readings survive and replay
idempotently by `client_uuid`.

### T11 — Pack/attachment/overlay writes fail with `PermissionError`

**Diagnosis:** the job's `failure_reason` is `error:PermissionError` and the
server log shows `Permission denied: '/app/media/packs'` (or `attachments`).
The worker (running as the unprivileged `app` user) cannot write the media
root. The shipped entrypoint hands the media root to `app` at boot — so this
means either the container was started with a **read-only** media mount, or
the process is not using the entrypoint.

**Fix:** give the container a writable media volume/mount (see §6). If you
mount a host path, make it writable by the container's `app` user, or start
through `./entrypoint.sh` so the ownership hand-off runs. Confirm the fix by
generating a pack and watching the `media root … handed to app` boot line.

---

## 11. Operational notes and limits

* **Hydrant locations are sensitive infrastructure data.** Do not expose the
  service without TLS, and do not publish exports.
* **Latitude/antimeridian limits:** coordinates are validated to |lat| ≤ 80 and
  |lon| ≤ 180; a district crossing the antimeridian is not supported by the
  coverage prefilter (v0 scope).
* **Attachment metadata:** uploads are content-sniffed (JPEG/PNG/WebP), then
  re-encoded to JPEG ≤ 1600 px with EXIF **stripped**; capture metadata is
  preserved in the database before the strip.
* **One district per installation.** `export_ledger` is the inter-district
  path; multi-district tenancy is a roadmap item.
* **No runtime outbound HTTP from the server process** — the single exception
  is `manage.py drain_outbox` posting to the static `ALERT_WEBHOOK_URL`.
* **Vendored Leaflet is 1.9.3**, not the 1.9.4 named in the specification —
  documented with provenance in `ledger/static/vendor/leaflet/VENDOR.md`; no
  product behavior depends on 1.9.4-only features.
