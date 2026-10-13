"""DRF serializers.

Read serializers surface the canonical derivation (readiness, trend, capacity
class, recency flags, exclusion code) computed by ``ledger.derive`` through
``ledger.services``; write serializers validate inputs and hand off to the
transactional service layer.
"""
from __future__ import annotations

from rest_framework import serializers

from . import constants as C
from .geo import valid_latitude, valid_longitude
from .models import (
    Attachment,
    CoverageResult,
    CoverageRun,
    Deficiency,
    DistrictSettings,
    EvidencePack,
    FlowTest,
    Hydrant,
    HydrantStatusEvent,
    ImportBatch,
    Inspection,
    MapOverlay,
    OosAcknowledgement,
    OutletReading,
    Parcel,
)


def _check_coordinates(lat, lon):
    if not valid_latitude(lat):
        raise serializers.ValidationError({"lat": "latitude must be between -80 and 80"})
    if not valid_longitude(lon):
        raise serializers.ValidationError({"lon": "longitude must be between -180 and 180"})


# ---------------------------------------------------------------------------
# District settings
# ---------------------------------------------------------------------------
class DistrictSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DistrictSettings
        fields = (
            "name", "iso_community_id", "state", "time_zone",
            "recency_days", "due_soon_days",
            "inspection_recency_days", "inspection_due_soon_days",
            "credit_radius_ft", "exclude_residual_below_target",
            "max_media_mb", "max_attachment_total_mb",
        )

    def validate(self, attrs):
        merged = {**{f: getattr(self.instance, f, None) for f in self.fields}, **attrs}
        if merged.get("due_soon_days", 0) >= merged.get("recency_days", 0):
            raise serializers.ValidationError({"due_soon_days": "must be less than recency_days"})
        if merged.get("inspection_due_soon_days", 0) >= merged.get("inspection_recency_days", 0):
            raise serializers.ValidationError(
                {"inspection_due_soon_days": "must be less than inspection_recency_days"}
            )
        if merged.get("max_attachment_total_mb", 0) > merged.get("max_media_mb", 0):
            raise serializers.ValidationError(
                {"max_attachment_total_mb": "must not exceed max_media_mb"}
            )
        return attrs


# ---------------------------------------------------------------------------
# Hydrants
# ---------------------------------------------------------------------------
class HydrantSerializer(serializers.ModelSerializer):
    """Read serializer carrying the canonical derivation."""

    readiness = serializers.SerializerMethodField()
    gating_flags = serializers.SerializerMethodField()
    attention_flags = serializers.SerializerMethodField()
    trend = serializers.SerializerMethodField()
    capacity_class = serializers.SerializerMethodField()
    test_recency_flag = serializers.SerializerMethodField()
    inspection_recency_flag = serializers.SerializerMethodField()
    exclusion_code = serializers.SerializerMethodField()
    district_credit_eligible = serializers.SerializerMethodField()
    open_deficiency = serializers.SerializerMethodField()
    open_deficiency_codes = serializers.SerializerMethodField()
    oos_expired = serializers.SerializerMethodField()
    oos_acknowledged_at = serializers.SerializerMethodField()
    latest_tested_on = serializers.SerializerMethodField()
    latest_rated_gpm_20 = serializers.SerializerMethodField()
    latest_computed_total_gpm = serializers.SerializerMethodField()
    test_warnings = serializers.SerializerMethodField()
    days_test_overdue = serializers.SerializerMethodField()
    days_inspection_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Hydrant
        fields = (
            "id", "asset_tag", "lat", "lon", "location_description",
            "manufacturer", "model", "year_installed", "main_size_inches",
            "outlet_diameter_in", "discharge_coefficient", "ownership",
            "status", "source", "source_org", "is_sample_data", "notes",
            "created_at", "updated_at",
            "readiness", "gating_flags", "attention_flags", "trend", "capacity_class",
            "test_recency_flag", "inspection_recency_flag", "exclusion_code",
            "district_credit_eligible", "open_deficiency", "open_deficiency_codes",
            "oos_expired", "oos_acknowledged_at", "latest_tested_on",
            "latest_rated_gpm_20", "latest_computed_total_gpm", "test_warnings",
            "days_test_overdue", "days_inspection_overdue",
        )

    # -- derivation lookups -------------------------------------------------
    def _derived(self, obj):
        derivations = self.context.get("derivations") or {}
        return derivations.get(obj.pk)

    def get_readiness(self, obj):
        d = self._derived(obj)
        return d.readiness if d else None

    def get_gating_flags(self, obj):
        d = self._derived(obj)
        return list(d.gating_flags) if d else []

    def get_attention_flags(self, obj):
        d = self._derived(obj)
        return list(d.attention_flags) if d else []

    def get_trend(self, obj):
        d = self._derived(obj)
        return d.trend if d else None

    def get_capacity_class(self, obj):
        d = self._derived(obj)
        return d.capacity_class if d else None

    def get_test_recency_flag(self, obj):
        d = self._derived(obj)
        return d.test_recency_flag if d else None

    def get_inspection_recency_flag(self, obj):
        d = self._derived(obj)
        return d.inspection_recency_flag if d else None

    def get_exclusion_code(self, obj):
        d = self._derived(obj)
        return d.exclusion_code if d else None

    def get_district_credit_eligible(self, obj):
        d = self._derived(obj)
        return bool(d.district_credit_eligible) if d else False

    def get_open_deficiency(self, obj):
        d = self._derived(obj)
        return bool(d.open_deficiency) if d else False

    def get_open_deficiency_codes(self, obj):
        d = self._derived(obj)
        return list(d.open_deficiency_codes) if d else []

    def get_oos_expired(self, obj):
        d = self._derived(obj)
        return bool(d.oos_expired) if d else False

    def get_oos_acknowledged_at(self, obj):
        d = self._derived(obj)
        return d.oos_acknowledged_at if d else None

    def get_latest_tested_on(self, obj):
        d = self._derived(obj)
        return d.latest_tested_on if d else None

    def get_latest_rated_gpm_20(self, obj):
        d = self._derived(obj)
        return d.rated_gpm_20 if d else None

    def get_latest_computed_total_gpm(self, obj):
        d = self._derived(obj)
        return d.computed_total_gpm if d else None

    def get_test_warnings(self, obj):
        d = self._derived(obj)
        return list(d.warnings) if d else []

    def get_days_test_overdue(self, obj):
        d = self._derived(obj)
        return d.days_test_overdue if d else None

    def get_days_inspection_overdue(self, obj):
        d = self._derived(obj)
        return d.days_inspection_overdue if d else None


