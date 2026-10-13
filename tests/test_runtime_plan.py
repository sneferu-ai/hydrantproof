"""The runtime test plan's journeys and the product facts they depend on.

``test_plan.md`` is the browser plan the runtime proof executes through a fixed
action DSL (goto / click / fill / wait_for / expect_text / expect_url /
evaluate).  The plan's first run produced four failures that were journey
mechanics, not product behaviour: a hidden CSRF input waited on for visibility,
two download endpoints navigated to as pages, and a sign-out written as
injected JavaScript the executor never ran (the action used the key
``expression``; the executor reads ``script``).  The same run's receipt shows
two quieter mechanics defects: the three capture items hollow-passed — the
tester signed in, waited for ``<body>`` and asserted nothing — because the
items instructed capabilities the DSL does not have ("take the context
offline", a driver-supplied GPS position, a file-picker photo, a queue carried
into the next item's fresh browser context), and the "Notes on scope" bullets
parsed as plan items 22–24, burning three more hollow journeys on prose.

A later run re-synthesized the plan into the same two failure classes once
more: the anonymous-surface item read the 401 by asserting text on ``<body>``
while the page was still ``about:blank`` (a JSON endpoint is not a page, and
the ``expression``-keyed fetch before it never ran), and the sign-out item's
own fetch again used ``expression`` — the silent no-op left the session alive,
so the following ``/dashboard/`` navigation never redirected to ``/login/``.
Both items now carry exact embedded actions: navigation proves the redirects,
``script``-keyed fetches prove the JSON statuses, and the sign-out is the
product's own POST-form control.  These tests pin the corrected mechanics and
the surfaces the journeys address, so the next edit to either fails here
instead of in a paid runtime run.

The retry run (still executing the plan sealed into the built artifact)
failed 16 items of 24: the executed tester plan wrote its fetches under the
``expression`` key — silent no-ops, so nothing ever reached the page — and
every item asserting a signed-in surface had to improvise its own sign-in
because the plan never said so.  Items 7-8 (dashboard, hydrant detail) and
11-13 (the delegated ledger read-only sides) now sign in first, the delegated
items carry exact read-only actions instead of leaving the tester to improvise
a write, and the mechanics section names the signed-in census.

The retry-2 receipt (2026-10-04, the 24-item plan still sealed in the built
artifact) failed 13 items; twelve were one mechanics class — the tester wrote
its API results into ``document.title`` inside ``expression``-keyed evaluates
(silent no-ops) and then waited for ``title`` to become VISIBLE (which can
never happen, the element lives in ``<head>``) or asserted ``expect_text`` on
``title`` against its own marker; the thirteenth was the phantom item 22 the
old plan's Notes bullets minted, fixed by the Notes-as-paragraphs change.  The
plan now names the title law (``title`` is never visible and never a result
channel; async results belong in one ``script``-keyed evaluate that throws —
the executor awaits its returned promise), item 8 carries the exact
list→detail actions the tester had to improvise, and the pins below hold
both.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

REPO = pathlib.Path(__file__).resolve().parent.parent
PLAN = (REPO / "test_plan.md").read_text(encoding="utf-8")

#: The endpoints that answer with file downloads (never ordinary pages).
DOWNLOAD_URLS = (
    "/api/v1/exports/dispatch/",
    "/api/v1/exports/forms/flow-test/",
    "/api/v1/exports/forms/inventory/",
)

_ACTION_RE = re.compile(r"`(\{\"op\".*?\})`", re.S)
_ITEM_RE = re.compile(r"^(\d+)\.\s")


def plan_actions() -> list[dict]:
    """Every executable action example embedded in the plan, parsed."""
    return [json.loads(match.group(1)) for match in _ACTION_RE.finditer(PLAN)]


def plan_items() -> dict[int, str]:
    """The plan's numbered items, keyed by number (``15a``/``15b`` fold into 15)."""
    items: dict[int, str] = {}
    current: int | None = None
    for line in PLAN.splitlines():
        match = _ITEM_RE.match(line)
        if match:
            current = int(match.group(1))
            items[current] = line + "\n"
        elif current is not None:
            items[current] += line + "\n"
    return items


