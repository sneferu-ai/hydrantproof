"""Root URL configuration."""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularJSONAPIView

from ledger.admin import hydrantproof_admin_site

urlpatterns = [
    path("admin/", hydrantproof_admin_site.urls),
    # OpenAPI 3.0.3, anonymous, schema only (the pinned public contract).
    path("openapi.json", SpectacularJSONAPIView.as_view(), name="openapi-json"),
    path("", include("ledger.urls")),
    path("api/v1/", include("ledger.urls_api")),
]
