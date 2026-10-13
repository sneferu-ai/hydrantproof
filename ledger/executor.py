"""The DB-leased job executor (specification §8 — "the row is the queue").

* One lazily constructed ``ThreadPoolExecutor(max_workers=1)`` per process —
  built on first job, never at module import, so gunicorn running **without
  ``--preload``** never forks a live pool.  Single-flight rests on the advisory
  lock + ``RUNNING``/``RENDERING`` check at POST time, not on the pool.
* A worker **claims** its row (``UPDATE … SET worker_id, lease_expires_at …
  WHERE id=… AND status='RUNNING' AND worker_id IS NULL``), reads inputs in one
  ``REPEATABLE READ`` transaction (recording ``snapshot_ts`` = that
  transaction's start), closes it, then computes.
* **Heartbeat every 60 s** (``UPDATE lease_expires_at … WHERE id=… AND
  worker_id=me``; zero rows → reaped → abort and discard).
* Results land in a second transaction (``bulk_create`` batches of 10,000);
  **completion is conditional** — the final ``UPDATE … SET status='COMPLETE'
  WHERE id=… AND status='RUNNING' AND worker_id=me`` matching zero rows means
  the job was reaped and the whole write transaction rolls back, so a reaped
  run can never resurrect as COMPLETE.
* The pair guard trips mid-write: the write transaction rolls back (no partial
  rows survive — the spec's "deleted in the same transaction" outcome) and the
  row is marked ``FAILED``/``pair_count_exceeded`` **conditionally** (a reaped
  row keeps the reaper's verdict).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.db import connections, transaction
from django.utils import timezone

from . import constants as C
from .coverage import JobReapedError, PairGuardExceeded, RunTallies
from .jobs import lease_deadline
from .models import CoverageResult, CoverageRun, EvidencePack

logger = logging.getLogger("ledger.jobs")

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

WRITE_BATCH = 10_000


def get_executor() -> ThreadPoolExecutor:
    """Lazily constructed inside the serving process on first job."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="hydrantproof-job"
            )
        return _executor


def submit_coverage_run(run_id: int) -> None:
    """Dispatch a RUNNING run to the pool; a submit failure fails the row loudly."""
    try:
        get_executor().submit(_coverage_job_entry, run_id)
    except Exception:
        logger.exception("coverage run %s could not be submitted", run_id)
        CoverageRun.objects.filter(pk=run_id, status=C.RUN_STATUS_RUNNING).update(
            status=C.RUN_STATUS_FAILED,
            failure_reason="executor_unavailable",
            finished_at=timezone.now(),
        )


def submit_pack_render(pack_id: int) -> None:
    try:
        get_executor().submit(_pack_job_entry, pack_id)
    except Exception:
        logger.exception("evidence pack %s could not be submitted", pack_id)
        EvidencePack.objects.filter(pk=pack_id, status=C.PACK_STATUS_RENDERING).update(
            status=C.PACK_STATUS_FAILED,
            failure_reason="executor_unavailable",
        )


# ---------------------------------------------------------------------------
# Lease primitives (shared by both job kinds)
# ---------------------------------------------------------------------------
def claim_run(run_id: int, worker_id: uuid.UUID) -> bool:
    now = timezone.now()
    updated = CoverageRun.objects.filter(
        pk=run_id, status=C.RUN_STATUS_RUNNING, worker_id__isnull=True
    ).update(
        worker_id=worker_id,
        lease_expires_at=lease_deadline(now),
        last_heartbeat=now,
        started_at=now,
    )
    return updated == 1


def heartbeat_run(run_id: int, worker_id: uuid.UUID) -> bool:
    """True while the lease is still ours; False means reaped — abort."""
    updated = CoverageRun.objects.filter(
        pk=run_id, status=C.RUN_STATUS_RUNNING, worker_id=worker_id
    ).update(lease_expires_at=lease_deadline(), last_heartbeat=timezone.now())
    return updated == 1


def claim_pack(pack_id: int, worker_id: uuid.UUID) -> bool:
    now = timezone.now()
    updated = EvidencePack.objects.filter(
        pk=pack_id, status=C.PACK_STATUS_RENDERING, worker_id__isnull=True
    ).update(worker_id=worker_id, lease_expires_at=lease_deadline(now), last_heartbeat=now)
    return updated == 1


