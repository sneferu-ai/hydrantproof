"""The coverage engine's pure core — no Django imports (specification §8).

:func:`iter_coverage` turns (hydrants, protected parcels, frozen parameters)
into the worksheet's pair rows and per-parcel summaries, streaming one parcel
at a time so the job layer can ``bulk_create`` in batches of 10,000 and check
the pair guard incrementally:

* distances are haversine on **unrounded** feet (``ledger.geo``); inclusion is
  ``distance_ft ≤ credit_radius_ft`` on the unrounded value;
* the prefilter is the spec's provably conservative bisect: hydrants sorted by
  latitude, bisect the ±radius latitude band, longitude half-window
  ``radius_deg / cos(max |lat| over the band)`` — a guaranteed lower bound, so
  no true in-radius hydrant is ever excluded — exact haversine on survivors;
* the district-credit rule is the canonical derivation (``ledger.derive``)
  evaluated under the run's **frozen** parameters: a hydrant contributes its
  stored ``rated_gpm_20`` iff district-credit-eligible; otherwise
  ``credited = 0`` with the first applicable exclusion code and OOS rows carry
  ``expired``;
* every protected parcel with zero in-radius hydrants gets exactly one
  ``NO_HYDRANT_IN_RADIUS`` null-hydrant row; a parcel with in-radius but zero
  credited hydrants is represented by its excluded pair rows — never
  double-represented;
* :class:`PairGuardExceeded` is raised on the parcel that would push written
  rows past ``max_pairs`` — the job turns it into ``FAILED``
  (``pair_count_exceeded``) with the ``parcel_filter`` remediation, never a 422.
"""
from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field

from . import constants as C
from .derive import DerivationParams, HydrantView, derive_hydrant
from .geo import haversine_ft, latitude_window_deg, longitude_half_window_deg


class PairGuardExceeded(Exception):
    """The run would write more than ``max_pairs`` result rows."""


class JobReapedError(Exception):
    """The heartbeat found the row reaped; abort and discard everything."""


# ---------------------------------------------------------------------------
# Frozen-parameter identity (params_sha256 / core_params_sha256)
# ---------------------------------------------------------------------------
def constants_snapshot() -> dict:
    """The code constants that can change coverage math or labels."""
    return {
        "DISCHARGE_CONSTANT": C.DISCHARGE_CONSTANT,
        "TARGET_RESIDUAL_PSI": C.TARGET_RESIDUAL_PSI,
        "DROP_EXPONENT": C.DROP_EXPONENT,
        "MAX_EXTRAPOLATION_FACTOR": C.MAX_EXTRAPOLATION_FACTOR,
        "CURRENT_FORMULA_VERSION": C.CURRENT_FORMULA_VERSION,
        "COEFFICIENT_PRESETS": dict(C.COEFFICIENT_PRESETS),
        "DISTRIBUTION_BANDS": [list(band) for band in C.DISTRIBUTION_BANDS],
        "FLOW_BAND_PROXY": [list(band) for band in C.FLOW_BAND_PROXY],
        "TREND_DECLINE_PCT": C.TREND_DECLINE_PCT,
        "MAX_PAIRS_PER_RUN": C.MAX_PAIRS_PER_RUN,
    }


def canonical_params_payload(settings_echo: dict, parcel_filter, *, core: bool = False) -> dict:
    """``{constants, settings, parcel_filter}`` — the spec's hash payload."""
    return {
        "constants": constants_snapshot(),
        "settings": settings_echo,
        "parcel_filter": None if core else parcel_filter,
    }


