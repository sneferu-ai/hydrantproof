"""Resolve the Django secret key: env -> generated+persisted -> ephemeral.

The DB-persisted fallback is **demo posture**: a startup warning fires whenever
it is used, and the README's production section requires the env key.  The
generated key is written under an advisory lock so concurrent application and
worker startup reuse the same value.
"""
from __future__ import annotations

import secrets

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from ledger.models import AppSetting
from ledger.secret_key import SECRET_KEY_NAME

_LOCK_KEY = 771_001


class Command(BaseCommand):
    help = "Ensure a usable DJANGO_SECRET_KEY (env, then persisted, then generated)."

    def add_arguments(self, parser):
        parser.add_argument("--print-env-hint", action="store_true",
                            help="Print the production guidance line.")
        parser.add_argument("--print-secret", action="store_true",
                            help="Print ONLY the resolved key on stdout (entrypoint capture).")

    def handle(self, *args, **options):
        import os

        if options.get("print_secret"):
            self.stdout.write(self._resolve_for_export(), ending="")
            return
        if os.environ.get("DJANGO_SECRET_KEY"):
            self.stdout.write("secret key: environment variable present")
            return
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
            existing = AppSetting.objects.filter(key=SECRET_KEY_NAME).first()
            if existing and existing.value:
                self.stdout.write(self.style.WARNING(
                    "secret key: using the DB-persisted key — DEMO POSTURE. "
                    "Set DJANGO_SECRET_KEY in production (see README)."
                ))
                return
            generated = secrets.token_urlsafe(64)
            AppSetting.objects.update_or_create(key=SECRET_KEY_NAME, defaults={"value": generated})
        self.stdout.write(self.style.WARNING(
            "secret key: generated and persisted to AppSetting — DEMO POSTURE. "
            "Set DJANGO_SECRET_KEY in production (see README)."
        ))
        if options.get("print_env_hint"):
            self.stdout.write("hint: export DJANGO_SECRET_KEY=$(python -c 'import secrets;print(secrets.token_urlsafe(64))')")

    def _resolve_for_export(self) -> str:
        """Resolve the key for the entrypoint: env, else persisted, else generate."""
        import os

        if os.environ.get("DJANGO_SECRET_KEY"):
            return os.environ["DJANGO_SECRET_KEY"]
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
            existing = AppSetting.objects.filter(key=SECRET_KEY_NAME).first()
            if existing and existing.value:
                return existing.value
            generated = secrets.token_urlsafe(64)
            AppSetting.objects.update_or_create(key=SECRET_KEY_NAME, defaults={"value": generated})
        return generated
