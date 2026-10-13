"""Admin: allow demo seeding again after ``purge_demo``."""
from django.core.management.base import BaseCommand

from ledger.models import AppSetting


class Command(BaseCommand):
    help = "Remove the demo_purged marker so seed_demo may run again."

    def handle(self, *args, **options):
        deleted, _ = AppSetting.objects.filter(key="demo_purged").delete()
        self.stdout.write(f"clear_purge_marker: {'cleared' if deleted else 'marker was absent'}")
