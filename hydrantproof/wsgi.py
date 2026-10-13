"""WSGI entry point — gunicorn ``hydrantproof.wsgi:application``.

The secret key is resolved here, at the serving entry point: a process that
serves traffic must never sign sessions and CSRF tokens with the public
build-time constant.  ``ensure_runtime_secret`` adopts the DB-persisted key
(``manage.py ensure_secret``) when the environment does not provide one and
says so loudly when neither exists; management commands that legitimately run
before the key exists (``migrate``, ``collectstatic``) never import this
module.
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hydrantproof.settings")

application = get_wsgi_application()

from ledger.secret_key import ensure_runtime_secret  # noqa: E402  (after app setup)

ensure_runtime_secret()