class TestPlanJourneyMechanics:
    def test_every_embedded_action_is_a_valid_json_object(self):
        actions = plan_actions()
        assert actions, "the plan carries no executable action examples"
        for action in actions:
            assert isinstance(action.get("op"), str) and action["op"], action

    def test_evaluate_actions_use_the_key_the_executor_reads(self):
        """The executor runs ``page.evaluate(action["script"])``.  A plan that
        writes ``expression`` evaluates an empty string, the action is a no-op,
        and the journey passes without asserting anything — the defect class
        behind the first run's hollow items."""
        evaluate_actions = [a for a in plan_actions() if a["op"] == "evaluate"]
        assert evaluate_actions, "the plan has no evaluate-based checks"
        for action in evaluate_actions:
            assert isinstance(action.get("script"), str) and action["script"].strip(), action
            assert "expression" not in action, action

    def test_download_endpoints_are_verified_with_fetch_scripts(self):
        """A ``goto`` to any of these starts a file download and aborts the
        journey with "Download is starting"; the plan verifies them by
        fetching the bytes from a signed-in page instead."""
        scripts = "\n".join(
            a.get("script", "") for a in plan_actions() if a["op"] == "evaluate"
        )
        assert "fetch(" in scripts
        for url in DOWNLOAD_URLS:
            assert url in scripts, f"{url} is not verified by a fetch script"
        assert "Download is starting" in PLAN
        for action in plan_actions():
            if action["op"] == "goto":
                assert not any(url in action.get("url", "") for url in DOWNLOAD_URLS)

    def test_the_row_creating_write_is_the_last_real_write_in_item_20(self):
        """The demo-deactivation invariant fires on the first non-sample row and
        the writing session is anonymous immediately after, so the plan performs
        exactly one row-creating write and it is item 20 (item 9's POST is the
        400 refusal, which creates nothing)."""
        writes = [
            number
            for number, text in plan_items().items()
            if "/tests/" in text and "method:'POST'" in text
        ]
        assert writes == [20], writes
        assert "deactivates every demo account" in PLAN

    def test_item_20_carries_the_fixture_a_numbers(self):
        item = plan_items()[20]
        assert "1105.2" in item and "capacity_class" in item

    def test_capture_journeys_delegate_the_sync_outcome_to_the_gate_suite(self):
        """A synced capture is a non-sample row — the deactivation trigger —
        and the field page syncs the queue the moment it sees connectivity
        (``ledger/static/ledger/field.js``: the ``online`` listener and the
        page-load check).  The first run's items read "submit online → exactly
        one flow test exists" and "return online → the queue syncs and creates
        exactly one test" — executable instructions a tester could turn into
        the write that deactivates the demo accounts and cascade-fails items
        17–21.  The sync's server-side outcome belongs to the gate suite, and
        the capture journeys must say so."""
        for hazardous in (
            "submit online → exactly one flow test exists",
            "queue syncs and creates exactly one test",
        ):
            assert hazardous not in PLAN, hazardous
        items = plan_items()
        assert "tests/test_ledger_api.py" in items[16]
        assert "tests/test_ledger_api.py" in items[17]

    def test_capture_journeys_use_only_executor_capabilities(self):
        """The executor's DSL is goto / click / fill / wait_for / expect_text
        / expect_url / screenshot / evaluate / restart_context — it has NO
        offline, geolocation or file-picker control, and every ordinary item
        runs in a FRESH browser context (a new ``browser.new_page()`` per
        entry, so no IndexedDB queue carries between items).  The first run's
        receipt is the evidence: items 16–18 asked for exactly those missing
        capabilities and hollow-passed, executing nothing past sign-in.  The
        capture journeys must therefore steer the product's own controls —
        queue through "Queue for later" (``#queue-offline``, whose handler
        writes IndexedDB and nothing else), mock GPS with an ``evaluate``
        stub, attach a generated ``File``, and let item 18 queue its own
        reading."""
        items = plan_items()
        section = items[16] + items[17] + items[18]
        for unexecutable in (
            "context offline",
            "still offline",
            "driver-supplied",
            "(item 18 exports it)",
        ):
            assert unexecutable not in section, unexecutable
        assert "#queue-offline" in items[17]
        assert "#queue-offline" in items[18], (
            "item 18 runs in a fresh context — it must queue its own reading"
        )
        for number in (16, 17, 18):
            assert '{"op": "goto", "url": "/field/"}' in items[number], (
                f"item {number} never opens /field/ — a fresh-context journey "
                "starts wherever the sign-in left it"
            )
        item18 = items[18]
        queue_click = item18.find('"op": "click", "selector": "#queue-offline"')
        queue_confirm = item18.find("queue table never listed the capture")
        export_click = item18.find('"op": "click", "selector": "#export-csv"')
        assert -1 < queue_click < queue_confirm < export_click, (
            "item 18 must confirm the async IndexedDB write before exporting — "
            "an immediate export races it and reads an empty queue"
        )

    def test_no_capture_journey_clicks_the_online_submit(self):
        """``#submit-online`` POSTs ``/api/v1/hydrants/{id}/tests/`` — the
        deactivation trigger — so no capture journey's embedded actions may
        click it, however the prose evolves."""
        items = plan_items()
        for number in (16, 17, 18):
            for match in _ACTION_RE.finditer(items[number]):
                action = json.loads(match.group(1))
                assert not (
                    action.get("op") == "click"
                    and action.get("selector") == "#submit-online"
                ), f"item {number} clicks the online submit"

    def test_no_journey_waits_for_the_hidden_csrf_input(self):
        """``wait_for`` observes visibility only; the login form's CSRF token
        is a ``type="hidden"`` input, so a wait on it times out after 5–10 s
        (the first run's second failure).  No plan action may wait for it."""
        for action in plan_actions():
            if action["op"] == "wait_for":
                assert "csrfmiddlewaretoken" not in (
                    action.get("selector") or ""
                ), action

    def test_no_plan_action_waits_for_or_asserts_on_the_document_title(self):
        """The retry-2 run's tester wrote every API result into
        ``document.title`` (``'csrf-present:valid'``, ``'201'``,
        ``'verify:true'``) inside an ``expression``-keyed evaluate — a silent
        no-op — then either waited for ``title`` to become VISIBLE (never:
        the element lives in ``<head>``, and ``wait_for`` observes visibility
        only, so 10 of 24 items burned a 10–20 s timeout on a correct page)
        or asserted ``expect_text`` on ``title`` against its own marker.
        Twelve of the receipt's thirteen failing rows are this class (the
        thirteenth was the phantom item 22 from the old plan's Notes bullets,
        fixed by the Notes-as-paragraphs change).  No embedded action may
        target ``title`` and no embedded script may write it."""
        for action in plan_actions():
            assert (action.get("selector") or "") != "title", action
            assert "document.title" not in (action.get("script") or ""), action

    def test_the_plan_names_the_title_mechanics(self):
        """The correction must live in the plan the tester reads: the title
        is never visible and never a result channel; async results belong in
        one ``script``-keyed evaluate that throws (the executor awaits its
        returned promise, so there is nothing to wait for); page readiness is
        a ``wait_for`` on a visible element.  Prose hard-wraps, so match on
        the whitespace-collapsed text (the signed-in census pin learned this
        the hard way)."""
        flat = " ".join(PLAN.split())
        assert "never a result channel" in flat
        assert "document.title" in flat
        assert "awaits the promise" in flat
        assert "`<title>`" in flat, (
            "the mechanics paragraph must name the document <title> element"
        )

    def test_item_8_carries_the_exact_list_to_detail_actions(self):
        """Item 8 was prose-only: the retry-2 tester stayed on the dashboard,
        checked for ``a[href*="/hydrants/"]`` links THERE (the header's own
        "Hydrants" nav link satisfies that prefix), smuggled the result
        through ``document.title`` (a no-op) and never reached the detail
        page.  The journey must carry its exact actions: the list page → the
        table's row link → the detail page → a script proving every section
        the item names and the map's 1000-ft ring from the rendered DOM, and
        then the restart/recover close — a fresh context is anonymous again,
        sign-in returns to the record, and the recovered page still renders
        its ledger."""
        item = plan_items()[8]
        actions = [json.loads(m.group(1)) for m in _ACTION_RE.finditer(item)]
        assert [a["op"] for a in actions] == [
            "goto", "wait_for", "click", "wait_for", "evaluate",
            "restart_context", "goto", "wait_for", "click", "expect_url",
            "fill", "fill", "click", "wait_for", "evaluate",
        ], actions
        assert actions[0].get("url") == "/hydrants/", actions[0]
        assert actions[1].get("selector") == "table a[href^='/hydrants/']", actions[1]
        assert actions[2].get("selector") == "table a[href^='/hydrants/']", actions[2]
        assert actions[3].get("selector") == "h1", actions[3]
        script = actions[4].get("script", "")
        for heading in (
            "Attributes", "Map", "Flow tests", "Inspections",
            "Deficiencies", "Status history", "Attribute history",
        ):
            assert heading in script, heading
        assert "leaflet-container" in script, (
            "the journey must prove the per-hydrant map initialized"
        )
        assert "leaflet-overlay-pane" in script, (
            "the journey must prove the map's 1000-ft ring rendered"
        )
        # The close: restart into a fresh context, land anonymous, sign in
        # through the product's own control, and recover the same record.
        assert actions[6].get("url") == "/hydrants/", actions[6]
        assert actions[7].get("selector") == "table a[href^='/hydrants/']", actions[7]
        assert actions[8].get("selector") == "table a[href^='/hydrants/']", actions[8]
        assert actions[9].get("value") == "/login/", actions[9]
        assert actions[10].get("selector") == "input[name=username]", actions[10]
        assert actions[11].get("selector") == "input[name=password]", actions[11]
        assert actions[12].get("selector") == "form[action='/login/'] button[type=submit]", actions[12]
        assert actions[13].get("selector") == "table", actions[13]
        recovery = actions[14].get("script", "")
        assert "Flow tests" in recovery, recovery
        assert "table tbody tr" in recovery, (
            "recovery must assert the recovered page rendered ledger rows, not just a 200"
        )

    def test_the_plan_names_the_expression_key_no_op(self):
        """The executor runs ``page.evaluate(action["script"])``; an action
        keyed ``expression`` is a silent no-op (the first run's sign-out was
        written this way and never ran).  The plan's mechanics must say so,
        so the tester is never left to rediscover it in a paid run."""
        assert "expression" in PLAN
        assert "silent no-op" in PLAN
        assert 'page.evaluate(action["script"])' in PLAN

    def test_anonymous_surface_item_navigates_first_and_never_reads_json_text(self):
        """The first run asserted the 401 by reading ``<body>`` while the
        page was still ``about:blank`` — a JSON endpoint is not a page.  Item
        4 must prove the redirect with navigation actions and the 401 with a
        ``script``-keyed fetch that runs only after a ``goto``."""
        item = plan_items()[4]
        actions = [json.loads(m.group(1)) for m in _ACTION_RE.finditer(item)]
        assert actions, "item 4 carries no executable actions"
        assert [a["op"] for a in actions] == [
            "goto", "expect_url", "wait_for", "evaluate",
        ], actions
        assert actions[0].get("url") == "/dashboard/", actions[0]
        assert "/login/" in actions[1].get("value", ""), actions[1]
        assert actions[2].get("selector") == "input[name=username]", actions[2]
        for action in actions:
            assert "expression" not in action, action
        assert all(a["op"] != "expect_text" for a in actions), (
            "the 401 is asserted inside the fetch script, never as page text"
        )
        assert "Authentication required." in actions[3].get("script", ""), (
            "the fetch script must assert the pinned 401 detail"
        )
        assert "api/v1/hydrants/" in actions[3].get("script", ""), actions[3]

    def test_sign_out_journey_clicks_the_product_control(self):
        """The sign-out journey failed twice: once with a ``wait_for`` on the
        sign-in form's username input while the session was still alive (the
        fetch that was meant to log out used the ``expression`` key and never
        ran), once with a navigation to ``/dashboard/`` that expected the
        login redirect while still signed in.  The journey must click the
        product's own POST-form control and prove the redirect by
        navigation, with the fetch assertion last so a dropped script cannot
        break the navigation proof."""
        item = plan_items()[19]
        actions = [json.loads(m.group(1)) for m in _ACTION_RE.finditer(item)]
        assert actions, "item 19 carries no executable actions"
        assert [a["op"] for a in actions] == [
            "click", "wait_for", "expect_text", "goto", "expect_url",
            "evaluate",
        ], actions
        assert actions[0].get("selector") == (
            "form[action='/logout/'] button[type=submit]"
        ), actions[0]
        for action in actions:
            assert "expression" not in action, action
            if action["op"] == "evaluate":
                assert "/logout/" not in action.get("script", ""), (
                    "the journey may not log out with its own JavaScript — "
                    "the product's control is the only sign-out"
                )
        assert actions[3].get("url") == "/dashboard/", actions[3]
        assert "/login/" in actions[4].get("value", ""), actions[4]
        assert "Do NOT write your own JavaScript" in item

    def test_every_evaluate_journey_reaches_an_app_page_first(self):
        """A fresh browser context starts on ``about:blank``: an ``evaluate``
        there finds an empty DOM, and a relative ``fetch`` has no origin to
        resolve against (a TypeError) — both fail the journey on a correct
        product.  Every item whose embedded actions include an ``evaluate``
        must therefore reach an app page first: either the item's prose
        commands the sign-in journey (which lands on the dashboard), or the
        item embeds its own leading ``goto``.  Items 5 and 21 are the
        anonymous cases — no sign-in — so their ``goto`` is embedded."""
        for number, text in plan_items().items():
            actions = [json.loads(m.group(1)) for m in _ACTION_RE.finditer(text)]
            if not any(a["op"] == "evaluate" for a in actions):
                continue
            navigates_first = actions[0]["op"] == "goto"
            prose = " ".join(text.lower().split())  # the plan hard-wraps prose
            signs_in = "sign in" in prose
            assert navigates_first or signs_in, (
                f"item {number} evaluates without first reaching an app page"
            )

    def test_every_signed_in_journey_signs_in_first(self):
        """Every item runs in a FRESH browser context (``_execute_plan_entry``
        → ``browser.new_page()``), so an item that asserts a signed-in
        surface must command its own sign-in.  The retry run's dashboard item
        went straight to ``/dashboard/``, landed on ``/login/?next=/dashboard/``
        and failed against a correct product ("expected 'READY'") — the
        adjudication panel denied it as a product finding.  Items 7
        (dashboard), 8 (hydrant detail) and 11-13 (ledger read-only sides)
        carried no sign-in instruction while the rest of the census did; the
        census is every item that reads or asserts a signed-in surface, and
        the mechanics section must name it."""
        items = plan_items()
        signed_in = (7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20)
        for number in signed_in:
            assert re.search(r"sign\s+in", items[number], re.IGNORECASE), (
                f"item {number} asserts a signed-in surface but never signs "
                "in — its fresh browser context starts anonymous, so it "
                "lands on /login/ and fails a correct product"
            )
        assert "every signed-in item (items 7-20) signs in first" in PLAN

    def test_delegated_ledger_items_carry_read_only_actions(self):
        """Items 11-13 delegate their row-creating outcomes to the gate suite
        (the ordering law: one real write, item 20).  The tester must still
        emit executable actions per item, and the retry run's executed plan
        shows what improvisation produced when it had none: attempted POSTs
        behind ``expression``-keyed no-ops (TP-011/012/013), which on a fixed
        key would have deactivated the demo accounts mid-plan.  Each item
        must carry a ``script``-keyed read-only fetch and no POST."""
        for number in (11, 12, 13):
            actions = [
                json.loads(m.group(1))
                for m in _ACTION_RE.finditer(plan_items()[number])
            ]
            fetches = [a for a in actions if a["op"] == "evaluate"]
            assert fetches, f"item {number} carries no read-only action"
            for action in fetches:
                script = action.get("script", "")
                assert "fetch(" in script, action
                flat = script.replace(" ", "").lower()
                assert "method:'post'" not in flat and 'method:"post"' not in flat, (
                    f"item {number} delegates its write to item 20 — it may "
                    "not POST"
                )
            assert "tests/test_ledger_api.py" in plan_items()[number]

    def test_item_13_does_not_overclaim_item_8s_coverage(self):
        """Item 8 opens the FIRST hydrant's row link — the list orders by
        ``asset_tag`` (``ledger/pages.py``), so the target is HP-001, which
        the demo seed gives no deficiency rows (the FAIL-then-OK hydrant is
        HP-016 — ``seed_demo.py``).  Item 13 once claimed its read-only side
        "is asserted by item 8's detail journey" via "the seeded FAIL-then-OK
        hydrant's deficiency history" — a false cross-reference that sends
        the tester hunting for FAIL-then-OK content on the wrong hydrant's
        page, failing a correct product.  Item 13 names the phrase only as
        gate-suite write-path coverage, never as item 8's assertion."""
        item = plan_items()[13]
        assert "FAIL-then-OK" not in item, item

    def test_hydrant_detail_journey_names_the_row_link(self):
        """Item 8 navigates list → detail.  The header's own "Hydrants"
        navigation link shares the ``/hydrants/`` href prefix, so a bare
        ``a[href^='/hydrants/']`` selector clicks the nav link and never
        leaves the list — live-proven while repairing this round (the retry
        run's tester used exactly that selector and passed vacuously against
        the list page).  The item must name the table row link."""
        item = plan_items()[8]
        assert "table a[href^='/hydrants/']" in item, item
        assert "never a bare" in item

    def test_item_21_navigates_to_login_before_the_basic_401_fetch(self):
        """Item 21 forbids the form login (item 20 deactivated the account),
        so without an embedded navigation its lone fetch runs from
        ``about:blank`` and fails on a correct product — the same defect
        class as the first run's anonymous-surface item.  It goes to the
        anonymous login page first, proves the page rendered, and only then
        fetches with Basic."""
        item = plan_items()[21]
        actions = [json.loads(m.group(1)) for m in _ACTION_RE.finditer(item)]
        assert [a["op"] for a in actions] == ["goto", "wait_for", "evaluate"], actions
        assert actions[0].get("url") == "/login/", actions[0]
        assert actions[1].get("selector") == "input[name=username]", actions[1]
        script = actions[2].get("script", "")
        assert "Authorization" in script and "demo-editor" in script, actions[2]
        assert "401" in script, actions[2]

    def test_notes_on_scope_does_not_parse_as_plan_items(self):
        """The runtime proof's ``parse_test_plan`` splits on numbered AND
        bulleted markers (``^\\s*(?:\\d+[.)]|[-*+])\\s+``); the first run
        parsed the three "Notes on scope" bullets as plan items 22–24 and
        burned three hollow journeys on prose.  The notes stay paragraphs, so
        the plan parses to exactly its 21 numbered items."""
        marker_re = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+", re.MULTILINE)
        markers = marker_re.findall(PLAN)
        assert len(markers) == 21, markers


