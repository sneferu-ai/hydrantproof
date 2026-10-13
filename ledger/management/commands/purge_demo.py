"""Purge every sample row and file — or refuse, but always deactivate demo users.

Refuses (exit 1, no data changes) when any non-sample hydrant, parcel, test,
inspection or attachment exists.  Even when refusing it deactivates the demo
users.  Otherwise one transaction: Tier A triggers are disabled inside that
transaction only, lifecycle tables are deleted through app logic, files go with
their rows, and the ``demo_purged`` marker blocks reseeding until
``clear_purge_marker`` runs.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from ledger.models import (
    AppSetting,
    Attachment,
    CoverageResult,
    CoverageRun,
    Deficiency,
    EvidencePack,
    ExportAudit,
    FlowTest,
    Hydrant,
    HydrantAttributeChange,
    HydrantStatusEvent,
    ImportBatch,
    Inspection,
    LoginAttempt,
    MapOverlay,
    MediaAudit,
    NotificationOutbox,
    OosAcknowledgement,
    OutletReading,
    Parcel,
    ParcelAttributeChange,
    SettingsAudit,
)
from ledger.signals import DEMO_USERNAMES, deactivate_demo_users

TIER_A_TABLES = (
    "ledger_flowtest", "ledger_outletreading", "ledger_hydrantstatusevent",
    "ledger_hydrantattributechange", "ledger_parcelattributechange", "ledger_inspection",
    "ledger_oosacknowledgement", "ledger_settingsaudit",
)


def non_sample_data_exists() -> bool:
    return (
        Hydrant.objects.filter(is_sample_data=False).exists()
        or Parcel.objects.filter(is_sample_data=False).exists()
        or FlowTest.objects.filter(is_sample_data=False).exists()
        or Inspection.objects.filter(is_sample_data=False).exists()
        or Attachment.objects.filter(is_sample_data=False).exists()
    )


class Command(BaseCommand):
    help = "Delete all sample rows/files and the demo users (refuses on real data)."

    def handle(self, *args, **options):
        if non_sample_data_exists():
            updated = deactivate_demo_users()
            self.stderr.write(self.style.ERROR(
                "purge_demo: real (non-sample) ledger rows exist — refusing to delete data. "
                f"Demo users deactivated ({updated})."
            ))
            raise SystemExit(1)

        with transaction.atomic():
            with connection.cursor() as cursor:
                for table in TIER_A_TABLES:
                    cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            self._delete_everything()
            with connection.cursor() as cursor:
                # The deletes queued deferred foreign-key constraint events;
                # PostgreSQL refuses ALTER TABLE … ENABLE TRIGGER on a table
                # with pending trigger events, so the queue must be flushed
                # (the checks pass — the deletion order is dependency order)
                # before the triggers are re-enabled for the commit.  On the
                # failure path nothing here runs: the rollback restores the
                # trigger state (ALTER TABLE is transactional in PostgreSQL).
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for table in TIER_A_TABLES:
                    cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
            AppSetting.set("demo_purged", "1")
            deactivate_demo_users()
        self.stdout.write(self.style.SUCCESS(
            "purge_demo: sample data deleted; seeding blocked until clear_purge_marker"
        ))

    def _delete_everything(self):
        from ledger.media import media_root

        for attachment in Attachment.objects.all():
            try:
                attachment.file.delete(save=False)
            except OSError:
                pass
        Attachment.objects.all().delete()
        MediaAudit.objects.all().delete()
        OosAcknowledgement.objects.all().delete()
        NotificationOutbox.objects.all().delete()
        for pack in EvidencePack.objects.all():
            for relative in (pack.zip_path, pack.pdf_path):
                if relative:
                    try:
                        (media_root() / relative).unlink(missing_ok=True)
                    except OSError:
                        pass
        EvidencePack.objects.all().delete()
        CoverageResult.objects.all().delete()
        CoverageRun.objects.all().delete()
        Deficiency.objects.all().delete()
        Inspection.objects.all().delete()
        OutletReading.objects.all().delete()
        FlowTest.objects.all().delete()
        HydrantStatusEvent.objects.all().delete()
        HydrantAttributeChange.objects.all().delete()
        ParcelAttributeChange.objects.all().delete()
        SettingsAudit.objects.all().delete()
        ExportAudit.objects.all().delete()
        LoginAttempt.objects.all().delete()
        Parcel.objects.all().delete()
        Hydrant.objects.all().delete()
        for overlay in MapOverlay.objects.all():
            try:
                (media_root() / overlay.geojson_path).unlink(missing_ok=True)
            except OSError:
                pass
        MapOverlay.objects.all().delete()
        for batch in ImportBatch.objects.all():
            if batch.file_path:
                try:
                    (media_root() / batch.file_path).unlink(missing_ok=True)
                except OSError:
                    pass
        ImportBatch.objects.all().delete()
        from django.contrib.auth.models import User

        User.objects.filter(username__in=DEMO_USERNAMES).delete()
