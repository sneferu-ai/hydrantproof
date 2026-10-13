"""The UI system's own contract (DESIGN.md § 10).

Three automated audits, one manual one:

1. **Token closure** — no raw colour, raw length or raw duration outside
   `tokens.css`; no inline style on a template except the one data-driven
   percentage width the readiness proportion bar needs.
2. **Anti-defaults** — the § 9 list, checked by name: banned families, pure
   black/white, gradient heroes, emoji, the deleted `.kpi` class.
3. **Page contract** — every page renders, carries exactly one sign-out form,
   and every control in a form has a real `<label for>`.

The manual state walk is reported in the round handoff; it is never asserted
here, because a test that claims a browser was driven is a test that lies.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ledger" / "static" / "ledger"
TEMPLATES = ROOT / "ledger" / "templates" / "ledger"
TOKENS = STATIC / "tokens.css"

REQUIRED_TOKENS = (
    # surfaces
    "--bg-base", "--bg-elevated", "--bg-overlay",
    # foreground
    "--fg-primary", "--fg-secondary", "--fg-tertiary", "--fg-disabled",
    # borders
    "--border-subtle", "--border-default", "--border-strong", "--border-focus",
    # accent
    "--accent", "--accent-hover", "--accent-pressed",
    # status — this product's four states, which is the same *shape* the
    # generic running/paused/halted/complete palette asks for: one hue per
    # state a record can be in, each with a background, a border and an edge.
    "--status-ready", "--status-watch", "--status-notready", "--status-neutral",
    "--status-info",
    # type
    "--font-display", "--font-body", "--font-mono",
    "--text-xs", "--text-sm", "--text-base", "--text-md", "--text-lg",
    "--text-xl", "--text-2xl", "--text-3xl",
    "--weight-regular", "--weight-medium", "--weight-semibold", "--weight-bold",
    "--line-tight", "--line-snug", "--line-normal", "--line-relaxed",
    # space
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6",
    "--space-7", "--space-8", "--space-9", "--space-10", "--space-11", "--space-12",
    # shape
    "--radius-xs", "--radius-sm", "--radius-md", "--radius-lg", "--radius-xl",
    "--radius-pill", "--radius-full",
    # elevation
    "--shadow-0", "--shadow-1", "--shadow-2", "--shadow-3", "--shadow-4",
    # motion
    "--dur-instant", "--dur-fast", "--dur-base", "--dur-slow",
    "--ease-default", "--ease-emphasized",
    # z
    "--z-dropdown", "--z-sticky", "--z-modal-bg", "--z-modal", "--z-toast",
)

RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
RAW_LENGTH = re.compile(r"(?<![\w-])\d+(?:\.\d+)?(?:px|rem|em|pt)\b")
RAW_DURATION = re.compile(r"(?<![\w-])\d+(?:\.\d+)?m?s\b")
# The one permitted inline style: a percentage width computed from live counts.
INLINE_PERCENT = re.compile(r'^style="(?:width|height): \{\{.*?\}\}%"$')


def css_files():
    return sorted(p for p in STATIC.rglob("*.css") if p.name != "tokens.css")


def css_block(text: str, selector: str) -> str:
    """The body of the first `selector { ... }` block, brace-matched.

    A plain `text.split(selector)` is not enough: the token file's own comments
    name the selectors it defines, and a comment must never satisfy a check.
    """
    pattern = re.compile(r"(?m)^\s*" + re.escape(selector) + r"\s*\{")
    match = pattern.search(text)
    assert match, f"{selector} not found"
    index = match.end()
    depth = 1
    start = index
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    return text[start:index - 1]


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def selector_before(needle: str):
    """The selector of the rule each `needle` declaration sits in.

    Walks back to the opening brace, then past any at-rule prelude, so a
    declaration inside `@media` still reports the selector that owns it.
    """
    found = set()
    for path in css_files():
        text = path.read_text()
        for match in re.finditer(re.escape(needle), text):
            head = text[:match.start()]
            depth = 0
            index = len(head) - 1
            while index >= 0:
                if head[index] == "}":
                    depth += 1
                elif head[index] == "{":
                    if depth == 0:
                        break
                    depth -= 1
                index -= 1
            line_start = head.rfind("\n", 0, index) + 1
            selector = head[line_start:index].strip().splitlines()[-1].strip()
            while selector.startswith("@"):
                head = head[:line_start]
                index = len(head) - 1
                while index >= 0:
                    if head[index] == "}":
                        depth += 1
                    elif head[index] == "{":
                        if depth == 0:
                            break
                        depth -= 1
                    index -= 1
                line_start = head.rfind("\n", 0, index) + 1
                selector = head[line_start:index].strip().splitlines()[-1].strip()
            found.add(selector)
    return found


def gradient_selectors():
    return selector_before("linear-gradient")


def template_files():
    return sorted(TEMPLATES.glob("*.html"))


class TestTokenClosure:
    def test_no_raw_colour_outside_the_token_layer(self):
        offenders = []
        for path in css_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith("/*") or line.strip().startswith("*"):
                    continue
                if RAW_COLOUR.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "raw colour outside tokens.css:\n" + "\n".join(offenders)

    def test_no_raw_length_outside_the_token_layer(self):
        offenders = []
        for path in css_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith("/*") or line.strip().startswith("*"):
                    continue
                if line.strip().startswith("@media"):
                    continue   # a breakpoint cannot read a custom property
                if RAW_LENGTH.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "raw length outside tokens.css:\n" + "\n".join(offenders)

    def test_no_raw_duration_outside_the_token_layer(self):
        offenders = []
        for path in css_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith("/*") or line.strip().startswith("*"):
                    continue
                if RAW_DURATION.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "raw duration outside tokens.css:\n" + "\n".join(offenders)

    def test_javascript_names_no_colour_of_its_own(self):
        offenders = []
        for path in sorted(STATIC.rglob("*.js")):
            text = path.read_text()
            for number, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("//") or line.strip().startswith("*"):
                    continue
                if RAW_COLOUR.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "raw colour in JavaScript:\n" + "\n".join(offenders)

    def test_javascript_never_sets_an_inline_colour(self):
        """A colour written through `el.style.<colour> = …` is captured from the
        token layer once, at script time: it cannot follow a theme flip and no
        later state can clear it through the cascade. State hues ride classes
        (`.warn`, `.error`), whose values the stylesheet owns in both themes."""
        offenders = []
        for path in sorted(STATIC.rglob("*.js")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith("//") or line.strip().startswith("*"):
                    continue
                if re.search(r"\.style\.(color|background|backgroundColor|borderColor|fill|outlineColor)\s*=", line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "inline colour set from JavaScript:\n" + "\n".join(offenders)

    def test_templates_have_no_inline_design_values(self):
        offenders = []
        for path in template_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for match in re.finditer(r"style=\"[^\"]*\"", line):
                    if not INLINE_PERCENT.match(match.group(0)):
                        offenders.append(f"{path.name}:{number}: {match.group(0)}")
        assert offenders == [], (
            "inline styles other than a computed percentage width:\n" + "\n".join(offenders)
        )

    def test_required_token_families_are_declared(self):
        root = css_block(TOKENS.read_text(), ":root")
        missing = [token for token in REQUIRED_TOKENS if f"{token}:" not in root]
        assert missing == [], f"tokens.css is missing: {missing}"

    def test_both_themes_declare_the_same_token_names(self):
        text = TOKENS.read_text()
        light = css_block(text, ":root")
        dark_blocks = [css_block(text, '[data-theme="dark"]')]
        media = re.search(r"@media \(prefers-color-scheme: dark\) \{", text)
        assert media, "no prefers-color-scheme block"
        dark_blocks.append(css_block(text[media.end():], ':root:not([data-theme="light"])'))

        def names(block: str):
            return {m.group(1) for m in re.finditer(r"(--[a-z0-9-]+):", block)}

        light_names = names(light)
        # Surfaces, foreground, borders, accent, status and elevation are
        # re-declared per theme; type, spacing, shape and motion are shared.
        # Theme-invariant by design: `none` is `none` in both themes, and the
        # NFPA 291 class palette mirrors the ReportLab pack renderer — two
        # renderers, one palette, so it may not fork per theme.
        theme_invariant = {
            "--shadow-0",
            "--class-aa", "--class-a", "--class-b", "--class-c", "--class-none", "--class-ink",
        }
        theme_scoped = {
            name for name in light_names
            if name.startswith(("--bg-", "--fg-", "--border-", "--accent", "--status-",
                                "--danger-", "--class-", "--shadow-"))
        } - theme_invariant
        for block in dark_blocks:
            dark_names = names(block)
            missing = sorted(theme_scoped - dark_names)
            assert missing == [], f"a dark block is missing theme-scoped tokens: {missing}"


# ---------------------------------------------------------------------------
# Computed WCAG contrast — the audit the structural scans cannot see.
# A textual check proves closure, not legibility: the round-3 review found two
# shipped pairings below the 4.5:1 AA floor (light `--fg-tertiary` on
# `--bg-sunken` and `--class-ink` on `--class-c`) that every scan had passed.
# Contrast is arithmetic, so these pins compute it from the token layer itself
# — a token edit can no longer move a pairing under the floor silently. The
# measurement is calibrated against WCAG's own reference pair (#767676 on
# #FFFFFF = 4.54:1) before any pairing is judged.
# ---------------------------------------------------------------------------

def relative_luminance(colour: str) -> float:
    """WCAG 2.x relative luminance of a `#RRGGBB` colour."""
    raw = colour.strip().lstrip("#")
    channels = [int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio (1.0–21.0)."""
    high, low = sorted(
        (relative_luminance(foreground), relative_luminance(background)), reverse=True
    )
    return (high + 0.05) / (low + 0.05)