class HydrantCreateSerializer(serializers.Serializer):
    """POST /api/v1/hydrants/ — accepts the initially-OOS fields."""

    asset_tag = serializers.CharField(max_length=64)
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    location_description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    manufacturer = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    model = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    year_installed = serializers.IntegerField(required=False, allow_null=True)
    main_size_inches = serializers.DecimalField(max_digits=4, decimal_places=2, required=False, allow_null=True)
    outlet_diameter_in = serializers.DecimalField(max_digits=4, decimal_places=2, required=False, default="2.50")
    discharge_coefficient = serializers.DecimalField(max_digits=4, decimal_places=2, required=False, default="0.90")
    ownership = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    source_org = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    initial_status = serializers.ChoiceField(choices=C.HYDRANT_STATUSES, required=False,
                                             default=C.STATUS_IN_SERVICE)
    initial_status_reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    expected_return_on = serializers.DateField(required=False, allow_null=True)
    indefinite = serializers.BooleanField(required=False, default=False)
    effective_on = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        _check_coordinates(attrs["lat"], attrs["lon"])
        if attrs.get("initial_status") == C.STATUS_OUT_OF_SERVICE:
            if not attrs.get("expected_return_on") and not attrs.get("indefinite"):
                raise serializers.ValidationError({
                    "expected_return_on": "required when initial_status=OUT_OF_SERVICE",
                    "indefinite": "required when initial_status=OUT_OF_SERVICE (set true for indefinite)",
                })
            if attrs.get("expected_return_on") and attrs.get("indefinite"):
                raise serializers.ValidationError({
                    "indefinite": "provide expected_return_on or indefinite=true, not both"
                })
        if attrs.get("initial_status") == C.STATUS_RETIRED and not attrs.get("initial_status_reason"):
            raise serializers.ValidationError({"initial_status_reason": "required for a RETIRED hydrant"})
        return attrs


class HydrantUpdateSerializer(serializers.ModelSerializer):
    """PATCH /api/v1/hydrants/{id}/ — attributes only; asset_tag immutable."""

    class Meta:
        model = Hydrant
        fields = (
            "location_description", "manufacturer", "model", "year_installed",
            "main_size_inches", "outlet_diameter_in", "discharge_coefficient",
            "ownership", "notes", "source_org", "lat", "lon",
        )

    def validate(self, attrs):
        if "asset_tag" in self.initial_data:
            raise serializers.ValidationError({"asset_tag": "asset_tag is immutable"})
        lat = attrs.get("lat", self.instance.lat)
        lon = attrs.get("lon", self.instance.lon)
        _check_coordinates(lat, lon)
        return attrs


