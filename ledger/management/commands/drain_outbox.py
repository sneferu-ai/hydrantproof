"""Deliver unsent outbox rows to the static env ALERT_WEBHOOK_URL.

No CLI URL flag exists on purpose: a runtime-chosen URL is exactly the
outbound-call class this product refuses.  This is a customer-host management
command; it is never exercised in proof or preview.
"""
from django.core.management.base import BaseCommand

from ledger.notifications import drain_outbox


class Command(BaseCommand):
    help = "POST unsent outbox rows to ALERT_WEBHOOK_URL (10 s timeout, ≤5 attempts)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        summary = drain_outbox(limit=options["limit"])
        self.stdout.write(f"drain_outbox: {summary}")