def token_map(block: str) -> dict:
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r"(--[a-z0-9-]+):\s*([^;]+);", block)
    }


def theme_tokens() -> tuple[dict, dict]:
    """The token layer as the cascade resolves it, per theme.

    Tokens declared once in `:root` (the theme-invariant capacity palette)
    fall through to dark; the `prefers-color-scheme` block is pinned equal to
    the explicit dark block separately.
    """
    text = strip_comments(TOKENS.read_text())
    light = token_map(css_block(text, ":root"))
    dark = {**light, **token_map(css_block(text, '[data-theme="dark"]'))}
    return light, dark


class TestComputedContrast:
    """Every shipped text pairing clears WCAG 2.2 AA (1.4.3): 4.5:1.

    `--fg-disabled` is deliberately absent: WCAG exempts inactive controls,
    and disabled text is exactly that.
    """

    SURFACES = ("--bg-base", "--bg-sunken", "--bg-elevated", "--bg-raised")

    def test_the_measurement_reproduces_the_canonical_reference_pair(self):
        # WCAG's own worked example: #767676 on #FFFFFF = 4.54:1.
        assert abs(contrast_ratio("#767676", "#FFFFFF") - 4.54) < 0.01

    def test_foregrounds_clear_the_floor_on_every_surface(self):
        for theme, tokens in zip(("light", "dark"), theme_tokens()):
            for fg in ("--fg-primary", "--fg-secondary", "--fg-tertiary"):
                for bg in self.SURFACES:
                    ratio = contrast_ratio(tokens[fg], tokens[bg])
                    assert ratio >= 4.5, f"{theme}: {fg} on {bg} = {ratio:.3f}:1 < 4.5:1"

    def test_the_class_chip_letter_clears_the_floor_on_every_hue(self):
        light, _ = theme_tokens()
        for hue in ("--class-aa", "--class-a", "--class-b", "--class-c", "--class-none"):
            ratio = contrast_ratio(light["--class-ink"], light[hue])
            assert ratio >= 4.5, f"--class-ink on {hue} = {ratio:.3f}:1 < 4.5:1"

    def test_status_accent_and_banner_text_clear_the_floor(self):
        for theme, tokens in zip(("light", "dark"), theme_tokens()):
            for status in ("--status-ready", "--status-watch", "--status-notready",
                           "--status-info", "--status-neutral"):
                for bg in (f"{status}-bg", *self.SURFACES):
                    ratio = contrast_ratio(tokens[status], tokens[bg])
                    assert ratio >= 4.5, f"{theme}: {status} on {bg} = {ratio:.3f}:1 < 4.5:1"
            for fg, bg in (
                ("--accent-on", "--accent"),
                ("--accent-on", "--accent-hover"),
                ("--accent-on", "--accent-pressed"),
                ("--accent", "--accent-soft"),
            ):
                ratio = contrast_ratio(tokens[fg], tokens[bg])
                assert ratio >= 4.5, f"{theme}: {fg} on {bg} = {ratio:.3f}:1 < 4.5:1"
            # Banner body copy rides --fg-primary on the tinted state fills.
            for bg in ("--status-ready-bg", "--status-watch-bg", "--status-notready-bg",
                       "--status-info-bg", "--accent-soft"):
                ratio = contrast_ratio(tokens["--fg-primary"], tokens[bg])
                assert ratio >= 4.5, f"{theme}: --fg-primary on {bg} = {ratio:.3f}:1 < 4.5:1"

    def test_the_two_dark_blocks_declare_identical_values(self):
        """The explicit choice and the OS preference must resolve to one
        theme: sharing names is not enough if a value drifts between blocks."""
        text = TOKENS.read_text()
        explicit = token_map(css_block(text, '[data-theme="dark"]'))
        media = re.search(r"@media \(prefers-color-scheme: dark\) \{", text)
        assert media, "no prefers-color-scheme block"
        implicit = token_map(css_block(text[media.end():], ':root:not([data-theme="light"])'))
        drift = {
            name: (explicit.get(name), implicit.get(name))
            for name in set(explicit) | set(implicit)
            if explicit.get(name) != implicit.get(name)
        }
        assert drift == {}, f"the two dark blocks disagree: {drift}"


