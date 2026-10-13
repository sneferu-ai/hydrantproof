"""The notification outbox: idempotent enqueue, env-only drain, ranked list.

``drain_outbox`` reads **only** the static env ``ALERT_WEBHOOK_URL`` (no CLI
URL flag — a runtime-chosen URL is exactly the outbound-call class this
product refuses); a 10 s timeout, at most 5 attempts per row, ``sent_result``
recorded for every outcome.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from . import constants as C
from .models import Hydrant, NotificationOutbox
from .services import derive_hydrants, params_from_settings


def enqueue_notifications(as_of_date=None, *, hydrants=None) -> dict:
    """Idempotent under the unique (hydrant, kind, as_of_date) constraint."""
    as_of_date = as_of_date or timezone.localdate()
    hydrants = list(hydrants if hydrants is not None else Hydrant.objects.all())
    derivations = derive_hydrants(hydrants, params_from_settings(today=as_of_date))
    created = 0
    for hydrant in hydrants:
        derived = derivations[hydrant.pk]
        candidates = []
        if derived.oos_expired:
            candidates.append(("OOS_EXPIRED", {
                "asset_tag": hydrant.asset_tag,
                "days_past_expiry": derived.days_past_expiry,
                "expected_return_on": str(derived.expected_return_on) if derived.expected_return_on else None,
            }))
        if derived.days_test_overdue:
            candidates.append(("TEST_OVERDUE", {
                "asset_tag": hydrant.asset_tag,
                "days_overdue": derived.days_test_overdue,
                "last_tested_on": str(derived.latest_tested_on) if derived.latest_tested_on else None,
            }))
        if derived.days_inspection_overdue:
            candidates.append(("INSPECTION_OVERDUE", {
                "asset_tag": hydrant.asset_tag,
                "days_overdue": derived.days_inspection_overdue,
                "last_inspected_on": str(derived.latest_inspected_on) if derived.latest_inspected_on else None,
            }))
        for kind, payload in candidates:
            _, was_created = NotificationOutbox.objects.get_or_create(
                hydrant=hydrant,
                kind=kind,
                as_of_date=as_of_date,
                defaults={"payload": payload, "created_by_job": "enqueue_notifications"},
            )
            created += 1 if was_created else 0
    return {"as_of_date": str(as_of_date), "created": created}


def _post_json(url: str, payload: dict, timeout: int = C.OUTBOX_TIMEOUT_S) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status_code": response.status}
    except urllib.error.HTTPError as exc:
        return {"status_code": exc.code}
    except Exception as exc:
        return {"error": type(exc).__name__}


def drain_outbox(limit: int = 100) -> dict:
    """Deliver unsent rows to the env-configured webhook. Never raises."""
    url = getattr(settings, "ALERT_WEBHOOK_URL", "") or ""
    rows = NotificationOutbox.objects.filter(sent_at__isnull=True, attempt_count__lt=C.OUTBOX_MAX_ATTEMPTS)
    attempted = sent = failed = 0
    if not url:
        return {"attempted": 0, "sent": 0, "failed": 0, "skipped": "ALERT_WEBHOOK_URL is not configured"}
    for row in rows.order_by("created_at", "id")[:limit]:
        attempted += 1
        result = _post_json(url, {
            "kind": row.kind,
            "hydrant": row.hydrant.asset_tag,
            "as_of_date": str(row.as_of_date),
            "payload": row.payload,
        })
        row.attempt_count += 1
        row.sent_result = result
        if "status_code" in result and 200 <= int(result["status_code"]) < 300:
            row.sent_at = timezone.now()
            sent += 1
        else:
            failed += 1
        row.save(update_fields=["attempt_count", "sent_result", "sent_at"])
    return {"attempted": attempted, "sent": sent, "failed": failed}


def live_notification_rows(limit: int = 200) -> list[dict]:
    """Ranked live notification list: expired OOS first, then overdue tests,
    then overdue inspections; unsent outbox rows surface beside them."""
    today = timezone.localdate()
    hydrants = list(Hydrant.objects.all())
    derivations = derive_hydrants(hydrants, params_from_settings(today=today))
    rows = []
    for hydrant in hydrants:
        derived = derivations[hydrant.pk]
        if derived.oos_expired:
            rows.append({
                "rank": 0,
                "kind": "OOS_EXPIRED",
                "hydrant": hydrant.pk,
                "asset_tag": hydrant.asset_tag,
                "detail": f"{derived.days_past_expiry} day(s) past expected return",
                "days": derived.days_past_expiry,
                "acknowledged": derived.oos_acknowledged_active,
            })
        if derived.days_test_overdue:
            rows.append({
                "rank": 1,
                "kind": "TEST_OVERDUE",
                "hydrant": hydrant.pk,
                "asset_tag": hydrant.asset_tag,
                "detail": f"test overdue by {derived.days_test_overdue} day(s)",
                "days": derived.days_test_overdue,
            })
        if derived.days_inspection_overdue:
            rows.append({
                "rank": 2,
                "kind": "INSPECTION_OVERDUE",
                "hydrant": hydrant.pk,
                "asset_tag": hydrant.asset_tag,
                "detail": f"inspection overdue by {derived.days_inspection_overdue} day(s)",
                "days": derived.days_inspection_overdue,
            })
    rows.sort(key=lambda row: (row["rank"], -(row["days"] or 0), row["asset_tag"]))
    return rows[:limit]
