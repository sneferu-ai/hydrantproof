# SOUL — HydrantProof

The three paragraphs below are the anchor for every design decision in this
worktree. If a future round wants to do something that contradicts them, the
round is wrong and the paragraphs win.

---

## 1. Who is using this, and what hurts

**Chief Dana Whitfield, 58**, runs the Coldwater Volunteer Fire Department — 41
square miles, 186 protected structures, 63 hydrants, two engines, a tanker and
22 volunteers who all have day jobs. She is the water-supply officer as well as
the chief, because there is nobody else. It is 9:40 on a Tuesday night and she
is at the hall's kitchen table with the department's five-year-old laptop, a mug
of coffee that has gone cold, and a manila folder of 2019 flow-test sheets that
a retiring assistant chief wrote in pencil. The ISO surveyor arrives in three
weeks. Her pain is narrow and constant: she cannot answer *"which hydrants can
we actually rely on tonight?"* without opening a spreadsheet she does not trust,
and she has never once handed a surveyor a worksheet she was not embarrassed by.
If the software makes her re-derive a number by hand, or buries one dead hydrant
inside a list of sixty green ones, she closes the laptop and goes to bed — and
the district keeps counting on a hydrant that failed in 2021.

Her people use the other half of this product: **two firefighters standing at a
hydrant with a phone, gloves on, sun on the screen**, entering a pitot reading
they will not type twice. Everything they touch must be reachable with a thumb
and readable at arm's length in daylight.

## 2. What this feels like, hour 14 of 14

HydrantProof feels like **Linear's issue stream applied to cast iron**: a dense,
calm, one-pass-scannable record where the eye lands on the exceptions first,
chroma is rationed to the three states that decide whether a truck can draft,
and every number is exact, right-aligned and unit-labelled. It should feel like
a well-kept apparatus log — small writing, straight ruling, nothing ornamental,
and you trust it because it has never once lied about a number.

## 3. What this explicitly does NOT feel like

1. **NOT a modern SaaS dashboard.** No gradient hero, no three evenly-spaced KPI
   cards across the top, no floating pill navigation, no marketing voice, no
   "Welcome back".
2. **NOT a government forms portal.** No beige civic grey, no oversized body
   copy, no one-field-per-screen wizard, no "Please complete all required
   fields" and no red asterisk confetti.
3. **NOT a smart-city GIS control room.** No dark neon map, no glowing dots, no
   frosted-glass panels floating over a satellite basemap, no animated pulse on
   anything that is merely alive.
4. **NOT a consumer app.** No 20px-rounded cards, no emoji standing in for
   status, no "Oops! Something went wrong", no confetti, no mascot, no
   exclamation marks in system copy.

---

## The three words this product is

**Ledger. Gauge. Ruling.** A ledger keeps attributed rows in order. A gauge
tells you the exact number and never guesses. Ruling is what makes a page
scannable at arm's length. When a screen is in doubt, it gets more ruling and
fewer surfaces.
