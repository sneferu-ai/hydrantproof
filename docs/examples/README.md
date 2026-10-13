# HydrantProof — runnable examples

Five examples covering the most common jobs. Every example is self-contained
and prints its expected outcome; two of them (02, 05) need a running instance,
one (03) needs an editor account, one (04) needs the management environment,
and one (01) runs offline.

| # | File | What it shows | Needs |
|---|---|---|---|
| 1 | [`01_compute_flow.py`](01_compute_flow.py) | NFPA 291 arithmetic with the product's own engine: `Q_F`, `Q_R`, capacity class, warnings, the exclusion cases | Python env with the app importable (no server, no DB) |
| 2 | [`02_api_walkthrough.sh`](02_api_walkthrough.sh) | Full HTTP cycle with curl: health → registry → preview → dispatch CSV → coverage → pack → byte-identical download | A running instance; demo credentials or a viewer/editor account |
| 3 | [`03_import_hydrants.sh`](03_import_hydrants.sh) | Two-phase CSV import (dry run always; commit behind `COMMIT=1`) | A running instance + an editor account |
| 4 | [`04_export_ledger.sh`](04_export_ledger.sh) | `manage.py export_ledger` + verification that `AppSetting` is excluded | The app's Python env + `DATABASE_URL` |
| 5 | [`05_python_client.py`](05_python_client.py) | Scripting the API from Python (stdlib `urllib` only): health, read, preview, coverage, pack, zip inspection | A running instance; same accounts as #2 |

## Running them

All examples assume the repository root as the working directory (or, for the
scripts, resolve their own paths).

```bash
# 1 — offline arithmetic (no server needed)
python docs/examples/01_compute_flow.py

# 2 and 5 — against a running instance (demo defaults shown)
BASE=http://localhost:8000 HP_USER=demo-editor HP_PASS=hydrantproof-demo \
  sh docs/examples/02_api_walkthrough.sh
BASE=http://localhost:8000 python docs/examples/05_python_client.py

# 3 — dry run (safe); add COMMIT=1 to apply (see the demo-deactivation note)
BASE=http://localhost:8000 sh docs/examples/03_import_hydrants.sh
COMMIT=1 BASE=http://localhost:8000 sh docs/examples/03_import_hydrants.sh

# 4 — export + verify (uses DATABASE_URL from the environment)
DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof \
  sh docs/examples/04_export_ledger.sh
```

## Expected results

* **01** — Fixture A prints `Q_F 887.9` / `Q_R 1105.2` / class `A`; the meter
  case prints `METER_AND_PITOT_PRESENT`; static-below-target and
  excessive-extrapolation cases print `Q_R None` with their warning; the
  rejected input names `residual_psi`.
* **02** — ends with `WALKTHROUGH OK`; the zip lists `pack.pdf`,
  `hydrants.csv`, `coverage_worksheet.csv`, `parcel_summary.csv`,
  `oos_register.csv`, `manifest.txt`, and the two downloads compare equal.
* **03** — dry run reports `rows_ok=2, rows_rejected=0` and returns a commit
  token; with `COMMIT=1` the commit reports `rows_ok=2` and the two new
  hydrants appear in the registry.
* **04** — `export_ledger: schema v1, N rows across M tables`, then
  `AppSetting excluded: yes` and `EXPORT OK`.
* **05** — `CLIENT CYCLE OK` after printing health, hydrant count, preview
  numbers, coverage counts, and the pack's byte size + zip entries.

## ⚠ Demo-deactivation note (example 3)

Committing the import creates a **real** hydrant row. The first real row
deactivates every demo account on that instance, in the same transaction — by
design (`ledger/signals.py`). Run the commit only against a scratch instance,
or with a real editor account you intend to keep using. The dry run is always
safe.

## Requirements

* Examples 1 and 4 use the application's Python environment
  (`pip install -r requirements.txt -r requirements-dev.txt`).
* Examples 2, 3 and 5 use `curl` + `python3` (stdlib only).
* Examples 2 and 5 create CoverageRun/EvidencePack rows (async jobs) but no
  ledger rows; they are safe to run repeatedly — each run creates a new
  coverage run and pack, which the retention job prunes later.
