"""Haversine and the conservative prefilter window. No database."""
from __future__ import annotations

import pytest

from ledger.geo import (
    degrees_for_feet,
    haversine_ft,
    haversine_m,
    longitude_half_window_deg,
    valid_latitude,
    valid_longitude,
)

RADIUS_FT = 1000.0


def _offset_lat(lat: float, feet: float) -> float:
    return lat + degrees_for_feet(feet)


class TestFixtureE:
    """Engineered 999.5 ft / 1000.5 ft boundary pairs on unrounded distance."""

    def test_inside_boundary_is_included(self):
        lat, lon = 38.0, -122.0
        distance = haversine_ft(lat, lon, _offset_lat(lat, 999.5), lon)
        assert distance == pytest.approx(999.5, abs=0.05)
        assert distance <= RADIUS_FT

    def test_outside_boundary_is_excluded(self):
        lat, lon = 38.0, -122.0
        distance = haversine_ft(lat, lon, _offset_lat(lat, 1000.5), lon)
        assert distance == pytest.approx(1000.5, abs=0.05)
        assert distance > RADIUS_FT

    def test_identical_points_are_zero(self):
        assert haversine_ft(38.0, -122.0, 38.0, -122.0) == pytest.approx(0.0, abs=1e-9)


class TestPrefilterWindow:
    def test_longitude_window_covers_true_delta_at_high_latitude(self):
        # A hydrant due east at the radius: its Δlon must fit inside the window.
        lat = 60.0
        delta_lat = degrees_for_feet(RADIUS_FT)
        max_abs_lat = abs(lat) + delta_lat
        window = longitude_half_window_deg(RADIUS_FT, max_abs_lat)
        # true Δlon for an east-west offset of exactly the radius
        import math

        true_delta_lon = degrees_for_feet(RADIUS_FT) / math.cos(math.radians(lat))
        assert window >= true_delta_lon

    def test_metres_to_feet_ratio(self):
        one_meter_ft = haversine_ft(0.0, 0.0, degrees_for_feet(3.28084), 0.0)
        assert one_meter_ft == pytest.approx(3.28084, abs=1e-4)

    def test_mean_earth_radius_pinned(self):
        # 1 degree of latitude ≈ 111,195 m at R = 6,371,008.8 m
        assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_194.93, abs=1.0)


class TestCoordinateValidation:
    @pytest.mark.parametrize("lat,ok", [(80.0, True), (-80.0, True), (80.1, False), (-90.0, False)])
    def test_latitude(self, lat, ok):
        assert valid_latitude(lat) is ok

    @pytest.mark.parametrize("lon,ok", [(180.0, True), (-180.0, True), (180.1, False)])
    def test_longitude(self, lon, ok):
        assert valid_longitude(lon) is ok
