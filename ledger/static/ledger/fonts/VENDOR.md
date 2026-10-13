# Vendored typeface — IBM Plex

| | |
|---|---|
| Family | IBM Plex Sans (400/500/600/700), IBM Plex Mono (400/500/600) |
| Upstream | https://github.com/IBM/plex |
| Package | `@fontsource/ibm-plex-sans@5`, `@fontsource/ibm-plex-mono@5` (latin subset, `woff2`) |
| Licence | SIL Open Font License 1.1 — see `OFL.txt` in this directory |
| Files | `ibm-plex-{sans,mono}-latin-{weight}-normal.woff2` |

**Why vendored.** HydrantProof's container is offline at runtime and serves no
request it did not receive. A webfont fetched from a CDN would be a runtime
outbound request, a third-party dependency in a fire district's records system,
and a silent failure on an air-gapped host. The seven files above are the whole
type system; `tokens.css` declares them and nothing else names a family.

**Why Plex.** Drawn for technical documentation and instrumentation, with a
companion mono cut, so a label and the number under it share a skeleton. It
carries the "engineered record" character this product needs without the
familiarity of the default UI faces (see `DESIGN.md` § 4).

**Changing it.** Replace the files, update the `@font-face` block in
`tokens.css`, and re-run `tests/test_ui_system.py` — the token-closure audit
fails if a component names a family that is not declared there.
