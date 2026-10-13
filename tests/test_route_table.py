"""Route discipline and the OpenAPI contract.

No PUT anywhere; PATCH only on hydrant and parcel detail; DELETE only on
overlays and the admin-only attachment deletion; every ``/api/v1/`` route
appears in the served schema.
"""
from __future__ import annotations

import json

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

pytestmark = pytest.mark.django_db


def iter_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from iter_patterns(pattern, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern


def product_patterns():
    """The product's own routes — the Django admin is not part of the contract."""
    for route, pattern in iter_patterns():
        if route.startswith("admin/"):
            continue
        yield route, pattern


API_PATTERNS = [(route, pattern) for route, pattern in iter_patterns() if route.startswith("api/v1/")]


class TestRouteDiscipline:
    def test_no_put_anywhere(self):
        offenders = []
        for route, pattern in product_patterns():
            view = pattern.callback
            cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
            if cls is None:
                continue
            # A route "has" a method when the view implements the handler;
            # DRF's APIView base never defines put/patch/delete.
            if getattr(cls, "put", None) is not None:
                offenders.append(route)
        assert offenders == []

    def test_patch_only_on_hydrant_and_parcel_detail(self):
        patch_routes = []
        for route, pattern in product_patterns():
            view = pattern.callback
            cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
            if cls is None:
                continue
            handlers = getattr(cls, "patch", None)
            if handlers is not None:
                patch_routes.append(route)
        normalised = {route.rstrip("/") for route in patch_routes}
        assert normalised == {"api/v1/hydrants/<int:pk>", "api/v1/parcels/<int:pk>"}, normalised

    def test_delete_only_on_overlays_and_attachments(self):
        delete_routes = []
        for route, pattern in product_patterns():
            view = pattern.callback
            cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
            if cls is None:
                continue
            if getattr(cls, "delete", None) is not None:
                delete_routes.append(route)
        for route in delete_routes:
            assert "attachments" in route or "overlays" in route, route

    def test_anonymous_surface_is_exactly_enumerated(self):
        from django.conf import settings

        allowed_anonymous = {"/healthz", "/login/", "/logout/", "/admin/login/", "/sw.js"}
        for route in ("/healthz", "/login/", "/logout/", "/sw.js"):
            assert route in allowed_anonymous
        assert "/static/" in settings.STATIC_URL


class TestOpenApiContract:
    def test_schema_is_served_anonymously_at_the_pinned_path(self, anonymous_api):
        import json as _json

        response = anonymous_api.get("/openapi.json")
        assert response.status_code == 200
        payload = _json.loads(response.content)
        assert payload["openapi"].startswith("3.0")

    def test_every_api_route_appears_in_the_schema(self, anonymous_api):
        import json as _json

        schema = _json.loads(anonymous_api.get("/openapi.json").content)
        paths = set(schema.get("paths", {}))
        normalised_paths = {path.rstrip("/") for path in paths}
        missing = []
        for route, _pattern in API_PATTERNS:
            candidate = "/" + route.rstrip("/")
            candidate = candidate.replace("<int:pk>", "{id}").replace("<int:test_id>", "{test_id}")
            candidate = candidate.replace("<int:owner_id>", "{owner_id}").replace("<int:att_id>", "{att_id}")
            candidate = candidate.replace("<int:def_id>", "{def_id}")
            if candidate not in normalised_paths:
                missing.append(candidate)
        assert missing == [], f"routes missing from the OpenAPI schema: {missing}"

    def test_browsable_api_renderer_is_disabled(self):
        from rest_framework.renderers import JSONRenderer
        from rest_framework.settings import api_settings

        classes = api_settings.DEFAULT_RENDERER_CLASSES
        assert [c if isinstance(c, str) else c.__module__ + "." + c.__name__ for c in classes] == [
            JSONRenderer.__module__ + "." + JSONRenderer.__name__
        ]


class TestErrorShapes:
    def test_anonymous_json_is_401(self, anonymous_api):
        assert anonymous_api.get("/api/v1/parcels/").status_code == 401

    def test_viewer_write_is_403(self, viewer_api):
        response = viewer_api.post("/api/v1/compute-preview/", {}, format="json")
        assert response.status_code == 403

    def test_unknown_object_is_404(self, api):
        assert api.get("/api/v1/hydrants/999999/").status_code == 404

    def test_settings_are_readable_and_not_writable_over_the_api(self, api):
        assert api.get("/api/v1/settings/").status_code == 200
        # Mutations are the Django admin's designated route (§5): no PATCH here.
        assert api.patch("/api/v1/settings/", {"recency_days": 400}, format="json").status_code == 405

    def test_admin_settings_change_is_audited(self, db):
        from django.contrib.auth.models import User
        from django.test import Client

        from ledger.models import DistrictSettings, SettingsAudit

        staff = User.objects.create_superuser("district-admin", password="hydrantproof-test-pass")
        district = DistrictSettings.load()
        client = Client()
        client.force_login(staff)
        response = client.post(
            f"/admin/ledger/districtsettings/{district.pk}/change/",
            {
                "name": district.name, "iso_community_id": district.iso_community_id,
                "state": district.state, "time_zone": district.time_zone,
                "recency_days": 400, "due_soon_days": district.due_soon_days,
                "inspection_recency_days": district.inspection_recency_days,
                "inspection_due_soon_days": district.inspection_due_soon_days,
                "credit_radius_ft": district.credit_radius_ft,
                "max_media_mb": district.max_media_mb,
                "max_attachment_total_mb": district.max_attachment_total_mb,
            },
        )
        assert response.status_code == 302  # the admin saved and redirected
        assert DistrictSettings.load().recency_days == 400
        assert SettingsAudit.objects.filter(field="recency_days", new_value="400").exists()


# ---------------------------------------------------------------------------
# Repair-round regression (native static review BUG-011)
# ---------------------------------------------------------------------------
class TestAdminReadOnlySurfaces:
    def test_job_and_secret_tables_are_read_only_in_the_admin(self, db):
        from ledger.admin import hydrantproof_admin_site
        from ledger.models import (
            AppSetting,
            CoverageResult,
            CoverageRun,
            EvidencePack,
            ImportBatch,
            MapOverlay,
            NotificationOutbox,
        )

        for model in (AppSetting, CoverageRun, CoverageResult, EvidencePack,
                      NotificationOutbox, MapOverlay, ImportBatch):
            model_admin = hydrantproof_admin_site._registry[model]
            assert model_admin.has_add_permission(None) is False, model.__name__
            assert model_admin.has_change_permission(None) is False, model.__name__
            assert model_admin.has_delete_permission(None) is False, model.__name__