# ---------------------------------------------------------------------------
# Flow tests
# ---------------------------------------------------------------------------
class OutletReadingInputSerializer(serializers.Serializer):
    outlet_diameter_in = serializers.DecimalField(max_digits=4, decimal_places=2)
    outlet_coefficient = serializers.DecimalField(max_digits=4, decimal_places=2)
    pitot_psi = serializers.DecimalField(max_digits=6, decimal_places=2)
    outlet_label = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    flow_asset_tag = serializers.CharField(max_length=64, required=False, allow_null=True, allow_blank=True)


class OutletReadingSerializer(serializers.ModelSerializer):
    flow_asset_tag = serializers.SerializerMethodField()

    class Meta:
        model = OutletReading
        fields = (
            "id", "outlet_label", "outlet_diameter_in", "outlet_coefficient",
            "pitot_psi", "computed_gpm", "flow_hydrant", "flow_asset_tag",
        )

    def get_flow_asset_tag(self, obj):
        return obj.flow_hydrant.asset_tag if obj.flow_hydrant_id else None


class FlowTestSerializer(serializers.ModelSerializer):
    outlets = OutletReadingSerializer(many=True, read_only=True)
    supersedes = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = FlowTest
        fields = (
            "id", "hydrant", "tested_on", "static_psi", "residual_psi",
            "measured_total_gpm", "computed_total_gpm", "pitot_sum_gpm",
            "rated_gpm_20", "capacity_class", "warnings", "formula_version",
            "crew_names", "notes", "supersedes", "outlets", "client_uuid",
            "tested_by", "created_at", "is_sample_data",
        )


class FlowTestCreateSerializer(serializers.Serializer):
    tested_on = serializers.DateField()
    static_psi = serializers.DecimalField(max_digits=6, decimal_places=2)
    residual_psi = serializers.DecimalField(max_digits=6, decimal_places=2)
    outlets = OutletReadingInputSerializer(many=True, required=False, default=list)
    measured_total_gpm = serializers.DecimalField(
        max_digits=10, decimal_places=1, required=False, allow_null=True
    )
    crew_names = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    supersedes = serializers.IntegerField(required=False, allow_null=True)
    client_uuid = serializers.UUIDField(required=False, allow_null=True)

    def canonical_payload(self) -> dict:
        data = self.validated_data
        return {
            "tested_on": str(data["tested_on"]),
            "static_psi": str(data["static_psi"]),
            "residual_psi": str(data["residual_psi"]),
            "measured_total_gpm": None if data.get("measured_total_gpm") is None else str(data["measured_total_gpm"]),
            "outlets": [
                {
                    "d": str(o["outlet_diameter_in"]),
                    "c": str(o["outlet_coefficient"]),
                    "p": str(o["pitot_psi"]),
                    "label": o.get("outlet_label", ""),
                    "flow": o.get("flow_asset_tag") or "",
                }
                for o in data.get("outlets", [])
            ],
            "crew_names": data.get("crew_names", ""),
            "notes": data.get("notes", ""),
            "supersedes": data.get("supersedes"),
        }


class ComputePreviewSerializer(FlowTestCreateSerializer):
    """POST /api/v1/compute-preview/ — the single authoritative preview."""


# ---------------------------------------------------------------------------
# Inspections / deficiencies
# ---------------------------------------------------------------------------
class InspectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Inspection
        fields = (
            "id", "hydrant", "inspected_on", "result", "deficiency_codes",
            "inspector_names", "notes", "inspected_by", "created_at", "is_sample_data",
        )


class InspectionCreateSerializer(serializers.Serializer):
    inspected_on = serializers.DateField()
    result = serializers.ChoiceField(choices=C.INSPECTION_RESULTS)
    deficiency_codes = serializers.ListField(
        child=serializers.ChoiceField(choices=C.DEFICIENCY_CODES), required=False, default=list
    )
    inspector_names = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class DeficiencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Deficiency
        fields = (
            "id", "hydrant", "inspection", "category", "gates_credit", "note",
            "assigned_to", "due_on", "status", "opened_by", "opened_at",
            "resolved_by", "resolved_at", "resolution_note", "resolution_kind",
            "is_sample_data",
        )


class DeficiencyResolveSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(max_length=255)
    resolved_at = serializers.DateField(required=False, allow_null=True)
    assigned_to = serializers.CharField(max_length=128, required=False, allow_blank=True)
    due_on = serializers.DateField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Status events / acknowledgements
