# DESIGN — HydrantProof

The interface contract for this worktree. Every rule here is enforceable by
reading the code; § 10 says how.

## 0. Soul

One sentence: **a district's water-supply ledger that reads like a well-kept
apparatus log — dense rows, straight ruling, exact numbers, chroma reserved for
the three states that decide whether a truck can draft.** Full brief in
[`SOUL.md`](./SOUL.md); where this document and SOUL.md disagree, SOUL.md wins.

## 1. Reference anchor

**Reference (exactly one): [Linear](https://linear.app).** The app, not the
marketing page — the issue stream at `/my-issues` and the issue detail view.

**Why this one.** Chief Whitfield's job at 9:40pm is triage: sixty-three rows,
five of which matter tonight. Linear is the shipping product that solved exactly
that problem — a dense record list where a status signal rides the row, the eye
finds the exception in one pass, and nothing is decorative enough to compete
with the data. Its restraint is the mechanism: one accent, grayscale everywhere
else, chroma spent only on state. That is the same bargain HydrantProof needs,
because a hydrant's readiness is the only thing on any screen that may raise its
voice.

### Five pixel-level patterns mirrored

1. **Row geometry and the status rail.** Linear's issue row is a full-bleed
   ~44px band with a 3px status pip pinned to the row's left edge at `left: 0`,
   running the full row height; the title is 13–14px/500 and the metadata is
   11–12px in the tertiary foreground; hover lifts the surface rather than
   flashing a background. → Mirror in `.ledger-row`: 44px minimum height, a 3px
   rail drawn with `box-shadow: inset 3px 0 0 <state>` on the first cell, title
   at `--text-md`/500, metadata at `--text-xs` in `--fg-tertiary`, and hover
   raising `--bg-raised` with no colour change.
2. **Tabular numerals in every data column.** Linear sets estimates and dates
   with tabular figures so digits line up column-to-column, and right-aligns
   them. → Every gpm / psi / ft / day / byte figure carries
   `font-variant-numeric: tabular-nums` via `.num`, is right-aligned in its
   column, and renders its unit as a separate 11px `--fg-tertiary` span that
   never wraps (`.num__unit`).
3. **One accent, grayscale everywhere else.** Linear spends its single indigo
   on interactive affordances only; status has its own tiny, equally-saturated
   set. → One petrol accent (`--accent`) on links, primary buttons and the focus
   ring; the status set is four hues at matched saturation (`--status-ready`,
   `--status-watch`, `--status-notready`, `--status-neutral`); nothing else in
   the product is allowed chroma.
4. **The two-column detail page.** Linear's issue detail is a wide left column
   (title, body, activity) beside a narrower right rail of label/value property
   pairs, labels 11–12px tertiary, values 13px primary, 24px of separation
   between pairs. → Mirror on the hydrant detail: left column = the ledger
   tables (tests, inspections, status history, attribute history), right rail =
   a sticky `.props` list of labelled properties, plus the map.
5. **Focus and hover discipline.** Linear's `:focus-visible` is a 2px ring in
   the accent colour offset 2px, hover transitions run ~150ms on
   `cubic-bezier(0.4, 0, 0.2, 1)`, and cards lift with
   `transform: translateY(-1px)`. → Mirror exactly: `--focus-ring: 0 0 0 2px
   var(--bg-base), 0 0 0 4px var(--border-focus)` on every interactive element,
   `--dur-fast`/`--ease-default` on hover, `translateY(-1px)` on `.lift:hover`.

### Three deliberate divergences (with the soul-brief reason)

1. **Light-first, not dark-native.** Linear is a dark-native application;
   HydrantProof is a daylight, paper-led one, because its output is printed
   (`SOUL.md` § 2 — "a well-kept apparatus log") and its users read it in a fire
   hall with the bay door open. Dark mode ships and is fully designed, but the
   light theme is the reference rendering and the one the tokens were authored
   from.
2. **No hover-revealed controls, no command palette.** Linear hides row actions
   behind hover and routes power users through `⌘K`. Chief Whitfield is a
   mouse-and-thumb user with gloves on (`SOUL.md` § 1); a control that exists
   only under a pointer is a control she will never find. Every action in this
   product is a visible, labelled button, and the only keyboard layer is
   ordinary tab order.
3. **Ledger tables, not an activity feed.** Linear renders history as a chat-like
   stream of events. HydrantProof renders history as append-only tables with
   explicit columns (effective date, from, to, reason, actor) because the
   product's entire proof posture is *attributed, append-only, versioned*
   (`SOUL.md` § 2 — "never once lied about a number"). A feed implies a
   narrative; a table is a record.

## 2. Density

**Dense.** Every screen is a working surface: 44px rows, 13px UI text, 32px
control heights, 12–16px panel padding, one 8pt-ish vertical rhythm (the scale
in § 5 is 4pt). Nothing gets a "breathing room" pass; the discipline is cut,
not pad.

**One density exception, and it is a floor, not a taste call.** The readiness
board's squares are 11 px on a 14 px pitch — a mosaic, not a control grid. The
*pointer target* is `--hit-pad` (24 px, the WCAG 2.2 AA 2.5.8 minimum), drawn
as an invisible overlay anchored to the square's top-left so each square's own
face and the gap beside it always belong to that square, and only the overlap
between neighbouring 24 px zones is claimed by the later sibling. Stated
plainly, because the honest version matters more than the tidy one: at this
pitch a square's *effective* target is its cell (14 px), and the 24 px zone is
fully available at the ends of a row. The strict 2.5.8 spacing exception cannot
be met without widening the mosaic to a 24 px pitch — a different design, and
one this board deliberately does not adopt; the 3 px rhythm is the product. The
overlay is what a thumb hits first; the mosaic is what the eye reads. Both are
asserted in `tests/test_ui_system.py`; `scripts/browser_walk.py` measures the
one thing a stylesheet cannot promise — a point 2 px below a square's visible
face resolves to that square, not to its neighbour.

**The same law governs every control.** `.btn` (32 px), `.btn--sm` (24 px),
`.nav__link` (32 px) and `.toast__close` carry the identical overlay: an
invisible `::before` whose height is `--hit-min` (44 px, the touch floor this
product holds itself to; WCAG 2.2 AA 2.5.8's 24 px is the floor beneath it).
The drawn control never grows — a control that grows to satisfy a validator is
the anti-default § 9 forbids. Two anchoring laws, both the pip's: a button's
zone is centred on it — the controls around a button sit in rows and cells
with enough surrounding space that the zone cannot reach a neighbour's face,
and the walk measures that on four surfaces — and a stacked link's zone
anchors top-left and extends down, so where two zones meet the later sibling
wins and the overlap lies inside the *later* control's own face; a control
never loses part of its own face to a neighbour. The toast dismiss
additionally carries a 24 × 24 visual box (its glyph alone is ~10 × 18).
`scripts/browser_walk.py` proves all of it against a real browser: a button's
zone reaches past its drawn box, the rail's stacked links tile with no dead
gap, no control's visible face is claimed by a neighbour's zone on the
dashboard, the hydrant ledger, the inspection sheet and the field capture
sheet (the deficiency queue runs at the full working width, so its Resolve
column is never the clipped one — the walk pins that in geometry), and the
toast dismiss measures 24 × 24 with a zone past its box.

## 3. Colour tokens

The closed layer lives in `ledger/static/ledger/tokens.css`. Light and dark are
authored together; `[data-theme="dark"]` overrides the same names, and
`@media (prefers-color-scheme: dark)` applies the same block when the user has
not chosen explicitly.

| Role | Light | Dark | Notes |
|---|---|---|---|
| `--bg-base` | `#F5F3EE` | `#101215` | warm paper page / slate night page |
| `--bg-sunken` | `#EDEAE3` | `#0B0D0F` | table headers, inset wells |
| `--bg-elevated` | `#FDFCFA` | `#171A1E` | panels — warm near-white, never pure `#FFF` |
| `--bg-raised` | `#FBFAF7` | `#1D2126` | row hover, sticky headers |
| `--fg-primary` | `#1B1A17` | `#E9E6E0` | never `#000`, never `#FFF` |
| `--fg-secondary` | `#55514A` | `#A8A39A` | body prose |
| `--fg-tertiary` | `#6A665D` | `#8C877D` | labels, metadata (≥4.5:1 on every surface — computed, pinned) |
| `--fg-disabled` | `#A9A399` | `#726E66` | disabled only (WCAG-exempt) |
| `--border-subtle` | `#E7E3DA` | `#23272C` | table rules |
| `--border-default` | `#D5D0C5` | `#2F343A` | panel edges, inputs |
| `--border-strong` | `#B4AEA1` | `#474D55` | emphasis edges |
| `--border-focus` | `#0F3D4F` | `#74B8CE` | the focus ring |
| `--accent` | `#0F3D4F` | `#74B8CE` | the single accent (petrol) |
| `--accent-hover` | `#155267` | `#8FC9DC` | |
| `--accent-pressed` | `#0A2C3A` | `#529BB2` | |
| `--accent-soft` | `#E3EEF2` | `#16303A` | tinted fills, active nav |
| `--accent-on` | `#FBFAF7` | `#0A171C` | text on an accent fill |
| `--status-ready` | `#1E6F3C` | `#6FC58C` | + `-bg`, `-border`, `-edge` |
| `--status-watch` | `#8A5305` | `#E0A855` | + `-bg`, `-border`, `-edge` |
| `--status-notready` | `#A32A1E` | `#E88274` | + `-bg`, `-border`, `-edge` |
| `--status-neutral` | `#625E56` | `#A29C92` | + `-bg`, `-border`, `-edge` |
| `--status-info` | `= --accent` | `= --accent` | + `-bg`, `-border` |
| `--class-aa` | `#7FC4E8` | `#7FC4E8` | NFPA 291 class AA chip — same in both themes |
| `--class-a` | `#4CAF50` | `#4CAF50` | class A chip — same in both themes |
| `--class-b` | `#FF9800` | `#FF9800` | class B chip — same in both themes |
| `--class-c` | `#EC4E42` | `#EC4E42` | class C chip — same in both themes (lightened for the ink floor) |
| `--class-none` | `#9E9E9E` | `#9E9E9E` | unclassified chip — same in both themes |
| `--class-ink` | `#1B1A17` | `#1B1A17` | the letter on a class chip — same in both themes |

Status colour is never the only signal: every use pairs the hue with a dot, a
glyph **and** the literal word (`READY` / `WATCH` / `NOT_READY` / `RETIRED`),
and every flagged row also carries its flag names in text.

The capacity palette is theme-invariant on purpose: `tokens.css` mirrors
`ledger/constants.py::CAPACITY_CLASS_COLORS`, which the ReportLab pack renderer
draws with — two renderers, one palette. Class C's red is one step lighter than
the stock material red so the `--class-ink` letter on it clears 4.5:1; the
whole palette (ink against every hue) is computed in the audit.

Every foreground/background pairing above is checked by computed WCAG contrast
in `tests/test_ui_system.py` (≥4.5:1, both themes), and this table is pinned
cell-for-cell against `tokens.css` — a token change that misses the table fails
the suite.

## 4. Type tokens

| Role | Token | Value |
|---|---|---|
| Display | `--font-display` | `"IBM Plex Sans", ui-sans-serif, system-ui, "Segoe UI", sans-serif` |
| Body | `--font-body` | same stack (one family for the whole UI) |
| Mono / data | `--font-mono` | `"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` |

IBM Plex is vendored (`ledger/static/ledger/fonts/`, SIL OFL 1.1, license file
shipped beside the `.woff2`s) — the container is offline by design, so no
webfont may be fetched at runtime. Plex was drawn for technical documentation
and instrumentation; its mono is a true companion cut, so a label and the number
under it share a skeleton. Scale (1.2-ish, practical): `--text-xs` 11px ·
`--text-sm` 12px · `--text-base` 13px · `--text-md` 14px · `--text-lg` 16px ·
`--text-xl` 19px · `--text-2xl` 23px · `--text-3xl` 28px. Weights 400/500/600/700.
Line heights `--line-tight` 1.15 · `--line-snug` 1.3 · `--line-normal` 1.5 ·
`--line-relaxed` 1.65. Mobile form controls render at 16px to stop iOS zoom
(`--text-input-mobile`).

## 5. Spacing tokens

4pt base: `--space-1` 4 · `-2` 8 · `-3` 12 · `-4` 16 · `-5` 20 · `-6` 24 · `-7`
32 · `-8` 40 · `-9` 48 · `-10` 64 · `-11` 80 · `-12` 96. Panel padding is
`--space-4`; the gap between stacked panels is `--space-5`; the gap between a
section heading and its table is `--space-3`; table cell padding is
`--space-2 --space-3`. No component may compute a spacing value from anything
else.

## 6. Component states

Every interactive component ships all six states. The differences are named
here; the implementation is in `app.css`.

| Component | default | hover | focus-visible | active/pressed | disabled | loading |
|---|---|---|---|---|---|---|
| `.btn` (primary) | accent fill, `--accent-on` text | `--accent-hover` fill | 2px `--border-focus` ring offset 2px | `--accent-pressed` + `translateY(1px)` | 45% opacity, `not-allowed`, title = reason | label swaps to `Saving…`/`Starting…`, `aria-busy`, spinner-free inline progress bar |
| `.btn--secondary` | `--bg-elevated` fill, 1px `--border-default` | `--bg-raised` fill, `--border-strong` | ring | `--bg-sunken` | as above | as above |
| `.btn--ghost` | transparent, `--fg-secondary` | `--bg-raised` fill | ring | `--bg-sunken` | as above | as above |
| `.btn--danger` | `--status-notready-bg` fill, `--status-notready` text | solid `--status-notready`, `--accent-on` text | ring | darker | as above | as above |
| `.field input/select/textarea` | `--bg-elevated`, 1px `--border-default`, 6px radius | `--border-strong` | ring + `--border-focus` border | — | `--bg-sunken`, `--fg-disabled`, `not-allowed` | the submitting button carries `aria-busy` + the label swap; the control itself stays editable and is never shimmed |
| `.panel` | `--bg-elevated`, 1px `--border-subtle`, `--shadow-2` | — | — | — | — | `.panel--skeleton` holds the body at the real row height while three `.skeleton` bars show (the flow-test result card toggles it around its preview wait) |
| `.ledger-row` | 44px, 3px state rail, `--border-subtle` rule | `--bg-raised`, no rail change | 2px inset ring on the row | — | `--fg-disabled`, rail desaturated | skeleton row |
| `.badge` | tinted bg, 1px `-border`, dot + uppercase label | — | ring (when focusable) | — | — | — |
| `.nav a` | `--fg-secondary` | `--bg-raised` | ring | `--accent-soft` + `--accent` text when current | `--fg-disabled` | — |
| `.board__pip` | 11px status-hue square, 24px hit zone (`--hit-pad`, top-left anchored) | `translateY(-1px)` + `--shadow-1` | ring | — | — | — |
| `.board__readout` | mono `--fg-tertiary` hint line, one reserved line | names the hovered square | names the focused square (`--fg-primary`, medium) | — | — | — |
| `.table` | sunken header, 13px, hairline rules | row hover | — | — | — | skeleton rows |
| `.modal` | `--bg-elevated`, `--shadow-3`, 1px `--border-default` | — | focus trapped | — | — | — |
| `.toast` | `--bg-elevated`, 1px border, `--shadow-3`, left edge in state colour | — | — | — | — | — |

Modal contract (hard gate): `role="dialog"`, `aria-modal="true"`,
`aria-labelledby` pointing at the title, focus moved in on open, focus returned
to the opener on close, `Esc` and backdrop click both close, and the page behind
gets `inert`-equivalent scroll lock.

**The refusal state (seventh state, on every control that can be refused).** A
custom refusal — a form's own rule, or an API 400 whose body names fields — sets
`aria-invalid="true"` on the failing control and points its `aria-describedby` at
the status line that carries the message (`app.js::markInvalid` /
`markInvalidFromBody`; the status spans carry stable ids in the templates). The
control shows a `--status-notready` border (or an outline, for a checkbox) until
the operator edits it or the next submit succeeds — both clear it. The message
is never only in the sentence beside the field. `scripts/browser_walk.py` drives
the status door's, the inspection sheet's and the field capture door's refusals
in a real browser and asserts the attribute pair, then asserts that editing the
field clears it.

## 7. Motion

Durations `--dur-instant` 50ms · `--dur-fast` 120ms · `--dur-base` 180ms ·
`--dur-slow` 300ms. Easings `--ease-default` `cubic-bezier(0.4,0,0.2,1)` ·
`--ease-emphasized` `cubic-bezier(0.2,0,0,1)`. Only `transform` and `opacity`
animate — never `width`, `height`, `top` or `left`. The one choreographed
entrance is the page head: title (0ms), subtitle (60ms), actions (120ms), all
`translateY(4px) → 0` + fade, `--dur-slow`. Skeletons, not spinners, wherever
the layout is known (the hydrant detail map, the flow-test result card, the
coverage run poll, the pack poll, the async table regions); a spinner is used
only for an action with no known shape. `prefers-reduced-motion: reduce`
removes every transition and animation except opacity changes — including the
entrance's delays, so a staggered head cannot blink in one frame at a time.

## 8. Voice and copy

**Voice, one sentence:** a district officer writing in the department log —
plain, specific, second person, no exclamation marks, no apology, every number
carrying its unit.

Empty states (three):

- Registry — "No hydrants yet. Import an inventory CSV, or add the first hydrant and drop its pin on the map."
- Open deficiencies — "No open deficiencies. A `FAIL` inspection opens a gating row per code; an `OK` closes every open row automatically."
- Evidence packs — "No packs yet. A pack is rendered once from a frozen coverage run and then never regenerated."

Errors (three):

- "Could not start the coverage run — a job is already running. Wait for run #12 to finish, then try again."
- "Resolution note is required. Write what was done, then resolve the row."
- "The pack could not be generated: pair_count_exceeded. Narrow the parcel filter and run coverage again."

Buttons (three): "Add hydrant" · "Record flow test" · "Generate evidence pack".

Board affordances (the copy and the mechanism are one promise):

- The readout's idle hint: "Hover or focus a square for the asset tag." — the
  readout names the square under the pointer or the focus ring; a promise in
  prose that the board does not keep is a lie with a border on it.
- The panel note's last line (only while the board renders): "Arrow keys walk
  the board; the line above the note names the square you are on."

Never: "Oops", "Something went wrong", "Loading magic", "We're glad you're
here", "Please note that", or any sentence with an exclamation mark.

## 9. Anti-defaults forbidden in this project

The four clichés from `SOUL.md` § 3, made concrete and checkable:

1. **No KPI-card hero.** The dashboard opens with the readiness board (a pip
   grid of every hydrant) and one stacked proportion bar — never three or more
   evenly-spaced stat cards in a row. *(grep guard: `.kpi` is deleted from the
   CSS; no `grid-template-columns: repeat(3` on the dashboard.)*
2. **No gradient, no glass, no glow.** No `linear-gradient` outside the three
   declared functional selectors the audit allows (`.map--graticule`, the
   `.select` caret, the `.skeleton::after` shimmer); no `backdrop-filter`
   anywhere except the modal backdrop scrim; no `box-shadow` used as a colour
   glow; no animated pulse.
3. **No emoji as status, no emoji in system copy.** Status is dot + word. The
   only non-ASCII glyphs in the product are `·` (separator), `—` (empty value),
   `±` (GPS accuracy), `→` (transition arrows in the status ledger), `…`
   (continuation — the busy labels of § 6 and truncated digests) and `×`
   (dismiss controls and the multiplication sign the extrapolation factor
   carries). Technical notation (`≤`, `²`, `√`) may appear where the underlying
   formula or limit is quoted, never as decoration.
4. **No pill everything, no radius roulette.** Radii come from the token set
   only: 3px tags, 4px buttons/inputs, 6px panels, 10px modal. A pill
   (`--radius-pill`) appears exactly once: the readiness proportion bar.
5. **No `Inter`, `Roboto`, `Open Sans`, `Lato`, `#000`, `#FFF`.** The families
   are IBM Plex Sans / Plex Mono (vendored); the darkest ink is `#1B1A17`, the
   lightest paper `#F5F3EE`.
6. **No placeholder-as-label, no top-of-form-only errors.** Every control has a
   real `<label for>`; every error renders beside its field and is wired with
   `aria-describedby`.
7. **No invisible-value inputs.** The flow-test sheet is a grid whose computed
   column is *labelled* "computed gpm"; it never silently fills a value the
   operator cannot see.
8. **No fake success.** A button that cannot reach a real route is rendered
   disabled with the reason inline; nothing optimistically reports success.

## 10. Audit

There is no JavaScript build step and no browser test runner in this stack, so
the audit is three-part and runs from the repository:

1. **Token closure (automated).** `python3 -m pytest tests/test_ui_system.py -q`
   fails on any raw hex/rgb/hsl colour, any raw `px`/`rem`/`em`/`pt` length and
   any raw `ms`/`s` duration in a stylesheet outside `tokens.css`, on any
   raw colour in the JavaScript, and on any inline *colour* property set from
   JavaScript (`el.style.color = …` and kin — a scripted inline colour is
   captured from the token layer once and cannot follow a theme flip; state
   hues ride classes such as `.warn`/`.error`, whose values the stylesheet owns
   in both themes). Two carve-outs, both deliberate and both
   asserted rather than assumed: a `@media` breakpoint may use `em` (a media
   query cannot read a custom property), and the print block may use pure white
   (the medium is paper). Templates may carry exactly one inline style shape —
   `style="width|height: {{ … }}%"`, a percentage computed from live counts.
   The same test asserts that `tokens.css` declares every required token family
   and that both theme blocks define every theme-scoped token name. It also
   **computes** the WCAG contrast of every pairing the product ships (all
   foregrounds × all four surfaces, the capacity-chip ink × every class hue,
   the status and accent pairs, both themes) against the 4.5:1 AA floor, its
   measurement calibrated on WCAG's own #767676-on-white reference — closure is
   not legibility, so the floor is arithmetic here, never a comment. And it
   pins § 3's table cell-for-cell against `tokens.css`, so a documented colour
   cannot silently drift from the shipped one.
2. **Anti-default scan (automated).** The same file checks the § 9 list by
   name: banned typefaces, pure black/white outside the print block, gradients
   (an allowlist of three selectors), `backdrop-filter` (the modal scrim only),
   emoji anywhere in a template, and the deleted `.kpi` class.
3. **Page contract (automated).** Every page renders for an editor, carries
   exactly one sign-out form, gives every control a real `<label for>` or
   `aria-label`, declares `lang` and `viewport`, has one `<h1>` and a skip link,
   and never states a readiness state by colour alone. The board's two
   affordances are pinned by name in the same file: the hit zone's token must
   be ≥ 24 px and drawn by `.board__pip::before`, and the rendered dashboard
   must carry `data-board-readout` with a `data-tag` on every square. The map's
   credit ring is pinned the same way: `map.js` reads the district's radius off
   the element's `data-credit-radius-m` (converted through `geo.FEET_PER_METER`
   in the view — one conversion constant for the whole product) and must draw
   no ring without one; a hardcoded radius may not return.
4. **The browser walk (`scripts/browser_walk.py`).** A real Chromium against a
   live server and a throwaway database: sign in through the product's own
   form, read the board, focus a pip and arrow along it, watch the readout name
   the square under the ring, hit-test the square's 24 px zone past its 11 px
   face, hit-test a button's 44 px zone past its box and the rail's stacked
   links (no dead gap, no stolen face — measured again after scrolling every
   dashboard table to its far edge), assert the deficiency queue's Resolve
   control starts on screen, measure the toast dismiss at 24 × 24
   with a zone past its box, watch the coverage and pack pages resume polling
   jobs they did not start, drive the status door's and the inspection sheet's
   refusals and assert `aria-invalid` + `aria-describedby` (and that editing
   clears them), confirm Leaflet initialises the map, measure the drawn credit
   ring against the caption with the map's own scale bar as the ruler (the
   walk's district carries a non-default 750 ft radius precisely so a
   hardcoded ring cannot pass), flip the theme on the map page and watch the
   ring's stroke change and return (its hue is the cascade's, not a value the
   script captured), drive the flow-test
   sheet's live preview to the stored numbers, watch the inspection sheet react
   to its result radio, watch the field door refuse an incomplete reading at
   each of its four rules (never queueing it — a row the server must refuse
   would fail every sync forever) and then queue a complete one into IndexedDB,
   flip the theme,
   tab the focus ring, and measure horizontal overflow at 375 px. It prints one
   line per claim and writes full-page screenshots to `.tmp/`. It is
   deliberately **not** in the gate suite — it needs a browser and boots a
   server, so the pinned `pytest` gate stays deterministic. Run it by hand
   after any layout change:

   ```bash
   python scripts/browser_walk.py
   ```
