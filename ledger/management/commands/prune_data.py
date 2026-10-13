"""The cron path: budgets, the expired-lease reaper, FAILED runs, age retention.

Row and files are always deleted together — never file-only.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger import constants as C
from ledger.jobs import reap_expired_leases
from ledger.media import delete_pack_files, media_bytes_total
from ledger.models import (
    CoverageResult,
    CoverageRun,
    EvidencePack,
    ImportBatch,
    LoginAttempt,
    NotificationOutbox,
)


class Command(BaseCommand):
    help = "Reap expired leases and enforce pack/run/media/age retention budgets."

    def add_arguments(self, parser):
        parser.add_argument("--keep-packs", type=int, default=10)
        parser.add_argument("--keep-runs", type=int, default=3)
        parser.add_argument("--max-result-rows", type=int, default=250_000)
        parser.add_argument("--login-attempts-days", type=int, default=30)
        parser.add_argument("--batches-days", type=int, default=365)
        parser.add_argument("--outbox-days", type=int, default=90)
        parser.add_argument("--max-media-mb", type=int, default=C.DEFAULT_MAX_MEDIA_MB)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        dry = options["dry_run"]
        summary = {"reaped": {}, "failed_runs": 0, "runs": 0, "packs": 0, "rows": 0,
                   "login_attempts": 0, "outbox": 0, "batches": 0, "media_bytes": 0}

        if not dry:
            summary["reaped"] = reap_expired_leases(now)
        else:
            summary["reaped"] = {"runs": 0, "packs": 0}

        # FAILED runs older than 90 days (with any packs they still hold)
        cutoff = now - timedelta(days=90)
        for run in CoverageRun.objects.filter(status=C.RUN_STATUS_FAILED, created_at__lt=cutoff):
            for pack in run.packs.all():
                if not dry:
                    delete_pack_files(pack)
                summary["packs"] += 1
            if not dry:
                run.delete()
            summary["failed_runs"] += 1

        # Result-row budget: delete oldest COMPLETE runs (always keeping newest).
        keep_runs = max(1, options["keep_runs"])
        complete = list(CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).order_by("-finished_at", "-id"))
        for run in complete[keep_runs:]:
            for pack in run.packs.all():
                if not dry:
                    delete_pack_files(pack)
                summary["packs"] += 1
            if not dry:
                run.delete()
            summary["runs"] += 1

        max_rows = options["max_result_rows"]
        newest = CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).order_by("-finished_at", "-id").first()
        total_rows = CoverageResult.objects.count()
        if total_rows > max_rows and newest is not None:
            for run in CoverageRun.objects.filter(status=C.RUN_STATUS_COMPLETE).exclude(pk=newest.pk).order_by("finished_at", "id"):
                if CoverageResult.objects.count() <= max_rows:
                    break
                count = CoverageResult.objects.filter(run=run).count()
                for pack in run.packs.all():
                    if not dry:
                        delete_pack_files(pack)
                    summary["packs"] += 1
                if not dry:
                    run.delete()
                summary["rows"] += count

        # Pack count and media-byte budgets (oldest first; row + files
        # together).  RENDERING packs are never pruned here — the lease reaper
        # owns in-flight rows; a cron prune may not delete a live job's row.
        keep_packs = max(1, options["keep_packs"])
        packs = list(
            EvidencePack.objects.exclude(status=C.PACK_STATUS_RENDERING)
            .order_by("-created_at", "-id")
        )
        for pack in packs[keep_packs:]:
            if not dry:
                delete_pack_files(pack)
            summary["packs"] += 1
        budget_bytes = options["max_media_mb"] * 1024 * 1024
        for pack in (
            EvidencePack.objects.exclude(status=C.PACK_STATUS_RENDERING)
            .order_by("created_at", "id")
        ):
            if media_bytes_total() <= budget_bytes:
                break
            if not dry:
                delete_pack_files(pack)
            summary["packs"] += 1

        # Age retention
        login_cutoff = now - timedelta(days=options["login_attempts_days"])
        qs = LoginAttempt.objects.filter(at__lt=login_cutoff)
        summary["login_attempts"] = qs.count()
        if not dry:
            qs.delete()

        outbox_cutoff = now - timedelta(days=options["outbox_days"])
        qs = NotificationOutbox.objects.filter(sent_at__isnull=False, sent_at__lt=outbox_cutoff)
        summary["outbox"] = qs.count()
        if not dry:
            qs.delete()

        batch_cutoff = now - timedelta(days=options["batches_days"])
        for batch in ImportBatch.objects.filter(created_at__lt=batch_cutoff):
            if batch.file_path and not dry:
                from ledger.media import media_root

                try:
                    (media_root() / batch.file_path).unlink(missing_ok=True)
                except OSError:
                    pass
            if not dry:
                batch.delete()
            summary["batches"] += 1

        summary["media_bytes"] = media_bytes_total()
        self.stdout.write(f"prune_data: {summary}")