# ---------------------------------------------------------------------------
class HydrantStatusEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = HydrantStatusEvent
        fields = (
            "id", "hydrant", "from_status", "to_status", "reason",
            "expected_return_on", "indefinite", "effective_on", "reversal_of",
            "changed_by", "changed_at",
        )


class StatusEventCreateSerializer(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=C.HYDRANT_STATUSES, required=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    expected_return_on = serializers.DateField(required=False, allow_null=True)
    indefinite = serializers.BooleanField(required=False, default=False)
    effective_on = serializers.DateField(required=False, allow_null=True)
    reversal_of = serializers.IntegerField(required=False, allow_null=True)


class OosAcknowledgementSerializer(serializers.ModelSerializer):
    class Meta:
        model = OosAcknowledgement
        fields = ("id", "hydrant", "acknowledged_by", "acknowledged_at", "note")


class OosAcknowledgementCreateSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Parcels
# ---------------------------------------------------------------------------
class ParcelSerializer(serializers.ModelSerializer):
    point_lat = serializers.SerializerMethodField()
    point_lon = serializers.SerializerMethodField()

    class Meta:
        model = Parcel
        fields = (
            "id", "parcel_id", "address", "lat", "lon", "structure_lat", "structure_lon",
            "is_protected_structure", "structure_type", "source", "source_org",
            "is_sample_data", "point_lat", "point_lon",
        )

    def get_point_lat(self, obj):
        return obj.point[0]

    def get_point_lon(self, obj):
        return obj.point[1]


class ParcelUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parcel
        fields = ("address", "lat", "lon", "structure_lat", "structure_lon",
                  "is_protected_structure", "structure_type")

    def validate(self, attrs):
        lat = attrs.get("lat", self.instance.lat)
        lon = attrs.get("lon", self.instance.lon)
        _check_coordinates(lat, lon)
        if attrs.get("structure_lat") is not None or attrs.get("structure_lon") is not None:
            slat = attrs.get("structure_lat", self.instance.structure_lat)
            slon = attrs.get("structure_lon", self.instance.structure_lon)
            if (slat is None) != (slon is None):
                raise serializers.ValidationError(
                    {"structure_lat": "structure_lat and structure_lon are set together"}
                )
            if slat is not None:
                _check_coordinates(slat, slon)
        return attrs


# ---------------------------------------------------------------------------
# Coverage / packs / overlays / imports / attachments
# ---------------------------------------------------------------------------
class CoverageRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoverageRun
        fields = (
            "id", "status", "recency_days", "inspection_recency_days", "credit_radius_ft",
            "exclude_residual_below_target", "settings_echo", "parcel_filter",
            "params_sha256", "core_params_sha256", "snapshot_ts",
            "formula_versions_present", "hydrant_count", "protected_parcel_count",
            "pair_count", "in_radius_count", "covered_count", "credited_band_counts",
            "started_at", "finished_at", "failure_reason", "created_at", "is_sample_data",
        )


class CoverageResultSerializer(serializers.ModelSerializer):
    parcel_id = serializers.CharField(source="parcel.parcel_id", read_only=True)
    asset_tag = serializers.SerializerMethodField()

    class Meta:
        model = CoverageResult
        fields = (
            "id", "run", "parcel", "parcel_id", "hydrant", "asset_tag", "distance_ft",
            "district_credited", "district_credited_gpm", "exclusion_code", "expired",
        )

    def get_asset_tag(self, obj):
        return obj.hydrant.asset_tag if obj.hydrant_id else None


class EvidencePackSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvidencePack
        fields = (
            "id", "run", "status", "byte_size", "pdf_sha256", "csv_zip_sha256",
            "page_count", "row_counts", "render_stack_version", "formula_versions_present",
            "include_attachments", "failure_reason", "generated_by", "generated_at",
            "created_at", "is_sample_data",
        )


class MapOverlaySerializer(serializers.ModelSerializer):
    class Meta:
        model = MapOverlay
        fields = ("id", "name", "feature_count", "byte_size", "uploaded_by", "uploaded_at")


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = (
            "id", "owner_type", "owner_id", "sha256", "byte_size", "content_type",
            "original_content_type", "capture_meta", "uploaded_by", "created_at",
        )


class ImportBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportBatch
        fields = (
            "id", "kind", "filename", "file_sha256", "supplier", "mode",
            "allow_overwrite", "rows_ok", "rows_rejected", "errors",
            "created_by", "created_at", "committed_at", "is_sample_data",
        )
