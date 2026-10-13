#!/usr/bin/env python3
"""HydrantProof — the interface's own walk, in a real browser.

Run it by hand (it is deliberately NOT part of the gate suite: it needs a
Chromium and it boots a live server, so the pinned `pytest` gate stays
deterministic):

    python scripts/browser_walk.py

It boots the product on a live server against a throwaway test database, drives
Chromium through the product's own controls, and prints one line per claim plus
full-page screenshots at 1280x720 and 375x812 under `.tmp/`. Exit status is 1 if
any claim failed.

What it proves that no unit test can: that the readiness board's pips take
focus and arrow keys, that Leaflet actually initialises the per-hydrant map,
that the map's credit ring draws the district's configured radius (a
non-default one here) and matches the caption measured against the map's own
scale bar, that the flow-test sheet's live preview reaches the product's own
`/api/v1/compute-preview/` route and renders the stored numbers (and renders
the engine's null answers as the ledger's em dash, never a phantom zero), that
the coverage and pack pages resume watching jobs they did not start, that the
inspection sheet reacts to the result radio and its refusal group marks and
clears as promised, that a queued reading reaches IndexedDB, that the theme
toggle flips the document, that the first tab stop paints a focus ring, and
that no page overflows a 375 px viewport.

It also walks the full product journey end to end — launch, authenticate,
record and save a flow test, read the guide, **restart in a fresh browser
context**, authenticate again, and recover the same saved record — because a
record system whose data cannot be recovered after a restart has no reason to
exist. That journey lives here rather than in `test_plan.md` on purpose: the
runtime plan's ordering law defers its single non-sample write to item 20 (a
real row deactivates the demo accounts mid-plan), and this script runs against
a throwaway database where the write is free.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hydrantproof.settings")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

import django  # noqa: E402

django.setup()

from django.conf import settings as dj  # noqa: E402
from django.contrib.staticfiles.handlers import StaticFilesHandler  # noqa: E402
from django.test.testcases import LiveServerThread  # noqa: E402
from django.test.runner import DiscoverRunner  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

dj.STORAGES = {**dj.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesHandler" if False else "django.contrib.staticfiles.storage.StaticFilesStorage"}}

RESULTS: list[str] = []

# The district credit radius the walk configures, in feet. Deliberately NOT
# the model default (1000): 1000 is also the value a hardcoded ring would
# draw, so only a non-default radius can tell the two apart.
WALK_CREDIT_RADIUS_FT = 750.0


# The hit-zone law's own instrument: for every control, the centre of the
# region the operator can actually see must resolve to that control (or a
# descendant). The visible region is the control's box intersected with every
# clipping ancestor and the viewport — a table wider than its panel is
# scrolled, and an element's layout centre can sit outside what is on screen.
STOLEN_FACES_JS = """() => {
    const visibleCenter = el => {
        const r = el.getBoundingClientRect();
        let box = {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
        let node = el.parentElement;
        while (node && node !== document.documentElement) {
            const cs = getComputedStyle(node);
            if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) {
                const nr = node.getBoundingClientRect();
                box.left = Math.max(box.left, nr.left);
                box.top = Math.max(box.top, nr.top);
                box.right = Math.min(box.right, nr.right);
                box.bottom = Math.min(box.bottom, nr.bottom);
            }
            node = node.parentElement;
        }
        box.left = Math.max(box.left, 0);
        box.top = Math.max(box.top, 0);
        box.right = Math.min(box.right, window.innerWidth);
        box.bottom = Math.min(box.bottom, window.innerHeight);
        if (box.right - box.left < 2 || box.bottom - box.top < 2) return null;
        return {x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2};
    };
    const stolen = [];
    document.querySelectorAll('.btn, .nav__link').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;
        const point = visibleCenter(el);
        if (!point) return;
        const probe = document.elementFromPoint(point.x, point.y);
        if (!probe || !(probe === el || el.contains(probe))) {
            stolen.push(((el.textContent || '').trim().slice(0, 20) || el.className)
                + ' @ ' + Math.round(point.x) + ',' + Math.round(point.y)
                + ' <- ' + (probe ? (probe.className || probe.tagName) : 'none'));
        }
    });
    return stolen;
}"""


def claim(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(f"{'PASS' if ok else 'FAIL'}  {name}{(' :: ' + detail) if detail else ''}")


def main() -> int:
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()

    server = None
    thread = None
    try:
        from django.contrib.auth.models import Group, User
        from ledger import geo, services
        from ledger.models import DistrictSettings, Hydrant, Parcel

        group, _ = Group.objects.get_or_create(name="editor")
        user = User.objects.create_user("walk-editor", password="walk-pass-123")
        user.groups.add(group)
        # The district's credit radius is a first-class, editable parameter
        # (the settings page names the admin as its door). The walk configures
        # it to a NON-DEFAULT value before anything renders, so every
        # radius-bearing surface here is exercised against a district that set
        # its own number — a hardcoded ring cannot pass the map claim below.
        district = DistrictSettings.load()
        district.credit_radius_ft = WALK_CREDIT_RADIUS_FT
        district.save()

        today = timezone.localdate()
        ready = Hydrant.objects.create(asset_tag="H-READY", lat=38.0, lon=-122.0,
                                       location_description="North side of Route 9",
                                       manufacturer="Mueller", year_installed=2004)
        # Fixtures enter through the product's own creation rule: every hydrant
        # carries an attributed initial status event, and the lifecycle door
        # reads its from-status from that event — not from the denormalised
        # status field.
        services.create_status_event(hydrant=ready, to_status="IN_SERVICE",
                                     reason="Initial record", user=user, is_initial=True)
        services.create_flow_test(
            hydrant=ready, tested_on=today - timedelta(days=20), static_psi="80", residual_psi="45",
            outlets=[{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "30"}],
            user=user)
        services.create_inspection(hydrant=ready, inspected_on=today - timedelta(days=20),
                                   result="OK", deficiency_codes=[], inspector_names="D. Whitfield",
                                   user=user)
        dead = Hydrant.objects.create(asset_tag="H-DEAD", lat=38.01, lon=-122.01, status="IN_SERVICE")
        services.create_status_event(hydrant=dead, to_status="IN_SERVICE",
                                     reason="Initial record", user=user, is_initial=True)
        # A gating deficiency on H-DEAD: the dashboard's resolve queue is the
        # declarative data-async path, and the walk drives its API refusal.
        services.create_inspection(hydrant=dead, inspected_on=today - timedelta(days=3),
                                   result="FAIL", deficiency_codes=["CAP"], inspector_names="D. Whitfield",
                                   user=user)
        parcel = Parcel.objects.create(parcel_id="APN-1", address="1 Main St", lat=38.0, lon=-122.0)

        # A coverage run and a pack render "started elsewhere" (rows created
        # through the product's own factory, never submitted to a worker, so
        # they sit RUNNING/RENDERING for the whole walk): the job pages must
        # resume watching them on load — the note under each form promises the
        # page polls until the job completes or fails.
        from ledger import constants as C
        from ledger.api_jobs import create_coverage_run
        from ledger.models import EvidencePack

        running_run = create_coverage_run(parcel_filter=None, user=user)
        EvidencePack.objects.create(run=running_run, status=C.PACK_STATUS_RENDERING)

        # --- live server -------------------------------------------------
        server = LiveServerThread("127.0.0.1", StaticFilesHandler)
        server.daemon = True
        server.start()
        server.is_ready.wait(15)
        if server.error is not None:
            raise RuntimeError(f"live server failed: {server.error!r}")
        base = f"http://{server.host}:{server.port}"
        time.sleep(0.4)

        # Read the stored numbers BEFORE the browser starts: Playwright's sync
        # API runs inside an async context, where the Django ORM refuses to run.
        from ledger.models import FlowTest

        stored_test = FlowTest.objects.filter(hydrant=ready).order_by("-id").first()
        expected_qf = f"{float(stored_test.computed_total_gpm):,.1f}"
        expected_qr = f"{float(stored_test.rated_gpm_20):,.1f}"
        parcel_pk = parcel.pk

        # --- the browser -------------------------------------------------
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            console_errors: list[str] = []
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))
            page.on("console", lambda msg: console_errors.append(
                f"{msg.text} @ {msg.location.get('url', '')}") if msg.type == "error" else None)

            # 1. sign in through the product's own form
            page.goto(base + "/dashboard/")
            claim("anonymous /dashboard/ redirects to the sign-in page",
                  "/login/" in page.url, page.url)
            # 1b. the preview bootstrap: the launcher's record is consumed as
            #     identity (a pre-filled form + a visible explanation), never
            #     as a credential — the session still comes from the sign-in.
            page.evaluate("""() => {
                window.localStorage.setItem('sneferu.preview.bootstrap.v1',
                    JSON.stringify({username: 'walk-editor', role: 'editor'}));
            }""")
            page.reload()
            claim("the preview record pre-fills the sign-in form (identity, not a bypass)",
                  page.input_value("input[name=username]") == "walk-editor"
                  and page.locator("#preview-note").is_visible(),
                  page.inner_text("#preview-note")[:80])
            page.evaluate("() => { window.localStorage.removeItem('sneferu.preview.bootstrap.v1'); }")
            page.fill("input[name=username]", "walk-editor")
            page.fill("input[name=password]", "walk-pass-123")
            page.click("form[action='/login/'] button[type=submit]")
            page.wait_for_url("**/dashboard/**", timeout=8000)
            claim("sign-in lands on the readiness board", page.url.endswith("/dashboard/"), page.url)

            # 2. the board
            pips = page.locator(".board__pip")
            claim("the readiness board renders one pip per hydrant", pips.count() == 2, f"{pips.count()} pips")
            tokens = page.eval_on_selector_all(".board-legend__token", "els => els.map(e => e.textContent.trim())")
            claim("the legend names the engine's three states",
                  tokens == ["READY", "WATCH", "NOT_READY"], str(tokens))
            page.locator(".board__pip").first.focus()
            page.keyboard.press("ArrowRight")
            claim("arrow keys walk the board (roving tabindex)",
                  page.evaluate("() => document.activeElement.className").startswith("board__pip"))
            # the readout: the copy's promise, kept for a sighted eye
            readout = page.inner_text("[data-board-readout]")
            active_tag = page.evaluate(
                "() => document.activeElement.getAttribute('data-tag')")
            claim("focusing a square names it in the board's readout",
                  readout == active_tag, f"readout={readout!r} tag={active_tag!r}")
            hit = page.evaluate(
                """() => {
                    const pip = document.querySelector('.board__pip');
                    const r = pip.getBoundingClientRect();
                    const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height + 2);
                    return el ? el.getAttribute('data-tag') : 'none';
                }""")
            claim("the square's hit zone reaches past the visible 11 px square",
                  hit == page.evaluate(
                      "() => document.querySelector('.board__pip').getAttribute('data-tag')"),
                  f"hit={hit!r}")

            # 2b. the hit zones: the pointer target is --hit-min even when the
            #     drawn control is --control-h (DESIGN.md § 2). The pip's law,
            #     generalized; a real browser resolves each zone, so a
            #     declaration that never renders cannot pass here.
            claim("a button's hit zone reaches past its drawn box",
                  page.evaluate("""() => {
                      const el = document.querySelector('.page-head__actions .btn');
                      const r = el.getBoundingClientRect();
                      const probe = document.elementFromPoint(r.x + r.width / 2, r.y + r.height + 4);
                      return !!probe && (probe === el || el.contains(probe));
                  }"""))
            claim("the rail's stacked links tile: below one link the next one's face wins",
                  page.evaluate("""() => {
                      const first = document.querySelector('.nav__link');
                      const r = first.getBoundingClientRect();
                      const probe = document.elementFromPoint(r.x + r.width / 2, r.y + r.height + 2);
                      return !!probe && !!probe.closest('.nav__link');
                  }"""))
            stolen_faces = page.evaluate(STOLEN_FACES_JS)
            claim("no control's visible face is claimed by a neighbour's zone",
                  stolen_faces == [], str(stolen_faces))
            # The deficiency queue stands alone at the full working width, so
            # its Resolve column is never clipped at desktop; the scroll below
            # runs every table on the page to its far edge, keeping the law
            # honest on any table that does overflow (the declared
            # `.table-wrap` mechanism, DESIGN.md § 10).
            page.evaluate("""() => {
                document.querySelectorAll('.table-wrap').forEach(w => { w.scrollLeft = w.scrollWidth; });
            }""")
            page.wait_for_timeout(150)
            stolen_faces_scrolled = page.evaluate(STOLEN_FACES_JS)
            claim("the zone law holds on the scrolled action column too",
                  stolen_faces_scrolled == [], str(stolen_faces_scrolled))
            # The 2026-10-04 fix, pinned in geometry: the queue runs at the
            # full working width, so its action is on screen when the page
            # loads — an action the operator must scroll to find is a defect,
            # not a scroll mechanism.
            claim("the queue's Resolve control starts on screen",
                  page.evaluate("""() => {
                      const btn = document.querySelector('.resolve-form button[type=submit]');
                      const wrap = btn.closest('.table-wrap');
                      const br = btn.getBoundingClientRect();
                      const wr = wrap.getBoundingClientRect();
                      return wrap.scrollWidth <= wrap.clientWidth + 1
                          || (br.left >= wr.left - 1 && br.right <= wr.right + 1);
                  }"""))
            toast_box = page.evaluate("""() => {
                window.HydrantProof.toast('Hit-zone measurement toast', 'info', { sticky: true });
                const close = document.querySelector('.toast__close');
                const r = close.getBoundingClientRect();
                const reaches = node => !!node && (node === close || close.contains(node));
                return {
                    w: r.width, h: r.height,
                    below: reaches(document.elementFromPoint(r.x + r.width / 2, r.y + r.height + 3)),
                    above: reaches(document.elementFromPoint(r.x + r.width / 2, r.y - 3))
                };
            }""")
            claim("the toast dismiss is at least 24 x 24 with a zone past its box",
                  toast_box["w"] >= 24 and toast_box["h"] >= 24 and toast_box["below"] and toast_box["above"],
                  str(toast_box))
            page.evaluate("() => { document.querySelectorAll('.toast__close').forEach(b => b.click()); }")

            # 2c. the declarative data-async refusal: the dashboard's resolve
            #     queue posts through app.js, and an API 400 that names a field
            #     must mark that field, not only the sentence beside it.
            page.wait_for_selector(".resolve-form")
            page.fill(".resolve-form input[name=resolution_note]", "   ")
            page.click(".resolve-form button[type=submit]")
            page.wait_for_timeout(400)
            claim("an API refusal marks the named field and points at its message",
                  page.evaluate("""() => {
                      const input = document.querySelector('.resolve-form input[name=resolution_note]');
                      const status = document.querySelector('.resolve-form [data-async-status]');
                      const described = (input.getAttribute('aria-describedby') || '').split(/\\s+/);
                      return input.getAttribute('aria-invalid') === 'true'
                          && !!status && described.includes(status.id);
                  }"""),
                  page.inner_text(".resolve-form [data-async-status]")[:90])
            page.evaluate("() => { document.querySelectorAll('.toast__close').forEach(b => b.click()); }")

            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-dashboard-1280.png"), full_page=True)

            # 2d. the job pages: a run (or a pack render) started in another
            #     tab parks the form AND the page watches it to its end — the
            #     note under each form promises exactly that. The seeded
            #     RUNNING run and RENDERING pack never finish (no worker was
            #     submitted), so the polls keep saying so.
            page.goto(base + "/coverage/")
            page.wait_for_selector("#run-coverage-form")
            claim("the coverage form is parked while a run is in flight",
                  page.evaluate(
                      "() => document.querySelector('#run-coverage-form button[type=submit]').disabled"))
            page.wait_for_function(
                "() => document.getElementById('run-status').textContent.toLowerCase().includes('running')",
                timeout=6000)
            claim("the coverage page resumes watching a run it did not start",
                  "running" in page.inner_text("#run-status").lower(),
                  page.inner_text("#run-status"))
            page.check("input[name=coverage_scope][value=bbox]")
            claim("the bounding-box scope wakes its four corners",
                  page.evaluate("""() => {
                      const fields = document.getElementById('bbox-fields');
                      return !fields.hidden
                          && [...fields.querySelectorAll('input')].every(i => !i.disabled);
                  }"""))

            page.goto(base + "/packs/")
            page.wait_for_selector("#pack-generate-form")
            claim("the pack form is parked while a render is in flight",
                  page.evaluate(
                      "() => document.querySelector('#pack-generate-form button[type=submit]').disabled"))
            page.wait_for_function(
                "() => document.getElementById('pack-status').textContent.toLowerCase().includes('rendering')",
                timeout=6000)
            claim("the packs page resumes watching a render it did not start",
                  "rendering" in page.inner_text("#pack-status").lower(),
                  page.inner_text("#pack-status"))

            # 3. the hydrant detail + map
            page.goto(base + "/hydrants/")
            page.click("a[href='/hydrants/%d/']" % ready.pk)
            page.wait_for_selector("#map")
            page.wait_for_timeout(700)
            claim("Leaflet initialises the per-hydrant map",
                  page.evaluate("() => !!document.querySelector('#map.leaflet-container')"))
            # 3d. the credit ring: the drawing must be the district's own
            #     number — the same number the caption states. The fixture set
            #     a non-default radius (750 ft), so a hardcoded ring cannot
            #     pass this claim. The map's own scale bar is the ruler:
            #     Leaflet labels it with a real distance and sets its pixel
            #     width, so metres-per-pixel comes from the product, not from
            #     a constant in this script.
            caption = page.inner_text("#credit-radius-note")
            claim("the map caption states the district's configured credit radius",
                  f"{WALK_CREDIT_RADIUS_FT:.0f} ft" in caption,
                  caption.replace("\n", " ")[:100])
            ring = page.evaluate(
                """(radiusM) => {
                    const path = document.querySelector('#map path.credit-ring');
                    if (!path) { return { error: 'no .credit-ring path in the overlay pane' }; }
                    const box = path.getBoundingClientRect();
                    const drawnPx = (box.width + box.height) / 4;
                    const line = [...document.querySelectorAll('.leaflet-control-scale-line')]
                        .map(el => ({ text: (el.textContent || '').trim(), px: parseFloat(el.style.width) }))
                        .find(s => /^[\\d.]+\\s*(m|km)$/.test(s.text));
                    if (!line) { return { error: 'no metric scale line to measure with' }; }
                    let meters = parseFloat(line.text);
                    if (/km/.test(line.text)) { meters *= 1000; }
                    return { drawnPx: drawnPx, expectedPx: radiusM / (meters / line.px), scale: line.text };
                }""",
                WALK_CREDIT_RADIUS_FT / geo.FEET_PER_METER,
            )
            claim("the drawn credit ring is that number, measured against the map's own scale bar",
                  "error" not in ring
                  and abs(ring["drawnPx"] - ring["expectedPx"]) <= max(2.0, ring["expectedPx"] * 0.08),
                  str(ring))
            # 3e. the ring's hue is owned by the cascade: a theme flip repaints
            #     it like every other surface. A colour map.js captured at load
            #     could never change; the ring's stroke must differ under dark
            #     and return under light (the ring and the marker carry semantic
            #     classes whose rules read the live --accent token — Leaflet's
            #     own colour option is a presentation attribute, which the
            #     stylesheet outranks). Flipped back before leaving the page so
            #     the stored theme is light for every later step.
            stroke_light = page.evaluate(
                "() => { const p = document.querySelector('#map path.credit-ring');"
                " return p ? getComputedStyle(p).stroke : null; }")
            page.click("#theme-toggle")
            page.wait_for_timeout(150)
            stroke_dark = page.evaluate(
                "() => { const p = document.querySelector('#map path.credit-ring');"
                " return p ? getComputedStyle(p).stroke : null; }")
            page.click("#theme-toggle")
            page.wait_for_timeout(150)
            stroke_back = page.evaluate(
                "() => { const p = document.querySelector('#map path.credit-ring');"
                " return p ? getComputedStyle(p).stroke : null; }")
            claim("the credit ring's hue follows the theme flip and returns",
                  stroke_light is not None and stroke_dark != stroke_light
                  and stroke_back == stroke_light,
                  f"light={stroke_light} dark={stroke_dark} back={stroke_back}")
            detail_stolen = page.evaluate(STOLEN_FACES_JS)
            claim("no face is stolen on the hydrant ledger's controls",
                  detail_stolen == [], str(detail_stolen))
            shown = page.inner_text(".table").replace("\n", " ")
            claim("the hydrant detail shows the stored Q_F, not a recomputation",
                  expected_qf in shown, f"expected {expected_qf} :: {shown[:110]}")

            # 3c. the service-status door: the lifecycle's six transitions have
            #     one UI door, and it enforces the machine's rules in words
            #     before any request leaves. H-READY is in service, so the door
            #     must offer exactly the two legal exits; an empty out-of-service
            #     transition is refused in place; a completed one reloads the
            #     ledger with the new state; and the reversal puts it back.
            door = page.locator("#status-event-form")
            claim("the hydrant ledger has a service-status door", door.count() == 1)
            action = door.get_attribute("action") or ""
            claim("the door posts to the hydrant's own status-events route",
                  action.endswith(f"/api/v1/hydrants/{ready.pk}/status-events/"), action)
            offered = page.eval_on_selector_all(
                "#status_to option", "els => els.map(e => e.value).sort()")
            claim("only the legal transitions from IN_SERVICE are offered",
                  offered == ["OUT_OF_SERVICE", "RETIRED"], str(offered))
            # The return-date block's visibility tracks the selected target
            # whatever the option order is (it follows the rendered value).
            claim("the return-date fields track the selected target",
                  page.evaluate("""() => {
                      const f = document.getElementById('status-oos-fields');
                      const s = document.getElementById('status_to');
                      return f.hidden === (s.value !== 'OUT_OF_SERVICE');
                  }"""))
            page.select_option("#status_to", "RETIRED")
            claim("choosing retired hides the return-date fields",
                  page.evaluate("() => document.getElementById('status-oos-fields').hidden"))
            page.select_option("#status_to", "OUT_OF_SERVICE")
            claim("choosing out of service wakes the return-date fields",
                  page.evaluate("() => !document.getElementById('status-oos-fields').hidden"))
            page.click("#submit-status-event")
            page.wait_for_timeout(250)
            claim("an out-of-service move with no return fields is refused in place",
                  "expected return date" in page.inner_text("#status-event-status"),
                  page.inner_text("#status-event-status"))
            claim("the refusal declares the field's own failed state",
                  page.evaluate("""() => {
                      const el = document.getElementById('status_expected_return_on');
                      const described = (el.getAttribute('aria-describedby') || '').split(/\\s+/);
                      return el.getAttribute('aria-invalid') === 'true'
                          && described.includes('status-event-status');
                  }"""))
            return_on = (today + timedelta(days=30)).isoformat()
            page.fill("#status_expected_return_on", return_on)
            claim("editing the field clears the declared state",
                  page.evaluate(
                      "() => !document.getElementById('status_expected_return_on').hasAttribute('aria-invalid')"))
            with page.expect_navigation(wait_until="load", timeout=10000):
                page.click("#submit-status-event")
            props = page.inner_text(".props").replace("\n", " ")
            # The rail's labels and badges are rendered uppercase (a design
            # choice, asserted in tests/test_ui_system.py); the claim is about
            # the fact the ledger states, so it compares case-insensitively —
            # exactly as the reversal claim below does.
            claim("the recorded transition reloads the ledger as out of service",
                  "out of service" in props.lower(), props[:120])
            claim("the history table marks the new current event",
                  page.locator(".badge--quiet", has_text="Current").count() == 1)
            claim("the reversal door opens on a real transition",
                  page.locator("#status-reversal-form").count() == 1)
            page.fill("#reversal_reason", "Wrong hydrant — the closure is H-DEAD")
            with page.expect_navigation(wait_until="load", timeout=10000):
                page.click("#status-reversal-form button[type=submit]")
            props = page.inner_text(".props").replace("\n", " ")
            claim("the reversal restores the hydrant to service",
                  "service status in service" in props.lower(), props[:120])
            claim("the reversal is labelled in the history",
                  page.locator(".badge--quiet", has_text="Reversal").count() == 1)
            claim("a reversal cannot itself be reversed (the door closes)",
                  page.locator("#status-reversal-form").count() == 0)

            # 3b. the parcel edit page: the structure override is real.
            #     The walk's seeded parcel (APN-1) has no override, so the form
            #     opens measuring from the centroid; unticking the box wakes the
            #     override inputs, the save goes through the real PATCH route,
            #     and the detail page then shows the override.
            page.goto(base + f"/parcels/{parcel_pk}/edit/")
            page.wait_for_selector("#parcel-form")
            page.wait_for_timeout(700)
            claim("Leaflet initialises the parcel pin map",
                  page.evaluate("() => !!document.querySelector('#pin-map.leaflet-container')"))
            claim("the override starts closed (centroid is the measuring point)",
                  page.evaluate("() => document.getElementById('no_override').checked")
                  and page.evaluate("() => document.getElementById('structure_lat').disabled"))
            page.uncheck("#no_override")
            claim("unticking wakes the override inputs",
                  page.evaluate("() => !document.getElementById('structure_lat').disabled"))
            page.fill("#structure_lat", "38.0005000")
            page.fill("#structure_lon", "-122.0005000")
            page.click("#submit-parcel")
            page.wait_for_selector("#success-panel .banner--ok", timeout=10000)
            claim("the parcel override saves through the real route",
                  "Parcel saved" in page.inner_text("#success-panel"),
                  page.inner_text("#success-panel").replace("\n", " ")[:110])
            page.goto(base + f"/parcels/{parcel_pk}/")
            claim("the parcel detail shows the saved override",
                  "override" in page.inner_text(".page-head__sub").lower(),
                  page.inner_text(".page-head__sub")[:110])
            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-parcel-1280.png"), full_page=True)

            # 4. the flow-test sheet's live preview
            page.goto(base + f"/hydrants/{ready.pk}/tests/new/")
            page.wait_for_selector("#test-form")
            claim("the result card starts in its idle state",
                  "Waiting for readings" in page.inner_text("#readout"))
            page.fill("#static_psi", "80")
            page.fill("#residual_psi", "40")
            page.fill("#outlet-grid [data-outlet='pitot']", "28")
            page.wait_for_function(
                "() => document.querySelector('#readout').textContent.includes('Rated at 20 psi')",
                timeout=8000)
            readout = page.inner_text("#readout")
            claim("the live preview computes against the product's own route",
                  "1,105" in readout and "887" in readout, readout.replace("\n", " ")[:120])
            claim("the preview shows the capacity class", "A" in page.inner_text(".class-mark"))
            claim("no meter total means no separate pitot-sum row (nothing to compare)",
                  "Pitot sum" not in readout, readout.replace("\n", " ")[:120])

            # The null cases (hydraulics.py): a static at or below the 20 psi
            # target cannot be extrapolated, so Q_R and the factor are None —
            # the card must show the ledger's em dash, never a phantom zero.
            page.fill("#static_psi", "15")
            page.fill("#residual_psi", "10")
            page.fill("#outlet-grid [data-outlet='pitot']", "5")
            page.wait_for_function(
                "() => document.querySelector('.readout__big') && "
                "document.querySelector('.readout__big').textContent === '\u2014'",
                timeout=8000)
            readout_below = page.inner_text("#readout")
            claim("a below-target static renders not-ratable (an em dash, never a zero)",
                  "0 gpm" not in readout_below and "0.000" not in readout_below,
                  readout_below.replace("\n", " ")[:130])
            claim("the card names why (the warning chip)",
                  "static below target" in readout_below.lower(),
                  readout_below.replace("\n", " ")[:130])
            # Restore the working readings; the rest of the journey submits them.
            page.fill("#static_psi", "80")
            page.fill("#residual_psi", "40")
            page.fill("#outlet-grid [data-outlet='pitot']", "28")
            page.wait_for_function(
                "() => document.querySelector('.readout__big') && "
                "document.querySelector('.readout__big').textContent.includes('1,105')",
                timeout=8000)
            page.click("#add-outlet")
            claim("a second outlet row can be added",
                  page.locator("#outlet-grid [data-row]").count() == 2)
            page.locator("[data-remove-outlet]").nth(1).click()
            claim("an added outlet row can be removed",
                  page.locator("#outlet-grid [data-row]").count() == 1)
            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-flowtest-1280.png"), full_page=True)

            # 4b. the full journey: save the primary outcome, read the guide,
            #     restart in a FRESH browser context, sign in again, and recover
            #     the same saved record.
            page.click("#submit-test")
            page.wait_for_selector("#success-panel .banner--ok", timeout=10000)
            claim("the flow-test sheet records the reading through the real route",
                  "Recorded on test #" in page.inner_text("#success-panel"),
                  page.inner_text("#success-panel").replace("\n", " ")[:120])
            page.goto(base + "/guide/")
            page.wait_for_selector("h1")
            claim("the product guide renders the reference",
                  "How HydrantProof works" in page.inner_text("h1")
                  and "does not predict a Public Protection Classification" in page.inner_text("body"))
            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-guide-1280.png"), full_page=True)

            fresh = browser.new_context(viewport={"width": 1280, "height": 720})
            page2 = fresh.new_page()
            page2.goto(base + f"/hydrants/{ready.pk}/")
            claim("a fresh browser context is anonymous again", "/login/" in page2.url, page2.url)
            page2.fill("input[name=username]", "walk-editor")
            page2.fill("input[name=password]", "walk-pass-123")
            page2.click("form[action='/login/'] button[type=submit]")
            page2.wait_for_url(f"**/hydrants/{ready.pk}/**", timeout=8000)
            recovered = page2.inner_text(".table").replace("\n", " ")
            claim("the saved outcome is recovered after the restart",
                  "887.9" in recovered and "1,105.2" in recovered and "nfpa291-pct-drop-v1" in recovered,
                  recovered[:130])
            fresh.close()

            # 5. the inspection sheet reacts to the result
            page.goto(base + f"/hydrants/{ready.pk}/inspections/new/")
            page.wait_for_selector("#inspection-form")
            codes_disabled_initially = page.evaluate(
                "() => document.querySelectorAll('input[name=deficiency_codes]')[0].disabled")
            page.check("input[value=FAIL]")
            codes_enabled = page.evaluate(
                "() => !document.querySelectorAll('input[name=deficiency_codes]')[0].disabled")
            claim("codes are inert for OK and live for FAIL",
                  codes_disabled_initially and codes_enabled,
                  f"initial={codes_disabled_initially} after FAIL={codes_enabled}")
            inspection_stolen = page.evaluate(STOLEN_FACES_JS)
            claim("no face is stolen on the inspection sheet's controls",
                  inspection_stolen == [], str(inspection_stolen))
            page.click("#submit-inspection")
            page.wait_for_timeout(250)
            claim("a FAIL with no codes is refused in place, with words",
                  "Tick at least one deficiency code" in page.inner_text("#submit-status"),
                  page.inner_text("#submit-status"))
            claim("the code refusal declares the group's own failed state on EVERY chip",
                  page.evaluate("""() => {
                      const boxes = [...document.querySelectorAll('input[name=deficiency_codes]')];
                      const first = boxes[0];
                      const described = (first.getAttribute('aria-describedby') || '').split(/\\s+/);
                      return boxes.length > 1
                          && boxes.every(b => b.getAttribute('aria-invalid') === 'true')
                          && described.includes('submit-status');
                  }"""))
            # The refusal's other exit, verbatim from its message: "or record
            # the result as OK". Choosing it must clear the marks too.
            page.check("input[value=OK]")
            claim("switching the result to OK answers the refusal as promised",
                  page.evaluate(
                      "() => !document.querySelector('input[name=deficiency_codes]').hasAttribute('aria-invalid')"))
            page.check("input[value=FAIL]")
            page.click("#submit-inspection")
            page.wait_for_timeout(250)
            claim("the refusal marks the group again on the next empty submit",
                  page.evaluate(
                      "() => document.querySelector('input[name=deficiency_codes]')"
                      ".hasAttribute('aria-invalid')"))
            # Tick a box that is NOT the first: the refusal names the group
            # ("at least one"), so any member's edit must clear every member.
            page.locator("input[name=deficiency_codes]").nth(1).check()
            claim("ticking ANY code answers the refusal and clears the whole group",
                  page.evaluate(
                      "() => !document.querySelector('input[name=deficiency_codes]').hasAttribute('aria-invalid')"))

            # 6. the field queue — and its door
            page.goto(base + "/field/")
            page.wait_for_selector("#capture-form")
            field_stolen = page.evaluate(STOLEN_FACES_JS)
            claim("no face is stolen on the field capture sheet's controls",
                  field_stolen == [], str(field_stolen))
            # The door: a reading the server must refuse is refused HERE,
            # never queued — a queued copy would fail every sync forever.
            page.click("#queue-offline")
            page.wait_for_timeout(200)
            claim("an empty reading is refused at the queue door, not queued",
                  page.evaluate("() => document.getElementById('status').textContent.includes('Select a hydrant')"),
                  page.evaluate("() => document.getElementById('status').textContent"))
            claim("the refusal declares the hydrant select's failed state",
                  page.evaluate(
                      "() => document.getElementById('hydrant').getAttribute('aria-invalid') === 'true'"))
            page.select_option("#hydrant", index=1)
            page.click("#queue-offline")
            page.wait_for_timeout(200)
            claim("a reading with no date is refused, still not queued",
                  page.evaluate("() => document.getElementById('status').textContent.includes('date the readings')"),
                  page.evaluate("() => document.getElementById('status').textContent"))
            page.fill("#tested_on", "2026-09-30")
            page.click("#queue-offline")
            page.wait_for_timeout(200)
            claim("a reading with no pressures is refused, still not queued",
                  page.evaluate("() => document.getElementById('status').textContent.includes('both required')"),
                  page.evaluate("() => document.getElementById('status').textContent"))
            page.fill("#static_psi", "80")
            page.fill("#residual_psi", "40")
            page.click("#queue-offline")
            page.wait_for_timeout(200)
            claim("a reading with no flow figure is refused, still not queued",
                  page.evaluate("() => document.getElementById('status').textContent.includes('pitot reading, or a meter total')"),
                  page.evaluate("() => document.getElementById('status').textContent"))
            claim("and the queue table still holds nothing",
                  page.evaluate("() => !document.getElementById('queue-table').textContent.includes('H-')"),
                  page.evaluate("() => document.getElementById('queue-table').textContent").replace("\n", " ")[:80])
            page.fill("[data-outlet='pitot'][data-index='0']", "28")
            page.click("#queue-offline")
            page.wait_for_function(
                "() => document.getElementById('queue-table').textContent.includes('H-')", timeout=6000)
            claim("a complete queued reading lands in the on-device queue",
                  "H-" in page.inner_text("#queue-table"), page.inner_text("#queue-table").replace("\n", " ")[:80])
            claim("editing the fields cleared the door's marks",
                  page.evaluate(
                      "() => !document.querySelector('#capture-form [aria-invalid=\"true\"]')"))

            # 7. the theme toggle
            page.click("#theme-toggle")
            claim("the theme toggle flips the document to dark",
                  page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark")
            page.click("#theme-toggle")
            claim("and back to light",
                  page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light")

            # 8. focus visibility on the first tab stop
            page.goto(base + "/dashboard/")
            page.wait_for_selector(".board__pip")
            page.keyboard.press("Tab")
            ring = page.evaluate(
                "() => { const el = document.activeElement; return getComputedStyle(el).boxShadow; }")
            claim("the first tab stop paints a focus ring", "rgb" in ring, ring)

            # 9. the narrow breakpoint
            page.set_viewport_size({"width": 375, "height": 812})
            page.wait_for_timeout(250)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            claim("no horizontal overflow at 375 px", overflow <= 1, f"overflow={overflow}px")
            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-dashboard-375.png"), full_page=True)
            page.goto(base + "/field/")
            page.wait_for_selector("#capture-form")
            overflow_field = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            claim("the field page fits 375 px", overflow_field <= 1, f"overflow={overflow_field}px")
            page.goto(base + f"/hydrants/{ready.pk}/")
            page.wait_for_selector("#status-event-form")
            overflow_detail = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            claim("the hydrant ledger (with the status door) fits 375 px",
                  overflow_detail <= 1, f"overflow={overflow_detail}px")
            page.goto(base + f"/parcels/{parcel_pk}/edit/")
            page.wait_for_selector("#parcel-form")
            overflow_parcel = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            claim("the parcel form fits 375 px", overflow_parcel <= 1, f"overflow={overflow_parcel}px")
            page.screenshot(path=str(REPO_ROOT / ".tmp" / "walk-field-375.png"), full_page=True)

            # 10. no page errors anywhere in the walk
            # /api/v1/overlays/active/ answers 404 when the district has no
            # overlay; map.js handles that as "no overlay" by design. The
            # resolve-queue 400 is the refusal step 2c drives on purpose — the
            # browser logs every failed fetch, and that one is the product
            # refusing exactly as the contract says it should.
            noisy = [e for e in console_errors
                     if "favicon" not in e.lower() and "overlays/active" not in e.lower()
                     and "404 (Not Found)" not in e
                     and not ("400 (Bad Request)" in e and "/resolve/" in e)]
            claim("no JavaScript errors during the walk", noisy == [], "; ".join(noisy[:3]))

            browser.close()
    finally:
        if server is not None:
            server.terminate()
        runner.teardown_databases(old_config)
        teardown_test_environment()

    print("\n".join(RESULTS))
    failures = [r for r in RESULTS if r.startswith("FAIL")]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
