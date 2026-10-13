"""ASGI entry point (unused by the shipped gunicorn deployment; kept for tooling)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hydrantproof.settings")

application = get_asgi_application()

from ledger.secret_key import ensure_runtime_secret  # noqa: E402  (after app setup)

ensure_runtime_secret()
