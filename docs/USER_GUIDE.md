# HydrantProof — User Guide

**Audience:** district staff who use HydrantProof — fire chiefs, officers, and
water-supply clerks. This guide covers signing in, the primary job (from raw
hydrant records to a downloaded water-supply evidence pack), field capture,
recovery after a restart, and every common error with its exact fix.

For installation and server administration, see
[`OPERATIONS.md`](OPERATIONS.md). For the developer-facing API, see
[`API.md`](API.md).

---

## 1. What HydrantProof is

HydrantProof is your district's self-hosted water-supply records system. It
keeps the hydrant registry, the NFPA 291 flow-test ledger, inspections and
deficiencies, readiness status, and the per-structure coverage worksheet — and
it can produce a surveyor-ready **evidence pack** (PDF + CSV bundle) from those
records in one click.

Two things it deliberately does **not** do:

* It does not predict a Public Protection Classification (PPC) score.
* It does not compute FSRS point values. Point values are licensed by ISO
  (Verisk) and are not computed anywhere in this product.

Every artifact is labeled: *"District-prepared from district-maintained
records. Integrity self-attested by SHA-256 manifest — not cryptographically
signed."*

---

## 2. Accounts and roles

Three roles exist. Your administrator creates your account and tells you which
role you have.

| Role | Can do |
|---|---|
| `viewer` | Read every page and export (dispatch CSV, forms, packs). Cannot change anything. |
| `editor` | Everything a viewer can do, plus: add/edit hydrants and parcels, record flow tests and inspections, resolve deficiencies, acknowledge expired out-of-service, run coverage, generate packs, upload map overlays, and run CSV imports. |
| `admin` | Everything an editor can do, plus: delete photo attachments, download the ledger export, change district settings (through the Django admin at `/admin/`), and manage user accounts. |

If you attempt a change you are not allowed to make, the server answers
**"Your role is read-only."** — ask your administrator for an `editor` account.

### Demo mode credentials

A fresh install seeded in demo mode (`SEED_DEMO=true`, the default for
evaluation and proof) ships three accounts:

| Username | Password | Notes |
|---|---|---|
| `demo-editor` | `hydrantproof-demo` | Full editing. |
| `demo-viewer` | `hydrantproof-demo` | Read-only. |
| `demo-admin` | *printed once in the server log* | Search the container/server log for `demo-admin one-time password`. |

> **The demo password is published — it must never guard real data.** The
> product enforces this: the first time any *real* (non-sample) hydrant, flow
> test, inspection, or parcel is saved, all demo accounts are deactivated in
> the same transaction. The sample-data banner disappears only after
> `python manage.py purge_demo` removes the seeded rows.

### Getting a real account

Your administrator creates real accounts with:

```bash
python manage.py createuser <username> --role editor      # or admin / viewer
```

The command prompts for a password (minimum 10 characters, not all-numeric,
not a common password). Passwords are stored hashed; nobody — including the
administrator — can read them back.

---

## 3. Signing in

1. Open your district's HydrantProof address in a browser, for example
   `http://localhost:8000/` or `https://hydrants.example.gov/`.
2. The site redirects you to **`/login/`** — the sign-in card.
3. Enter your username and password, then click **Sign in**.
4. You land on the **Dashboard** (`/dashboard/`).

Sessions last **2 hours of inactivity**, and **at most 12 hours** regardless of
activity. When a session ends you are returned to the sign-in card; the page
you were on is remembered and you are returned to it after signing in again.

If you mistype your password, the page simply says so — but after **10 failed
attempts for one username within 15 minutes** the server answers **429 Too
Many Attempts** and blocks further attempts for up to 15 minutes. Wait for the
window to pass, or ask your administrator to run
`python manage.py clear_lockouts --username <your-username>`.

---

## 4. The primary journey — from records to a downloaded evidence pack

This is the complete first-run workflow. Each step names the page, the exact
button or field, and the visible result. Steps 1–4 can be done in any order,
but the coverage run (step 5) reads whatever is in the ledger at that moment.

### Step 1 — Sign in

Follow §3. You should see the dashboard with five panels: **Hydrants**,
**READY**, **WATCH**, **NOT_READY**, and **Unsent notifications**.

### Step 2 — Load your hydrant inventory

*If you are evaluating with sample data, skip to step 3 — the demo ships 24
hydrants, 60 parcels, and a full test history.*

1. Prepare a CSV using the shipped header template
   (`/static/ledger/import_templates/hydrants.csv`):
   `asset_tag,lat,lon,location_description,manufacturer,model,year_installed,main_size_inches,outlet_diameter_in,discharge_coefficient,ownership,source_org,notes,initial_status,initial_status_reason,expected_return_on,indefinite`