def heartbeat_pack(pack_id: int, worker_id: uuid.UUID) -> bool:
    updated = EvidencePack.objects.filter(
        pk=pack_id, status=C.PACK_STATUS_RENDERING, worker_id=worker_id
    ).update(lease_expires_at=lease_deadline(), last_heartbeat=timezone.now())
    return updated == 1


def _fail_run_if_mine(run_id: int, worker_id: uuid.UUID, reason: str) -> None:
    """The conditional failure mark — never overwrites the reaper's verdict."""
    from .jobs import _clean_partial_results

    updated = CoverageRun.objects.filter(
        pk=run_id, status=C.RUN_STATUS_RUNNING, worker_id=worker_id
    ).update(
        status=C.RUN_STATUS_FAILED,
        failure_reason=reason[:64],
        finished_at=timezone.now(),
        worker_id=None,
        lease_expires_at=None,
    )
    if updated == 1:
        run = CoverageRun.objects.filter(pk=run_id).first()
        if run is not None:
            _clean_partial_results(run)


def _fail_pack_if_mine(pack_id: int, worker_id: uuid.UUID, reason: str) -> None:
    updated = EvidencePack.objects.filter(
        pk=pack_id, status=C.PACK_STATUS_RENDERING, worker_id=worker_id
    ).update(status=C.PACK_STATUS_FAILED, failure_reason=reason[:64],
             worker_id=None, lease_expires_at=None)
    if updated == 1:
        pack = EvidencePack.objects.filter(pk=pack_id).first()
        if pack is not None and (pack.zip_path or pack.pdf_path):
            from .media import delete_pack_files_only

            delete_pack_files_only(pack)


class _Heartbeat:
    """Time-throttled lease heartbeat usable as the engine's callback."""

    def __init__(self, beat):
        self._beat = beat
        self._last = time.monotonic()

    def __call__(self, *_args) -> bool:
        now = time.monotonic()
        if now - self._last < C.JOB_HEARTBEAT_SECONDS:
            return True
        self._last = now
        return self._beat()


# ---------------------------------------------------------------------------
# The coverage job
# ---------------------------------------------------------------------------
def _coverage_job_entry(run_id: int) -> None:
    try:
        run_coverage_job(run_id)
    finally:
        connections.close_all()


def run_coverage_job(run_id: int, worker_id: uuid.UUID | None = None) -> CoverageRun | None:
    """Claim → snapshot (REPEATABLE READ) → compute → conditional complete.

    Synchronously callable (tests); the executor submits it on the pool.
    """
    from .coverage_runner import compute_run_inputs

    worker_id = worker_id or uuid.uuid4()
    if not claim_run(run_id, worker_id):
        return None  # already claimed or reaped
    run = CoverageRun.objects.get(pk=run_id)
    try:
        snapshot_ts, inputs = compute_run_inputs(run)
        beat = _Heartbeat(lambda: heartbeat_run(run_id, worker_id))
        tallies = RunTallies()
        with transaction.atomic():
            batch: list[CoverageResult] = []
            for pair_rows, summary, _sub in inputs["engine"]:
                for row in pair_rows:
                    batch.append(CoverageResult(
                        run_id=run_id,
                        parcel_id=row.parcel_pk,
                        hydrant_id=row.hydrant_pk,
                        distance_ft=row.distance_ft,
                        district_credited=row.credited,
                        district_credited_gpm=row.credited_gpm,
                        exclusion_code=row.exclusion_code,
                        expired=row.expired,
                        is_sample_data=run.is_sample_data,
                    ))
                tallies.absorb(pair_rows, summary, _sub)
                if tallies.rows > C.MAX_PAIRS_PER_RUN:
                    raise PairGuardExceeded(
                        f"run would write more than {C.MAX_PAIRS_PER_RUN:,} result rows; "
                        f"narrow it with parcel_filter (parcel_ids ≤ {C.MAX_PARCEL_IDS:,} or a bbox)"
                    )
                if len(batch) >= WRITE_BATCH:
                    CoverageResult.objects.bulk_create(batch)
                    batch.clear()
                    if not beat():
                        raise JobReapedError()
            if batch:
                CoverageResult.objects.bulk_create(batch)
            # Conditional completion: zero rows updated ⇒ reaped mid-write ⇒
            # the exception rolls the whole write transaction back.
            updated = CoverageRun.objects.filter(
                pk=run_id, status=C.RUN_STATUS_RUNNING, worker_id=worker_id
            ).update(
                status=C.RUN_STATUS_COMPLETE,
                snapshot_ts=snapshot_ts,
                finished_at=timezone.now(),
                lease_expires_at=None,
                hydrant_count=inputs["hydrant_count"],
                protected_parcel_count=inputs["protected_parcel_count"],
                pair_count=tallies.rows,
                in_radius_count=tallies.in_radius_pairs,
                covered_count=tallies.covered,
                credited_band_counts=tallies.band_counts,
                formula_versions_present=list(inputs["formula_versions_present"]),
            )
            if updated == 0:
                raise JobReapedError()
    except PairGuardExceeded:
        logger.warning("coverage run %s failed: pair guard exceeded", run_id)
        _fail_run_if_mine(run_id, worker_id, C.FAILURE_PAIR_COUNT_EXCEEDED)
    except JobReapedError:
        logger.info("coverage run %s was reaped mid-flight; writes discarded", run_id)
    except Exception as exc:  # a failed job is evidence, never a wedge
        logger.exception("coverage run %s failed unexpectedly", run_id)
        _fail_run_if_mine(run_id, worker_id, f"error:{type(exc).__name__}")
    return CoverageRun.objects.filter(pk=run_id).first()