class TestJourneySurfaceFacts:
    """The page and response shapes the corrected journeys address."""

    def test_sign_out_is_a_post_form_with_a_submit_button(self, editor_user):
        client = Client()
        client.force_login(editor_user)
        body = client.get("/dashboard/").content.decode()
        form = re.search(r'<form[^>]*action="/logout/"[^>]*>.*?</form>', body, re.S)
        assert form, "the signed-in header has no /logout/ form for the journey to click"
        assert 'method="post"' in form.group(0)
        assert re.search(r'<button[^>]*type="submit"', form.group(0)), form.group(0)

    def test_the_sign_out_journey_end_to_end(self, editor_user):
        """The exact corrected browser journey, executed against the real
        views: sign in through the form, submit the header's own POST form
        (what the journey's click does — with the form's hidden CSRF token),
        land on ``/login/`` with the records line in ``<main>``, then an
        anonymous ``/dashboard/`` visit must redirect to ``/login/`` and the
        anonymous JSON API must answer the pinned 401."""
        client = Client()
        token = client.get("/login/").cookies["csrftoken"].value
        response = client.post(
            "/login/",
            {
                "username": editor_user.username,
                "password": "hydrantproof-test-pass",
                "csrfmiddlewaretoken": token,
            },
        )
        assert response.status_code == 302
        assert response["Location"] == "/dashboard/"

        dashboard = client.get("/dashboard/")
        assert dashboard.status_code == 200
        form = re.search(
            r'<form[^>]*action="/logout/"[^>]*>.*?</form>',
            dashboard.content.decode(),
            re.S,
        )
        assert form, "the header's sign-out control is missing"
        hidden = re.search(
            r'<input[^>]*type="hidden"[^>]*name="csrfmiddlewaretoken"'
            r'[^>]*value="([^"]+)"',
            form.group(0),
        )
        assert hidden, "the sign-out form carries no CSRF token to submit"

        response = client.post("/logout/", {"csrfmiddlewaretoken": hidden.group(1)})
        assert response.status_code == 302
        assert response["Location"] == "/login/"

        landing = client.get("/login/")
        assert landing.status_code == 200
        main = re.search(r"<main[^>]*>.*?</main>", landing.content.decode(), re.S)
        assert main and "District water-supply records" in main.group(0)

        anonymous = client.get("/dashboard/")
        assert anonymous.status_code == 302
        assert anonymous["Location"].startswith("/login/")

        hydrants = client.get("/api/v1/hydrants/")
        assert hydrants.status_code == 401
        assert hydrants.json()["detail"] == "Authentication required."

    def test_login_page_carries_the_hidden_csrf_input_and_records_line(self):
        body = Client().get("/login/").content.decode()
        assert re.search(
            r'<input[^>]*type="hidden"[^>]*name="csrfmiddlewaretoken"', body
        ), "the login form's CSRF token must be the hidden input the journey asserts"
        main = re.search(r"<main[^>]*>.*?</main>", body, re.S)
        assert main and "District water-supply records" in main.group(0), (
            "the sign-out journey asserts expect_text on <main> for the records "
            "line — it must render inside the login page's <main>"
        )
        username = re.search(r"<input[^>]*name=\"username\"[^>]*>", body)
        assert username and 'type="hidden"' not in username.group(0), (
            "the journeys wait_for input[name=username] — a hidden username "
            "input can never become visible (the original CSRF defect class)"
        )

    def test_login_form_submit_button_is_scoped_to_the_login_form(self):
        """The journeys click ``form[action='/login/'] button[type=submit]`` —
        a bare ``button[type=submit]`` also matches the header's Sign out
        button on a signed-in page."""
        body = Client().get("/login/").content.decode()
        form = re.search(r'<form[^>]*action="/login/"[^>]*>.*?</form>', body, re.S)
        assert form, "the login page has no form posting to /login/"
        assert re.search(r'<button[^>]*type="submit"', form.group(0)), form.group(0)

    def test_dispatch_export_answers_as_a_download(self, api):
        response = api.get("/api/v1/exports/dispatch/")
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")
        assert response["Content-Type"].startswith("text/csv")

    def test_printable_forms_answer_as_pdfs(self, api):
        for url in DOWNLOAD_URLS[1:]:
            response = api.get(url)
            assert response.status_code == 200, url
            assert response["Content-Type"] == "application/pdf", url
            assert response.content.startswith(b"%PDF"), url