def params_hash(settings_echo: dict, parcel_filter, *, core: bool = False) -> str:
    payload = canonical_params_payload(settings_echo, parcel_filter, core=core)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_parcel_filter(raw) -> dict | None:
    """Validate the POST body's ``parcel_filter`` (specification §8 schema).

    Returns the normalized filter (``None``, ``{"parcel_ids": [...]}`` or
    ``{"bbox": [minlon, minlat, maxlon, maxlat]}``).  Raises
    :class:`ValueError` with a caller-facing message on a malformed filter and
    :class:`ParcelIdsExceeded` past the 50,000-id cap (413 at the route).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("parcel_filter must be null or an object")
    keys = set(raw)
    if keys == {"parcel_ids"}:
        ids = raw["parcel_ids"]
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ValueError("parcel_filter.parcel_ids must be a list of integer pks")
        if len(ids) > C.MAX_PARCEL_IDS:
            raise ParcelIdsExceeded(f"parcel_ids may name at most {C.MAX_PARCEL_IDS:,} parcels")
        # sorted + deduplicated: the same filter always hashes identically
        return {"parcel_ids": sorted(set(ids))}
    if keys == {"bbox"}:
        bbox = raw["bbox"]
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("parcel_filter.bbox must be [minlon, minlat, maxlon, maxlat]")
        try:
            minlon, minlat, maxlon, maxlat = (float(v) for v in bbox)
        except (TypeError, ValueError):
            raise ValueError("parcel_filter.bbox values must be numbers") from None
        if not (minlon < maxlon and minlat < maxlat):
            raise ValueError("parcel_filter.bbox requires minlon < maxlon and minlat < maxlat")
        if not (-180.0 <= minlon <= 180.0 and -180.0 <= maxlon <= 180.0):
            raise ValueError("parcel_filter.bbox longitudes must be within ±180")
        if not (-80.0 <= minlat <= 80.0 and -80.0 <= maxlat <= 80.0):
            raise ValueError("parcel_filter.bbox latitudes must be within ±80")
        return {"bbox": [minlon, minlat, maxlon, maxlat]}
    raise ValueError("parcel_filter must have exactly one of parcel_ids or bbox")


class ParcelIdsExceeded(Exception):
    """``parcel_ids`` past the 50,000 cap — answered 413 at the route."""


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoverageHydrant:
    pk: int
    lat: float
    lon: float
    view: HydrantView


@dataclass(frozen=True)
class CoverageParcel:
    pk: int
    lat: float  # the point used: structure override when present, else centroid
    lon: float


@dataclass(frozen=True)
class PairRow:
    """One worksheet row (a parcel–hydrant in-radius pair, or the null row)."""

    parcel_pk: int
    hydrant_pk: int | None
    distance_ft: float          # unrounded; meaningless (0.0) on null rows
    credited: bool
    credited_gpm: float | None
    exclusion_code: str | None
    expired: bool


@dataclass(frozen=True)
class ParcelSummary:
    parcel_pk: int
    in_radius_count: int
    credited_count: int
    credited_gpm_sum: float
    distribution_band: str          # headline: "3+" / "2" / "1" / "0"
    flow_band_proxy: str            # labeled proxy: "1000+" / "500-999" / "1-499" / "NONE"
    nearest_within_2x_ft: float | None
    exclusion_code_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CoverageComputation:
    """The collected result (tests and the pack renderer's per-parcel join)."""

    rows: tuple[PairRow, ...]
    parcels: tuple[ParcelSummary, ...]
    hydrant_count: int
    protected_parcel_count: int
    pair_count: int                 # every result row (pairs + null rows)
    in_radius_count: int            # rows with a hydrant (true pairs)
    covered_count: int              # parcels with ≥ 1 credited hydrant
    credited_band_counts: dict
    formula_versions_present: tuple[str, ...]
    sub_target_residual_included_count: int
    flow_band_counts: dict = field(default_factory=dict)


def distribution_band(credited_count: int) -> str:
    for label, threshold in C.DISTRIBUTION_BANDS:
        if credited_count >= threshold:
            return label
    return "0"  # pragma: no cover - the ("0", 0) band always matches


def flow_band_proxy(credited_gpm_sum: float) -> str:
    for label, threshold in C.FLOW_BAND_PROXY:
        if credited_gpm_sum >= threshold:
            return label
    return "NONE"  # pragma: no cover - the ("NONE", 0.0) band always matches


class RunTallies:
    """Accumulates the run-level counters while parcels stream by."""

    def __init__(self) -> None:
        self.rows = 0
        self.in_radius_pairs = 0
        self.covered = 0
        self.band_counts = {label: 0 for label, _ in C.DISTRIBUTION_BANDS}
        self.flow_counts = {label: 0 for label, _ in C.FLOW_BAND_PROXY}
        self.sub_target_included = 0

    def absorb(self, rows: list[PairRow], summary: ParcelSummary, sub_target: int) -> None:
        self.rows += len(rows)
        self.in_radius_pairs += sum(1 for row in rows if row.hydrant_pk is not None)
        self.covered += 1 if summary.credited_count >= 1 else 0
        self.band_counts[summary.distribution_band] += 1
        self.flow_counts[summary.flow_band_proxy] += 1
        self.sub_target_included += sub_target


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
def iter_coverage(
    hydrants: list[CoverageHydrant],
    parcels: list[CoverageParcel],
    *,
    params: DerivationParams,
    credit_radius_ft: float,
    heartbeat=None,
):
    """Yield ``(pair_rows, parcel_summary, sub_target_included)`` per parcel.

    ``heartbeat`` (optional) is called with the count of parcels processed so
    far every 512 parcels; returning ``False`` raises :class:`JobReapedError`.
    """
    derivations = {h.pk: derive_hydrant(h.view, params) for h in hydrants}
    ordered = sorted(hydrants, key=lambda h: (h.lat, h.lon, h.pk))
    lats = [h.lat for h in ordered]

    lat_half = latitude_window_deg(credit_radius_ft)
    lat_half_2x = latitude_window_deg(credit_radius_ft * 2.0)

    for index, parcel in enumerate(parcels):
        if heartbeat is not None and index and index % 512 == 0:
            if heartbeat(index) is False:
                raise JobReapedError()

        in_radius = _in_radius(ordered, lats, parcel.lat, parcel.lon, credit_radius_ft, lat_half)
        pair_rows: list[PairRow] = []
        if not in_radius:
            pair_rows.append(PairRow(
                parcel_pk=parcel.pk, hydrant_pk=None, distance_ft=0.0,
                credited=False, credited_gpm=None,
                exclusion_code=C.EXCLUSION_NO_HYDRANT_IN_RADIUS, expired=False,
            ))
        exclusion_tally: dict[str, int] = {}
        credited_count = 0
        credited_sum = 0.0
        sub_target = 0
        for hydrant, distance in in_radius:
            derived = derivations[hydrant.pk]
            credited = derived.district_credit_eligible
            credited_gpm = derived.rated_gpm_20 if credited else None
            if credited:
                credited_count += 1
                credited_sum += credited_gpm or 0.0
                if C.WARNING_RESIDUAL_BELOW_TARGET in derived.warnings:
                    sub_target += 1
            else:
                # The derivation guarantees a code for every non-credited
                # hydrant (a NULL rating always carries an excluding warning;
                # a gating deficiency maps to INSPECTION_FAIL).
                code = derived.exclusion_code or C.EXCLUSION_UNTESTED
                exclusion_tally[code] = exclusion_tally.get(code, 0) + 1
            pair_rows.append(PairRow(
                parcel_pk=parcel.pk, hydrant_pk=hydrant.pk, distance_ft=distance,
                credited=credited, credited_gpm=credited_gpm,
                exclusion_code=None if credited else code,
                expired=derived.oos_expired,
            ))

        nearest = _nearest_within(
            ordered, lats, parcel.lat, parcel.lon, credit_radius_ft * 2.0, lat_half_2x
        )
        summary = ParcelSummary(
            parcel_pk=parcel.pk,
            in_radius_count=len([r for r in pair_rows if r.hydrant_pk is not None]),
            credited_count=credited_count,
            credited_gpm_sum=credited_sum,
            distribution_band=distribution_band(credited_count),
            flow_band_proxy=flow_band_proxy(credited_sum),
            nearest_within_2x_ft=nearest,
            exclusion_code_counts=tuple(sorted(exclusion_tally.items())),
        )
        yield pair_rows, summary, sub_target


def compute_coverage(
    hydrants: list[CoverageHydrant],
    parcels: list[CoverageParcel],
    *,
    params: DerivationParams,
    credit_radius_ft: float,
    max_pairs: int = C.MAX_PAIRS_PER_RUN,
) -> CoverageComputation:
    """Collect :func:`iter_coverage` (the test/pack convenience wrapper)."""
    tallies = RunTallies()
    all_rows: list[PairRow] = []
    summaries: list[ParcelSummary] = []
    for pair_rows, summary, sub_target in iter_coverage(
        hydrants, parcels, params=params, credit_radius_ft=credit_radius_ft
    ):
        all_rows.extend(pair_rows)
        summaries.append(summary)
        tallies.absorb(pair_rows, summary, sub_target)
        if tallies.rows > max_pairs:
            raise PairGuardExceeded(
                f"run would write more than {max_pairs:,} result rows; narrow the "
                f"run with parcel_filter (parcel_ids ≤ {C.MAX_PARCEL_IDS:,} or a bbox)"
            )
    return CoverageComputation(
        rows=tuple(all_rows),
        parcels=tuple(summaries),
        hydrant_count=len(hydrants),
        protected_parcel_count=len(parcels),
        pair_count=tallies.rows,
        in_radius_count=tallies.in_radius_pairs,
        covered_count=tallies.covered,
        credited_band_counts=tallies.band_counts,
        formula_versions_present=tuple(sorted({
            h.view.current_test.formula_version for h in hydrants if h.view.current_test is not None
        })),
        sub_target_residual_included_count=tallies.sub_target_included,
        flow_band_counts=tallies.flow_counts,
    )


def _band_slice(ordered, lats, lat: float, lat_half: float):
    lo = bisect.bisect_left(lats, lat - lat_half)
    hi = bisect.bisect_right(lats, lat + lat_half)
    return ordered[lo:hi]


def _in_radius(ordered, lats, lat: float, lon: float, radius_ft: float, lat_half: float):
    """Exact in-radius survivors of the conservative bisect prefilter."""
    band = _band_slice(ordered, lats, lat, lat_half)
    if not band:
        return []
    max_abs_lat = max(abs(lat - lat_half), abs(lat + lat_half))
    lon_half = longitude_half_window_deg(radius_ft, max_abs_lat)
    survivors = []
    for hydrant in band:
        if abs(hydrant.lon - lon) > lon_half:
            continue
        distance = haversine_ft(lat, lon, hydrant.lat, hydrant.lon)
        if distance <= radius_ft:
            survivors.append((hydrant, distance))
    survivors.sort(key=lambda item: (item[1], item[0].pk))
    return survivors


def _nearest_within(ordered, lats, lat: float, lon: float, radius_ft: float, lat_half: float):
    """Nearest hydrant of any status within the window — the gap metric."""
    band = _band_slice(ordered, lats, lat, lat_half)
    if not band:
        return None
    max_abs_lat = max(abs(lat - lat_half), abs(lat + lat_half))
    lon_half = longitude_half_window_deg(radius_ft, max_abs_lat)
    best: float | None = None
    for hydrant in band:
        if abs(hydrant.lon - lon) > lon_half:
            continue
        distance = haversine_ft(lat, lon, hydrant.lat, hydrant.lon)
        if distance <= radius_ft and (best is None or distance < best):
            best = distance
    return best
