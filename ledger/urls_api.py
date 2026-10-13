"""The /api/v1/ route table (mounted at ``/api/v1/``).

Route discipline (asserted by tests/test_route_table.py): no PUT anywhere;
PATCH only on hydrant and parcel detail; DELETE only on overlays and the
admin-only attachment deletion.
"""
from django.urls import path

from . import api, api_files, api_jobs, exports

urlpatterns = [
    # Hydrants
    path("hydrants/", api.HydrantListCreateView.as_view(), name="hydrant-list"),
    path("hydrants/<int:pk>/", api.HydrantDetailView.as_view(), name="hydrant-detail"),
    path("hydrants/<int:pk>/tests/", api.HydrantTestsView.as_view(), name="hydrant-tests"),
    path("hydrants/<int:pk>/tests/<int:test_id>/", api.HydrantTestDetailView.as_view(),
         name="hydrant-test-detail"),
    path("hydrants/<int:pk>/tests/<int:owner_id>/attachments/",
         api.TestAttachmentCollectionView.as_view(), name="test-attachments"),
    path("hydrants/<int:pk>/tests/<int:owner_id>/attachments/<int:att_id>/",
         api.TestAttachmentDetailView.as_view(), name="test-attachment-detail"),
    path("hydrants/<int:pk>/inspections/", api.HydrantInspectionsView.as_view(),
         name="hydrant-inspections"),
    path("hydrants/<int:pk>/inspections/<int:owner_id>/attachments/",
         api.InspectionAttachmentCollectionView.as_view(), name="inspection-attachments"),
    path("hydrants/<int:pk>/inspections/<int:owner_id>/attachments/<int:att_id>/",
         api.InspectionAttachmentDetailView.as_view(), name="inspection-attachment-detail"),
    path("hydrants/<int:pk>/status-events/", api.HydrantStatusEventsView.as_view(),
         name="hydrant-status-events"),
    path("hydrants/<int:pk>/oos-acknowledgements/", api.HydrantOosAcknowledgementsView.as_view(),
         name="hydrant-oos-acknowledgements"),
    path("hydrants/<int:pk>/deficiencies/", api.HydrantDeficienciesView.as_view(),
         name="hydrant-deficiencies"),
    path("hydrants/<int:pk>/deficiencies/<int:def_id>/resolve/", api.DeficiencyResolveView.as_view(),
         name="hydrant-deficiency-resolve"),
    path("hydrants/<int:pk>/coverage/", api.HydrantCoverageView.as_view(), name="hydrant-coverage"),

    # Compute / lists
    path("compute-preview/", api.ComputePreviewView.as_view(), name="compute-preview"),
    path("overdue/", api.OverdueView.as_view(), name="overdue"),
    path("notifications/", api.NotificationsView.as_view(), name="notifications"),

    # Parcels
    path("parcels/", api.ParcelListCreateView.as_view(), name="parcel-list"),
    path("parcels/<int:pk>/", api.ParcelDetailView.as_view(), name="parcel-detail"),
    path("parcels/<int:pk>/coverage/", api.ParcelCoverageView.as_view(), name="parcel-coverage"),

    # Settings
    path("settings/", api.DistrictSettingsView.as_view(), name="district-settings"),

    # Coverage runs (async, DB-leased) and evidence packs
    path("coverage-runs/", api_jobs.CoverageRunListCreateView.as_view(), name="coverage-run-list"),
    path("coverage-runs/<int:pk>/", api_jobs.CoverageRunDetailView.as_view(), name="coverage-run-detail"),
    path("coverage-runs/<int:pk>/results/", api_jobs.CoverageRunResultsView.as_view(),
         name="coverage-run-results"),
    path("evidence-packs/", api_jobs.EvidencePackListCreateView.as_view(), name="evidence-pack-list"),
    path("evidence-packs/<int:pk>/", api_jobs.EvidencePackDetailView.as_view(), name="evidence-pack-detail"),
    path("evidence-packs/<int:pk>/zip/", api_jobs.PackZipDownloadView.as_view(), name="evidence-pack-zip"),
    path("evidence-packs/<int:pk>/pdf/", api_jobs.PackPdfDownloadView.as_view(), name="evidence-pack-pdf"),

    # Overlays (one active; editor+ upload/delete)
    path("overlays/", api_files.OverlayCollectionView.as_view(), name="overlay-list"),
    path("overlays/active/", api_files.OverlayActiveView.as_view(), name="overlay-active"),
    path("overlays/<int:pk>/", api_files.OverlayDetailView.as_view(), name="overlay-detail"),

    # Two-phase CSV imports
    path("imports/", api_files.ImportHistoryView.as_view(), name="import-history"),
    path("imports/hydrants/", api_files.HydrantImportView.as_view(), name="import-hydrants"),
    path("imports/parcels/", api_files.ParcelImportView.as_view(), name="import-parcels"),
    path("imports/flow-tests/", api_files.FlowTestImportView.as_view(), name="import-flow-tests"),

    # Exports
    path("exports/dispatch/", exports.DispatchExportView.as_view(), name="export-dispatch"),
    path("exports/forms/flow-test/", exports.FlowTestFormView.as_view(), name="export-form-flow-test"),
    path("exports/forms/inventory/", exports.InventoryFormView.as_view(), name="export-form-inventory"),
    path("exports/ledger/", api_files.LedgerExportView.as_view(), name="export-ledger"),
]
