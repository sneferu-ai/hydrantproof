# HydrantProof — runtime test plan

**runtime_test_mode:** `playwright` · **runtime_test_command:** `PORT={port} ./entrypoint.sh`
**health_endpoint:** `/healthz` (200 only when migrations are applied and the
database answers `SELECT 1`)

This plan is the ordered user flow the runtime proof and the browser journeys
verify. It is written so the demo-deactivation invariant is **proven last**,
never tripped over.

## Mechanics these journeys must respect

Several response shapes and action mechanics in this product are not ordinary
pages, and a journey that treats them as pages aborts on the product's own
correct behaviour; one ordering law runs through the whole plan. Each item
below restates the mechanic it needs.

**Downloads are not pages.** `GET /api/v1/exports/dispatch/` answers
`Content-Disposition: attachment`, and both printable-form endpoints answer
`application/pdf`; a headless-browser navigation to any of them starts a file
download and is aborted with "Download is starting". Verify them from the
signed-in page with one `evaluate` action that fetches the URL and throws when
the response does not match the item — never with `goto`.

**Hidden inputs are never visible.** The sign-in form's CSRF token is a
`type="hidden"` input (`input[name=csrfmiddlewaretoken]`); `wait_for` observes
visibility only and can never see it. Assert it with an `evaluate` action that
throws when the input is missing or empty.

**`title` is never visible and never a result channel.** The document
`<title>` lives in `<head>`; `wait_for` observes visibility only, so a
`wait_for` on `title` times out on a correct page — the retry run burned a
10–20 s timeout on every item that waited for it, and the timeout buried the
real assertion. Do not write results into `document.title` either: setting
`document.title = 'status:' + r.status` and asserting `expect_text` on
`title` makes the check depend on your own script having run; when that
script is keyed `expression` (a silent no-op) the marker is never written
and the item fails on a correct product. Async results belong in ONE
`script`-keyed `evaluate` that throws on mismatch: the executor awaits the
promise the script returns before the next action, so the fetch settles
before the journey moves on — there is nothing to wait for and no marker to
read. Prove a page rendered with `wait_for` on a visible element:
`input[name=username]` on the login page; `main`, `table` or `h1` on
content pages.

