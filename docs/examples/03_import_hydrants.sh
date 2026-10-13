#!/bin/sh
# Example 3 — two-phase CSV import of a hydrant inventory.
#
# Always runs the DRY RUN (validates the whole file; applies nothing). The
# COMMIT only runs when you explicitly set COMMIT=1, because committing
# creates a REAL (non-sample) hydrant row — and the first real row deactivates
# every demo account on the instance, by design. Point this at a scratch
# instance if you want to see the full cycle.
#
# Setup:
#   A running HydrantProof reachable at $BASE, with an editor account.
#
# Run:
#   # validate only (safe anywhere)
#   BASE=http://localhost:8000 HP_USER=demo-editor HP_PASS=hydrantproof-demo \
#     sh docs/examples/03_import_hydrants.sh
#
#   # validate + commit (creates real rows; demo accounts deactivate)
#   COMMIT=1 BASE=http://scratch:8000 HP_USER=admin HP_PASS=... \
#     sh docs/examples/03_import_hydrants.sh
#
# Expected output: a row report with rows_ok=2, rows_rejected=0 and a
# commit_token; with COMMIT=1, a commit report with rows_ok=2. (Re-running
# against an instance where EX-001/EX-002 already exist reports
# rows_rejected=2 with "status changes only through status events" — the
# documented no-silent-status-upsert rule, not a failure.)
set -e

BASE="${BASE:-http://localhost:8000}"
HP_USER="${HP_USER:-demo-editor}"
HP_PASS="${HP_PASS:-hydrantproof-demo}"
AUTH="-u ${HP_USER}:${HP_PASS}"
OUT="${TMPDIR:-/tmp}/hydrantproof-example"

mkdir -p "$OUT"
CSV="$OUT/hydrants-example.csv"

cat > "$CSV" <<'CSV'
asset_tag,lat,lon,location_description,manufacturer,model,year_installed,main_size_inches,outlet_diameter_in,discharge_coefficient,ownership,source_org,notes,initial_status,initial_status_reason,expected_return_on,indefinite
EX-001,38.0100,-122.0100,Example St & 1st Ave,Acme,S-200,2005,8.00,2.50,0.90,Example District,Example District,,IN_SERVICE,,,
EX-002,38.0200,-122.0200,Example St & 2nd Ave,Acme,S-200,2006,8.00,2.50,0.90,Example District,Example District,,OUT_OF_SERVICE,Main break,,true
CSV
echo "wrote $CSV"; echo

echo "=== DRY RUN (validates; applies nothing) ==="
DRY=$(curl -s $AUTH -F mode=dry_run -F file=@"$CSV" -F supplier="Example District" \
  "$BASE/api/v1/imports/hydrants/")
echo "$DRY" | python3 -c '
import json, sys
d = json.load(sys.stdin)
b = d["batch"]
print("batch:", b["id"], "| mode:", b["mode"], "| rows_ok:", d["rows_ok"],
      "| rows_rejected:", d["rows_rejected"])
print("errors:", d["errors"])
print("warnings:", d["warnings"])
print("commit_token:", d["commit_token"][:12] + "…")
'
TOKEN=$(echo "$DRY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit_token"])')

if [ "${COMMIT:-0}" != "1" ]; then
  echo
  echo "Dry run complete. Re-run with COMMIT=1 to apply (creates real rows;"
  echo "the demo accounts deactivate on the first real row — by design)."
  exit 0
fi

echo
echo "=== COMMIT (applies the stored dry-run file) ==="
curl -s $AUTH -F mode=commit -F commit_token="$TOKEN" \
  "$BASE/api/v1/imports/hydrants/" | python3 -c '
import json, sys
d = json.load(sys.stdin)
b = d["batch"]
print("batch:", b["id"], "| mode:", b["mode"], "| rows_ok:", d["rows_ok"],
      "| rows_rejected:", d["rows_rejected"])
print("committed_at:", b["committed_at"])
'

echo
echo "=== Verify the new hydrants are listed ==="
curl -s $AUTH "$BASE/api/v1/hydrants/?q=EX-0" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "results" not in d:
    print("  (account no longer authorized:", d.get("detail", "unknown"), ")")
    print("  -> if you used a demo account, this is the deactivation invariant working:")
    print("     the first real row deactivated it. Re-run verification with a real")
    print("     editor account (HP_USER/HP_PASS).")
else:
    for row in d["results"]:
        print(" ", row["asset_tag"], "|", row["status"], "|", row["readiness"])
'

echo
echo "IMPORT CYCLE OK"
