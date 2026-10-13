"""Scale envelope (specification §15) — timing-gated, excluded from the gate
command via the ``scale`` marker.

Envelope enforcement is a precondition, not a courtesy: with
``HYDRANTPROOF_SCALE_ENFORCE=1`` a missing 2 CPU / 2 GB cgroup envelope is a
test FAILURE; without the flag (developer machines) the group prints
``SKIP: envelope absent — timings inconclusive``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from django.utils import timezone
from datetime import timedelta

from ledger import constants as C

pytestmark = [pytest.mark.django_db, pytest.mark.scale]

HYDRANTS = 10_000
PARCELS = 50_000
EXPECTED_PAIR_RANGE = (150_000, 250_000)
RUN_TARGET_S = 30.0
RENDER_TARGET_S = 30.0
LIST_TARGET_S = 0.300
DOWNLOAD_TARGET_S = 1.0

# The pinned synthetic geometry: a 20 × 15-mile box, uniform placement.
BOX_WIDTH_FT = 20 * 5280.0
BOX_HEIGHT_FT = 15 * 5280.0
BASE_LAT = 38.0
BASE_LON = -122.0


def read_envelope() -> dict | None:
    """CPU/memory limits from cgroup v2 or v1; None when unconstrained."""
    cpu_cores = None
    memory_bytes = None
    v2_cpu = Path("/sys/fs/cgroup/cpu.max")
    v2_mem = Path("/sys/fs/cgroup/memory.max")
    if v2_cpu.exists() and v2_mem.exists():
        raw_cpu = v2_cpu.read_text().split()
        if raw_cpu and raw_cpu[0] != "max":
            cpu_cores = int(raw_cpu[0]) / int(raw_cpu[1])
        raw_mem = v2_mem.read_text().strip()
        if raw_mem != "max":
            memory_bytes = int(raw_mem)
    else:
        v1_quota = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        v1_period = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        v1_mem = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if v1_quota.exists() and v1_period.exists():
            quota, period = int(v1_quota.read_text()), int(v1_period.read_text())
            if quota > 0:
                cpu_cores = quota / period
        if v1_mem.exists():
            value = int(v1_mem.read_text())
            if 0 < value < 1 << 60:
                memory_bytes = value
    if cpu_cores is None and memory_bytes is None:
        return None
    return {"cpu_cores": cpu_cores, "memory_bytes": memory_bytes}


@pytest.fixture(scope="module")
def envelope():
    env = read_envelope()
    enforce = os.environ.get("HYDRANTPROOF_SCALE_ENFORCE") == "1"
    if env is None:
        if enforce:
            pytest.fail("HYDRANTPROOF_SCALE_ENFORCE=1 but no cgroup envelope is present")
        pytest.skip("SKIP: envelope absent — timings inconclusive")
    return env


def _bulk_seed(django_db_blocker):
    """10,000 hydrants + 50,000 protected parcels, uniformly in the box."""
    from ledger.hydraulics import compute_flow_test
    from ledger.hydraulics import OutletReadingInput
    from ledger.models import FlowTest, Hydrant, Parcel

    from ledger.geo import degrees_for_feet

    with django_db_blocker.unblock():
        if Hydrant.objects.count() >= HYDRANTS and Parcel.objects.count() >= PARCELS:
            return
        computation = compute_flow_test(
            static_psi=80.0, residual_psi=40.0,
            outlets=[OutletReadingInput(diameter_in=2.5, coefficient=0.90, pitot_psi=28.0)],
        )
        today = timezone.localdate() - timedelta(days=30)
        hydrants = []
        for i in range(HYDRANTS):
            # Uniform grid placement over the 20×15-mile box.
            col = i % 100
            row = i // 100
            hydrants.append(Hydrant(
                asset_tag=f"SC-{i:05d}",
                lat=BASE_LAT + degrees_for_feet(BOX_HEIGHT_FT) * (row / 100.0),
                lon=BASE_LON + degrees_for_feet(BOX_WIDTH_FT) * (col / 100.0),
                status=C.STATUS_IN_SERVICE,
            ))
            if len(hydrants) >= 5000:
                Hydrant.objects.bulk_create(hydrants)
                hydrants = []
        if hydrants:
            Hydrant.objects.bulk_create(hydrants)

        tests = []
        for hydrant in Hydrant.objects.order_by("id").iterator(chunk_size=2000):
            tests.append(FlowTest(
                hydrant_id=hydrant.pk,
                tested_on=today,
                static_psi="80.00", residual_psi="40.00",
                computed_total_gpm=str(computation.computed_total_gpm),
                rated_gpm_20=str(computation.rated_gpm_20),
                capacity_class=computation.capacity_class,
                warnings=[],
                formula_version=C.CURRENT_FORMULA_VERSION,
            ))
            if len(tests) >= 5000:
                FlowTest.objects.bulk_create(tests)
                tests = []
        if tests:
            FlowTest.objects.bulk_create(tests)

        parcels = []
        for i in range(PARCELS):
            col = i % 250
            row = i // 250
            parcels.append(Parcel(
                parcel_id=f"SCP-{i:05d}",
                lat=BASE_LAT + degrees_for_feet(BOX_HEIGHT_FT) * ((row + 0.5) / 200.0),
                lon=BASE_LON + degrees_for_feet(BOX_WIDTH_FT) * ((col + 0.5) / 250.0),
                is_protected_structure=True,
            ))
            if len(parcels) >= 10_000:
                Parcel.objects.bulk_create(parcels)
                parcels = []
        if parcels:
            Parcel.objects.bulk_create(parcels)


@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker, envelope):
    _bulk_seed(django_db_blocker)
    yield


class TestScaleEnvelope:
    def test_full_coverage_run_within_target(self, seeded, envelope):
        from ledger.api_jobs import _insert_run_locked
        from ledger.executor import run_coverage_job

        run = _insert_run_locked(parcel_filter=None, user=None)
        started = time.monotonic()
        finished = run_coverage_job(run.pk)
        elapsed = time.monotonic() - started
        assert finished.status == C.RUN_STATUS_COMPLETE, finished.failure_reason
        lo, hi = EXPECTED_PAIR_RANGE
        assert lo <= finished.pair_count <= hi, (
            f"pair_count {finished.pair_count} outside the pinned {lo}–{hi} band"
        )
        assert elapsed < RUN_TARGET_S, f"coverage run took {elapsed:.1f}s (target {RUN_TARGET_S}s)"

    def test_pack_render_within_target(self, seeded, envelope):
        from ledger.api_jobs import _insert_run_locked
        from ledger.executor import run_coverage_job, run_pack_job
        from ledger.models import EvidencePack

        run = _insert_run_locked(parcel_filter=None, user=None)
        run_coverage_job(run.pk)
        run.refresh_from_db()
        pack = EvidencePack.objects.create(run=run, status=C.PACK_STATUS_RENDERING)
        started = time.monotonic()
        run_pack_job(pack.pk)
        elapsed = time.monotonic() - started
        pack.refresh_from_db()
        assert pack.status == C.PACK_STATUS_COMPLETE, pack.failure_reason
        assert elapsed < RENDER_TARGET_S, f"pack render took {elapsed:.1f}s (target {RENDER_TARGET_S}s)"

        # Stored-pack download < 1 s (bytes served from the media volume).
        from django.test import Client
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user(username="scale-reader", password="hydrantproof-scale-pass")
        user.groups.add(Group.objects.get_or_create(name="viewer")[0])
        client = Client()
        client.force_login(user)
        started = time.monotonic()
        response = client.get(f"/api/v1/evidence-packs/{pack.pk}/zip/")
        body = b"".join(response.streaming_content)
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        import hashlib

        assert hashlib.sha256(body).hexdigest() == pack.csv_zip_sha256
        assert elapsed < DOWNLOAD_TARGET_S, f"download took {elapsed:.2f}s"

    def test_hydrant_list_query_within_target(self, seeded, envelope):
        from django.test import Client
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user(username="scale-list-reader", password="hydrantproof-scale-pass")
        user.groups.add(Group.objects.get_or_create(name="viewer")[0])
        client = Client()
        client.force_login(user)
        started = time.monotonic()
        response = client.get("/api/v1/hydrants/")
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert response.json()["count"] == HYDRANTS
        assert elapsed < LIST_TARGET_S, (
            f"10,000-row hydrant list took {elapsed:.3f}s (target {LIST_TARGET_S}s)"
        )


@pytest.mark.scale
class TestDenseClusterGuard:
    """The boundary path, cheaply: a dense cluster trips the pair guard and the
    run fails async with the zonal remediation (never a 422)."""

    def test_dense_cluster_fails_with_hint(self, monkeypatch):
        from ledger.api_jobs import _insert_run_locked
        from ledger.executor import run_coverage_job
        from ledger.models import Hydrant, Parcel

        # 2,000 hydrants on top of a parcel cluster; the guard (patched down
        # for a cheap test) trips inside the write loop.
        monkeypatch.setattr(C, "MAX_PAIRS_PER_RUN", 10_000)
        hydrants = [
            Hydrant(asset_tag=f"DENSE-{i:04d}", lat=38.0, lon=-122.0, status=C.STATUS_IN_SERVICE)
            for i in range(2000)
        ]
        Hydrant.objects.bulk_create(hydrants)
        Parcel.objects.bulk_create([
            Parcel(parcel_id=f"DP-{i:03d}", lat=38.0, lon=-122.0, is_protected_structure=True)
            for i in range(20)
        ])
        run = _insert_run_locked(parcel_filter=None, user=None)
        finished = run_coverage_job(run.pk)
        assert finished.status == C.RUN_STATUS_FAILED
        assert finished.failure_reason == C.FAILURE_PAIR_COUNT_EXCEEDED
        from ledger.models import CoverageResult

        assert CoverageResult.objects.filter(run=run).count() == 0
