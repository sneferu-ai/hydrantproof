"""Coverage runs and evidence packs over the DB: the lease discipline, the
reaper, conditional completion, staleness, and the HTTP contract (202/409/poll,
404/410 downloads, FAILED-with-remediation)."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from ledger import constants as C
from ledger.executor import (
    _fail_run_if_mine,
    claim_run,
    heartbeat_run,
    run_coverage_job,
    run_pack_job,
)
from ledger.jobs import reap_expired_leases, recover_jobs
from ledger.models import CoverageResult, CoverageRun, EvidencePack, Hydrant, Parcel
from ledger.services import create_flow_test, create_status_event

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
def make_hydrant(tag="H-COV", *, lat=38.0, lon=-122.0, with_test=True, user=None):
    hydrant = Hydrant.objects.create(asset_tag=tag, lat=lat, lon=lon)
    create_status_event(hydrant=hydrant, to_status=C.STATUS_IN_SERVICE,
                        reason="Initial record", user=None, is_initial=True)
    if with_test:
        create_flow_test(
            hydrant=hydrant,
            tested_on=timezone.localdate() - timedelta(days=10),
            static_psi="80.00", residual_psi="40.00",
            outlets=[{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90",
                      "pitot_psi": "28.00"}],
        )
    return hydrant


def make_parcel(pid="P-1", *, lat=38.0, lon=-122.0, protected=True):
    return Parcel.objects.create(parcel_id=pid, lat=lat, lon=lon, is_protected_structure=protected)


def make_run(**overrides) -> CoverageRun:
    from ledger.api_jobs import _insert_run_locked

    defaults = {"parcel_filter": None, "user": None}
    defaults.update(overrides)
    return _insert_run_locked(**defaults)


# ---------------------------------------------------------------------------
# The happy path (synchronous job body)
# ---------------------------------------------------------------------------
class TestRunLifecycle:
    def test_run_completes_with_counts_and_rows(self):
        hydrant = make_hydrant()
        parcel = make_parcel()
        run = make_run()
        finished = run_coverage_job(run.pk)
        assert finished.status == C.RUN_STATUS_COMPLETE
        assert finished.snapshot_ts is not None
        assert finished.hydrant_count == 1
        assert finished.protected_parcel_count == 1
        assert finished.pair_count == 1
        assert finished.covered_count == 1
        assert finished.credited_band_counts.get("1") == 1
        row = CoverageResult.objects.get(run=run)
        assert row.hydrant_id == hydrant.pk
        assert row.parcel_id == parcel.pk
        assert row.district_credited is True
        assert float(row.district_credited_gpm) == pytest.approx(1105.2, abs=0.2)
        # The denormalized pointer followed the canonical rule.
        hydrant.refresh_from_db()
        assert hydrant.current_test_ref is not None

    def test_empty_scope_writes_null_rows(self):
        make_hydrant(lat=39.5)  # far away
        make_parcel()
        run = make_run()
        finished = run_coverage_job(run.pk)
        assert finished.status == C.RUN_STATUS_COMPLETE
        row = CoverageResult.objects.get(run=run)
        assert row.hydrant_id is None
        assert row.exclusion_code == C.EXCLUSION_NO_HYDRANT_IN_RADIUS

    def test_frozen_params_survive_a_settings_change(self):
        from ledger.models import DistrictSettings

        make_hydrant()
        make_parcel()
        run = make_run()
        assert run.params_sha256
        district = DistrictSettings.load()
        district.recency_days = 400
        district.save()
        run.refresh_from_db()
        assert run.recency_days == 365  # frozen at creation
        # …and the core hash drift is what the staleness banner reads.
        from ledger.staleness import coverage_staleness

        finished = run_coverage_job(run.pk)
        assert finished.status == C.RUN_STATUS_COMPLETE
        # The settings change (a SettingsAudit row) makes the run stale.
        assert coverage_staleness(finished)["stale"] is True

    def test_claim_is_single_owner(self):
        run = make_run()
        worker = uuid.uuid4()
        assert claim_run(run.pk, worker) is True
        assert claim_run(run.pk, uuid.uuid4()) is False
        assert heartbeat_run(run.pk, worker) is True
        assert heartbeat_run(run.pk, uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# The guard, the reaper, conditional completion
# ---------------------------------------------------------------------------
class TestFailurePaths:
    def test_pair_guard_fails_cleanly_with_zero_partial_rows(self, monkeypatch):
        monkeypatch.setattr(C, "MAX_PAIRS_PER_RUN", 1)
        make_hydrant("H-1")
        make_hydrant("H-2", lon=-121.999)
        make_parcel()
        run = make_run()
        finished = run_coverage_job(run.pk)
        assert finished.status == C.RUN_STATUS_FAILED
        assert finished.failure_reason == C.FAILURE_PAIR_COUNT_EXCEEDED
        assert CoverageResult.objects.filter(run=run).count() == 0  # nothing partial

    def test_reaper_fails_expired_lease_and_cleans_partials(self):
        run = make_run()
        CoverageResult.objects.create(
            run=run, parcel=make_parcel(), hydrant=None, distance_ft=0.0,
            district_credited=False, exclusion_code=C.EXCLUSION_NO_HYDRANT_IN_RADIUS,
        )
        run.worker_id = uuid.uuid4()
        run.lease_expires_at = timezone.now() - timedelta(seconds=5)
        run.save()
        summary = reap_expired_leases()
        assert summary["runs"] == 1
        run.refresh_from_db()
        assert run.status == C.RUN_STATUS_FAILED
        assert run.failure_reason == C.FAILURE_ORPHANED
        assert CoverageResult.objects.filter(run=run).count() == 0

    def test_reaper_never_touches_a_heartbeating_job(self):
        run = make_run()
        run.worker_id = uuid.uuid4()
        run.lease_expires_at = timezone.now() + timedelta(seconds=60)
        run.save()
        assert reap_expired_leases()["runs"] == 0
        run.refresh_from_db()
        assert run.status == C.RUN_STATUS_RUNNING

    def test_recover_jobs_sweeps_running_rows(self):
        run = make_run()
        summary = recover_jobs()
        assert summary["runs"] == 1
        run.refresh_from_db()
        assert run.status == C.RUN_STATUS_FAILED
        assert run.failure_reason == C.FAILURE_ORPHANED

    def test_reaped_jobs_late_completion_is_discarded(self):
        """Claim, get reaped mid-compute (heartbeat False), complete → discarded."""
        make_hydrant()
        make_parcel()
        run = make_run()
        worker = uuid.uuid4()
        assert claim_run(run.pk, worker) is True

        # The reaper takes the row (lease expired).
        CoverageRun.objects.filter(pk=run.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        reap_expired_leases()

        # The worker's heartbeat now fails — the job aborts before writing.
        assert heartbeat_run(run.pk, worker) is False
        # A conditional completion attempt matches zero rows.
        updated = CoverageRun.objects.filter(
            pk=run.pk, status=C.RUN_STATUS_RUNNING, worker_id=worker
        ).update(status=C.RUN_STATUS_COMPLETE)
        assert updated == 0
        run.refresh_from_db()
        assert run.status == C.RUN_STATUS_FAILED
        assert CoverageResult.objects.filter(run=run).count() == 0

    def test_conditional_failure_never_overwrites_the_reapers_verdict(self):
        run = make_run()
        worker = uuid.uuid4()
        claim_run(run.pk, worker)
        CoverageRun.objects.filter(pk=run.pk).update(
            status=C.RUN_STATUS_FAILED, failure_reason=C.FAILURE_ORPHANED
        )
        _fail_run_if_mine(run.pk, worker, "error:ValueError")
        run.refresh_from_db()
        assert run.failure_reason == C.FAILURE_ORPHANED


# ---------------------------------------------------------------------------
# The HTTP contract
# ---------------------------------------------------------------------------
class TestCoverageApi:
    def test_post_returns_202_and_poll_completes(self, api):
        make_hydrant()
        make_parcel()
        response = api.post("/api/v1/coverage-runs/", {"parcel_filter": None}, format="json")
        assert response.status_code == 202
        run_id = response.data["id"]
        # Drive the job synchronously (the pool thread sees test transactions
        # only under django_db(transaction=True); the threaded proof is its
        # own test below).
        run_coverage_job(run_id)
        poll = api.get(f"/api/v1/coverage-runs/{run_id}/")
        assert poll.status_code == 200
        assert poll.data["status"] == "COMPLETE"
        assert poll.data["staleness"]["stale"] is False
        results = api.get(f"/api/v1/coverage-runs/{run_id}/results/")
        assert results.status_code == 200
        assert results.data["count"] == 1

    def test_post_409_while_a_job_is_running(self, api):
        make_run()  # a RUNNING row exists
        response = api.post("/api/v1/coverage-runs/", {}, format="json")
        assert response.status_code == 409

    def test_post_409_when_the_advisory_lock_is_held(self, api):
        # A second session holds the lock (advisory locks are re-entrant within
        # one session, so the test cannot hold it on the request's connection).
        from django.db import connections

        other = connections.create_connection("default")
        other.ensure_connection()
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [C.JOB_ADVISORY_LOCK_KEY])
            response = api.post("/api/v1/coverage-runs/", {}, format="json")
            assert response.status_code == 409
        finally:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [C.JOB_ADVISORY_LOCK_KEY])
            other.close()

    def test_bad_filter_is_400_never_422(self, api):
        response = api.post("/api/v1/coverage-runs/", {"parcel_filter": {"bbox": [1, 2]}}, format="json")
        assert response.status_code == 400
        assert "parcel_filter" in response.data

    def test_oversized_parcel_ids_is_413(self, api):
        response = api.post(
            "/api/v1/coverage-runs/",
            {"parcel_filter": {"parcel_ids": list(range(50_001))}},
            format="json",
        )
        assert response.status_code == 413

    def test_failed_run_polls_200_with_remediation(self, api, monkeypatch):
        monkeypatch.setattr(C, "MAX_PAIRS_PER_RUN", 1)
        make_hydrant("H-1")
        make_hydrant("H-2", lon=-121.999)
        make_parcel()
        response = api.post("/api/v1/coverage-runs/", {}, format="json")
        run_coverage_job(response.data["id"])
        poll = api.get(f"/api/v1/coverage-runs/{response.data['id']}/")
        assert poll.status_code == 200
        assert poll.data["status"] == "FAILED"
        assert poll.data["failure_reason"] == "pair_count_exceeded"
        assert "parcel_filter" in poll.data["remediation"]

    def test_parcel_and_hydrant_coverage_reads(self, api):
        hydrant = make_hydrant()
        parcel = make_parcel()
        run = make_run()
        run_coverage_job(run.pk)
        response = api.get(f"/api/v1/parcels/{parcel.pk}/coverage/")
        assert response.status_code == 200
        assert response.data["results"][0]["asset_tag"] == "H-COV"
        response = api.get(f"/api/v1/hydrants/{hydrant.pk}/coverage/")
        assert response.status_code == 200
        assert response.data["results"][0]["parcel_id"] == "P-1"

    def test_coverage_404s_with_pointer_when_no_complete_run(self, api):
        parcel = make_parcel()
        response = api.get(f"/api/v1/parcels/{parcel.pk}/coverage/")
        assert response.status_code == 404
        assert response.data["hint"] == "POST /api/v1/coverage-runs/"

    def test_viewer_reads_runs_and_results(self, viewer_api):
        make_hydrant()
        make_parcel()
        run = make_run()
        run_coverage_job(run.pk)
        assert viewer_api.get("/api/v1/coverage-runs/").status_code == 200
        assert viewer_api.get(f"/api/v1/coverage-runs/{run.pk}/").status_code == 200
        assert viewer_api.get(f"/api/v1/coverage-runs/{run.pk}/results/").status_code == 200

    def test_viewer_cannot_launch_a_run(self, viewer_api):
        assert viewer_api.post("/api/v1/coverage-runs/", {}, format="json").status_code == 403


@pytest.mark.django_db(transaction=True)
class TestThreadedEndToEnd:
    def test_post_drives_the_job_on_the_pool(self, api):
        """The real 202 → background-thread → poll-COMPLETE loop."""
        make_hydrant()
        make_parcel()
        response = api.post("/api/v1/coverage-runs/", {}, format="json")
        assert response.status_code == 202
        run_id = response.data["id"]
        for _ in range(200):
            poll = api.get(f"/api/v1/coverage-runs/{run_id}/")
            if poll.data["status"] in (C.RUN_STATUS_COMPLETE, C.RUN_STATUS_FAILED):
                break
            import time

            time.sleep(0.05)
        assert poll.data["status"] == C.RUN_STATUS_COMPLETE
        assert poll.data["pair_count"] == 1
