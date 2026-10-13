"""Coverage job input loading — the REPEATABLE READ snapshot (§8).

The worker reads its inputs in ONE ``REPEATABLE READ`` transaction whose start
becomes the run's ``snapshot_ts`` (the staleness anchor: writes landing in the
``(started_at, snapshot_ts)`` window are included in the run and do not
false-fire the banner).  Derivation parameters come from the run's **frozen**
row, never live settings.
"""
from __future__ import annotations

from django.db import connection, transaction
from django.utils import timezone

from . import constants as C
from .coverage import CoverageHydrant, CoverageParcel, iter_coverage
from .derive import DerivationParams
from .models import Hydrant, Parcel
from .services import load_hydrant_views


def params_for_run(run) -> DerivationParams:
    """Rebuild the frozen derivation parameters from the run row.

    ``today`` is the run's snapshot date (falling back to its creation date),
    so a pack rendered days later re-derives the run's world, not today's.
    """
    echo = run.settings_echo or {}
    anchor = run.snapshot_ts or run.created_at
    snapshot_day = timezone.localtime(anchor).date() if anchor else timezone.localdate()
    return DerivationParams(
        today=snapshot_day,
        recency_days=int(run.recency_days),
        due_soon_days=int(echo.get("due_soon_days", C.DEFAULT_DUE_SOON_DAYS)),
        inspection_recency_days=int(run.inspection_recency_days),
        inspection_due_soon_days=int(
            echo.get("inspection_due_soon_days", C.DEFAULT_INSPECTION_DUE_SOON_DAYS)
        ),
        exclude_residual_below_target=bool(run.exclude_residual_below_target),
    )


def _load_inputs(run):
    """One transaction's worth of reads; returns everything the engine needs."""
    hydrants = list(Hydrant.objects.all().only("pk", "lat", "lon", "status"))
    views = load_hydrant_views(hydrants)
    parcels_qs = Parcel.objects.filter(is_protected_structure=True)
    parcel_filter = run.parcel_filter
    if parcel_filter and "parcel_ids" in parcel_filter:
        parcels_qs = parcels_qs.filter(pk__in=parcel_filter["parcel_ids"])
    elif parcel_filter and "bbox" in parcel_filter:
        minlon, minlat, maxlon, maxlat = parcel_filter["bbox"]
        # The point used decides inclusion: a structure override inside the
        # bbox counts, a bare centroid counts; either coordinate pair inside
        # the box keeps the parcel (generous filter, exact geometry later).
        from django.db.models import Q

        parcels_qs = parcels_qs.filter(
            (Q(structure_lon__gte=minlon, structure_lon__lte=maxlon,
               structure_lat__gte=minlat, structure_lat__lte=maxlat))
            | (Q(lon__gte=minlon, lon__lte=maxlon, lat__gte=minlat, lat__lte=maxlat))
        )
    parcels = list(parcels_qs.only("pk", "lat", "lon", "structure_lat", "structure_lon"))
    return hydrants, views, parcels


def compute_run_inputs(run) -> tuple:
    """Snapshot the inputs under REPEATABLE READ; return the engine iterator.

    Returns ``(snapshot_ts, inputs)`` where ``inputs`` carries the streaming
    engine generator and the run-level facts the completion write persists.

    In production the job runs on the executor's own thread/connection, so the
    ``SET TRANSACTION`` is the first statement of a top-level transaction and
    the snapshot is truly REPEATABLE READ.  A nested transaction (the test
    harness wraps tests in one) cannot change isolation mid-transaction —
    Postgres rejects it — so a nested caller inherits the outer level, which
    is safe for the harness because no concurrent writer exists there.  The
    check is structural (never an exception round-trip: a failed statement
    would abort the transaction under Postgres).
    """
    top_level = not connection.in_atomic_block
    with transaction.atomic():
        if top_level:
            with connection.cursor() as cursor:
                # First statement of the transaction: pin the isolation level
                # so no write landing mid-read can tear the snapshot.
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        snapshot_ts = timezone.now()
        hydrants, views, parcels = _load_inputs(run)

    params = params_for_run(run)
    engine_hydrants = [
        CoverageHydrant(pk=h.pk, lat=float(h.lat), lon=float(h.lon), view=views[h.pk])
        for h in hydrants
    ]
    engine_parcels = [
        CoverageParcel(pk=p.pk, lat=float(p.point[0]), lon=float(p.point[1])) for p in parcels
    ]
    engine = iter_coverage(
        engine_hydrants,
        engine_parcels,
        params=params,
        credit_radius_ft=float(run.credit_radius_ft),
    )
    inputs = {
        "engine": engine,
        "hydrant_count": len(engine_hydrants),
        "protected_parcel_count": len(engine_parcels),
        "formula_versions_present": sorted({
            view.current_test.formula_version
            for view in views.values()
            if view.current_test is not None
        }),
    }
    return snapshot_ts, inputs
