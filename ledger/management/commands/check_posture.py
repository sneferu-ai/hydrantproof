"""The entrypoint's refusal gates (specification §13-13, §14).

Refuse to start when ``ALLOWED_HOSTS=*`` or ``COOKIE_SECURE=false`` while
``SEED_DEMO=false`` or any non-sample ledger row exists — unless
``ALLOW_INSECURE_LOOPBACK=true``, which waives **both** gates and prints a loud
banner naming them.  ``--static`` skips the database probe (used before the DB
wait); the post-migrate call checks the real data.

The non-static call additionally refuses to serve with the public build-time
secret key: the entrypoint runs ``ensure_secret`` first, so by this point a
real key (environment variable or the DB-persisted one) must exist.
"""
from __future__ import annotations

import os
import sys

from django.core.management.base import BaseCommand

BANNER = (
    "***********************************************************************\n"
    "* INSECURE LOOPBACK MODE — the following gates are WAIVED on purpose:  *\n"
    "*   - ALLOWED_HOSTS=*  (host header validation disabled)               *\n"
    "*   - COOKIE_SECURE=false  (session/CSRF cookies may travel in clear)  *\n"
    "* This is the documented air-gapped, loopback-only escape. Never expose *\n"
    "* this instance to a network. Set ALLOWED_HOSTS, COOKIE_SECURE=true    *\n"
    "* and remove ALLOW_INSECURE_LOOPBACK for any other deployment.         *\n"
    "***********************************************************************"
)


class Command(BaseCommand):
    help = "Refuse an insecure production posture (exit 1) unless explicitly waived."

    def add_arguments(self, parser):
        parser.add_argument("--static", action="store_true",
                            help="Skip the database probe (pre-migrate entrypoint gate).")

    def handle(self, *args, **options):
        allowed_hosts = os.environ.get("ALLOWED_HOSTS", "*")
        cookie_secure = os.environ.get("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        seed_demo = os.environ.get("SEED_DEMO", "true").strip().lower() in {"1", "true", "yes", "on"}
        waived = os.environ.get("ALLOW_INSECURE_LOOPBACK", "").strip().lower() in {"1", "true", "yes", "on"}

        if not options["static"]:
            self._check_secret_key()

        insecure = allowed_hosts.strip() == "*" or not cookie_secure
        if not insecure:
            self.stdout.write("posture: secure settings detected")
            self._warn_proxy_posture(seed_demo)
            return

        reasons = []
        if allowed_hosts.strip() == "*":
            reasons.append("ALLOWED_HOSTS=*")
        if not cookie_secure:
            reasons.append("COOKIE_SECURE=false")

        non_sample = False
        if not options["static"]:
            try:
                from ledger.management.commands.purge_demo import non_sample_data_exists

                non_sample = non_sample_data_exists()
            except Exception as exc:  # database not ready yet — defer to the post-migrate call
                self.stdout.write(f"posture: data probe deferred ({type(exc).__name__})")
                self._warn_proxy_posture(seed_demo)
                return

        blocking = (not seed_demo) or non_sample
        if blocking:
            if not waived:
                detail = []
                if not seed_demo:
                    detail.append("SEED_DEMO=false")
                if non_sample:
                    detail.append("non-sample ledger rows exist")
                self.stderr.write(self.style.ERROR(
                    "refusing to start: " + ", ".join(reasons) + " while " + ", ".join(detail)
                    + ". Set ALLOWED_HOSTS/COOKIE_SECURE, or ALLOW_INSECURE_LOOPBACK=true "
                      "for an air-gapped loopback-only instance."
                ))
                sys.exit(1)
            self.stderr.write(BANNER)
        self._warn_proxy_posture(seed_demo)

    def _check_secret_key(self) -> None:
        """Refuse to serve with the public build-time key (see module docstring)."""
        from django.conf import settings

        from ledger.secret_key import resolve_persisted_secret

        env_key = os.environ.get("DJANGO_SECRET_KEY", "")
        if env_key and env_key != settings.EPHEMERAL_SECRET_KEY:
            self.stdout.write("secret key: environment variable present")
            return
        persisted = resolve_persisted_secret()
        if persisted is None:  # database not ready yet — defer, as the data probe does
            self.stdout.write("posture: secret probe deferred (database unavailable)")
            return
        if persisted and persisted != settings.EPHEMERAL_SECRET_KEY:
            self.stdout.write("secret key: DB-persisted key present")
            return
        self.stderr.write(self.style.ERROR(
            "refusing to start: the Django secret key is the public build-time constant "
            "(sessions and CSRF tokens would be forgeable). Run 'manage.py ensure_secret' "
            "(the entrypoint runs it before gunicorn) or set DJANGO_SECRET_KEY."
        ))
        sys.exit(1)

    def _warn_proxy_posture(self, seed_demo: bool) -> None:
        trust_proxy = os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() in {"1", "true", "yes", "on"}
        if trust_proxy:
            return
        non_sample = False
        try:
            from ledger.management.commands.purge_demo import non_sample_data_exists

            non_sample = non_sample_data_exists()
        except Exception:
            pass
        if non_sample or not seed_demo:
            self.stderr.write(self.style.WARNING(
                "TRUST_PROXY_HEADERS=false: the per-IP throttle dimension is OFF. It is the only "
                "password-spray defense; enable it only behind a header-stripping TLS proxy (README)."
            ))
