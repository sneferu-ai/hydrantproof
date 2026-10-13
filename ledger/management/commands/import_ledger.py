"""Admin restore from an ``export_ledger`` JSON (``--confirm`` required).

Validates the schema version, restores with ``bulk_create`` preserving primary
keys (a documented bypass of the Tier A ``save()`` pk-guard) and disables the
Tier A triggers only inside the restore transaction.  Refuses on a version
mismatch.

Two restore-truth rules live here because ``bulk_create`` never fires the
Tier A ``post_save`` signal (§10):

* user rows carry their group memberships as names (a many-to-many is not a
  constructor kwarg) — memberships are re-applied, to newly created users
  only, after the insert;
* the first-real-row deactivation invariant is reconciled directly at the end
  of the restore: a restore that leaves any non-sample row deactivates the
  demo accounts in the same transaction; a sample-only restore clears a stale
  ``demo_deactivated_at`` marker so the signal is armed for the next real row.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from ledger.export_document import SCHEMA_VERSION
from ledger.management.commands.purge_demo import TIER_A_TABLES, non_sample_data_exists
from ledger.models import AppSetting
from ledger.signals import DEMO_DEACTIVATED_MARKER, deactivate_demo_users


class Command(BaseCommand):
    help = "Restore the ledger from a versioned JSON export (destructive; --confirm)."

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("import_ledger restores data; pass --confirm to proceed")
        payload = json.loads(Path(options["source"]).read_text())
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise CommandError(
                f"schema version mismatch: file={payload.get('schema_version')} "
                f"supported={SCHEMA_VERSION}"
            )
        from django.apps import apps

        with transaction.atomic():
            with connection.cursor() as cursor:
                for table in TIER_A_TABLES:
                    cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            restored = 0
            for model_name, rows in payload.get("tables", {}).items():
                if model_name == "auth.User":
                    continue
                model = apps.get_model("ledger", model_name)
                if model_name == "DistrictSettings":
                    # The singleton (pk=1) always exists — it is created by
                    # migration and deliberately not deleted by purge_demo —
                    # so it restores as an upsert; every other exported
                    # table is empty on the documented purge/fresh-install
                    # restore paths and inserts with pk preservation.
                    for row in rows:
                        row = dict(row)
                        pk = row.pop("id", model.SINGLETON_PK)
                        model.objects.update_or_create(pk=pk, defaults=row)
                    restored += len(rows)
                    continue
                instances = [model(**row) for row in rows]
                model.objects.bulk_create(instances, ignore_conflicts=False)
                restored += len(instances)
            users = payload.get("tables", {}).get("auth.User", [])
            if users:
                restored += self._restore_users(users)
            with connection.cursor() as cursor:
                # The inserts queued deferred foreign-key constraint events;
                # flush them (the restore wrote every referenced row) so the
                # re-enable is legal and the commit carries enabled triggers.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for table in TIER_A_TABLES:
                    cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
            if non_sample_data_exists():
                deactivate_demo_users()
            else:
                AppSetting.objects.filter(key=DEMO_DEACTIVATED_MARKER).delete()
        self.stdout.write(self.style.SUCCESS(f"import_ledger: restored {restored} rows"))

    def _restore_users(self, users: list[dict]) -> int:
        """Insert user rows (existing accounts untouched) and re-apply their
        group memberships by name — the export carries ``groups`` as a list of
        names, which is not a valid ``User(**row)`` kwarg."""
        from django.contrib.auth.models import Group, User

        incoming = [str(row.get("username", "")) for row in users]
        preexisting = set(User.objects.filter(username__in=incoming).values_list("username", flat=True))
        rows = []
        memberships = {}
        for row in users:
            row = dict(row)
            memberships[row.get("username")] = list(row.pop("groups", []) or [])
            rows.append(User(**row))
        User.objects.bulk_create(rows, ignore_conflicts=True)
        for username, names in memberships.items():
            if not names or username in preexisting:
                continue  # ignore_conflicts: an existing account keeps its roles
            user = User.objects.filter(username=username).first()
            if user is None:
                continue
            for name in names:
                group, _ = Group.objects.get_or_create(name=name)
                user.groups.add(group)
        return len(rows)
