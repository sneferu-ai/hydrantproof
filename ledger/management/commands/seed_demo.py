"""Idempotent demo seed (advisory-lock guarded).

24 hydrants covering every class and lifecycle edge, ~60 parcels (structure
overrides, not-protected rows), ~40 flow tests (supersession chains,
sub-target residual, excessive extrapolation, static-below-target,
meter+pitot divergent, a degrading pair), inspections (FAIL with codes then a
later OK), one sample GeoJSON overlay, and the demo users.  Every row carries
``is_sample_data=True``.  Seeding stays off after ``purge_demo`` until
``clear_purge_marker`` runs.
"""
from __future__ import annotations

import json
import secrets
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from ledger import constants as C
from ledger.models import AppSetting, DistrictSettings, Hydrant, MapOverlay, Parcel
from ledger.services import create_flow_test, create_inspection, create_status_event
from ledger.signals import DEMO_DEACTIVATED_MARKER

DEMO_PASSWORD = "hydrantproof-demo"
_LOCK_KEY = 552_210


class Command(BaseCommand):
    help = "Seed the demo district (idempotent; requires SEED_DEMO posture)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Seed even if a marker would normally block it (tests only).")

    def handle(self, *args, **options):
        force = options["force"]
        if AppSetting.marker_set("demo_purged") and not force:
            self.stdout.write("seed_demo: demo_purged marker present — seeding stays off "
                              "(run clear_purge_marker to re-enable)")
            return
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
            if Hydrant.objects.filter(is_sample_data=True).exists() and not force:
                self.stdout.write("seed_demo: sample data already present — nothing to do")
                return
            if Hydrant.objects.exists() and not force:
                self.stdout.write("seed_demo: ledger is not empty — refusing to seed")
                return
            self._seed()

    # ------------------------------------------------------------------
    def _seed(self):
        today = timezone.localdate()
        district = DistrictSettings.load()
        district.name = district.name if district.name != "Sample District" else "Sample District"
        district.save()

        demo_users = self._ensure_demo_users()
        # A new demo generation begins with live published-password accounts,
        # so the first-real-row trigger must be armed: a prior purge leaves
        # ``demo_deactivated_at`` set, and without this re-arm the signal would
        # skip and the accounts would survive contact with real data (§10).
        AppSetting.objects.filter(key=DEMO_DEACTIVATED_MARKER).delete()
        overlay = self._seed_overlay()
        parcels = self._seed_parcels()

        # 24 hydrants: classes AA/A/B/C, never-tested, OOS future/past, retired.
        plan = [
            # (tag, class target via rated flow, lifecycle, note)
            ("HP-001", 1868.0, "IN_SERVICE", None),
            ("HP-002", 1500.0, "IN_SERVICE", None),
            ("HP-003", 1200.0, "IN_SERVICE", "degrading-pair"),
            ("HP-004", 900.0, "IN_SERVICE", None),
            ("HP-005", 700.0, "IN_SERVICE", None),
            ("HP-006", 520.0, "IN_SERVICE", None),
            ("HP-007", 400.0, "IN_SERVICE", None),
            ("HP-008", 1105.0, "IN_SERVICE", "meter+pitot divergent"),
            ("HP-009", 887.9, "IN_SERVICE", "sub-target residual"),
            ("HP-010", None, "IN_SERVICE", "static below target"),
            ("HP-011", None, "IN_SERVICE", "excessive extrapolation"),
            ("HP-012", None, "IN_SERVICE", "never tested"),
            ("HP-013", None, "IN_SERVICE", "never tested"),
            ("HP-014", 1300.0, "IN_SERVICE", "supersession chain"),
            ("HP-015", 1300.0, "IN_SERVICE", "supersession chain (correction)"),
            ("HP-016", 980.0, "IN_SERVICE", "FAIL inspection then OK"),
            ("HP-017", 640.0, "IN_SERVICE", "overdue test"),
            ("HP-018", 1420.0, "IN_SERVICE", "overdue inspection"),
            ("HP-019", 1150.0, "OUT_OF_SERVICE", "future expected return"),
            ("HP-020", 1050.0, "OUT_OF_SERVICE", "past expected return (expired)"),
            ("HP-021", 760.0, "OUT_OF_SERVICE", "indefinite"),
            ("HP-022", 890.0, "RETIRED", "retired"),
            ("HP-023", 1010.0, "IN_SERVICE", "degrading-pair (older)"),
            ("HP-024", 300.0, "IN_SERVICE", "low class C"),
        ]
        created = {}
        for index, (tag, target, lifecycle, note) in enumerate(plan):
            lat = 38.0 + (index % 6) * 0.004
            lon = -122.0 + (index // 6) * 0.006
            hydrant = Hydrant.objects.create(
                asset_tag=tag,
                lat=lat,
                lon=lon,
                location_description=f"{tag} sample location",
                manufacturer="Sample Works",
                model="S-100",
                year_installed=1995 + (index % 25),
                outlet_diameter_in="2.50",
                discharge_coefficient="0.90",
                ownership="Sample District",
                source_org="Sample District",
                is_sample_data=True,
                notes=note or "",
            )
            create_status_event(hydrant=hydrant, to_status=C.STATUS_IN_SERVICE,
                                reason="Initial record", user=None, is_initial=True)
            created[tag] = hydrant

            if lifecycle == "OUT_OF_SERVICE":
                if "expired" in (note or ""):
                    return_on = today - timedelta(days=21)
                elif "indefinite" in (note or ""):
                    return_on = None
                else:
                    return_on = today + timedelta(days=14)
                create_status_event(
                    hydrant=hydrant,
                    to_status=C.STATUS_OUT_OF_SERVICE,
                    reason="Sample outage",
                    expected_return_on=return_on,
                    indefinite=return_on is None,
                    effective_on=today - timedelta(days=2),
                    user=None,
                )
            elif lifecycle == "RETIRED":
                create_status_event(hydrant=hydrant, to_status=C.STATUS_RETIRED,
                                    reason="Removed with main replacement", user=None)

        # Flow tests
        self._seed_tests(created, today)
        # Inspections
        self._seed_inspections(created, today)

        # demo-admin gets a random one-time password printed to stdout.
        admin_password = secrets.token_urlsafe(12)
        demo_admin = demo_users["demo-admin"]
        demo_admin.set_password(admin_password)
        demo_admin.save()

        self.stdout.write(self.style.SUCCESS(
            f"seed_demo: {len(created)} hydrants, {len(parcels)} parcels, "
            f"{overlay and 1 or 0} overlay, demo users ready"
        ))
        self.stdout.write(f"demo-editor / demo-viewer password: {DEMO_PASSWORD} (demo mode only)")
        self.stdout.write(f"demo-admin one-time password: {admin_password} (printed once)")

    # ------------------------------------------------------------------
    def _ensure_demo_users(self):
        for name in ("admin", "editor", "viewer"):
            Group.objects.get_or_create(name=name)
        users = {}
        for username, group in (("demo-editor", "editor"), ("demo-viewer", "viewer"),
                                ("demo-admin", "admin")):
            user, created = User.objects.get_or_create(username=username)
            if created or not user.has_usable_password():
                user.set_password(DEMO_PASSWORD)
                user.is_active = True
                user.save()
            user.groups.add(Group.objects.get(name=group))
            users[username] = user
        return users

    def _seed_overlay(self):
        from django.conf import settings
        from pathlib import Path

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Sample water district boundary"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-122.01, 37.99], [-121.98, 37.99], [-121.98, 38.03],
                            [-122.01, 38.03], [-122.01, 37.99],
                        ]],
                    },
                }
            ],
        }
        relative = "overlays/sample-district.geojson"
        path = Path(settings.MEDIA_ROOT) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(geojson, sort_keys=True)
        path.write_text(body)
        MapOverlay.objects.get_or_create(
            name="Sample district boundary",
            defaults={
                "geojson_path": relative,
                "feature_count": len(geojson["features"]),
                "byte_size": len(body.encode("utf-8")),
                "is_sample_data": True,
            },
        )
        return relative

    def _seed_parcels(self):
        parcels = []
        for index in range(60):
            parcel_id = f"SAMPLE-{index + 1:03d}"
            lat = 38.0 + (index % 10) * 0.003
            lon = -122.0 + (index // 10) * 0.005
            parcels.append(Parcel(
                parcel_id=parcel_id,
                address=f"{100 + index} Sample Rd",
                lat=lat,
                lon=lon,
                structure_lat=lat + 0.0004 if index % 7 == 0 else None,
                structure_lon=lon + 0.0004 if index % 7 == 0 else None,
                is_protected_structure=(index % 11 != 0),
                structure_type="single-family" if index % 3 else "commercial",
                source=Parcel.SOURCE_CSV,
                source_org="Sample County Assessor",
                is_sample_data=True,
            ))
        Parcel.objects.bulk_create(parcels)
        return parcels

    def _seed_tests(self, hydrants, today):
        def add(tag, days_ago, static, residual, outlets, **kwargs):
            hydrant = hydrants[tag]
            return create_flow_test(
                hydrant=hydrant,
                tested_on=today - timedelta(days=days_ago),
                static_psi=str(static),
                residual_psi=str(residual),
                outlets=outlets,
                is_sample_data=True,
                **kwargs,
            )

        standard = lambda pitot: [
            {"outlet_diameter_in": "2.5", "outlet_coefficient": "0.90", "pitot_psi": str(pitot)}
        ]

        add("HP-001", 30, 80, 40, standard(40) + standard(40))
        add("HP-002", 45, 80, 40, standard(30) + standard(30))
        add("HP-003", 60, 80, 40, standard(28))
        add("HP-023", 240, 80, 40, standard(34))          # older, higher
        add("HP-003", 20, 80, 40, standard(23), supersedes=None)  # degrading pair
        add("HP-004", 90, 80, 40, standard(20))
        add("HP-005", 120, 80, 40, standard(15))
        add("HP-006", 150, 80, 40, standard(11))
        add("HP-007", 200, 80, 40, standard(8))
        add("HP-008", 75, 80, 40, standard(28), measured_total_gpm="1105.0")
        add("HP-009", 300, 80, 15, standard(10))          # sub-target residual (credited)
        add("HP-010", 100, 18, 9, standard(20))           # static below target
        add("HP-011", 110, 80, 79.5, standard(15))        # excessive extrapolation
        add("HP-014", 400, 80, 40, standard(31))
        add("HP-015", 30, 80, 40, standard(31))           # supersession chain
        add("HP-016", 200, 80, 40, standard(24))
        add("HP-017", 500, 80, 40, standard(16))          # overdue test
        add("HP-018", 40, 80, 40, standard(32))
        add("HP-019", 60, 80, 40, standard(27))
        add("HP-020", 60, 80, 40, standard(25))
        add("HP-021", 60, 80, 40, standard(18))
        add("HP-022", 700, 80, 40, standard(21))          # retired hydrant keeps history
        add("HP-024", 90, 80, 40, standard(6))

        # A supersession chain: the correction excludes the original from current.
        original = add("HP-014", 5, 80, 41, standard(30))
        add("HP-014", 4, 80, 40, standard(31), supersedes=original)

    def _seed_inspections(self, hydrants, today):
        for tag, days_ago in (("HP-001", 20), ("HP-002", 30), ("HP-003", 25), ("HP-004", 60),
                              ("HP-005", 40), ("HP-006", 55), ("HP-007", 70), ("HP-008", 35),
                              ("HP-009", 45), ("HP-014", 15), ("HP-016", 90), ("HP-017", 30),
                              ("HP-019", 20), ("HP-020", 25), ("HP-021", 30), ("HP-024", 50)):
            create_inspection(
                hydrant=hydrants[tag],
                inspected_on=today - timedelta(days=days_ago),
                result=C.INSPECTION_OK,
                deficiency_codes=[],
                inspector_names="Sample Inspector",
                is_sample_data=True,
            )
        # HP-016: FAIL with codes, then a later OK (the auto-close path).
        create_inspection(
            hydrant=hydrants["HP-016"],
            inspected_on=today - timedelta(days=120),
            result=C.INSPECTION_FAIL,
            deficiency_codes=["CAP", "PAINT"],
            inspector_names="Sample Inspector",
            is_sample_data=True,
        )
        create_inspection(
            hydrant=hydrants["HP-016"],
            inspected_on=today - timedelta(days=10),
            result=C.INSPECTION_OK,
            deficiency_codes=[],
            inspector_names="Sample Inspector",
            is_sample_data=True,
        )
        # HP-018: overdue inspection (last one long ago).
        create_inspection(
            hydrant=hydrants["HP-018"],
            inspected_on=today - timedelta(days=500),
            result=C.INSPECTION_OK,
            deficiency_codes=[],
            inspector_names="Sample Inspector",
            is_sample_data=True,
        )