class TestTheDesignTableMatchesTheTokenLayer:
    """§ 3 documents the token layer; a documented cell that drifts from the
    shipped token misleads whoever tunes from the document (the round-3 review
    found two such cells). This pin turns the table into a test."""

    def _rows(self):
        text = (ROOT / "DESIGN.md").read_text()
        section = text.split("## 3. Colour tokens", 1)[1].split("## 4. Type tokens", 1)[0]
        rows = re.findall(
            r"^\|\s*`(--[a-z0-9-]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|",
            section, re.M,
        )
        assert len(rows) >= 20, f"the § 3 table parsed as {len(rows)} rows — its format changed"
        return rows

    def test_every_documented_cell_matches_the_shipped_token(self):
        light, dark = theme_tokens()
        for token, light_cell, dark_cell in self._rows():
            for theme, cell, tokens in zip(("light", "dark"), (light_cell, dark_cell), (light, dark)):
                expected = tokens[cell.lstrip("= ").strip()] if cell.startswith("=") else cell
                actual = tokens[token]
                assert actual.lower() == expected.lower(), (
                    f"DESIGN.md § 3 says {theme} `{token}` is {cell} but tokens.css ships {actual}"
                )

    def test_the_capacity_palette_is_documented(self):
        documented = {token for token, _, _ in self._rows()}
        missing = sorted({"--class-aa", "--class-a", "--class-b", "--class-c",
                          "--class-none", "--class-ink"} - documented)
        assert missing == [], f"the § 3 table does not document the class palette: {missing}"


