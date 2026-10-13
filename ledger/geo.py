"""Great-circle distance helpers — pure Python, no Django imports.

Mean Earth radius 6,371,008.8 m; metres to feet ×3.28084.  The coverage engine
compares **unrounded** distances against the credit radius; display rounds to
0.1 ft.
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_008.8
FEET_PER_METER = 3.28084
MAX_ABS_LATITUDE = 80.0
MAX_ABS_LONGITUDE = 180.0


def valid_latitude(value: float) -> bool:
    return value is not None and abs(float(value)) <= MAX_ABS_LATITUDE


def valid_longitude(value: float) -> bool:
    return value is not None and abs(float(value)) <= MAX_ABS_LONGITUDE


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def haversine_ft(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in feet (unrounded)."""
    return haversine_m(lat1, lon1, lat2, lon2) * FEET_PER_METER


def degrees_for_feet(feet: float) -> float:
    """A latitude difference in degrees for an arc length in feet."""
    return (float(feet) / FEET_PER_METER) / EARTH_RADIUS_M * (180.0 / math.pi)


def latitude_window_deg(radius_ft: float) -> float:
    """Half-window in latitude degrees covering ``radius_ft``."""
    return degrees_for_feet(radius_ft)


def longitude_half_window_deg(radius_ft: float, max_abs_lat_deg: float) -> float:
    """Conservative longitude half-window.

    The prefilter must never exclude a true in-radius hydrant: a point at
    latitude ``φ`` inside the radius has |Δlon| ≤ radius_deg / cos(φ), which is
    maximised at the largest |φ| in the band.  ``max_abs_lat_deg`` is that
    maximum over the scanned band, so the returned window is a guaranteed lower
    bound on the needed window (an upper bound on any true Δlon).
    """
    cos_lat = math.cos(math.radians(min(89.9, abs(float(max_abs_lat_deg)))))
    if cos_lat <= 1e-9:
        return 180.0
    return degrees_for_feet(radius_ft) / cos_lat
