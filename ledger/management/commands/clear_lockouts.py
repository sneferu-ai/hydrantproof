"""Operator escape hatch: clear the username-keyed throttle window."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ledger.models import LoginAttempt


class Command(BaseCommand):
    help = "Delete LoginAttempt rows so a locked-out username can authenticate again."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        deleted, _ = LoginAttempt.objects.filter(username=options["username"]).delete()
        self.stdout.write(f"clear_lockouts: removed {deleted} attempt row(s) for {options['username']}")
