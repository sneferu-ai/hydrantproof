"""Code constants and fixed strings for HydrantProof.

Policy lives in ``DistrictSettings`` (data); arithmetic and vocabulary live
here (code).  Two constant classes are labelled distinctly and the label
travels into every artifact:

* **Grounded** — the arithmetic of the NFPA 291 method as the specification
  pins it (``DISCHARGE_CONSTANT``, ``TARGET_RESIDUAL_PSI``, ``DROP_EXPONENT``).
* **Provisional, verification-gated** — district policy pending verification
  against a licensed NFPA 291 edition and current public FSRS documentation
  (coefficient presets, extrapolation factor, trend threshold, reporting
  bands, capacity colours).  Every pack prints the provisional banner.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Grounded arithmetic (NFPA 291 method as pinned by the specification)
# ---------------------------------------------------------------------------
DISCHARGE_CONSTANT = 29.83          # q = 29.83 * c * d^2 * sqrt(p)  [gpm]
TARGET_RESIDUAL_PSI = 20.0
DROP_EXPONENT = 0.54

# ---------------------------------------------------------------------------
# Provisional, verification-gated code constants
# ---------------------------------------------------------------------------
COEFFICIENT_PRESETS = {
    "smooth": 0.90,
    "sharp-edged": 0.80,
    "square-projecting": 0.70,
}
MAX_EXTRAPOLATION_FACTOR = 4.0      # [TBD: district policy; verify against licensed NFPA 291]
TREND_DECLINE_PCT = 15.0            # [TBD: district policy; informational only, never gates credit]

DISTRIBUTION_BANDS = (
    ("3+", 3),   # [TBD: verify against current public FSRS documentation]
    ("2", 2),
    ("1", 1),
    ("0", 0),
)
FLOW_BAND_PROXY = (
    ("1000+", 1000.0),   # [TBD: verify thresholds]
    ("500-999", 500.0),
    ("1-499", 1.0),
    ("NONE", 0.0),
)
CAPACITY_CLASSES = ("AA", "A", "B", "C")
CAPACITY_CLASS_COLORS = {
    "AA": "#7fc4e8",   # light blue
    "A": "#4caf50",    # green
    "B": "#ff9800",    # orange [TBD: verify colour scheme]
    "C": "#ec4e42",    # red — one step lighter than stock so the chip's ink letter clears 4.5:1
    None: "#9e9e9e",   # gray "Unclassified"
}

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_TOTAL_GPM = 100_000
MAX_OUTLETS_PER_TEST = 4
MAX_PAIRS_PER_RUN = 1_000_000
MAX_STATIC_PSI = 500.0
MAX_PITOT_PSI = 200.0
MAX_ATTACHMENTS_PER_RECORD = 4
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
MAX_PARCEL_IDS = 50_000
MAX_OVERLAY_BYTES = 5 * 1024 * 1024
MAX_OVERLAY_FEATURES = 20_000
ALLOWED_IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
STORED_IMAGE_CONTENT_TYPE = "image/jpeg"
STORED_IMAGE_MAX_PX = 1600

CURRENT_FORMULA_VERSION = "nfpa291-pct-drop-v1"
KNOWN_FORMULA_VERSIONS = (CURRENT_FORMULA_VERSION,)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
STATUS_IN_SERVICE = "IN_SERVICE"
STATUS_OUT_OF_SERVICE = "OUT_OF_SERVICE"
STATUS_RETIRED = "RETIRED"
HYDRANT_STATUSES = (STATUS_IN_SERVICE, STATUS_OUT_OF_SERVICE, STATUS_RETIRED)

STATUS_TRANSITIONS = {
    (STATUS_IN_SERVICE, STATUS_OUT_OF_SERVICE),
    (STATUS_OUT_OF_SERVICE, STATUS_IN_SERVICE),
    (STATUS_IN_SERVICE, STATUS_RETIRED),
    (STATUS_OUT_OF_SERVICE, STATUS_RETIRED),
    (STATUS_RETIRED, STATUS_IN_SERVICE),
    (STATUS_RETIRED, STATUS_OUT_OF_SERVICE),
}
TRANSITIONS_REQUIRING_RETURN_FIELDS = {
    (STATUS_IN_SERVICE, STATUS_OUT_OF_SERVICE),
    (STATUS_RETIRED, STATUS_OUT_OF_SERVICE),
}
TRANSITIONS_REQUIRING_REASON = {
    (STATUS_RETIRED, STATUS_IN_SERVICE),
    (STATUS_RETIRED, STATUS_OUT_OF_SERVICE),
}

RECENCY_CURRENT = "CURRENT"
RECENCY_DUE_SOON = "DUE_SOON"
RECENCY_OVERDUE = "OVERDUE"
RECENCY_NEVER_TESTED = "NEVER_TESTED"
RECENCY_NEVER_INSPECTED = "NEVER_INSPECTED"

READINESS_READY = "READY"
READINESS_WATCH = "WATCH"
READINESS_NOT_READY = "NOT_READY"
READINESS_STATES = (READINESS_READY, READINESS_WATCH, READINESS_NOT_READY)

GATING_FLAG_ORDER = (
    "OUT_OF_SERVICE",
    "EXPIRED_OOS",
    "UNTESTED",
    "TEST_STALE",
    "INSPECTION_FAIL",
    "NEVER_INSPECTED",
    "INSPECTION_OVERDUE",
    "OPEN_DEFICIENCY",
)

ATTENTION_FLAGS = (
    "TEST_DUE_SOON",
    "INSPECTION_DUE_SOON",
    "FLOW_DEGRADING",
    "RATED_FLOW_LOST",
)

TREND_UNRATED = "UNRATED"
TREND_RATED_FLOW_LOST = "RATED_FLOW_LOST"
TREND_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
TREND_DEGRADING = "DEGRADING"
TREND_STABLE = "STABLE"

# Warnings, ordered exactly as they are serialized.
WARNING_STATIC_BELOW_TARGET = "STATIC_BELOW_TARGET"
WARNING_RESIDUAL_BELOW_TARGET = "RESIDUAL_BELOW_TARGET"
WARNING_EXCESSIVE_EXTRAPOLATION = "EXCESSIVE_EXTRAPOLATION"
WARNING_METER_AND_PITOT_PRESENT = "METER_AND_PITOT_PRESENT"
WARNING_METER_PITOT_DIVERGENT = "METER_PITOT_DIVERGENT"
WARNING_DUPLICATE_READING = "DUPLICATE_READING"
WARNING_ORDER = (
    WARNING_STATIC_BELOW_TARGET,
    WARNING_RESIDUAL_BELOW_TARGET,
    WARNING_EXCESSIVE_EXTRAPOLATION,
    WARNING_METER_AND_PITOT_PRESENT,
    WARNING_METER_PITOT_DIVERGENT,
    WARNING_DUPLICATE_READING,
)
METER_PITOT_DIVERGENCE_PCT = 10.0

EXCLUSION_RETIRED = "RETIRED"
EXCLUSION_OUT_OF_SERVICE = "OUT_OF_SERVICE"
EXCLUSION_UNTESTED = "UNTESTED"
EXCLUSION_TEST_STALE = "TEST_STALE"
EXCLUSION_INSPECTION_FAIL = "INSPECTION_FAIL"
EXCLUSION_STATIC_BELOW_TARGET = "STATIC_BELOW_TARGET"
EXCLUSION_EXCESSIVE_EXTRAPOLATION = "EXCESSIVE_EXTRAPOLATION"
EXCLUSION_FORMULA_VERSION_MISMATCH = "FORMULA_VERSION_MISMATCH"
EXCLUSION_RESIDUAL_BELOW_TARGET = "RESIDUAL_BELOW_TARGET"
EXCLUSION_NO_HYDRANT_IN_RADIUS = "NO_HYDRANT_IN_RADIUS"
# Ordered precedence — first match wins (specification §6).
EXCLUSION_PRECEDENCE = (
    EXCLUSION_RETIRED,
    EXCLUSION_OUT_OF_SERVICE,
    EXCLUSION_UNTESTED,
    EXCLUSION_TEST_STALE,
    EXCLUSION_INSPECTION_FAIL,
    EXCLUSION_STATIC_BELOW_TARGET,
    EXCLUSION_EXCESSIVE_EXTRAPOLATION,
    EXCLUSION_FORMULA_VERSION_MISMATCH,
    EXCLUSION_RESIDUAL_BELOW_TARGET,
)

INSPECTION_OK = "OK"
INSPECTION_DEFICIENCY = "DEFICIENCY"
INSPECTION_FAIL = "FAIL"
INSPECTION_RESULTS = (INSPECTION_OK, INSPECTION_DEFICIENCY, INSPECTION_FAIL)

DEFICIENCY_CODES = ("CAP", "CHAIN", "NOZZLE", "VALVE", "LEAK", "PAINT", "ACCESS", "OBSTRUCTION", "OTHER")

DEFICIENCY_OPEN = "OPEN"
DEFICIENCY_RESOLVED = "RESOLVED"
RESOLUTION_MANUAL = "manual"
RESOLUTION_AUTO_OK = "auto_ok"

CHANGE_REASONS = ("manual", "import", "import_overwrite_conflict", "ownership_transfer")

OUTBOX_KINDS = ("OOS_EXPIRED", "TEST_OVERDUE", "INSPECTION_OVERDUE")
OUTBOX_MAX_ATTEMPTS = 5
OUTBOX_TIMEOUT_S = 10

RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_COMPLETE = "COMPLETE"
RUN_STATUS_FAILED = "FAILED"
PACK_STATUS_RENDERING = "RENDERING"
PACK_STATUS_COMPLETE = "COMPLETE"
PACK_STATUS_FAILED = "FAILED"
JOB_LEASE_SECONDS = 120
JOB_HEARTBEAT_SECONDS = 60
JOB_ADVISORY_LOCK_KEY = 4242

FAILURE_PAIR_COUNT_EXCEEDED = "pair_count_exceeded"
FAILURE_MEDIA_BUDGET_EXCEEDED = "media_budget_exceeded"
FAILURE_ORPHANED = "orphaned"

DEFAULT_RECENCY_DAYS = 365
DEFAULT_DUE_SOON_DAYS = 90
DEFAULT_INSPECTION_RECENCY_DAYS = 365
DEFAULT_INSPECTION_DUE_SOON_DAYS = 90
DEFAULT_CREDIT_RADIUS_FT = 1000.0
DEFAULT_MAX_MEDIA_MB = 200
DEFAULT_MAX_ATTACHMENT_TOTAL_MB = 80

OOS_ACK_AMBER_DAYS = 30

# ---------------------------------------------------------------------------
# Fixed artifact strings (mandatory, not decorative)
# ---------------------------------------------------------------------------
DISCLAIMER = (
    "This worksheet is prepared by the district using HydrantProof from district-maintained records. It is not issued by, endorsed by, or affiliated with ISO (Verisk) or NFPA. Public Protection Classifications are determined solely by ISO. Flow-test frequency and credit criteria vary — confirm with your ISO field representative and your authority having jurisdiction."
)
PROVISIONAL_BANNER = (
    "Computed with provisional constants (coefficient presets, extrapolation factor, attribution rule, distribution bands, flow-band proxy, trend threshold). Verify against a licensed current NFPA 291 edition and current FSRS documentation before submitting to a surveyor."
)
GAP_SUMMARY_DISCLAIMER = (
    "District-computed gap summary against publicly described FSRS water-supply categories. "
    "NOT a score. NOT ISO output. Point values are licensed and are not computed."
)
VERIFY_IN_FIELD = "Field-verify status, location, and expected flow before relying on this record."
ESCALATION_EXPIRED_OOS = "PAST EXPECTED RETURN — re-verify immediately"
ARTIFACT_LABEL = (
    "District-prepared from district-maintained records. Integrity self-attested by SHA-256 "
    "manifest — not cryptographically signed."
)
SINGLE_TEST_SNAPSHOT_NOTE = (
    "A rated flow at 20 psi residual is a snapshot of one test, not a main-capacity model."
)
CENTROID_PROXY_NOTE = (
    "Coverage is computed from parcel centroids or optional structure overrides; a centroid is "
    "a proxy whose error on large rural parcels can exceed the credit radius."
)
ATTRIBUTION_RULE_NOTE = (
    "Attribution rule (provisional): a test's credit attaches to the hydrant where static and "
    "residual pressures were read (the graded hydrant); the flowed-hydrant alternative was "
    "considered and is preserved in outlet tags for re-attribution via a formula-version bump."
)
SUBTARGET_RISK_NOTE = (
    "Sub-target-residual tests are credited by default; the percentage-drop extrapolation is "
    "conservative there (Q_R < Q_F)."
)
DISTRIBUTION_BANDS_NOTE = (
    "Distribution bands are district-chosen reporting bands pending verification against "
    "current public FSRS documentation."
)
FLOW_BAND_PROXY_LABEL = "district-computed flow-band proxy — not the FSRS distribution criterion"
FORBIDDEN_PHRASES = ("ISO-compliant", "certified", "guaranteed", "endorsed")

MANIFEST_NAME = "manifest.txt"
PACK_ZIP_NAME = "pack.zip"
PACK_PDF_NAME = "pack.pdf"

DISPATCH_COLUMNS = (
    "asset_tag",
    "latitude",
    "longitude",
    "location_description",
    "status",
    "oos_reason",
    "oos_expected_return_on",
    "oos_expired",
    "oos_acknowledged_at",
    "open_deficiency",
    "latest_tested_on",
    "latest_rated_gpm_20",
    "nfpa_class",
    "recency_flag",
    "inspection_recency_flag",
    "test_warnings",
    "readiness",
    "trend",
    "verify_in_field",
    "escalation",
    "disclaimer",
    "data_as_of_utc",
)
