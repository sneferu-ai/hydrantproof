"""The versioned ledger export document — one builder shared by the
``export_ledger`` management command and ``GET /api/v1/exports/ledger/``.

The document includes ledger and user tables (password hashes are required for
restore) and **excludes AppSetting** — the DB-persisted secret key never
enters a JSON backup.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

SCHEMA_VERSION = 1

EXPORT_MODELS = (
    "DistrictSettings", "Hydrant", "FlowTest", "OutletReading", "HydrantStatusEvent",
    "HydrantAttributeChange", "Parcel", "ParcelAttributeChange", "Inspection", "Deficiency",
    "OosAcknowledgement", "Attachment", "MapOverlay", "ImportBatch", "NotificationOutbox",
    "SettingsAudit", "CoverageRun", "CoverageResult", "EvidencePack", "ExportAudit", "MediaAudit",
    "UserProfile",
)


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def serialize_row(instance) -> dict:
    row = {}
    for field in instance._meta.concrete_fields:
        row[field.attname] = getattr(instance, field.attname)
    return row


def build_export_payload() -> dict:
    from django.apps import apps
    from django.contrib.auth.models import User

    payload = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": timezone.now().isoformat(),
        "tables": {},
    }
    for model_name in EXPORT_MODELS:
        model = apps.get_model("ledger", model_name)
        payload["tables"][model_name] = [serialize_row(row) for row in model.objects.all()]
    payload["tables"]["auth.User"] = [
        {
            "id": user.pk,
            "username": user.username,
            "password": user.password,  # required for restore
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "date_joined": user.date_joined.isoformat() if user.date_joined else None,
            "groups": sorted(user.groups.values_list("name", flat=True)),
        }
        for user in User.objects.all()
    ]
    return payload
