"""HydrantProof data model (specification §5, three-tier classification).

Tier A — append-only truth tables: a DB ``BEFORE UPDATE OR DELETE`` trigger
(migration 0003) refuses mutation, ``save()`` refuses to update an existing
row, and ``delete()`` raises.  Eight tables exactly.

Tier B — lifecycle tables: no DB trigger; immutability is enforced by the
route table (only the sanctioned mutations enumerated in the specification
exist) and tested.

Tier C — mutable core records: ``Hydrant``, ``DistrictSettings``, users.
"""
from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Func, Q, UniqueConstraint
from django.utils import timezone

from . import constants as C


class AppendOnlyViolation(RuntimeError):
    """Raised when a Tier A row is mutated through the ORM."""


class AppendOnlyModel(models.Model):
    """Tier A base: insert-only through the ORM, trigger-enforced in the DB."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyViolation(
                f"{type(self).__name__} rows are append-only; record a new row "
                f"(supersession/reversal/acknowledgement) instead of editing pk={self.pk}."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyViolation(
            f"{type(self).__name__} rows are append-only; they are removed only by the "
            f"sanctioned lifecycle commands (purge_demo/import restore) which disable the "
            f"Tier A triggers inside their own transaction."
        )


# ---------------------------------------------------------------------------
# Tier C — mutable core
# ---------------------------------------------------------------------------
class DistrictSettings(models.Model):
    """The district singleton (pk=1).  Policy is data; arithmetic is code."""

    SINGLETON_PK = 1

    name = models.CharField(max_length=128, default="Sample District")
    iso_community_id = models.CharField(max_length=64, blank=True, default="")
    state = models.CharField(max_length=32, blank=True, default="")
    time_zone = models.CharField(max_length=64, default="UTC")

    recency_days = models.PositiveIntegerField(
        default=C.DEFAULT_RECENCY_DAYS, validators=[MinValueValidator(30), MaxValueValidator(1095)]
    )
    due_soon_days = models.PositiveIntegerField(
        default=C.DEFAULT_DUE_SOON_DAYS, validators=[MinValueValidator(1), MaxValueValidator(364)]
    )
    inspection_recency_days = models.PositiveIntegerField(
        default=C.DEFAULT_INSPECTION_RECENCY_DAYS, validators=[MinValueValidator(30), MaxValueValidator(1095)]
    )
    inspection_due_soon_days = models.PositiveIntegerField(
        default=C.DEFAULT_INSPECTION_DUE_SOON_DAYS, validators=[MinValueValidator(1), MaxValueValidator(364)]
    )
    credit_radius_ft = models.FloatField(
        default=C.DEFAULT_CREDIT_RADIUS_FT, validators=[MinValueValidator(250.0), MaxValueValidator(2640.0)]
    )
    exclude_residual_below_target = models.BooleanField(default=False)
    max_media_mb = models.PositiveIntegerField(default=C.DEFAULT_MAX_MEDIA_MB)
    max_attachment_total_mb = models.PositiveIntegerField(default=C.DEFAULT_MAX_ATTACHMENT_TOTAL_MB)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "district settings"
        verbose_name_plural = "district settings"

    def __str__(self) -> str:
        return self.name or "District"

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.due_soon_days >= self.recency_days:
            errors["due_soon_days"] = "must be less than recency_days"
        if self.inspection_due_soon_days >= self.inspection_recency_days:
            errors["inspection_due_soon_days"] = "must be less than inspection_recency_days"
        if self.max_attachment_total_mb > self.max_media_mb:
            errors["max_attachment_total_mb"] = "must not exceed max_media_mb"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        self.full_clean(exclude=None)
        return super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "DistrictSettings":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    def derivation_params(self, today=None):
        from .derive import DerivationParams

        return DerivationParams(
            today=today or timezone.localdate(),
            recency_days=self.recency_days,
            due_soon_days=self.due_soon_days,
            inspection_recency_days=self.inspection_recency_days,
            inspection_due_soon_days=self.inspection_due_soon_days,
            exclude_residual_below_target=self.exclude_residual_below_target,
        )


class Hydrant(models.Model):
    """Tier C mutable core; carries the two denormalized caches."""

    SOURCE_MANUAL = "MANUAL"
    SOURCE_CSV = "CSV_IMPORT"
    SOURCE_CHOICES = ((SOURCE_MANUAL, "Manual"), (SOURCE_CSV, "CSV import"))

    asset_tag = models.CharField(max_length=64, unique=True)
    lat = models.FloatField()
    lon = models.FloatField()
    location_description = models.CharField(max_length=255, blank=True, default="")
    manufacturer = models.CharField(max_length=128, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    year_installed = models.PositiveSmallIntegerField(null=True, blank=True)
    main_size_inches = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    outlet_diameter_in = models.DecimalField(max_digits=4, decimal_places=2, default="2.50")
    discharge_coefficient = models.DecimalField(max_digits=4, decimal_places=2, default="0.90")
    ownership = models.CharField(max_length=128, blank=True, default="")

    status = models.CharField(max_length=16, choices=[(s, s) for s in C.HYDRANT_STATUSES],
                              default=C.STATUS_IN_SERVICE)
    current_test_ref = models.ForeignKey(
        "ledger.FlowTest", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_org = models.CharField(max_length=128, blank=True, default="")
    normalized_source_org = models.GeneratedField(
        expression=Func(
            F("source_org"),
            function="",
            template="lower(btrim(regexp_replace(%(expressions)s, '\\s+', ' ', 'g')))",
        ),
        output_field=models.CharField(max_length=128),
        db_persist=True,
    )

    is_sample_data = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lon"], name="hydrant_lat_lon_idx"),
            models.Index(fields=["status"], name="hydrant_status_idx"),
            models.Index(fields=["normalized_source_org"], name="hydrant_norm_org_idx"),
        ]

    def __str__(self) -> str:
        return self.asset_tag


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    org_label = models.CharField(max_length=64, default="District")

    def __str__(self) -> str:
        return f"{self.user.username} ({self.org_label})"


# ---------------------------------------------------------------------------
# Tier A — append-only truth tables (eight exactly)
# ---------------------------------------------------------------------------
class FlowTest(AppendOnlyModel):
    """NFPA 291 flow test.  The FK target is the graded (residual) hydrant."""

    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="tests")
    tested_on = models.DateField()
    static_psi = models.DecimalField(max_digits=6, decimal_places=2)
    residual_psi = models.DecimalField(max_digits=6, decimal_places=2)
    measured_total_gpm = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)

    client_uuid = models.UUIDField(null=True, blank=True, unique=True)
    client_payload_hash = models.CharField(max_length=64, blank=True, default="")

    computed_total_gpm = models.DecimalField(max_digits=10, decimal_places=1)
    pitot_sum_gpm = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    rated_gpm_20 = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    capacity_class = models.CharField(max_length=4, null=True, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    formula_version = models.CharField(max_length=64, default=C.CURRENT_FORMULA_VERSION)

    crew_names = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    supersedes = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by"
    )

    tested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["hydrant", "-tested_on"], name="flowtest_hyd_tested_idx"),
            models.Index(fields=["formula_version"], name="flowtest_formula_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.hydrant_id}@{self.tested_on} QF={self.computed_total_gpm}"


class OutletReading(AppendOnlyModel):
    flow_test = models.ForeignKey(FlowTest, on_delete=models.CASCADE, related_name="outlets")
    flow_hydrant = models.ForeignKey(
        Hydrant, null=True, blank=True, on_delete=models.SET_NULL, related_name="flowed_outlets"
    )
    outlet_label = models.CharField(max_length=32, blank=True, default="")
    outlet_diameter_in = models.DecimalField(max_digits=4, decimal_places=2)
    outlet_coefficient = models.DecimalField(max_digits=4, decimal_places=2)
    pitot_psi = models.DecimalField(max_digits=6, decimal_places=2)
    computed_gpm = models.DecimalField(max_digits=10, decimal_places=1)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"outlet d={self.outlet_diameter_in} p={self.pitot_psi} q={self.computed_gpm}"


class HydrantStatusEvent(AppendOnlyModel):
    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=16, blank=True, default="")
    to_status = models.CharField(max_length=16, choices=[(s, s) for s in C.HYDRANT_STATUSES])
    reason = models.CharField(max_length=255)
    expected_return_on = models.DateField(null=True, blank=True)
    indefinite = models.BooleanField(default=False)
    effective_on = models.DateField()
    reversal_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reversed_by"
    )
    changed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_on", "changed_at", "id"]

    def __str__(self) -> str:
        return f"{self.hydrant_id}: {self.from_status}->{self.to_status} on {self.effective_on}"


class HydrantAttributeChange(AppendOnlyModel):
    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="attribute_changes")
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    change_reason = models.CharField(max_length=32, choices=[(r, r) for r in C.CHANGE_REASONS])
    changed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    import_batch = models.ForeignKey("ledger.ImportBatch", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["id"]


class ParcelAttributeChange(AppendOnlyModel):
    parcel = models.ForeignKey("ledger.Parcel", on_delete=models.CASCADE, related_name="attribute_changes")
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    change_reason = models.CharField(max_length=32, choices=[(r, r) for r in C.CHANGE_REASONS])
    changed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Inspection(AppendOnlyModel):
    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="inspections")
    inspected_on = models.DateField()
    result = models.CharField(max_length=16, choices=[(r, r) for r in C.INSPECTION_RESULTS])
    deficiency_codes = models.JSONField(default=list, blank=True)
    inspector_names = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    inspected_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["hydrant", "-inspected_on"], name="insp_hyd_date_idx")]
        ordering = ["-inspected_on", "-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.hydrant_id} {self.result} on {self.inspected_on}"


class OosAcknowledgement(AppendOnlyModel):
    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="oos_acknowledgements")
    acknowledged_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-acknowledged_at", "-id"]


class SettingsAudit(AppendOnlyModel):
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


# ---------------------------------------------------------------------------
# Tier B — lifecycle tables
# ---------------------------------------------------------------------------
class Parcel(models.Model):
    SOURCE_MANUAL = "MANUAL"
    SOURCE_CSV = "CSV_IMPORT"

    parcel_id = models.CharField(max_length=64, unique=True)
    address = models.CharField(max_length=255, blank=True, default="")
    lat = models.FloatField()
    lon = models.FloatField()
    structure_lat = models.FloatField(null=True, blank=True)
    structure_lon = models.FloatField(null=True, blank=True)
    is_protected_structure = models.BooleanField(default=True)
    structure_type = models.CharField(max_length=64, blank=True, default="")
    source = models.CharField(max_length=16, default=SOURCE_MANUAL)
    source_org = models.CharField(max_length=128, blank=True, default="")
    normalized_source_org = models.GeneratedField(
        expression=Func(
            F("source_org"),
            function="",
            template="lower(btrim(regexp_replace(%(expressions)s, '\\s+', ' ', 'g')))",
        ),
        output_field=models.CharField(max_length=128),
        db_persist=True,
    )
    is_sample_data = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"], name="parcel_lat_lon_idx")]

    def __str__(self) -> str:
        return self.parcel_id

    @property
    def point(self) -> tuple[float, float]:
        """The point used by coverage: structure override when present, else centroid."""
        if self.structure_lat is not None and self.structure_lon is not None:
            return float(self.structure_lat), float(self.structure_lon)
        return float(self.lat), float(self.lon)


class Deficiency(models.Model):
    """The single source of truth for open deficiencies."""

    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="deficiencies")
    inspection = models.ForeignKey(Inspection, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="deficiencies")
    category = models.CharField(max_length=16, choices=[(c, c) for c in C.DEFICIENCY_CODES])
    gates_credit = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True, default="")
    assigned_to = models.CharField(max_length=128, blank=True, default="")
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default=C.DEFICIENCY_OPEN)
    opened_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True, default="")
    resolution_kind = models.CharField(max_length=16, blank=True, default="")
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["hydrant", "status"], name="def_hyd_status_idx")]
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.category} ({self.status})"


class Attachment(models.Model):
    OWNER_FLOW_TEST = "FLOW_TEST"
    OWNER_INSPECTION = "INSPECTION"

    owner_type = models.CharField(max_length=16)
    owner_id = models.PositiveBigIntegerField()
    file = models.FileField(upload_to="attachments/")
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=64, default=C.STORED_IMAGE_CONTENT_TYPE)
    original_content_type = models.CharField(max_length=64)
    capture_meta = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["owner_type", "owner_id"], name="attach_owner_idx")]

    def __str__(self) -> str:
        return f"{self.owner_type}#{self.owner_id} {self.sha256[:12]}"


class MapOverlay(models.Model):
    name = models.CharField(max_length=128)
    geojson_path = models.CharField(max_length=255)
    feature_count = models.PositiveIntegerField()
    byte_size = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        ordering = ["-uploaded_at", "-id"]


class CoverageRun(models.Model):
    status = models.CharField(max_length=16, default=C.RUN_STATUS_RUNNING)

    recency_days = models.PositiveIntegerField()
    inspection_recency_days = models.PositiveIntegerField()
    credit_radius_ft = models.FloatField()
    exclude_residual_below_target = models.BooleanField()
    settings_echo = models.JSONField(default=dict)
    parcel_filter = models.JSONField(null=True, blank=True)
    params_sha256 = models.CharField(max_length=64)
    core_params_sha256 = models.CharField(max_length=64)
    snapshot_ts = models.DateTimeField(null=True, blank=True)
    formula_versions_present = models.JSONField(default=list, blank=True)

    worker_id = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)

    hydrant_count = models.PositiveIntegerField(default=0)
    protected_parcel_count = models.PositiveIntegerField(default=0)
    pair_count = models.PositiveIntegerField(default=0)
    in_radius_count = models.PositiveIntegerField(default=0)
    covered_count = models.PositiveIntegerField(default=0)
    credited_band_counts = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status"], name="covrun_status_idx")]

    def __str__(self) -> str:
        return f"coverage run {self.pk} ({self.status})"


class CoverageResult(models.Model):
    run = models.ForeignKey(CoverageRun, on_delete=models.CASCADE, related_name="results")
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="coverage_results")
    hydrant = models.ForeignKey(Hydrant, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="coverage_results")
    distance_ft = models.FloatField()
    district_credited = models.BooleanField(default=False)
    district_credited_gpm = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    exclusion_code = models.CharField(max_length=32, null=True, blank=True)
    expired = models.BooleanField(default=False)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["run", "parcel", "hydrant"], condition=Q(hydrant__isnull=False),
                             name="uniq_run_parcel_hydrant"),
            UniqueConstraint(fields=["run", "parcel"], condition=Q(hydrant__isnull=True),
                             name="uniq_run_parcel_nullhydrant"),
        ]
        indexes = [models.Index(fields=["run", "parcel"], name="covres_run_parcel_idx")]


class EvidencePack(models.Model):
    run = models.ForeignKey(CoverageRun, on_delete=models.PROTECT, related_name="packs")
    status = models.CharField(max_length=16, default=C.PACK_STATUS_RENDERING)
    snapshot = models.JSONField(default=dict)
    zip_path = models.CharField(max_length=255, blank=True, default="")
    pdf_path = models.CharField(max_length=255, blank=True, default="")
    byte_size = models.PositiveBigIntegerField(default=0)
    pdf_sha256 = models.CharField(max_length=64, blank=True, default="")
    csv_zip_sha256 = models.CharField(max_length=64, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    row_counts = models.JSONField(default=dict, blank=True)
    render_stack_version = models.CharField(max_length=64, blank=True, default="")
    formula_versions_present = models.JSONField(default=list, blank=True)
    include_attachments = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=64, blank=True, default="")
    # The same DB-backed lease discipline as coverage runs (§8): the reaper
    # keys on an expired lease, never wall-clock age.
    worker_id = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"pack {self.pk} ({self.status})"


class ImportBatch(models.Model):
    KIND_HYDRANTS = "hydrants"
    KIND_PARCELS = "parcels"
    KIND_FLOW_TESTS = "flow-tests"
    KIND_CHOICES = ((KIND_HYDRANTS, "hydrants"), (KIND_PARCELS, "parcels"), (KIND_FLOW_TESTS, "flow tests"))
    MODE_DRY_RUN = "DRY_RUN"
    MODE_COMMIT = "COMMIT"

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    file_path = models.CharField(max_length=255, blank=True, default="")
    supplier = models.CharField(max_length=128, blank=True, default="")
    mode = models.CharField(max_length=16, default=MODE_DRY_RUN)
    commit_token_hash = models.CharField(max_length=64, blank=True, default="")
    allow_overwrite = models.BooleanField(default=False)
    rows_ok = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    is_sample_data = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def token_expired(self) -> bool:
        if not self.created_at:
            return True
        return (timezone.now() - self.created_at).total_seconds() > 24 * 60 * 60


class NotificationOutbox(models.Model):
    kind = models.CharField(max_length=32, choices=[(k, k) for k in C.OUTBOX_KINDS])
    hydrant = models.ForeignKey(Hydrant, on_delete=models.CASCADE, related_name="outbox_rows")
    as_of_date = models.DateField()
    payload = models.JSONField(default=dict, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    created_by_job = models.CharField(max_length=64, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["hydrant", "kind", "as_of_date"], name="uniq_outbox_dedup"),
        ]
        ordering = ["-created_at", "-id"]


class LoginAttempt(models.Model):
    SURFACE_FORM = "form"
    SURFACE_BASIC = "basic"
    SURFACE_ADMIN = "admin"
    SURFACE_CHOICES = ((SURFACE_FORM, "form"), (SURFACE_BASIC, "basic"), (SURFACE_ADMIN, "admin"))

    username = models.CharField(max_length=150, blank=True, default="")
    surface = models.CharField(max_length=16, choices=SURFACE_CHOICES)
    success = models.BooleanField(default=False)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["username", "at"], name="login_user_at_idx")]


class AppSetting(models.Model):
    """Key/value store for markers and the DB-persisted demo secret key."""

    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.key

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key: str, value: str) -> "AppSetting":
        row, _ = cls.objects.update_or_create(key=key, defaults={"value": value})
        return row

    @classmethod
    def marker_set(cls, key: str) -> bool:
        return cls.objects.filter(key=key).exists()


class MediaAudit(models.Model):
    """Append-only by code (no routes); written by the admin attachment delete."""

    attachment = models.ForeignKey(Attachment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    attachment_ref = models.PositiveBigIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]


class ExportAudit(models.Model):
    """Append-only by code (no routes); written by every bulk export."""

    export_path = models.CharField(max_length=255)
    kind = models.CharField(max_length=64, blank=True, default="")
    row_count = models.PositiveIntegerField(default=0)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def new_worker_id() -> uuid.UUID:
    return uuid.uuid4()


def settings_singleton() -> DistrictSettings:
    return DistrictSettings.load()


def user_org_label(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    profile = getattr(user, "profile", None)
    return profile.org_label if profile else "District"


def settings_dump(district: DistrictSettings) -> dict:
    return {
        "name": district.name,
        "iso_community_id": district.iso_community_id,
        "state": district.state,
        "time_zone": district.time_zone,
        "recency_days": district.recency_days,
        "due_soon_days": district.due_soon_days,
        "inspection_recency_days": district.inspection_recency_days,
        "inspection_due_soon_days": district.inspection_due_soon_days,
        "credit_radius_ft": district.credit_radius_ft,
        "exclude_residual_below_target": district.exclude_residual_below_target,
        "max_media_mb": district.max_media_mb,
        "max_attachment_total_mb": district.max_attachment_total_mb,
    }
