"""Startup sweep — run by the entrypoint after ``migrate``."""
from django.core.management.base import BaseCommand

from ledger.jobs import recover_jobs


class Command(BaseCommand):
    help = "Mark orphaned RUNNING/RENDERING jobs FAILED and clean partial rows/files."

    def handle(self, *args, **options):
        summary = recover_jobs()
        self.stdout.write(
            f"recover_jobs: {summary['runs']} coverage run(s) and {summary['packs']} pack(s) "
            f"marked FAILED (orphaned)"
        )
