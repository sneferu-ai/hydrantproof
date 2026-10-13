"""HTML routes (server-rendered pages + the enumerated anonymous surface)."""
from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz_view, name="healthz"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("sw.js", views.service_worker, name="service-worker"),
    path("field/", views.field_page, name="field"),
    path("hydrants/", views.hydrant_list, name="hydrant-list-page"),
    path("hydrants/new/", views.hydrant_create, name="hydrant-create-page"),
    path("hydrants/<int:pk>/", views.hydrant_detail, name="hydrant-detail-page"),
    path("hydrants/<int:pk>/edit/", views.hydrant_edit, name="hydrant-edit-page"),
    path("hydrants/<int:pk>/tests/new/", views.flow_test_new, name="flow-test-page"),
    path("hydrants/<int:pk>/inspections/new/", views.inspection_new, name="inspection-page"),
    path("parcels/", views.parcel_list, name="parcel-list-page"),
    path("parcels/<int:pk>/", views.parcel_detail, name="parcel-detail-page"),
    path("parcels/<int:pk>/edit/", views.parcel_edit, name="parcel-edit-page"),
    path("exports/", views.exports_page, name="exports-page"),
    path("guide/", views.guide_page, name="guide-page"),
    path("settings/", views.settings_page, name="settings-page"),
    path("coverage/", views.coverage_page, name="coverage-page"),
    path("packs/", views.pack_page, name="pack-page"),
]