class TestAntiDefaults:
    def test_no_banned_typeface(self):
        text = TOKENS.read_text() + "".join(p.read_text() for p in css_files())
        for banned in ("Inter", "Roboto", "Open Sans", "Lato", "Helvetica"):
            assert banned not in text, f"banned typeface named in the stylesheets: {banned}"

    def test_no_pure_black_or_pure_white(self):
        """Pure white is correct in exactly one place: the print block, where
        the medium is paper and white is the absence of toner."""
        text = strip_comments(TOKENS.read_text())
        print_block = css_block(text, "@media print")
        text = text.replace(print_block, "")
        for banned in ("#000000", "#000;", "#000 ", "#fff;", "#ffffff", "#FFFFFF;"):
            assert banned not in text, f"pure black/white token: {banned}"

    def test_gradients_are_only_where_they_are_declared(self):
        """Three gradients exist on purpose: the offline map graticule, the
        select control's caret, and the skeleton shimmer. A fourth — a hero, a
        panel fill, a button — is the cliche this product forbids."""
        allowed = {".map--graticule", ".select", ".skeleton::after"}
        assert gradient_selectors() <= allowed, (
            f"a gradient appeared outside the allowlist: {sorted(gradient_selectors() - allowed)}"
        )

    def test_backdrop_filter_is_only_the_modal_scrim(self):
        seen = selector_before("backdrop-filter")
        assert seen == {".modal__backdrop"}, f"backdrop-filter outside the modal scrim: {sorted(seen)}"

    def test_no_emoji_as_status_or_in_system_copy(self):
        pattern = re.compile(
            "[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]"
        )
        offenders = []
        for path in template_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        assert offenders == [], "emoji in a template:\n" + "\n".join(offenders)

    def test_template_comments_are_dtl_legal(self):
        """Django's tag regex is compiled without DOTALL: a `{# ... #}` comment
        that spans a newline is not a comment at all — it renders as literal
        text in the page (the readiness board shipped with the mark's docstring
        printed down the rail). Jinja-style `{#- -#}` trims are equally alien.
        Judge every `{#` against Django's own lexer pattern, not ours."""
        from django.template.base import tag_re

        leaks = []
        for path in template_files():
            src = path.read_text()
            spans = [m.span() for m in tag_re.finditer(src) if m.group(0).startswith("{#")]
            for match in re.finditer(r"{#", src):
                if not any(start <= match.start() < end for start, end in spans):
                    line = src[:match.start()].count("\n") + 1
                    leaks.append(f"{path.name}:{line}")
        assert leaks == [], (
            "comments Django will render as visible text (multi-line or `{#- -#}`):\n"
            + "\n".join(leaks)
        )

    def test_the_kpi_card_class_is_gone(self):
        for path in css_files():
            assert ".kpi" not in path.read_text(), f"{path.name} still styles a KPI card"

    def test_no_spinner_only_loading_markup(self):
        text = TOKENS.read_text() + "".join(p.read_text() for p in css_files())
        assert "spinner" not in text or ".btn__spinner" in text


PAGES = (
    "/dashboard/",
    "/hydrants/",
    "/hydrants/new/",
    "/parcels/",
    "/coverage/",
    "/packs/",
    "/exports/",
    "/guide/",
    "/settings/",
    "/field/",
)


@pytest.fixture
def signed_in(editor_user):
    client = Client()
    client.force_login(editor_user)
    return client


@pytest.fixture
def record_pages(api):
    """The record surfaces that need a row to exist before they render.

    They are not in ``PAGES`` because they need fixtures; they are held to the
    same page contract by ``TestRecordPageContract`` below — a detail page that
    skips an `<h1>` or an input label is the same defect wherever it renders.
    """
    hydrant = api.post(
        "/api/v1/hydrants/", {"asset_tag": "UI-CONTRACT-1", "lat": 38.0, "lon": -122.0}, format="json"
    ).data
    parcel = api.post(
        "/api/v1/parcels/", {"parcel_id": "UI-CONTRACT-P-1", "lat": 38.0, "lon": -122.0}, format="json"
    ).data
    return (
        f"/hydrants/{hydrant['id']}/",
        f"/hydrants/{hydrant['id']}/edit/",
        f"/hydrants/{hydrant['id']}/tests/new/",
        f"/hydrants/{hydrant['id']}/inspections/new/",
        f"/parcels/{parcel['id']}/",
        f"/parcels/{parcel['id']}/edit/",
    )


class TestLayoutInvariants:
    def test_the_table_scroll_container_contains_its_overflow(self):
        """`contain: layout` on `.table-wrap` is load-bearing, not decoration.

        Chromium propagates a wide table's scrollable overflow past a plain
        `overflow-x: auto` container to the document, so the whole page gains a
        horizontal scrollbar on a phone. Measured on the readiness board's
        gating table at 375 px: 744 px of document scroll width without it,
        375 px with it. `scripts/browser_walk.py` proves it in a real browser;
        this pin stops the declaration being tidied away.
        """
        css = (STATIC / "app.css").read_text()
        rule = re.search(r"\.table-wrap \{([^}]*)\}", css)
        assert rule, ".table-wrap is not defined"
        assert "overflow-x: auto" in rule.group(1), rule.group(1)
        assert "contain: layout" in rule.group(1), (
            "removing `contain: layout` re-opens the 375 px horizontal-overflow defect"
        )


