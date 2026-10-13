"""The staleness banner (specification §8 — anchored on ``snapshot_ts``).

A run reads as stale when (a) any ledger row's latest timestamp (Tier A tables
+ Parcel + Deficiency; latest SettingsAudit.changed_at) is newer than the
run's ``snapshot_ts``, or (b) the live ``core_params_sha256`` (the same payload
schema with ``parcel_filter: null``) differs from the run's stored value.  A
filtered run is therefore not permanently stale by construction, and writes
the run itself included never false-fire.
"""
from __future__ import annotations

from django.db.models import Max

from .coverage import params_hash
from .models import (
    Deficiency,
    DistrictSettings,
    FlowTest,
    HydrantAttributeChange,
    HydrantStatusEvent,
    Inspection,
    OosAcknowledgement,
    Parcel,
    ParcelAttributeChange,
    SettingsAudit,
)
from .models import settings_dump


def _max_timestamp(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


def latest_ledger_write_at():
    """The newest mutation timestamp across the tables a run reads."""
    stamps = [
        FlowTest.objects.aggregate(m=Max("created_at"))["m"],
        HydrantStatusEvent.objects.aggregate(m=Max("changed_at"))["m"],
        HydrantAttributeChange.objects.aggregate(m=Max("changed_at"))["m"],
        ParcelAttributeChange.objects.aggregate(m=Max("changed_at"))["m"],
        Inspection.objects.aggregate(m=Max("created_at"))["m"],
        OosAcknowledgement.objects.aggregate(m=Max("acknowledged_at"))["m"],
        SettingsAudit.objects.aggregate(m=Max("changed_at"))["m"],
        Parcel.objects.aggregate(m=Max("updated_at"))["m"],
        Deficiency.objects.aggregate(m=Max("opened_at"))["m"],
        Deficiency.objects.aggregate(m=Max("resolved_at"))["m"],
    ]
    return _max_timestamp(*stamps)


def live_core_params_sha256() -> str:
    """The live core-params hash (constants + settings, parcel_filter null)."""
    district = DistrictSettings.load()
    return params_hash(settings_dump(district), None, core=True)


def coverage_staleness(run) -> dict:
    """``{stale, reasons[]}`` for a CoverageRun; never raises on missing data."""
    reasons: list[str] = []
    if run.snapshot_ts is not None:
        latest = latest_ledger_write_at()
        if latest is not None and latest > run.snapshot_ts:
            reasons.append("ledger rows changed after this run's snapshot")
    if run.core_params_sha256 and run.core_params_sha256 != live_core_params_sha256():
        reasons.append("district settings or code constants changed since this run")
    return {"stale": bool(reasons), "reasons": reasons}
