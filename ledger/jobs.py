"""Job lifecycle: leases, the expired-lease reaper, and the startup sweep.

The DB row is the queue.  The reaper keys on an **expired lease**, never
wall-clock age, so a 30-minute live job with a live heartbeat is never reaped;
a reaped job's late writes are discarded by its conditional final UPDATE
(``WHERE status='RUNNING' AND worker_id=me``) in the executor.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import constants as C
from .models import CoverageResult, CoverageRun, EvidencePack


def _clean_partial_results(run: CoverageRun) -> int:
    deleted, _ = CoverageResult.objects.filter(run=run).delete()
    return deleted


def fail_run(run: CoverageRun, reason: str) -> None:
    with transaction.atomic():
        _clean_partial_results(run)
        run.status = C.RUN_STATUS_FAILED
        run.failure_reason = reason
        run.finished_at = timezone.now()
        run.worker_id = None
        run.lease_expires_at = None
        run.save(update_fields=["status", "failure_reason", "finished_at", "worker_id",
                                "lease_expires_at"])


def fail_pack(pack: EvidencePack, reason: str) -> None:
    """Mark a pack FAILED and clean any partial FILES — the row stays visible
    in pack history (a reaped job must never silently vanish)."""
    from .media import delete_pack_files_only

    pack.status = C.PACK_STATUS_FAILED
    pack.failure_reason = reason
    pack.save(update_fields=["status", "failure_reason"])
    if pack.zip_path or pack.pdf_path:
        delete_pack_files_only(pack)


def reap_expired_leases(now=None) -> dict:
    """Any RUNNING run / RENDERING pack whose lease has expired → FAILED (orphaned)."""
    now = now or timezone.now()
    reaped_runs = 0
    for run in CoverageRun.objects.filter(status=C.RUN_STATUS_RUNNING).exclude(lease_expires_at=None):
        if run.lease_expires_at and run.lease_expires_at < now:
            fail_run(run, C.FAILURE_ORPHANED)
            reaped_runs += 1
    reaped_packs = 0
    for pack in EvidencePack.objects.filter(status=C.PACK_STATUS_RENDERING).exclude(lease_expires_at=None):
        if pack.lease_expires_at and pack.lease_expires_at < now:
            fail_pack(pack, C.FAILURE_ORPHANED)
            reaped_packs += 1
    return {"runs": reaped_runs, "packs": reaped_packs}


def recover_jobs() -> dict:
    """Startup sweep: no phantom RUNNING/RENDERING row survives a restart."""
    runs = 0
    for run in CoverageRun.objects.filter(status=C.RUN_STATUS_RUNNING):
        fail_run(run, C.FAILURE_ORPHANED)
        runs += 1
    packs = 0
    for pack in EvidencePack.objects.filter(status=C.PACK_STATUS_RENDERING):
        fail_pack(pack, C.FAILURE_ORPHANED)
        packs += 1
    return {"runs": runs, "packs": packs}


def lease_deadline(now=None):
    return (now or timezone.now()) + timedelta(seconds=C.JOB_LEASE_SECONDS)
