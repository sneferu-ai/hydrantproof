#!/bin/sh
# HydrantProof entrypoint — fixed order (specification §14).
#
# (0) static refusal gate  (1) database wait  (2) migrate  (3) ensure_secret
# (4) data-bearing posture gate  (5) recover_jobs  (6) collectstatic if needed
# (7) seed_demo  (8) media ownership hand-off  (9) gunicorn (no --preload)
set -e

PORT="${PORT:-8000}"
WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# ---------------------------------------------------------------------------
# (0) Refusal gates.  Static gate first (no database needed); the data-bearing
#     gate runs again after migrate, when the ledger can be read.
# ---------------------------------------------------------------------------
python manage.py check_posture --static
log "posture: static gate passed"

# ---------------------------------------------------------------------------
# (1) Wait for the database (150 s = 30 x 5 s; exit if it never arrives).
# ---------------------------------------------------------------------------
i=0
until python manage.py migrate --plan >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    log "FATAL: database did not become reachable within 150 s"
    exit 1
  fi
  sleep 5
done
log "database reachable"

# ---------------------------------------------------------------------------
# (2) Migrations  (3) secret key  (4) data gate  (5) orphan sweep  (7) demo seed
# ---------------------------------------------------------------------------
python manage.py migrate --noinput
log "migrations applied"

# The secret key is resolved BEFORE the data-bearing posture gate: the gate
# refuses to serve with the public build-time constant, and ensure_secret is
# what makes a real key exist (env -> persisted -> generated+persisted).
python manage.py ensure_secret
if [ -z "${DJANGO_SECRET_KEY:-}" ]; then
  DJANGO_SECRET_KEY="$(python manage.py ensure_secret --print-secret)"
  export DJANGO_SECRET_KEY
fi

python manage.py check_posture
log "posture: data gate passed"

python manage.py recover_jobs
log "job sweep complete"

# Static assets: the image collects them at build time; a bare-metal or
# fresh-volume boot collects them here (idempotent, tiny asset set).
if [ ! -f "${STATIC_ROOT:-staticfiles}/staticfiles.json" ]; then
  python manage.py collectstatic --noinput >/dev/null
  log "static assets collected"
fi

if [ "${SEED_DEMO:-true}" = "true" ]; then
  python manage.py seed_demo
fi

# ---------------------------------------------------------------------------
# (8) Media ownership.  When this script runs as root, gunicorn's workers run
#      as the unprivileged `app` user — but a fresh named volume (or a host
#      mount) arrives root-owned, and every media write (packs, attachments,
#      overlays, stored import files) would fail with PermissionError.  Hand
#      the media tree to `app` before the workers start.  Best-effort: a
#      read-only or absent media tree logs and continues (the failure then
#      surfaces per job, as before).
# ---------------------------------------------------------------------------
MEDIA_ROOT_DIR="${HYDRANTPROOF_MEDIA_ROOT:-/app/media}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$MEDIA_ROOT_DIR"
  if chown -R app:app "$MEDIA_ROOT_DIR" 2>/dev/null; then
    log "media root ${MEDIA_ROOT_DIR} handed to app"
  else
    log "WARNING: could not chown ${MEDIA_ROOT_DIR} to app; media writes may fail"
  fi
fi

# ---------------------------------------------------------------------------
# (9) gunicorn — WITHOUT --preload (the executor is lazily constructed inside
#     each worker on first job, so a forked pool is impossible).
# ---------------------------------------------------------------------------
if [ "$(id -u)" = "0" ] && id app >/dev/null 2>&1; then
  # The master may bind a privileged port; the workers always run as the
  # unprivileged app user (drop after bind).
  log "starting gunicorn on 0.0.0.0:${PORT}, workers as app"
  exec python -m gunicorn hydrantproof.wsgi:application \
    --bind "0.0.0.0:${PORT}" --workers "$WORKERS" --timeout "$TIMEOUT" \
    --user app --group app --access-logfile - --error-logfile -
fi

if [ "$(id -u)" = "0" ]; then
  # Bare-metal host: the `app` user exists only inside the Docker image (the
  # Dockerfile creates it).  Warn loudly here instead of dying later inside
  # gunicorn's privilege drop with a bare "no such user".
  log "WARNING: user 'app' does not exist on this host (bare-metal boot); starting gunicorn as root"
fi

log "starting gunicorn on 0.0.0.0:${PORT} as uid $(id -u)"
exec python -m gunicorn hydrantproof.wsgi:application \
  --bind "0.0.0.0:${PORT}" --workers "$WORKERS" --timeout "$TIMEOUT" \
  --access-logfile - --error-logfile -
