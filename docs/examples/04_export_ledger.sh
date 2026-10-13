#!/bin/sh
# Example 4 — export the ledger and verify the document (backup / migration).
#
# Uses the product's own management commands. The export is read-only and
# excludes AppSetting (the DB-persisted secret key never leaves in JSON).
#
# Setup:
#   Run from the repository root with the app's Python environment and a
#   working DATABASE_URL (same one the server uses).
#
# Run:
#   DATABASE_URL=postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof \
#     sh docs/examples/04_export_ledger.sh
#
# Expected output: "export_ledger: schema v1, N rows across M tables → …"
# followed by a verification block ending in "EXPORT OK".
set -e

OUT="${TMPDIR:-/tmp}/hydrantproof-example"
mkdir -p "$OUT"
EXPORT="$OUT/ledger-export.json"

echo "=== Export ==="
python manage.py export_ledger --output "$EXPORT"

echo
echo "=== Verify the document ==="
python3 - "$EXPORT" <<'PY'
import json, sys

with open(sys.argv[1]) as handle:
    payload = json.load(handle)

assert payload["schema_version"] == 1, payload.get("schema_version")
tables = payload["tables"]
rows = sum(len(v) for v in tables.values())
print(f"schema_version: {payload['schema_version']}")
print(f"tables ({len(tables)}): {', '.join(sorted(tables))}")
print(f"total rows: {rows}")
assert "AppSetting" not in tables, "AppSetting must never be exported"
assert "auth.User" in tables, "user tables ride the export (needed for restore)"
print("AppSetting excluded: yes")
print("auth.User included: yes")
PY

echo
echo "=== Restore (destructive; shown for reference — not executed) ==="
cat <<'TXT'
  # On the target instance (empty or purged ledger):
  python manage.py import_ledger ledger-export.json --confirm

  # Reminder: the database dump and the media volume are the two objects that
  # must be backed up together:
  #   pg_dump -Fc hydrantproof > hydrantproof.dump
  #   + backup of the hydrantproof_media volume
TXT

echo
echo "EXPORT OK — $EXPORT"
