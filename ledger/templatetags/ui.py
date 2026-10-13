"""Presentation-only template filters.

These translate engine vocabulary into CSS class names and human labels. They
never decide anything: the readiness state, the flags and the numbers all come
from ledger/derive.py, and a filter that changed a value would be a defect.
"""
from __future__ import annotations

from django import template

register = template.Library()

READINESS_SLUGS = {"READY": "ready", "WATCH": "watch", "NOT_READY": "notready"}
READINESS_LABELS = {"READY": "Ready", "WATCH": "Watch", "NOT_READY": "Not ready"}
STATUS_LABELS = {
    "IN_SERVICE": "In service",
    "OUT_OF_SERVICE": "Out of service",
    "RETIRED": "Retired",
}
TREND_LABELS = {
    "STABLE": "Stable",
    "DEGRADING": "Degrading",
    "RATED_FLOW_LOST": "Rated flow lost",
    "INSUFFICIENT_HISTORY": "Insufficient history",
    "UNRATED": "Unrated",
}
RESULT_LABELS = {"OK": "OK", "DEFICIENCY": "Deficiency", "FAIL": "Fail"}


@register.filter
def class_slug(value) -> str:
    """AA -> aa, A -> a, None -> none. The chip class, never the value."""
    if not value:
        return "none"
    return str(value).strip().lower()


@register.filter
def readiness_slug(value: str) -> str:
    return READINESS_SLUGS.get(value, "none")


@register.filter
def readiness_label(value: str) -> str:
    return READINESS_LABELS.get(value, value or "Unknown")


@register.filter
def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value or "")


@register.filter
def trend_label(value: str) -> str:
    return TREND_LABELS.get(value, value or "")


@register.filter
def trend_slug(value: str) -> str:
    if value in ("DEGRADING", "RATED_FLOW_LOST"):
        return "bad"
    if value in ("INSUFFICIENT_HISTORY", "UNRATED"):
        return "quiet"
    return "ok"


@register.filter
def result_label(value: str) -> str:
    return RESULT_LABELS.get(value, value or "")


@register.filter
def flag_label(value: str) -> str:
    """TEST_STALE -> Test stale. The engine's vocabulary, made readable."""
    text = str(value or "").replace("_", " ").strip().lower()
    return text[:1].upper() + text[1:]


@register.filter
def recency_label(value: str) -> str:
    return {
        "CURRENT": "Current",
        "DUE_SOON": "Due soon",
        "OVERDUE": "Overdue",
        "NEVER_TESTED": "Never tested",
        "NEVER_INSPECTED": "Never inspected",
    }.get(value, value or "")


@register.filter
def recency_slug(value: str) -> str:
    if value == "CURRENT":
        return "ok"
    if value == "DUE_SOON":
        return "warn"
    if value in ("OVERDUE", "NEVER_TESTED", "NEVER_INSPECTED"):
        return "bad"
    return "quiet"


@register.filter
def gpm(value, digits=0) -> str:
    """One number, one unit, no guessing: `None` renders an em dash."""
    if value is None or value == "":
        return "\u2014"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "\u2014"
    if digits:
        return f"{number:,.{int(digits)}f}"
    return f"{number:,.0f}"


@register.filter
def pct(part, whole) -> int:
    try:
        total = float(whole)
        if total <= 0:
            return 0
        return int(round(float(part) / total * 100))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def dict_get(obj, key):
    """`{{ district|dict_get:"recency_days" }}` — a model attribute by name.

    Used only by the read-only settings reference, where the field list is data
    (a tuple of key/label/unit/range/note) rather than markup. Returns "" for a
    missing attribute so a renamed field degrades to a blank, never a 500.
    """
    return getattr(obj, str(key), "")