# ---------------------------------------------------------------------------
# The pack job (render lives in pack_render.py)
# ---------------------------------------------------------------------------
def _pack_job_entry(pack_id: int) -> None:
    try:
        run_pack_job(pack_id)
    finally:
        connections.close_all()


def run_pack_job(pack_id: int, worker_id: uuid.UUID | None = None) -> EvidencePack | None:
    """Claim → (fresh run if the pack created one) → render → conditional complete.

    ``render_pack`` computes every byte and row field but touches no row; the
    single conditional UPDATE then installs status + fields atomically, so a
    reaped pack never keeps this render's outputs.
    """
    from .pack_render import MediaBudgetExceeded, render_pack

    worker_id = worker_id or uuid.uuid4()
    if not claim_pack(pack_id, worker_id):
        return None
    pack = EvidencePack.objects.get(pk=pack_id)
    try:
        # A pack submitted without run_id owns a fresh RUNNING run: execute it
        # first under the same lease discipline.
        run = pack.run
        if run.status == C.RUN_STATUS_RUNNING:
            run = run_coverage_job(run.pk)
            if run is None or run.status != C.RUN_STATUS_COMPLETE:
                _fail_pack_if_mine(pack_id, worker_id, run.failure_reason if run else "run_failed")
                return EvidencePack.objects.filter(pk=pack_id).first()
        beat = _Heartbeat(lambda: heartbeat_pack(pack_id, worker_id))
        fields = render_pack(pack, heartbeat=beat)
        updated = EvidencePack.objects.filter(
            pk=pack_id, status=C.PACK_STATUS_RENDERING, worker_id=worker_id
        ).update(status=C.PACK_STATUS_COMPLETE, lease_expires_at=None, **fields)
        if updated == 0:
            raise JobReapedError()
    except MediaBudgetExceeded:
        # The pre-render check fired before a single byte was written.
        _fail_pack_if_mine(pack_id, worker_id, C.FAILURE_MEDIA_BUDGET_EXCEEDED)
    except JobReapedError:
        logger.info("evidence pack %s was reaped mid-render; outputs discarded", pack_id)
        # Fields never landed (the conditional update owns them); sweep the
        # pack's own directory so no unreferenced bytes survive.  Nothing here
        # may raise — this is the cleanup path.
        try:
            from .media import media_root

            stray_dir = media_root() / "packs" / str(pack_id)
            if stray_dir.exists():
                import shutil

                shutil.rmtree(stray_dir, ignore_errors=True)
        except Exception:  # pragma: no cover - defensive
            logger.exception("reaped pack %s left stray files", pack_id)
    except Exception as exc:
        logger.exception("evidence pack %s failed unexpectedly", pack_id)
        _fail_pack_if_mine(pack_id, worker_id, f"error:{type(exc).__name__}")
    return EvidencePack.objects.filter(pk=pack_id).first()
