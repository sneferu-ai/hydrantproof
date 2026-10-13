"""Django admin: read-only ledger surfaces plus user/settings administration.

The admin login is one of the three authentication surfaces (the throttle
records every admin-login failure), so the site class is ours.
"""
from __future__ import annotations

from django.contrib import admin
from django.contrib.admin import AdminSite

from . import models


class HydrantProofAdminSite(AdminSite):
    site_header = "HydrantProof administration"
    site_title = "HydrantProof"
    index_title = "District administration"

    def login(self, request, extra_context=None):
        from .auth import record_login_attempt

        response = super().login(request, extra_context=extra_context)
        # Every admin-surface outcome is recorded, successes included: the
        # throttle resets a username's window on a recorded success, so an
        # admin-only account that never recorded one would accumulate toward
        # the failure limit and eventually 429 its own valid logins.  A POST
        # answered with a redirect accepted the credentials; a 200 re-renders
        # the form — a failure, including a valid non-staff login the admin
        # form refuses.
        if request.method == "POST":
            username = request.POST.get("username", "")
            if response.status_code in (301, 302) and getattr(request, "user", None) is not None \
                    and request.user.is_authenticated:
                record_login_attempt(request, request.user.get_username() or username,
                                     surface=models.LoginAttempt.SURFACE_ADMIN, success=True)
            elif response.status_code == 200:
                record_login_attempt(request, username,
                                     surface=models.LoginAttempt.SURFACE_ADMIN, success=False)
        return response


hydrantproof_admin_site = HydrantProofAdminSite(name="admin")


class ReadOnlyLedgerAdmin(admin.ModelAdmin):
    """Tier A tables are visible in the admin and never editable there."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.Hydrant, site=hydrantproof_admin_site)
class HydrantAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "status", "source_org", "is_sample_data")
    search_fields = ("asset_tag", "location_description", "ownership", "source_org")
    list_filter = ("status", "source", "is_sample_data")


@admin.register(models.DistrictSettings, site=hydrantproof_admin_site)
class DistrictSettingsAdmin(admin.ModelAdmin):
    """The designated mutation route for DistrictSettings — every changed
    field writes a SettingsAudit row (specification §5)."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        for field in form.changed_data:
            models.SettingsAudit.objects.create(
                field=field,
                old_value=str(form.initial.get(field, "")),
                new_value=str(form.cleaned_data.get(field, "")),
                changed_by=request.user if request.user.is_authenticated else None,
            )
        super().save_model(request, obj, form, change)


@admin.register(models.Parcel, site=hydrantproof_admin_site)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ("parcel_id", "address", "is_protected_structure", "is_sample_data")
    search_fields = ("parcel_id", "address")


@admin.register(models.Deficiency, site=hydrantproof_admin_site)
class DeficiencyAdmin(admin.ModelAdmin):
    list_display = ("hydrant", "category", "gates_credit", "status", "assigned_to", "due_on")
    list_filter = ("status", "gates_credit", "category")


@admin.register(models.Attachment, site=hydrantproof_admin_site)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("owner_type", "owner_id", "sha256", "byte_size", "created_at")
    readonly_fields = ("sha256", "byte_size", "content_type", "original_content_type", "capture_meta")

    def delete_model(self, request, obj):
        from .media import admin_delete_attachment

        admin_delete_attachment(obj, actor=request.user, reason="admin delete")

    def delete_queryset(self, request, queryset):
        from .media import admin_delete_attachment

        for obj in queryset:
            admin_delete_attachment(obj, actor=request.user, reason="admin bulk delete")


@admin.register(models.MapOverlay, site=hydrantproof_admin_site)
class MapOverlayAdmin(ReadOnlyLedgerAdmin):
    """Upload/replace/delete are the sanctioned mutations (§11) — an admin
    edit here would desync the row from its stored GeoJSON file."""

    list_display = ("name", "feature_count", "byte_size", "uploaded_at")


@admin.register(models.ImportBatch, site=hydrantproof_admin_site)
class ImportBatchAdmin(ReadOnlyLedgerAdmin):
    """The import lane owns batch rows and their stored files together; a
    bare admin delete would orphan the stored CSV."""

    list_display = ("kind", "filename", "mode", "rows_ok", "rows_rejected", "created_at")
    list_filter = ("kind", "mode")


class UserProfileAdmin(admin.ModelAdmin):
    """Only ``org_label`` is district-editable (§4); the user binding is fixed."""

    list_display = ("user", "org_label")
    search_fields = ("user__username", "org_label")
    readonly_fields = ("user",)


for model in (
    models.FlowTest,
    models.OutletReading,
    models.HydrantStatusEvent,
    models.HydrantAttributeChange,
    models.ParcelAttributeChange,
    models.Inspection,
    models.OosAcknowledgement,
    models.SettingsAudit,
    models.ExportAudit,
    models.MediaAudit,
    models.LoginAttempt,
    # Lifecycle/job rows: no admin mutations (their sanctioned paths are the
    # API routes, the job lease and prune_data).  AppSetting in particular
    # holds the DB-persisted secret key and the demo markers — reading it is
    # administration, editing it from a list view is not (§5/§10).
    models.CoverageRun,
    models.CoverageResult,
    models.EvidencePack,
    models.NotificationOutbox,
    models.AppSetting,
):
    hydrantproof_admin_site.register(model, ReadOnlyLedgerAdmin)

hydrantproof_admin_site.register(models.UserProfile, UserProfileAdmin)
