"""``deactivate_demo_users_on_first_real_row`` — the named mechanism.

A ``post_save`` receiver on Hydrant, FlowTest, OutletReading, Inspection and
Parcel: when a non-sample row is committed and the ``demo_deactivated_at``
marker is absent, all demo users are deactivated **in the same transaction**
and the marker is written (advisory-lock guarded, idempotent).  Signals run
inside the model save's transaction, so the invariant — the published-password
account never persists alongside real data — is transactional.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import connection, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AppSetting, FlowTest, Hydrant, Inspection, OutletReading, Parcel

DEMO_DEACTIVATED_MARKER = "demo_deactivated_at"
DEMO_USERNAMES = ("demo-editor", "demo-viewer", "demo-admin")
_DEACTIVATION_LOCK_KEY = 918_273


def deactivate_demo_users(now=None) -> int:
    """Deactivate demo accounts and stamp the marker (idempotent)."""
    from django.utils import timezone

    stamp = (now or timezone.now()).isoformat()
    updated = User.objects.filter(username__in=DEMO_USERNAMES, is_active=True).update(is_active=False)
    AppSetting.set(DEMO_DEACTIVATED_MARKER, stamp)
    return updated


def _is_sample_row(instance) -> bool:
    """Rows without their own flag inherit the owning record's (OutletReading)."""
    flag = getattr(instance, "is_sample_data", None)
    if flag is not None:
        return bool(flag)
    owner = getattr(instance, "flow_test", None)
    return bool(getattr(owner, "is_sample_data", False))


@receiver(post_save, sender=Hydrant)
@receiver(post_save, sender=FlowTest)
@receiver(post_save, sender=OutletReading)
@receiver(post_save, sender=Inspection)
@receiver(post_save, sender=Parcel)
def deactivate_demo_users_on_first_real_row(sender, instance, created, **kwargs):
    if not created:
        return
    if _is_sample_row(instance):
        return
    if AppSetting.marker_set(DEMO_DEACTIVATED_MARKER):
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [_DEACTIVATION_LOCK_KEY])
            acquired = cursor.fetchone()[0]
        if not acquired:
            return
        # Re-check inside the lock: a concurrent writer may have stamped it.
        if AppSetting.marker_set(DEMO_DEACTIVATED_MARKER):
            return
        deactivate_demo_users()
