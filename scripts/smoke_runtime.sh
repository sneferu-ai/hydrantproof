#!/bin/sh
# HydrantProof — runtime smoke: boot the real entrypoint and walk the HTTP surface.
#
# Usage: PORT=8123 ./scripts/smoke_runtime.sh
# Requires DATABASE_URL (the declared db service).  Exercises the ordered smoke
# plan in test_plan.md sections 0–2 and 4 (not the field PWA journey, which
# needs a browser).
set -e

PORT="${PORT:-8123}"
BASE="http://127.0.0.1:${PORT}"
LOG="${TMPDIR:-/tmp}/hydrantproof-smoke.log"
PASS=0
FAIL=0

check() {
  description="$1"; expected="$2"; actual="$3"
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1)); printf 'ok   %-52s %s\n' "$description" "$actual"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL %-52s expected %s got %s\n' "$description" "$expected" "$actual"
  fi
}

echo "starting: PORT=$PORT ./entrypoint.sh  (log: $LOG)"
PORT="$PORT" ./entrypoint.sh >"$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

i=0
until curl -fsS "$BASE/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -ge 60 ] && { echo "server did not become ready"; tail -40 "$LOG"; exit 1; }
  sleep 2
done
echo "server ready; log tail:"; tail -12 "$LOG"

check "healthz is 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")"
check "healthz reports ok" "ok" "$(curl -s "$BASE/healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
check "openapi.json is 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/openapi.json")"
check "openapi is 3.0.3" "3.0.3" "$(curl -s "$BASE/openapi.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["openapi"])')"
check "login page is 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/login/")"
check "anonymous JSON is 401" "401" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/hydrants/")"
check "anonymous JSON detail" "Authentication required." \
  "$(curl -s "$BASE/api/v1/hydrants/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["detail"])')"
check "anonymous HTML redirects" "302" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/dashboard/")"

AUTH="demo-editor:hydrantproof-demo"
check "basic-auth hydrant list is 200" "200" \
  "$(curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/api/v1/hydrants/")"
check "seeded hydrants >= 20" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/hydrants/" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str(d["count"] >= 20).lower())')"
check "readiness field present" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/hydrants/" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str("readiness" in d["results"][0] and "trend" in d["results"][0]).lower())')"
check "compute-preview fixture A" "887.9" \
  "$(curl -s -u "$AUTH" -H 'Content-Type: application/json' -d '{"tested_on":"2026-10-03","static_psi":"80.00","residual_psi":"40.00","outlets":[{"outlet_diameter_in":"2.50","outlet_coefficient":"0.90","pitot_psi":"28.00"}]}' "$BASE/api/v1/compute-preview/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["computed_total_gpm"])')"
check "dispatch CSV is 200" "200" \
  "$(curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/api/v1/exports/dispatch/")"
check "dispatch carries the verify literal" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/exports/dispatch/" | grep -q "Field-verify status, location, and expected flow before relying on this record." && echo true || echo false)"
check "static asset served" "200" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/static/ledger/app.css")"
check "static asset is non-empty" "true" \
  "$(curl -s "$BASE/static/ledger/app.css" | wc -c | awk '{print ($1 > 100) ? "true" : "false"}')"
check "login page renders" "true" \
  "$(curl -s "$BASE/login/" | grep -q "csrfmiddlewaretoken" && echo true || echo false)"
check "service worker served" "200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/sw.js")"
check "field page redirects when anonymous" "302" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/field/")"

# --- the worksheet loop: coverage run → poll → results → pack → downloads ----
RUN_ID="$(curl -s -u "$AUTH" -H 'Content-Type: application/json' -d '{"parcel_filter": null}' "$BASE/api/v1/coverage-runs/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
check "coverage POST is 202 with an id" "true" "$([ -n "$RUN_ID" ] && echo true || echo false)"
i=0
while :; do
  STATUS="$(curl -s -u "$AUTH" "$BASE/api/v1/coverage-runs/$RUN_ID/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$STATUS" = "COMPLETE" ] && break
  [ "$STATUS" = "FAILED" ] && break
  i=$((i + 1)); [ "$i" -ge 120 ] && break
  sleep 1
done
check "coverage run completes" "COMPLETE" "$STATUS"
check "coverage results are served" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/coverage-runs/$RUN_ID/results/" | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["count"] > 0).lower())')"
check "parcel coverage read is 200" "200" \
  "$(curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$BASE/api/v1/parcels/1/coverage/")"

PACK_ID="$(curl -s -u "$AUTH" -H 'Content-Type: application/json' -d "{\"run_id\": $RUN_ID}" "$BASE/api/v1/evidence-packs/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
check "pack POST is accepted with an id" "true" "$([ -n "$PACK_ID" ] && echo true || echo false)"
i=0
while :; do
  PSTATUS="$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  [ "$PSTATUS" = "COMPLETE" ] && break
  [ "$PSTATUS" = "FAILED" ] && break
  i=$((i + 1)); [ "$i" -ge 120 ] && break
  sleep 1
done
check "evidence pack completes" "COMPLETE" "$PSTATUS"
ZIP_A="$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/zip/" | sha256sum | awk '{print $1}')"
ZIP_B="$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/zip/" | sha256sum | awk '{print $1}')"
check "pack zip re-download is byte-identical" "true" "$([ "$ZIP_A" = "$ZIP_B" ] && echo true || echo false)"
check "pack zip is a zip" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/zip/" | head -c 2 | grep -q 'PK' && echo true || echo false)"
check "pack pdf is a pdf" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/pdf/" | head -c 5 | grep -q '%PDF-' && echo true || echo false)"
check "pack manifest rides in the zip" "true" \
  "$(curl -s -u "$AUTH" "$BASE/api/v1/evidence-packs/$PACK_ID/zip/" -o /tmp/pack.zip && python3 -c 'import zipfile; z=zipfile.ZipFile("/tmp/pack.zip"); n=z.namelist(); print(str("manifest.txt" in n and "coverage_worksheet.csv" in n and "pack.pdf" in n).lower())')"

echo
echo "smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || { echo "--- server log tail ---"; tail -60 "$LOG"; exit 1; }
