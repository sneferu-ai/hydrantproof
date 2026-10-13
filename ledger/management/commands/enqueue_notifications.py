"""Enqueue outbox rows for the current as-of date (idempotent)."""
from django.core.management.base import BaseCommand

from ledger.notifications import enqueue_notifications


class Command(BaseCommand):
    help = "Enqueue OOS_EXPIRED / TEST_OVERDUE / INSPECTION_OVERDUE rows."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", default=None, help="ISO date (default: today)")

    def handle(self, *args, **options):
        as_of = None
        if options["as_of"]:
            from datetime import date

            as_of = date.fromisoformat(options["as_of"])
        summary = enqueue_notifications(as_of)
        self.stdout.write(f"enqueue_notifications: {summary}")