class TestPageContract:
    @pytest.mark.parametrize("path", PAGES)
    def test_page_renders_for_an_editor(self, signed_in, path):
        response = signed_in.get(path)
        assert response.status_code == 200, path
        body = response.content.decode()
        assert "<html" in body
        assert "tokens.css" in body, "the token layer is not linked"

    def test_detail_pages_render(self, signed_in, api):
        hydrant = api.post(
            "/api/v1/hydrants/", {"asset_tag": "UI-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        parcel = api.post(
            "/api/v1/parcels/", {"parcel_id": "UI-P-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        for path in (f"/hydrants/{hydrant['id']}/", f"/hydrants/{hydrant['id']}/edit/",
                     f"/hydrants/{hydrant['id']}/tests/new/",
                     f"/hydrants/{hydrant['id']}/inspections/new/",
                     f"/parcels/{parcel['id']}/", f"/parcels/{parcel['id']}/edit/"):
            response = signed_in.get(path)
            assert response.status_code == 200, path

    @pytest.mark.parametrize("path", PAGES)
    def test_exactly_one_sign_out_form(self, signed_in, path):
        body = signed_in.get(path).content.decode()
        assert body.count('action="/logout/"') == 1, (
            f"{path} must carry exactly one sign-out form — the runtime journey clicks "
            f"form[action='/logout/'] button[type=submit] and Playwright refuses an ambiguous match"
        )

    @pytest.mark.parametrize("path", PAGES)
    def test_every_control_has_a_label(self, signed_in, path):
        body = signed_in.get(path).content.decode()
        ids = re.findall(r"<(?:input|select|textarea)\b[^>]*\bid=\"([^\"]+)\"", body)
        labelled = set(re.findall(r"<label\b[^>]*\bfor=\"([^\"]+)\"", body))
        labelled |= set(re.findall(r'aria-label="', body) and [])
        unlabelled = [
            control_id for control_id in ids
            if control_id not in labelled
            and not re.search(r'id="%s"[^>]*aria-label=' % re.escape(control_id), body)
        ]
        assert unlabelled == [], f"{path} has unlabelled controls: {unlabelled}"

    def test_anonymous_pages_redirect_to_login(self):
        client = Client()
        for path in PAGES:
            response = client.get(path)
            assert response.status_code == 302, path
            assert "/login/" in response["Location"], path

    def test_login_page_uses_the_token_layer_too(self):
        body = Client().get("/login/").content.decode()
        assert "tokens.css" in body
        assert "app.css" in body


class TestSampleDataConfirm:
    """The one irreversible side effect in the product gets a confirmation.

    A real (non-sample) row deactivates the published demo accounts in the same
    transaction. That is surprising, it cannot be undone from the interface, and
    it is exactly what a confirm dialog is for — so it is wired to the three
    capture forms, and only while sample rows exist.
    """

    def test_capture_forms_confirm_before_the_first_real_write(self, signed_in, api):
        from ledger.models import Hydrant

        sample = Hydrant.objects.create(asset_tag="SAMPLE-1", lat=38.0, lon=-122.0, is_sample_data=True)
        for path in ("/hydrants/new/", f"/hydrants/{sample.pk}/tests/new/",
                     f"/hydrants/{sample.pk}/inspections/new/"):
            response = signed_in.get(path)
            body = response.content.decode()
            assert response.status_code == 200, path
            assert 'data-confirm=' in body, f"{path} does not confirm the demo-account deactivation"
            assert 'data-confirm-accept=' in body, path
        assert 'id="confirm-modal"' in signed_in.get("/dashboard/").content.decode(), (
            "the shell ships no dialog for the confirmation to open"
        )

    def test_no_confirm_when_there_is_nothing_to_deactivate(self, signed_in, api):
        hydrant = api.post(
            "/api/v1/hydrants/", {"asset_tag": "REAL-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        body = signed_in.get(f"/hydrants/{hydrant['id']}/tests/new/").content.decode()
        assert "data-confirm=" not in body, "a real-row district should not be nagged"


class TestAccessibilityFloor:
    def test_every_page_declares_a_language_and_a_viewport(self, signed_in):
        for path in PAGES:
            body = signed_in.get(path).content.decode()
            assert '<html lang="en"' in body, path
            assert 'name="viewport"' in body, path

    def test_skip_link_and_landmark(self, signed_in):
        for path in PAGES:
            body = signed_in.get(path).content.decode()
            assert 'class="skip-link"' in body, path
            assert "<main" in body, path

    def test_one_h1_per_page(self, signed_in):
        for path in PAGES:
            body = signed_in.get(path).content.decode()
            assert body.count("<h1") == 1, f"{path} has {body.count('<h1')} h1 elements"

    def test_status_is_never_colour_alone(self, signed_in, api):
        """Every readiness badge pairs the hue with the literal word."""
        api.post("/api/v1/hydrants/", {"asset_tag": "UI-2", "lat": 38.0, "lon": -122.0}, format="json")
        body = signed_in.get("/hydrants/").content.decode()
        for word in ("Ready", "Not ready", "Watch"):
            assert word in body, f"the readiness badge for {word!r} is missing its word"


class TestRecordPageContract:
    """The page contract, applied to the record surfaces that need a row.

    Round 1 held these pages to render-only assertions; the audit is the same
    one ``PAGES`` gets — one ``<h1>``, lang + viewport, a skip link and a
    landmark, and a real label for every control.
    """

    def test_record_pages_declare_language_and_viewport(self, signed_in, record_pages):
        for path in record_pages:
            body = signed_in.get(path).content.decode()
            assert '<html lang="en"' in body, path
            assert 'name="viewport"' in body, path

    def test_record_pages_carry_the_skip_link_and_landmark(self, signed_in, record_pages):
        for path in record_pages:
            body = signed_in.get(path).content.decode()
            assert 'class="skip-link"' in body, path
            assert "<main" in body, path

    def test_record_pages_have_one_h1(self, signed_in, record_pages):
        for path in record_pages:
            body = signed_in.get(path).content.decode()
            assert body.count("<h1") == 1, f"{path} has {body.count('<h1')} h1 elements"

    def test_record_pages_label_every_control(self, signed_in, record_pages):
        for path in record_pages:
            body = signed_in.get(path).content.decode()
            ids = re.findall(r"<(?:input|select|textarea)\b[^>]*\bid=\"([^\"]+)\"", body)
            labelled = set(re.findall(r"<label\b[^>]*\bfor=\"([^\"]+)\"", body))
            unlabelled = [
                control_id for control_id in ids
                if control_id not in labelled
                and not re.search(r'id="%s"[^>]*aria-label=' % re.escape(control_id), body)
            ]
            assert unlabelled == [], f"{path} has unlabelled controls: {unlabelled}"


class TestSignInReturnPath:
    """The anonymous redirects carry `?next=`; sign-in honours it, on-site only.

    The full-journey proof restarts in a fresh browser context, signs in again
    and expects to land back on the record it was reading — which only works if
    the sign-in form carries the return path. The path is never followed
    off-site: a crafted `next` is ignored, not redirected to.
    """

    def _post_login(self, client, editor_user, **extra):
        page = client.get("/login/")
        token = page.cookies["csrftoken"].value
        body = {"username": editor_user.username, "password": "hydrantproof-test-pass",
                "csrfmiddlewaretoken": token}
        body.update(extra)
        return client.post("/login/", body)

    def test_anonymous_page_redirect_carries_the_return_path(self):
        response = Client().get("/hydrants/")
        assert response.status_code == 302
        assert response["Location"].startswith("/login/")
        assert "next=/hydrants/" in response["Location"]

    def test_sign_in_returns_to_the_requested_page(self, editor_user):
        client = Client()
        response = self._post_login(client, editor_user, next="/hydrants/")
        assert response.status_code == 302
        assert response["Location"] == "/hydrants/"
        assert client.get("/hydrants/").status_code == 200

    def test_sign_in_defaults_to_the_dashboard_without_a_return_path(self, editor_user):
        response = self._post_login(Client(), editor_user)
        assert response.status_code == 302
        assert response["Location"] == "/dashboard/"

    def test_a_foreign_return_path_is_ignored(self, editor_user):
        for hostile in ("https://evil.example/steal", "//evil.example/steal", "javascript:alert(1)",
                        # Browsers treat a backslash as a slash: both of these
                        # are protocol-relative URLs to a prefix-only test.
                        "/\\evil.example/steal", "/\\/evil.example/steal",
                        "///evil.example/steal"):
            response = self._post_login(Client(), editor_user, next=hostile)
            assert response.status_code == 302
            assert response["Location"] == "/dashboard/", hostile


class TestReadinessBoardAffordances:
    """The board's two hard-won affordances, pinned so they cannot be tidied away.

    1. **The hit zone.** The visible square is 11 px; the pointer target is
       `--hit-pad` (WCAG 2.2 AA 2.5.8 Target Size, Minimum — the 24 px spacing
       exception cannot be met at this board's 14 px pitch, so the target
       itself carries the floor). The overlay is anchored to the square's
       top-left, which keeps the tiling deterministic: where two zones overlap,
       the later sibling wins, and that overlap lies inside the later square's
       own face. A square never loses part of its own face to a neighbour.
    2. **The readout.** The panel copy promises the asset tag on hover and on
       focus; `[data-board-readout]` is that promise kept for a sighted eye
       (the pip's `aria-label` keeps it for assistive technology). A roving
       focus ring that moves over anonymous squares is the defect this pins
       against.
    """

    def test_the_pip_hit_zone_is_at_least_the_wcag_minimum(self):
        token = re.search(r"--hit-pad:\s*([0-9.]+)px", TOKENS.read_text())
        assert token, "--hit-pad is not declared in tokens.css"
        assert float(token.group(1)) >= 24, (
            f"--hit-pad is {token.group(1)}px; WCAG 2.2 AA 2.5.8 requires a 24 px target "
            "where the spacing exception fails (it does: 14 px pitch)"
        )
        rule = re.search(r"\.board__pip::before \{([^}]*)\}", (STATIC / "app.css").read_text())
        assert rule, "the pip hit zone (.board__pip::before) is not defined"
        body = rule.group(1)
        assert "width: var(--hit-pad)" in body, body
        assert "height: var(--hit-pad)" in body, body
        pip_rule = re.search(r"\.board__pip \{([^}]*)\}", (STATIC / "app.css").read_text())
        assert "position: relative" in pip_rule.group(1), (
            "the hit-zone overlay positions against the pip; without position: relative it "
            "resolves against some other ancestor"
        )

    def test_the_board_readout_names_the_square(self, signed_in, api):
        hydrant = api.post(
            "/api/v1/hydrants/", {"asset_tag": "UI-BOARD-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        body = signed_in.get("/dashboard/").content.decode()
        assert "data-board-readout" in body, "the board has no readout line"
        assert "Hover or focus a square for the asset tag" in body, (
            "the readout's idle hint is the promise the board's copy makes"
        )
        assert f'data-tag="{hydrant["asset_tag"]}"' in body, (
            "a board square carries no data-tag; the readout would have nothing to name"
        )
        assert body.count("data-tag=") >= 1 and "board__pip" in body, body[:200]

    def test_the_roving_step_is_measured_not_assumed(self):
        """Up/Down must move by a row; `data-roving-columns` is never rendered."""
        source = (STATIC / "app.js").read_text()
        assert "function columnCount()" in source, "the roving step is not measured"
        assert "offsetTop" in source, "the wrapped column count is not read from layout"
        assert 'container.dataset.rovingColumns' in source, (
            "an explicit server pin must still win when it is present"
        )


class TestHitZoneFloor:
    """The pip's law, generalized (DESIGN.md § 2): every pointer target is at
    least ``--hit-min``, drawn as an invisible overlay on the control itself,
    so density is never traded for target size. ``scripts/browser_walk.py``
    resolves the zones in a real browser; these pins stop the declarations
    being tidied away."""

    def test_the_pointer_target_token_is_at_least_the_touch_floor(self):
        token = re.search(r"--hit-min:\s*([0-9.]+)px", TOKENS.read_text())
        assert token, "--hit-min is not declared in tokens.css"
        assert float(token.group(1)) >= 44, (
            f"--hit-min is {token.group(1)}px; the touch floor the skill sets is 44 px "
            "(WCAG 2.2 AA 2.5.8's 24 px is the floor beneath it)"
        )

    def test_buttons_links_and_the_toast_dismiss_carry_a_zone(self):
        css = (STATIC / "app.css").read_text()
        for selector in (".btn", ".nav__link", ".toast__close"):
            assert f"{selector}::before" in css, f"{selector} carries no hit-zone overlay"
        assert ".btn, .nav__link, .toast__close { position: relative; }" in css, (
            "the zone overlays position against their own control; without position: relative "
            "they resolve against some other ancestor"
        )
        assert "height: var(--hit-min)" in css, "the zone does not reach --hit-min"

    def test_the_toast_dismiss_has_a_24px_visual_box(self):
        body = css_block((STATIC / "app.css").read_text(), ".toast__close")
        assert "min-width: var(--space-6)" in body, body
        assert "min-height: var(--space-6)" in body, body


class TestTheChoreographedEntrance:
    """DESIGN.md § 7's one entrance, shipped: the page head rises in three
    staggered steps. Transform + opacity only, and the reduced-motion block
    collapses the delays as well as the durations."""

    def test_the_page_head_rises_in_three_steps(self):
        css = (STATIC / "app.css").read_text()
        assert "@keyframes hp-enter" in css, "the entrance keyframes are not shipped"
        assert "animation: hp-enter var(--dur-slow) var(--ease-emphasized) both" in css
        assert "animation-delay: var(--stagger-1)" in css, "the subtitle step is not staggered"
        assert "animation-delay: var(--stagger-2)" in css, "the actions step is not staggered"

    def test_reduced_motion_collapses_the_delay_too(self):
        block = css_block((STATIC / "app.css").read_text(), "@media (prefers-reduced-motion: reduce)")
        assert "animation-delay: var(--dur-none) !important" in block, (
            "a staggered entrance without a delay reset still blinks in under reduced motion"
        )


class TestTheDocumentedCompositionsExist:
    """DESIGN.md documents `.panel--skeleton`; the CSS must ship it and the
    flow-test result card must be its real instance (a documented class that
    nothing renders is the same defect as a promise the board does not keep)."""

    def test_the_skeleton_panel_is_shipped_and_used(self):
        assert ".panel--skeleton" in (STATIC / "app.css").read_text(), (
            "DESIGN.md § 6 documents .panel--skeleton; the stylesheet does not define it"
        )
        assert "panel--skeleton" in (STATIC / "flowtest.js").read_text(), (
            "the flow-test result card is the shipped .panel--skeleton instance"
        )


class TestErrorAssociation:
    """A custom refusal sets `aria-invalid` on the failing control and points
    `aria-describedby` at the message line (DESIGN.md § 9.6). The walk drives
    the refusals in a real browser; these pins keep the wiring in place."""

    def test_the_shared_helpers_exist(self):
        app = (STATIC / "app.js").read_text()
        for helper in ("HP.markInvalid = function", "HP.clearInvalid = function",
                       "HP.markInvalidFromBody = function"):
            assert helper in app, f"app.js does not expose {helper}"

    def test_every_custom_refusal_marks_the_field(self):
        for name, needle in (
            ("statusform.js", "HP.markInvalid(focusEl"),
            ("flowtest.js", 'HP.markInvalid([document.getElementById("static_psi")'),
            ("inspection.js", "HP.markInvalid(codeInputs, statusEl, true)"),
            ("hydrantform.js", 'HP.markInvalid(document.getElementById("asset_tag")'),
            ("parcelform.js", "HP.markInvalid(el"),
            ("field.js", "HP.markInvalid(refusal.fields"),
            ("coverage.js", "HP.markInvalid(scope.fields"),
        ):
            assert needle in (STATIC / name).read_text(), (
                f"{name} refuses without declaring the failing control's state"
            )

    def test_the_code_pickers_refusal_is_visible(self):
        """The code-picker's chip (the label) is the visible control — the bare
        checkbox inside it is 13 px of nothing — so the chip must carry the
        refusal border. A state only a screen reader can perceive is the
        sighted-operator version of a top-of-form error."""
        assert '.code-picker label:has(input[aria-invalid="true"])' in (STATIC / "app.css").read_text(), (
            "the deficiency code-picker declares aria-invalid with no visible state"
        )

    def test_the_status_lines_have_stable_ids(self):
        """`aria-describedby` needs a target; the ids are the templates'."""
        for name, needle in (
            ("dashboard.html", 'id="ack-status-{{ hydrant.pk }}"'),
            ("dashboard.html", 'id="resolve-status-{{ deficiency.pk }}"'),
            ("hydrant_detail.html", 'id="reversal-status"'),
            ("flow_test_form.html", 'id="submit-status"'),
            ("inspection_form.html", 'id="submit-status"'),
            ("hydrant_form.html", 'id="submit-status"'),
            ("parcel_form.html", 'id="submit-status"'),
        ):
            assert needle in (TEMPLATES / name).read_text(), f"{name} lost {needle}"


class TestTheCreditRadiusRing:
    """The per-hydrant map's credit ring draws the district's own number — the
    same number the caption under the map states (SOUL.md ¶ 2: every number is
    exact and the tool has never lied about one). `map.js` reads the radius off
    the element's `data-credit-radius-m`, which the view supplies through
    `geo.FEET_PER_METER` — the product's one feet-per-metre constant, the same
    one coverage measures with. `scripts/browser_walk.py` measures the drawn
    ring against the caption in a real browser (the map's own scale bar is the
    ruler). These pins keep the wiring in place; a hardcoded radius returning
    to `map.js` is exactly the defect the walk's claim now catches."""

    def test_map_js_reads_the_radius_from_the_element(self):
        js = (STATIC / "map.js").read_text()
        assert "el.dataset.creditRadiusM" in js, (
            "map.js does not read the credit radius from the element's dataset"
        )
        assert "radius: creditRadiusM" in js, (
            "map.js does not draw the ring at the dataset's radius"
        )
        assert "creditRadiusM > 0" in js, (
            "map.js must draw no ring without a radius — a guessed ring would "
            "caption a number the drawing does not use"
        )
        assert "304.8" not in js, (
            "a hardcoded radius returned to map.js — the ring would misstate "
            "the district's own number"
        )

    def test_the_view_supplies_the_district_radius_in_metres(self):
        page = (ROOT / "ledger" / "pages.py").read_text()
        assert "FEET_PER_METER" in page and '"credit_radius_m"' in page, (
            "the hydrant view does not convert the district's credit radius "
            "through geo.FEET_PER_METER"
        )
        template = (TEMPLATES / "hydrant_detail.html").read_text()
        assert 'data-credit-radius-m="{{ credit_radius_m }}"' in template, (
            "the map element does not carry the district's credit radius"
        )

    def test_both_caption_branches_carry_the_stable_id(self):
        """The walk reads the caption to compare it with the drawing; both
        branches (a tile URL configured, and none) must carry the same id."""
        template = (TEMPLATES / "hydrant_detail.html").read_text()
        assert template.count('id="credit-radius-note"') == 2, (
            "the credit-radius caption must carry its stable id in both branches"
        )

    def test_the_rendered_page_carries_the_district_radius(self, signed_in, api):
        """The end of the wire the static pins cannot see: the district's
        configured radius reaches the element in metres — converted through
        the same constant coverage measures with — and the caption states the
        same number in feet."""
        from ledger import geo
        from ledger.models import DistrictSettings

        district = DistrictSettings.load()
        district.credit_radius_ft = 750.0
        district.save()
        hydrant = api.post(
            "/api/v1/hydrants/", {"asset_tag": "RING-1", "lat": 38.0, "lon": -122.0}, format="json"
        ).data
        body = signed_in.get(f"/hydrants/{hydrant['id']}/").content.decode()
        match = re.search(r'data-credit-radius-m="([^"]+)"', body)
        assert match, "the map element does not carry the district's credit radius"
        assert abs(float(match.group(1)) - 750.0 / geo.FEET_PER_METER) < 1e-6, (
            "the element's radius is not the district's setting through geo.FEET_PER_METER"
        )
        # The caption wraps across source lines; compare its words, not its
        # line breaks (the walk reads the same sentence through inner_text).
        flat = re.sub(r"\s+", " ", body)
        assert "750 ft credit radius" in flat, (
            "the caption and the element disagree about the district's number"
        )

    def test_the_map_hues_are_owned_by_the_cascade(self):
        """A theme flip repaints every other surface; the map's proof layers
        must follow. Leaflet writes its `color` option onto the path as a
        presentation attribute, which any stylesheet rule outranks — so the
        ring and the hydrant marker carry semantic classes whose rules read
        the live token, never the value map.js captured at load (the captured
        option stays as the no-stylesheet fallback). The walk flips the theme
        on the map page and watches the ring's stroke change and return."""
        js = (STATIC / "map.js").read_text()
        assert 'className: "credit-ring"' in js, "the credit ring lost its semantic class"
        assert 'className: "hydrant-marker"' in js, "the hydrant marker lost its semantic class"
        pinmap = (STATIC / "pinmap.js").read_text()
        assert 'className: "ref-marker"' in pinmap, (
            "the pin map's reference circle lost its semantic class"
        )
        css = (STATIC / "app.css").read_text()
        for selector, value in (
            ("#map .credit-ring", "stroke: var(--accent)"),
            ("#map .hydrant-marker", "stroke: var(--accent)"),
            ("#pin-map .ref-marker", "stroke: var(--status-neutral-edge)"),
        ):
            rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
            assert rule and value in rule.group(1), (
                f"{selector} lost its cascade-owned stroke"
            )

