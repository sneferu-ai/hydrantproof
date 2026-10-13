"""Versioned JSON export — ledger and user tables, **excluding AppSetting**.

The DB-persisted secret key never enters a JSON backup.  Every export writes an
``ExportAudit`` row.  The document itself is built by
``ledger.export_document.build_export_payload`` — the same builder serves
``GET /api/v1/exports/ledger/`` (admin-only: it carries password hashes).
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from ledger.export_document import SCHEMA_VERSION, build_export_payload, json_default
from ledger.exports import audit_export


class Command(BaseCommand):
    help = "Write a versioned JSON export of the ledger (excludes AppSetting)."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="ledger-export.json")
        parser.add_argument("--user", default="")

    def handle(self, *args, **options):
        payload = build_export_payload()
        output = Path(options["output"])
        output.write_text(json.dumps(payload, default=json_default, indent=2))
        row_count = sum(len(rows) for rows in payload["tables"].values())
        actor = None
        if options["user"]:
            from django.contrib.auth.models import User as UserModel

            actor = UserModel.objects.filter(username=options["user"]).first()
        audit_export(path=str(output), kind="export_ledger", row_count=row_count, user=actor)
        self.stdout.write(self.style.SUCCESS(
            f"export_ledger: schema v{SCHEMA_VERSION}, {row_count} rows across "
            f"{len(payload['tables'])} tables → {output}"
        ))
