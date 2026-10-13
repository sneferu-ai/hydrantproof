#!/bin/sh
# Example 2 — walk the HydrantProof HTTP API end to end with curl.
#
# Read-only plus the two async jobs: it lists hydrants, previews hydraulics,
# downloads the dispatch CSV, runs coverage, generates an evidence pack, and
# verifies the stored zip bytes. It creates NO ledger rows (no hydrant, test,
# inspection or parcel), so the demo accounts stay active.
#
# Setup:
#   1. A running HydrantProof (see docs/OPERATIONS.md §5) reachable at $BASE.
#   2. Demo credentials work only when the instance was seeded with
#      SEED_DEMO=true (the default).
#
# Run:
#   BASE=http://localhost:8000 HP_USER=demo-editor HP_PASS=hydrantproof-demo \
#     sh docs/examples/02_api_walkthrough.sh
#
# Expected output: a numbered walkthrough ending in "WALKTHROUGH OK" — the
# zip contains pack.pdf, the four CSVs, and manifest.txt, and the second
# download of the zip is byte-identical to the first.
set -e

BASE="${BASE:-http://localhost:8000}"
HP_USER="${HP_USER:-demo-editor}"
HP_PASS="${HP_PASS:-hydrantproof-demo}"
AUTH="-u ${HP_USER}:${HP_PASS}"
OUT="${TMPDIR:-/tmp}/hydrantproof-example"

mkdir -p "$OUT"
step() { printf '\n=== %s ===\n' "$1"; }

step "1. Health"
curl -s "$BASE/healthz"; echo

step "2. Hydrant list (first asset tag + readiness)"
curl -s $AUTH "$BASE/api/v1/hydrants/" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("count:", d["count"])
r = d["results"][0]
print("first:", r["asset_tag"], "|", r["readiness"], "|", r["capacity_class"])
'

step "3. Hydraulics preview (fixture A: 2.5in @ 0.90, 28 psi pitot)"
curl -s $AUTH -H 'Content-Type: application/json' -d '{
  "tested_on": "2026-10-03", "static_psi": "80.00", "residual_psi": "40.00",
  "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "28.00"}]
}' "$BASE/api/v1/compute-preview/"; echo

step "4. Dispatch CSV (header + first data row)"
curl -s $AUTH "$BASE/api/v1/exports/dispatch/" | head -2

step "5. Start a coverage run"
RUN=$(curl -s $AUTH -H 'Content-Type: application/json' -d '{"parcel_filter": null}' \
  "$BASE/api/v1/coverage-runs/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "run id: $RUN"
i=0
while :; do
  STATUS=$(curl -s $AUTH "$BASE/api/v1/coverage-runs/$RUN/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "COMPLETE" ] && break
  [ "$STATUS" = "FAILED" ] && { echo "coverage FAILED"; exit 1; }
  i=$((i+1)); [ "$i" -ge 120 ] && { echo "coverage timed out"; exit 1; }
  sleep 1
done
curl -s $AUTH "$BASE/api/v1/coverage-runs/$RUN/" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("status:", d["status"], "| pairs:", d["pair_count"],
      "| covered:", d["covered_count"], "/", d["protected_parcel_count"])
print("distribution bands:", d["credited_band_counts"])
print("staleness:", d.get("staleness"))
'

step "6. Generate an evidence pack from the run"
PACK=$(curl -s $AUTH -H 'Content-Type: application/json' -d "{\"run_id\": $RUN}" \
  "$BASE/api/v1/evidence-packs/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "pack id: $PACK"
i=0
while :; do
  PSTATUS=$(curl -s $AUTH "$BASE/api/v1/evidence-packs/$PACK/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$PSTATUS" = "COMPLETE" ] && break
  [ "$PSTATUS" = "FAILED" ] && { echo "pack FAILED"; exit 1; }
  i=$((i+1)); [ "$i" -ge 120 ] && { echo "pack timed out"; exit 1; }
  sleep 1
done
curl -s $AUTH "$BASE/api/v1/evidence-packs/$PACK/" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("status:", d["status"], "| bytes:", d["byte_size"], "| pages:", d["page_count"])
print("pdf sha256:", (d["pdf_sha256"] or "")[:16], "| zip sha256:", (d["csv_zip_sha256"] or "")[:16])
'

step "7. Download the stored zip twice and verify byte-identity + entries"
curl -s $AUTH -o "$OUT/pack-a.zip" "$BASE/api/v1/evidence-packs/$PACK/zip/"
curl -s $AUTH -o "$OUT/pack-b.zip" "$BASE/api/v1/evidence-packs/$PACK/zip/"
if cmp -s "$OUT/pack-a.zip" "$OUT/pack-b.zip"; then
  echo "two downloads are byte-identical: yes"
else
  echo "two downloads differ — FAIL"; exit 1
fi
python3 - "$OUT/pack-a.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    names = z.namelist()
    print("zip entries:", names)
    required = {"pack.pdf", "hydrants.csv", "coverage_worksheet.csv",
                "parcel_summary.csv", "oos_register.csv", "manifest.txt"}
    missing = required - set(names)
    assert not missing, f"missing entries: {missing}"
    print()
    print(z.read("manifest.txt").decode()[:400])
PY

printf '\nWALKTHROUGH OK — artifacts saved under %s\n' "$OUT"