2. Import it with the two-phase API (an `editor` account). **Dry run first** —
   it validates the whole file and applies nothing:

   ```bash
   curl -u <user>:<password> -F mode=dry_run -F file=@hydrants.csv \
     http://<host>/api/v1/imports/hydrants/
   ```

   The response reports `rows_ok`, `rows_rejected`, per-row errors, and a
   one-shot `commit_token`.
3. Apply it with the token. The stored dry-run file is reused automatically:

   ```bash
   curl -u <user>:<password> -F mode=commit -F commit_token=<token> \
     http://<host>/api/v1/imports/hydrants/
   ```

4. Verify: open **Hydrants** in the top navigation. Every imported hydrant is
   listed with its status, readiness badge, class, recency flags, and last
   test date.

> The same two-phase pattern works for parcels
> (`/api/v1/imports/parcels/`) and historical flow tests
> (`/api/v1/imports/flow-tests/`). Full column references are in
> [`API.md` §9](API.md#9-imports-two-phase-csv).

### Step 3 — Record a flow test (or verify the seeded ones)

The UI's recording surface is the **Field page** (`/field/`) — it works on a
desktop too, see §5. Scripted recording uses
`POST /api/v1/hydrants/{id}/tests/` (see [`API.md` §3](API.md#3-hydrants)).

A test needs: date, static pressure (psi), residual pressure (psi), and at
least one outlet reading (diameter, coefficient, pitot psi) *or* a meter total.
The server computes the discharge `Q_F` and the rated flow at 20 psi residual
`Q_R` immediately and stores the snapshot. You do not compute anything by hand.

Recorded correctly, the hydrant's detail page shows the new row in **Flow
tests** with `Q_F`, `Q_R`, class, warnings, and the formula version.

### Step 4 — Record an inspection

1. Open the hydrant's detail page and click **Record inspection** (or open
   `/hydrants/{id}/inspections/new/` directly).
2. Fill the inspection form: date, result (`OK` / `DEFICIENCY` / `FAIL`),
   inspector name(s), deficiency codes when the result is not `OK`, notes, and
   optional photos. Submitting records the inspection through
   `POST /api/v1/hydrants/{id}/inspections/` — the same engine the API uses.

What each result does:

* `result: OK` — clears the hydrant's open deficiency rows automatically.
* `result: DEFICIENCY` — opens non-gating deficiency rows (one per code).
* `result: FAIL` — opens **gating** deficiency rows (one per code); the
  hydrant is excluded from district credit until the rows are resolved or a
  later `OK` inspection closes them.

Deficiency codes: `CAP`, `CHAIN`, `NOZZLE`, `VALVE`, `LEAK`, `PAINT`,
`ACCESS`, `OBSTRUCTION`, `OTHER`.

Resolve a deficiency early from the **Dashboard → Open deficiency queue** with
the inline **Resolve** button (a resolution note is required).

### Step 5 — Run the water-supply coverage worksheet

1. Open **Coverage** in the top navigation.
2. Click **Run coverage now**. A job starts and the page polls until it
   finishes (seconds for a small district; the button is disabled while any
   job is running — one job runs at a time).
3. When the run completes, the page shows the **Latest complete run** panel:
   frozen parameters (recency windows, credit radius), the snapshot time, and
   counts — *pairs*, *in radius*, *covered of protected parcels* — plus the
   distribution bands (`3+` / `2` / `1` / `0` credited hydrants per protected
   parcel).

If the ledger or district settings change after a run, the page and dashboard
show a **stale** banner with the reason. Re-run coverage to refresh.

### Step 6 — Generate the evidence pack

1. Open **Evidence packs**.
2. Choose the coverage run from the dropdown (or leave **fresh run** selected
   to recompute coverage first). Optionally tick **include photo
   attachments** (counts against the media budget).
3. Click **Generate pack**. The page polls until the pack is `COMPLETE`.
4. The pack row appears in the table with its byte size, PDF/zip SHA-256
   digests, and page count.
5. Click **PDF** or **ZIP** to download. The zip contains `pack.pdf`,
   `hydrants.csv`, `coverage_worksheet.csv`, `parcel_summary.csv`,
   `oos_register.csv`, `manifest.txt`, and (if included)
   `attachments/*.jpg`. `manifest.txt` lists the SHA-256 of every other entry.

**A pack is rendered once and stored.** Re-downloading serves the identical
bytes. A pruned pack answers 404; a missing stored file answers 410 — a pack is
never regenerated under an existing id.

**Visible saved outcome:** the downloaded `pack.zip` on your machine, plus the
pack row in **Evidence packs** with its digests — both survive restarts.

---

## 5. Field capture (`/field/`)

Open **Field** in the top navigation (or `/field/` directly). The page works on
a phone or tablet and is built for hydrant-side work.

* **Capture GPS** fills the readout with your position and accuracy (the
  browser asks permission once).
* **Outlet readings**: up to 4 rows of diameter / coefficient / pitot.
* **Photo**: attach one gauge or nameplate photo (JPEG/PNG/WebP, ≤ 5 MB; the
  server re-encodes it to JPEG and strips EXIF, keeping capture time and GPS
  in the database).
* **Submit now** — sends the test immediately. On success the reading is
  stored and the form is ready for the next hydrant.
* **Queue for later** — stores the reading in the browser (IndexedDB). Queued
  readings appear in the **Queued submissions** table and survive closing the
  browser. They sync automatically when the page is open and online again, and
  on the next page load.
* **Export queued readings as CSV** — downloads the queue in the exact
  historical-import column format so any connected workstation can import it
  (`mode=commit` on `/api/v1/imports/flow-tests/`). **Photos do not survive
  the CSV path** — the page says so on export.

If the queue cannot sync because the session expired, the page shows a **Sign
in to sync** panel: enter your username and password there; the queue syncs
without losing anything.

**Replay safety.** Every queued reading carries a `client_uuid`. Submitting the
same reading twice is idempotent — the server recognizes the duplicate and
answers with the original test rather than creating a second one. If a queued
reading was already stored by another route, the queue clears it on the
`client_uuid` match.

> **Proof of records, not proof of presence.** GPS coordinates and photos are
> client-asserted; EXIF is stripped; nothing in this version attests that a
> person physically stood at the hydrant (no QR/NFC scan, no device
> attestation). The records are attributed, append-only, and versioned — that
> is the guarantee.

---

## 6. Restart and recovery

### Restarting the service

If the server needs a restart, the operator runs the deployment restart (see
[`OPERATIONS.md` §6](OPERATIONS.md#6-deployment)). Your data lives in two
places, both preserved by a correct deployment:

1. the **PostgreSQL database** (all records, history, settings), and
2. the **media volume** (photos, overlays, stored pack bytes, stored import
   files).

### Signing back in

After a restart, open the site again and sign in (§3). Sessions do not survive
a server restart if the database or secret key changed; a normal restart keeps
you signed in until the session window ends.

### Verifying your saved work

1. **Dashboard** — the Hydrants / READY / WATCH / NOT_READY counts match what
   you saw before.
2. **Hydrants → any asset tag** — the flow-test rows, inspection rows,
   deficiency rows, status history, and attribute history are all intact.
3. **Coverage** — the latest complete run is still listed with its frozen
   parameters and counts.
4. **Evidence packs** — previously generated packs are still listed and their
   **PDF**/**ZIP** downloads still serve the stored bytes.

### What recovery cannot restore

* If the **media volume is lost**, attachment photos are unrecoverable, and
  stored pack files answer 410 — you can generate *new* packs from the
  retained coverage runs, but the old files are gone.
* If the **database is lost**, everything is gone unless a backup exists
  (`pg_dump` + the media volume; see [`OPERATIONS.md` §9](OPERATIONS.md#9-backup-and-restore)).

### If a job was running when the server stopped

The server marks orphaned jobs `FAILED (orphaned)` at boot — a job never comes
back as a phantom "running" row. Open **Coverage** or **Evidence packs** and
start the run again; nothing partial is kept (a reaped run's partial rows are
discarded).

---

## 7. Common user-facing errors and exact recovery

| What you see | What it means | Do this |
|---|---|---|
| "Invalid username or password." | The credentials are wrong, or the account was deactivated. | Re-check the password. If it still fails, ask your administrator (the account may have been deactivated when real data was first loaded). |
| "Too many attempts. Try again later." (HTTP 429) | 10 failures for this username within 15 minutes. | Wait up to 15 minutes, then try again. Administrator shortcut: `python manage.py clear_lockouts --username <name>`. |
| "Your role is read-only." | You are signed in as `viewer` and attempted a change. | Ask for an `editor` account, or use the read-only surfaces (viewing, exports, downloads). |
| 409 "A coverage or pack job is already running." / "…being created; retry." | Single-flight rule: one coverage/pack job at a time. | Wait a few seconds and retry. The page shows which job is in flight. |
| 429 "evidence-pack rate limit: 10 per minute per user" | You generated more than 10 packs in a minute. | Wait a minute; generate packs only when you need them. |
| 413 "attachment exceeds 5 MB" / "at most 4 attachments per record" / "attachment budget exceeded" / "media budget exceeded" | Upload or storage caps. | Shrink the photo, delete unneeded photos (administrator), or raise the budget in district settings / run `manage.py prune_data`. |
| "client_uuid_conflict" (409) | A queued field reading was edited and re-submitted with the same id. | Submit it again — the field page assigns a fresh id automatically. Nothing was lost. |
| Coverage run status `FAILED` with `pair_count_exceeded` | The run would write more than 1,000,000 worksheet rows. | Narrow the run with a `parcel_filter` (a parcel-id list ≤ 50,000, or a bounding box). |
| Coverage run status `FAILED (orphaned)` | The server restarted mid-run. | Run coverage again. |
| Pack status `FAILED` with `media_budget_exceeded` | The pack plus attachments would exceed the media budget. | Generate the pack without attachments, or prune old packs, or raise the budget. |
| A pack download answers 404 | The pack was pruned by the retention job. | Generate a new pack from the same run. |
| A pack download answers 410 | The stored file is missing (volume problem). | Generate a new pack; if this repeats for every pack, tell your operator the media volume is not persistent. |
| "No completed coverage run" (404 on a coverage read) | No coverage run has finished yet. | Run coverage (Coverage page → **Run coverage now**). |
| The sample-data banner appears over your real data | Some seeded rows are still present. | Administrator: `python manage.py purge_demo`. |
| Dashboard shows **stale** coverage banner | Ledger rows or district settings changed after the last run. | Click **Run coverage again** / re-run coverage. |

---

## 8. In-product help

This version has **no separate in-product help panel**. The surfaces that help
you while you work are:

* this guide (`docs/USER_GUIDE.md` in the repository, alongside the product),
* the **footer** on every page, which repeats the artifact label and the
  provisional-constants banner,
* the **sample-data** and **stale** banners, which name the exact condition and
  the action to take,
* the **API schema** at `/openapi.json` (anonymous) for anyone scripting
  against the district's records,
* your district administrator for account and settings questions.

---

## 9. Vocabulary

| Term | Meaning |
|---|---|
| **Q_F** | The test's discharge — the meter total when given, otherwise the sum of the per-outlet pitot computations. |
| **Q_R** | Rated available flow at 20 psi residual — `Q_F` extrapolated by the percentage-drop method. `—` (null) means the test could not be rated (static at/below 20 psi, or excessive extrapolation). |
| **Class** | `AA` ≥ 1500 gpm, `A` ≥ 1000, `B` ≥ 500, `C` < 500, computed from `Q_R`. |
| **READY / WATCH / NOT_READY** | Readiness. WATCH = serviceable but degrading or due soon. NOT_READY = one or more gating flags (out of service, untested, test stale, inspection fail, never inspected, inspection overdue, open deficiency). |
| **Recency flags** | `CURRENT` / `DUE_SOON` / `OVERDUE` for tests and inspections, from the district's windows (default 365 days, due-soon 90). |
| **Trend** | `STABLE`, `DEGRADING` (≥ 15 % drop vs the previous rated test), `RATED_FLOW_LOST` (the current test lost its rating), `INSUFFICIENT_HISTORY`, `UNRATED`. |
| **District-credited** | A hydrant contributes its `Q_R` to a parcel's worksheet when it is eligible: in service, current test, rated, no gating deficiency. Every exclusion carries a named reason. |
| **Distribution bands** | The worksheet headline: credited-hydrant count per protected parcel — `3+`, `2`, `1`, `0`. |
| **Flow-band proxy** | A labeled, district-computed sum of credited gpm (`1000+` / `500-999` / `1-499` / `NONE`) — explicitly **not** the FSRS distribution criterion. |
| **Evidence pack** | The rendered PDF + CSV zip (+ optional photos) with a SHA-256 manifest, stored once and downloadable byte-identically. |
| **Expired OOS** | An out-of-service hydrant past its expected return date. It is flagged, escalated, and acknowledgeable — **never auto-reverted to service**. |

---

## 10. Where to next

* [`OPERATIONS.md`](OPERATIONS.md) — install, configure, run, monitor, debug.
* [`API.md`](API.md) — the full HTTP API for scripting and integrations.
* [`UI.md`](UI.md) — every screen, flow, and state.
* [`ARCHITECTURE.md`](ARCHITECTURE.md) — how the system is put together.
* [`examples/`](examples/README.md) — runnable examples for the common jobs.
