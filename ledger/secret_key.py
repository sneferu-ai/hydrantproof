"""Runtime resolution of the Django secret key.

``hydrantproof.settings`` keeps the *build-time* ephemeral constant so that
``collectstatic`` and ``bake_render_stack`` can run during the image build
without a database.  A serving process must never sign sessions and CSRF
tokens with a constant published in the source tree, so the serving entry
point (``hydrantproof/wsgi.py`` / ``asgi.py``) resolves the key at import:

* an explicit ``DJANGO_SECRET_KEY`` environment variable wins (nothing to do);
* otherwise a key persisted in ``AppSetting`` (written by ``manage.py
  ensure_secret``, which the entrypoint runs before gunicorn) is adopted — a
  bare ``gunicorn hydrantproof.wsgi:application`` start after the first boot
  serves with the real key, not the public constant;
* when neither exists the process says so loudly (CRITICAL log), and the
  entrypoint's ``check_posture`` gate refuses to serve that posture.

Resolution never raises: a database that is not migrated yet (``migrate``,
``collectstatic``, a fresh container) must not break the command that is
about to create the key.
"""
from __future__ import annotations

import logging
import os

from django.conf import settings

logger = logging.getLogger("ledger.secret_key")

SECRET_KEY_NAME = "django_secret_key"


def resolve_persisted_secret() -> str | None:
    """The DB-persisted key, ``""`` when absent, ``None`` when unreadable.

    Never raises: a database that is not migrated yet (``migrate``,
    ``collectstatic``, a fresh container) must not break the command that is
    about to create the key.
    """
    try:
        from .models import AppSetting

        row = AppSetting.objects.filter(key=SECRET_KEY_NAME).first()
        return (row.value or "") if row is not None else ""
    except Exception:  # database not migrated / unreachable / table missing
        return None


def ensure_runtime_secret() -> str:
    """Adopt the persisted key when the env var is absent.  Never raises."""
    try:
        env_key = os.environ.get("DJANGO_SECRET_KEY", "")
        if env_key:
            return env_key
        persisted = resolve_persisted_secret()
        if persisted:
            if settings.SECRET_KEY != persisted:
                settings.SECRET_KEY = persisted
                settings.USING_EPHEMERAL_SECRET_KEY = False
                logger.info("secret key: adopted the DB-persisted key")
            return persisted
        if persisted is None:
            # The store is unreadable — cannot judge; the health endpoint and
            # the entrypoint's posture gate will surface the database itself.
            logger.warning("secret key: could not read the persisted key (database unavailable)")
            return ""
        if getattr(settings, "USING_EPHEMERAL_SECRET_KEY", False):
            logger.critical(
                "SECRET_KEY is the public build-time constant: sessions and CSRF tokens are "
                "forgeable. Start through ./entrypoint.sh (which persists a real key) or set "
                "DJANGO_SECRET_KEY."
            )
        return ""
    except Exception:  # pragma: no cover - never break app startup over a log line
        return ""