**`evaluate` runs `script`, never `expression`.** The executor calls
`page.evaluate(action["script"])`; an action keyed `expression` evaluates an
empty string — a silent no-op that throws nothing and fails the journey later
with no trace (the first run's sign-out was written this way and never ran).
Every `evaluate` action in this plan carries `script`; copy it verbatim.

**JSON endpoints are not pages.** A `goto` to `/api/v1/hydrants/` serves JSON
that is not asserted as page text, and an `evaluate` that never ran leaves the
page on `about:blank` — so every `evaluate` action below runs only after a
`goto` or the sign-in journey has put the browser on the app's own page.
Never evaluate from a fresh context's `about:blank`: the DOM is empty there
and a relative `fetch` has no origin to resolve against — both fail the
journey on a correct product. The anonymous items (4, 5, 21) embed their own
`goto` for exactly this reason.

**One submit button per page.** Every sign-in click uses the login form's own
button — selector `form[action='/login/'] button[type=submit]`. A bare
`button[type=submit]` also matches the header's Sign out button on any
signed-in page and can sign the journey out mid-item.

**The demo-deactivation invariant runs this plan's clock.** The first
non-sample ledger row — a hydrant, flow test, outlet reading, inspection or
parcel — deactivates every demo account in the same transaction, and the
writing session is anonymous on its very next request. Every journey before
item 20 therefore reads, refuses (400), computes or queues a capture offline;
exactly one real write happens, in item 20, and item 21 proves the
published-password account did not survive it. A row-creating write earlier in
the plan would deactivate the accounts and fail every later item — do not add
one.

**Every journey is a fresh browser.** The executor runs each item in a new
browser context — no cookies, IndexedDB or queue state carries between items,
which is why every signed-in item (items 7-20) signs in first and why item 18
queues its own reading instead of reading item 17's. The action DSL has no
offline, geolocation or file-picker control: connectivity never changes
mid-journey, GPS positions enter through an `evaluate` stub, and photos attach
as generated `File` objects. The proof also serves plain HTTP from a container
address — not a secure context — so the browser exposes no
`navigator.serviceWorker` and no `navigator.geolocation` unless the journey
stubs them; the page treats both as best-effort (guarded registration, a "no
geolocation" readout), and those deployment-only behaviours are not asserted
here.

## 0. Boot

1. Start the product with the runtime command against the declared `db`
   service; poll `GET /healthz` until 200 with `database: reachable` and
   `migrations: applied`.
2. Confirm the demo seed completed (log line) and the container is serving on
   `$PORT` as the unprivileged `app` user.

## 1. Anonymous surface

3. `GET /openapi.json` → 200, OpenAPI 3.0.3, listing the `/api/v1/` routes.
4. The anonymous surface: `GET /dashboard/` redirects to `/login/`, and
   `GET /api/v1/hydrants/` answers **401** `{"detail": "Authentication
   required."}`. Never navigate the browser to the JSON endpoint and never
   assert its text on `<body>` — JSON is not a page. Assert both with exactly
   these actions, in this order (the navigation first; the fetch then runs
   from the login page the redirect landed on, never from `about:blank`):
   `{"op": "goto", "url": "/dashboard/"}`
   `{"op": "expect_url", "value": "/login/"}`
   `{"op": "wait_for", "selector": "input[name=username]"}`
   `{"op": "evaluate", "script": "async () => { const r = await fetch('/api/v1/hydrants/', {headers: {'Accept': 'application/json'}}); if (r.status !== 401) throw new Error('anonymous hydrant list returned ' + r.status + ', expected 401'); const d = await r.json(); if (d.detail !== 'Authentication required.') throw new Error('401 detail was ' + JSON.stringify(d)); return 'ok'; }"}`
5. `GET /login/` → HTML carrying a CSRF token. The token is rendered as a
   **hidden** input (`input[name=csrfmiddlewaretoken]`, `type="hidden"`) inside
   the sign-in form — a hidden element is never "visible", so do not wait for
   it. Navigate to the login page first — the assert needs the app's own DOM,
   and a fresh browser context starts on `about:blank`, where there is no
   document to query. Assert it with exactly these actions, in this order:
   `{"op": "goto", "url": "/login/"}`
   `{"op": "evaluate", "script": "() => { const el = document.querySelector('input[name=csrfmiddlewaretoken]'); if (!el) throw new Error('login form has no csrfmiddlewaretoken input'); if (!el.value) throw new Error('csrf token input is empty'); return 'ok'; }"}`
   — the successful form POST in item 6 is the functional proof the token is
   accepted.

## 2. Sign in (browser journey)

6. POST the login form as `demo-editor` / `hydrantproof-demo` → 302 to
   `/dashboard/`. Fill `input[name=username]` and `input[name=password]`, then
   click the login form's own submit button — selector
   `form[action='/login/'] button[type=submit]` (a bare `button[type=submit]`
   also matches the header's Sign out button on a signed-in page).
7. Sign in first (as in item 6) — every journey runs in a fresh browser, so
   this item signs in for itself — then the dashboard shows the readiness
   board (one square per hydrant, carrying the three-state legend
   READY / WATCH / NOT_READY with each state's count), the ranked overdue
   lists for tests and inspections, the expired-OOS list with its acknowledge
   action, the degrading-flow list, the open-deficiency queue with its resolve
   action, the sample-data banner, and the unsent-notification count. The
   board is the count surface — its squares are links to the hydrants and its
   legend names each state in words, so no state is ever asserted by colour
   alone.
8. Sign in first (as in item 6), then hydrant list → detail: attributes,
   attribute history, status history, flow tests with warnings and formula
   version, inspections, deficiencies, and the per-hydrant map with the
   1000-ft ring. Open the detail page through the first hydrant's **row link
   in the list table** — `table a[href^='/hydrants/']` — never a bare
   `a[href^='/hydrants/']`: the header's own "Hydrants" navigation link also
   matches that prefix and lands back on the list. Exactly these actions, in
   this order — they are the whole journey (the script proves every section
   the item names, and the map's 1000-ft ring, from the DOM the page renders;
   never from the page title):
   `{"op": "goto", "url": "/hydrants/"}`
   `{"op": "wait_for", "selector": "table a[href^='/hydrants/']"}`
   `{"op": "click", "selector": "table a[href^='/hydrants/']"}`
   `{"op": "wait_for", "selector": "h1"}`
   `{"op": "evaluate", "script": "async () => { const need = ['Attributes', 'Map', 'Flow tests', 'Inspections', 'Deficiencies', 'Status history', 'Attribute history']; for (let i = 0; i < 50; i++) { const heads = Array.from(document.querySelectorAll('h2')).map(function (h) { return (h.textContent || '').trim(); }); const missing = need.filter(function (n) { return heads.indexOf(n) === -1; }); const mapEl = document.getElementById('map'); const ring = !!mapEl && mapEl.classList.contains('leaflet-container') && mapEl.querySelectorAll('.leaflet-overlay-pane path').length >= 2; if (missing.length === 0 && ring) return 'ok'; if (i === 49) { if (missing.length) throw new Error('detail page is missing sections: ' + missing.join(', ')); throw new Error('the per-hydrant map or its 1000-ft ring did not render'); } await new Promise(function (r) { setTimeout(r, 100); }); } }"}`

   The item then closes the full journey — restart, re-authenticate, recover —
   in the same browser item. A fresh context is anonymous again, and signing in
   returns to the record: the sign-in form carries the return path the
   anonymous redirect supplied, so the redirect lands back on the detail page
   rather than the dashboard. The recovered page is the same ledger, reached
   through the product's own sign-in control in a browser that had never seen
   it. Exactly these actions, in this order, after the checks above:
   `{"op": "restart_context"}`
   `{"op": "goto", "url": "/hydrants/"}`
   `{"op": "wait_for", "selector": "table a[href^='/hydrants/']"}`
   `{"op": "click", "selector": "table a[href^='/hydrants/']"}`
   `{"op": "expect_url", "value": "/login/"}`
   `{"op": "fill", "selector": "input[name=username]", "value": "demo-editor"}`
   `{"op": "fill", "selector": "input[name=password]", "value": "hydrantproof-demo"}`
   `{"op": "click", "selector": "form[action='/login/'] button[type=submit]"}`
   `{"op": "wait_for", "selector": "table"}`
   `{"op": "evaluate", "script": "() => { const heads = Array.from(document.querySelectorAll('h2')).map(function (h) { return (h.textContent || '').trim(); }); if (heads.indexOf('Flow tests') === -1) throw new Error('the recovered detail page lost its ledger: ' + heads.join(', ')); if (!document.querySelectorAll('table tbody tr').length) throw new Error('the recovered detail page rendered no ledger rows'); return 'ok'; }"}`

## 3. Ledger write path (HTTP)

9. `POST /api/v1/hydrants/` with the initially-OOS fields → 201, and a status
   event appears; omitting the expected-return fields → 400 naming them.
   Row-creating writes run only in item 20 (the ordering law above), so this
   journey asserts the refusal, which creates nothing and is safe mid-plan.
   Sign in first, then verify it with exactly this action:
   `{"op": "evaluate", "script": "async () => { const m = document.cookie.match(/csrftoken=([^;]+)/); const csrf = m ? m[1] : ''; const r = await fetch('/api/v1/hydrants/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf}, body: JSON.stringify({asset_tag:'RF-9-OOS-MISSING', lat:40.0, lon:-75.0, initial_status:'OUT_OF_SERVICE'})}); if (r.status !== 400) throw new Error('expected 400 for an OUT_OF_SERVICE hydrant without the expected-return fields, got ' + r.status); const t = JSON.stringify(await r.json()); if (!t.includes('expected_return_on')) throw new Error('the 400 did not name expected_return_on: ' + t); return 'ok'; }"}`
   The 201 creation path, the attributed initial status event, the six
   transitions and the supersession/duplicate scenarios are covered by the
   gate suite (`tests/test_ledger_api.py`).
10. `POST /api/v1/compute-preview/` with the fixture-A readings (S=80, R=40,
    one outlet d=2.5 c=0.90 pitot=28) → the pinned numbers (`Q_F` 887.9,
    `Q_rated` ≈ 1105.2, class A). The preview computes and stores nothing, so
    it is safe mid-plan. Sign in first, then verify it with exactly this
    action:
    `{"op": "evaluate", "script": "async () => { const m = document.cookie.match(/csrftoken=([^;]+)/); const csrf = m ? m[1] : ''; const r = await fetch('/api/v1/compute-preview/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf}, body: JSON.stringify({tested_on:'2026-09-30', static_psi:'80', residual_psi:'40', outlets:[{outlet_diameter_in:'2.50', outlet_coefficient:'0.90', pitot_psi:'28'}]})}); if (r.status !== 200) throw new Error('compute-preview status ' + r.status); const d = await r.json(); if (Math.abs(Number(d.computed_total_gpm) - 887.9) > 0.1) throw new Error('Q_F ' + d.computed_total_gpm + ' != 887.9'); if (Math.abs(Number(d.rated_gpm_20) - 1105.2) > 0.15) throw new Error('Q_R ' + d.rated_gpm_20 + ' != 1105.2'); if (d.capacity_class !== 'A') throw new Error('capacity_class ' + d.capacity_class); return 'ok'; }"}`
11. `POST /api/v1/hydrants/{id}/tests/` with fixture-A readings → 201 with the
    same pinned numbers. This is the ONE real write the plan performs, and it
    is executed in item 20 (the deactivation trigger, proven last) — doing it
    here would deactivate the demo accounts and fail every later journey. Do
    **not** POST here; the write's 201 outcome, its stored snapshots and the
    supersession matrix are covered by the gate suite
    (`tests/test_ledger_api.py`) and executed once, in item 20. Sign in first
    (as in item 6), then assert the read-only ledger side with exactly this
    action:
    `{"op": "evaluate", "script": "async () => { const lr = await fetch('/api/v1/hydrants/', {headers: {'Accept': 'application/json'}}); if (lr.status !== 200) throw new Error('hydrant list status ' + lr.status); const ld = await lr.json(); const first = (ld.results || ld)[0]; if (!first || !first.id) throw new Error('no hydrant in the ledger list'); const tr = await fetch('/api/v1/hydrants/' + first.id + '/tests/', {headers: {'Accept': 'application/json'}}); if (tr.status !== 200) throw new Error('hydrant tests list status ' + tr.status); return 'ok'; }"}`
12. Replay the same `client_uuid` and payload → 200 with
    `duplicate_client_uuid`; replay with a changed payload → 409
    `client_uuid_conflict`. A replay needs a second write after the first, and
    the first write deactivates the account; both outcomes are covered by the
    gate suite (`tests/test_ledger_api.py`). Do **not** POST here. Sign in
    first (as in item 6), then assert the stored-snapshot shape the replay
    path protects with exactly this action:
    `{"op": "evaluate", "script": "async () => { const lr = await fetch('/api/v1/hydrants/', {headers: {'Accept': 'application/json'}}); if (lr.status !== 200) throw new Error('hydrant list status ' + lr.status); const ld = await lr.json(); const first = (ld.results || ld)[0]; if (!first || !first.id) throw new Error('no hydrant in the ledger list'); const tr = await fetch('/api/v1/hydrants/' + first.id + '/tests/', {headers: {'Accept': 'application/json'}}); if (tr.status !== 200) throw new Error('hydrant tests list status ' + tr.status); const td = await tr.json(); const rows = td.results || td; if (!Array.isArray(rows)) throw new Error('tests list is not an array'); rows.forEach(function (row) { if (!row.formula_version) throw new Error('stored test row lost its formula_version snapshot'); }); return 'ok'; }"}`
13. `POST …/inspections/` with `result=FAIL` and two codes → two gating
    deficiency rows; `POST …/deficiencies/{id}/resolve/` clears the queue; a
    later `OK` inspection auto-closes the rest. Inspections and deficiencies
    are non-sample rows, so the write path — FAIL opening one gating row per
    code, resolve clearing the queue, a later OK auto-closing the rest — is
    covered by the gate suite (`tests/test_ledger_api.py`); its read-only side
    is asserted by the action below and by item 8's journey, which renders the
    detail page's Deficiencies section. Do **not** POST here. Sign in first
    (as in item 6), then assert the read-only deficiency surface with exactly
    this action:
    `{"op": "evaluate", "script": "async () => { const lr = await fetch('/api/v1/hydrants/', {headers: {'Accept': 'application/json'}}); if (lr.status !== 200) throw new Error('hydrant list status ' + lr.status); const ld = await lr.json(); const first = (ld.results || ld)[0]; if (!first || !first.id) throw new Error('no hydrant in the ledger list'); const dr = await fetch('/api/v1/hydrants/' + first.id + '/deficiencies/', {headers: {'Accept': 'application/json'}}); if (dr.status !== 200) throw new Error('deficiencies list status ' + dr.status); const rows = await dr.json(); if (!Array.isArray(rows)) throw new Error('deficiencies list is not an array'); return 'ok'; }"}`

## 4. Exports

14. `GET /api/v1/exports/dispatch/` → 200 `text/csv`, every row carrying the
    field-verify literal and the disclaimer; retired hydrants excluded;
    `=cmd` neutralized; negative longitudes unprefixed. This endpoint delivers
    a **download** — do not navigate to it (`goto` aborts with "Download is
    starting"). Sign in first (as in item 6), then verify it with exactly this
    action:
    `{"op": "evaluate", "script": "async () => { const lit = 'Field-verify status, location, and expected flow before relying on this record.'; const r = await fetch('/api/v1/exports/dispatch/'); if (r.status !== 200) throw new Error('dispatch export status ' + r.status); if (!(r.headers.get('content-type') || '').includes('text/csv')) throw new Error('content-type ' + r.headers.get('content-type')); const t = await r.text(); if (!t.includes('verify_in_field') || !t.includes('disclaimer') || !t.includes('data_as_of_utc')) throw new Error('dispatch CSV header is missing a required column'); const rows = t.split('\\r\\n').filter(function (l) { return l.trim().length > 0; }); const dataRows = rows.length - 1; if (dataRows < 1) throw new Error('dispatch CSV has no data rows'); const hits = t.split(lit).length - 1; if (hits < dataRows) throw new Error('field-verify literal on ' + hits + ' of ' + dataRows + ' rows'); return 'ok'; }"}`
15. `GET /api/v1/exports/forms/flow-test/` and `/forms/inventory/` → printable
    PDFs with the provisional-constants note and the disclaimer footer. Both
    answer `application/pdf` and must not be navigated to with `goto`. Sign in
    first, then verify both with exactly this action:
    `{"op": "evaluate", "script": "async () => { for (const u of ['/api/v1/exports/forms/flow-test/', '/api/v1/exports/forms/inventory/']) { const r = await fetch(u); if (r.status !== 200) throw new Error(u + ' status ' + r.status); if (!(r.headers.get('content-type') || '').includes('application/pdf')) throw new Error(u + ' content-type ' + r.headers.get('content-type')); const b = await r.arrayBuffer(); const magic = new TextDecoder().decode(new Uint8Array(b.slice(0, 5))); if (magic !== '%PDF-') throw new Error(u + ' did not serve PDF bytes'); if (b.byteLength < 1000) throw new Error(u + ' PDF is suspiciously small (' + b.byteLength + ' bytes)'); } return 'ok'; }"}`
    The PDFs' text-level assertions (the provisional-constants note and the
    disclaimer footer on every page) are covered by the product's own suite
    (`tests/test_exports_and_disclaimers.py`).

## 4b. The worksheet loop (HTTP — covered by scripts/smoke_runtime.sh)

15a. `POST /api/v1/coverage-runs/` → 202 with `poll_url`; poll until
    `COMPLETE`; `GET …/results/` serves rows; `GET
    /api/v1/parcels/{id}/coverage/` reads the parcel's rows.
15b. `POST /api/v1/evidence-packs/` with the run id → 202; poll until
    `COMPLETE`; `GET …/zip/` twice is byte-identical by SHA-256; the zip
    holds `pack.pdf`, the three CSVs, the OOS register and `manifest.txt`;
    `GET …/pdf/` serves a PDF.

## 5. Field capture (browser journey)

16. Sign in (as in item 6), then `{"op": "goto", "url": "/field/"}` and
    `wait_for` `#capture-form`: the capture shell renders — the hydrant
    selector, the tested-on/static/residual inputs, the per-outlet grid, the
    photo input and the queued-submissions panel. Exercise the GPS path with
    a mock (never a device) — stub the geolocation method, click Capture GPS,
    and assert the readout — with exactly these actions:
    `{"op": "evaluate", "script": "() => { Object.defineProperty(navigator, 'geolocation', {configurable: true, value: {getCurrentPosition: function (ok) { ok({coords: {latitude: 40.0, longitude: -75.0, accuracy: 12}}); }}}); return 'stubbed'; }"}`
    `{"op": "click", "selector": "#gps"}`
    `{"op": "expect_text", "selector": "#gps-readout", "value": "Position 40.000000, -75.000000"}`
    Then fill a reading and attach a generated photo — `fill` cannot drive a
    `<select>`, and the proof has no file picker, so both use `evaluate`:
    `{"op": "evaluate", "script": "() => { const s = document.getElementById('hydrant'); if (!s || s.options.length < 2) throw new Error('hydrant selector is empty'); s.value = s.options[1].value; return s.options[1].textContent; }"}`
    `{"op": "fill", "selector": "#tested_on", "value": "2026-09-30"}`
    `{"op": "fill", "selector": "#static_psi", "value": "80"}`
    `{"op": "fill", "selector": "#residual_psi", "value": "40"}`
    `{"op": "fill", "selector": "[data-outlet='pitot'][data-index='0']", "value": "28"}`
    `{"op": "evaluate", "script": "() => { const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array([137, 80, 78, 71])], 'gauge.png', {type: 'image/png'})); const input = document.getElementById('photo'); input.files = dt.files; if (!input.files.length) throw new Error('generated photo did not attach'); return 'attached'; }"}`
    and confirm the capture form holds the reading:
    `{"op": "evaluate", "script": "() => { const v = function (id) { return document.getElementById(id).value; }; if (!v('hydrant')) throw new Error('no hydrant selected'); if (v('tested_on') !== '2026-09-30') throw new Error('tested_on did not hold: ' + v('tested_on')); if (v('static_psi') !== '80' || v('residual_psi') !== '40') throw new Error('psi fields did not hold'); const p = document.querySelectorAll('[data-outlet=pitot]')[0]; if (!p || p.value !== '28') throw new Error('pitot did not hold'); const photo = document.getElementById('photo'); if (!photo.files || !photo.files.length) throw new Error('photo not held'); return 'ok'; }"}`
    Do **not** click "Submit now" (`#submit-online`): an online submit POSTs
    `/api/v1/hydrants/{id}/tests/` — the plan's first non-sample flow test —
    and deactivates the demo accounts mid-plan. A synced capture is the same
    row class as the plan's single real write (item 20) — so these journeys
    prove the shell, the capture form and the offline queue without syncing a
    row mid-plan; the online-submit outcome ("exactly one flow test exists"),
    its server-side creation and `client_uuid` idempotency are covered by the
    gate suite (`tests/test_ledger_api.py`).
17. Sign in (as in item 6) and open the field page —
    `{"op": "goto", "url": "/field/"}` — then queue the reading through the
    product's own offline control, "Queue for later" (`#queue-offline`). Its
    handler writes IndexedDB and nothing else (no fetch): queueing is
    network-free by construction, so the journey needs no offline emulation —
    the executor has none — and cannot create a row. Nothing here triggers a
    sync either: the field page syncs the queue only on the `online` event and
    on page load when online, and this fresh context's queue was empty at
    load. Fill a reading as in item 16 (hydrant, tested_on, static, residual,
    row-1 pitot), then:
    `{"op": "click", "selector": "#queue-offline"}`
    `{"op": "evaluate", "script": "async () => { const table = document.getElementById('queue-table'); const sel = document.getElementById('hydrant'); const tag = ((sel.selectedOptions[0] || {}).textContent || '').trim(); for (let i = 0; i < 50; i++) { const text = table.textContent || ''; if (tag && text.indexOf(tag) !== -1) return 'ok'; await new Promise(function (r) { setTimeout(r, 100); }); } throw new Error('queue table never listed the capture: ' + (table.textContent || '').slice(0, 200)); }"}`
    `{"op": "evaluate", "script": "async () => { const db = await new Promise(function (res, rej) { const r = indexedDB.open('hydrantproof-field', 1); r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); }; }); const rows = await new Promise(function (res, rej) { const g = db.transaction('queue', 'readonly').objectStore('queue').getAll(); g.onsuccess = function () { res(g.result || []); }; g.onerror = function () { rej(g.error); }; }); if (rows.length !== 1) throw new Error('expected exactly one queued capture, found ' + rows.length); const row = rows[0]; if (!row.client_uuid) throw new Error('queued capture has no client_uuid'); if (!row.payload || row.payload.tested_on !== '2026-09-30') throw new Error('queued payload lost the reading'); return 'ok'; }"}`
    A synced capture is a non-sample flow test — the deactivation trigger — so
    the queued row stays queued for the rest of the plan; the sync's
    server-side creation and `client_uuid` idempotency are covered by the gate
    suite (`tests/test_ledger_api.py`).
18. Sign in (as in item 6) and open the field page —
    `{"op": "goto", "url": "/field/"}` — fill a reading as in item 16 and
    queue it with "Queue for later" (`#queue-offline`). The queue write is an
    async IndexedDB transaction, so confirm the queue holds the row BEFORE
    exporting (an immediate export can read an empty queue):
    `{"op": "click", "selector": "#queue-offline"}`
    `{"op": "evaluate", "script": "async () => { const table = document.getElementById('queue-table'); const sel = document.getElementById('hydrant'); const tag = ((sel.selectedOptions[0] || {}).textContent || '').trim(); for (let i = 0; i < 50; i++) { const text = table.textContent || ''; if (tag && text.indexOf(tag) !== -1) return 'ok'; await new Promise(function (r) { setTimeout(r, 100); }); } throw new Error('queue table never listed the capture: ' + (table.textContent || '').slice(0, 200)); }"}`
    Then "Export queued readings as CSV" (`#export-csv`) downloads the queue
    in the historical-import column format:
    `{"op": "click", "selector": "#export-csv"}`
    `{"op": "evaluate", "script": "async () => { const el = document.getElementById('status'); for (let i = 0; i < 50; i++) { const t = el.textContent || ''; if (t.indexOf('Exported 1 queued reading(s)') !== -1) { if (t.indexOf('photos are not included') === -1) throw new Error('export confirmation lost the photos limitation: ' + t); return 'ok'; } await new Promise(function (r) { setTimeout(r, 100); }); } throw new Error('export confirmation never appeared: ' + (el.textContent || '').slice(0, 200)); }"}`
    The export confirmation carries the photos-excluded limitation, as does
    the note at the control; the CSV's byte format and its re-import through
    the historical flow-test path are covered by the unit import checks.

## 6. Sign out, then the invariant (last)

19. Sign out ends the session; an anonymous page route redirects to login. Sign
    in first (as in item 6), then sign out through the product's own control:
    the header renders a POST form to `/logout/` whose submit button is the
    Sign out control — click it. Do NOT write your own JavaScript or fetch
    POST to `/logout/`: a fetch is not the product's control, and an
    `evaluate` action without a `script` key is a silent no-op that leaves the
    session signed in (the first run failed exactly this way). Exactly these
    actions, in this order:
    `{"op": "click", "selector": "form[action='/logout/'] button[type=submit]"}`
    `{"op": "wait_for", "selector": "input[name=username]"}`
    `{"op": "expect_text", "selector": "main", "value": "District water-supply records"}`
    `{"op": "goto", "url": "/dashboard/"}`
    `{"op": "expect_url", "value": "/login/"}`
    The click lands the browser on `/login/` (the sign-in form is visible);
    the navigation pair then proves the anonymous redirect to `/login/`.
    Finally, from that anonymous login page (never `about:blank` — the goto
    above put you on it), prove the session is gone with exactly this action:
    `{"op": "evaluate", "script": "async () => { const r = await fetch('/api/v1/hydrants/'); if (r.status !== 401) throw new Error('expected 401 after sign-out, got ' + r.status); return 'ok'; }"}`
20. Sign in again as `demo-editor`, POST fixture-A readings to
    `/api/v1/hydrants/{id}/tests/` → **the first non-sample ledger row**; the
    deactivation trigger fires in that transaction. Sign in first, then run
    exactly this action — it lists the hydrants, records the fixture-A test on
    the first one and asserts the pinned numbers:
    `{"op": "evaluate", "script": "async () => { const m = document.cookie.match(/csrftoken=([^;]+)/); const csrf = m ? m[1] : ''; const lr = await fetch('/api/v1/hydrants/', {headers:{'Accept':'application/json'}}); if (lr.status !== 200) throw new Error('hydrant list status ' + lr.status); const ld = await lr.json(); const first = (ld.results || ld)[0]; if (!first || !first.id) throw new Error('no hydrant to record a test against'); const r = await fetch('/api/v1/hydrants/' + first.id + '/tests/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf}, body: JSON.stringify({tested_on:'2026-09-30', static_psi:'80', residual_psi:'40', outlets:[{outlet_diameter_in:'2.50', outlet_coefficient:'0.90', pitot_psi:'28'}]})}); if (r.status !== 201) throw new Error('flow-test POST status ' + r.status); const d = await r.json(); if (Math.abs(Number(d.rated_gpm_20) - 1105.2) > 0.15) throw new Error('rated_gpm_20 ' + d.rated_gpm_20 + ' != 1105.2'); if (d.capacity_class !== 'A') throw new Error('capacity_class ' + d.capacity_class + ' != A'); return 'ok'; }"}`
    This action and item 21's 401 check are one proof: if the write does not
    execute, the accounts stay active and item 21 fails.
21. `GET /api/v1/hydrants/` with the same `demo-editor` Basic credentials →
    **401**. The published-password account cannot survive contact with real
    data — demonstrated, not bypassed. Do not attempt a form login (item 20
    deactivated the account); check the API directly with HTTP Basic. The
    fetch still needs the app's own page under it: this journey runs in a
    fresh browser context, and from `about:blank` a relative fetch has no
    origin to resolve against — so navigate to the (anonymous) login page
    first. Exactly these actions, in this order:
    `{"op": "goto", "url": "/login/"}`
    `{"op": "wait_for", "selector": "input[name=username]"}`
    `{"op": "evaluate", "script": "async () => { const r = await fetch('/api/v1/hydrants/', {headers:{'Authorization':'Basic ' + btoa('demo-editor:hydrantproof-demo')}}); if (r.status !== 401) throw new Error('expected 401 for the deactivated demo account, got ' + r.status); return 'ok'; }"}`

## Notes on scope

Steps 16–18 exercise the capture shell, form and queue through the product's
own controls; the exported CSV's re-import is covered by the unit import
checks.

Steps that need an authenticated write after step 21 use an account created
through `manage.py createuser`.

Live outbound webhook delivery is a customer-host operation and is not
exercised here; the outbox enqueue/dedup path is covered by the suite against
a controlled in-process stand-in.
