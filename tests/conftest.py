"""Test bootstrap.

The pinned gate command requires the declared ``db`` service providing
``DATABASE_URL``; when it is unset this module fails loudly rather than
silently skipping the database groups (specification §Test plan).
"""
from __future__ import annotations

import os

import pytest

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL is not set. The HydrantProof test suite requires the declared "
        "PostgreSQL service; run the pinned gate command with DATABASE_URL pointing at it."
    )

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hydrantproof.settings")


@pytest.fixture(autouse=True)
def _static_storage(tmp_path, settings):
    """Tests render templates without a collected static manifest.

    The static root is pointed inside the test's own tmp dir (and created):
    the suite must not depend on a `collectstatic` run in the source tree —
    the image build produces that tree, and WhiteNoise would otherwise warn
    about its absence on every request.
    """
    static_root = tmp_path / "staticfiles"
    static_root.mkdir(parents=True, exist_ok=True)
    settings.STATIC_ROOT = static_root
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    """Keep every test's media writes inside the test's own tmp dir."""
    settings.MEDIA_ROOT = tmp_path / "media"
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)
    return settings.MEDIA_ROOT


# ---------------------------------------------------------------------------
# Users / roles / API clients
# ---------------------------------------------------------------------------
import base64  # noqa: E402

from django.contrib.auth.models import Group, User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


def make_user(username: str, group: str, password: str = "hydrantproof-test-pass"):
    user = User.objects.create_user(username=username, password=password)
    Group.objects.get_or_create(name=group)
    user.groups.add(Group.objects.get(name=group))
    return user


def basic_auth(client: APIClient, username: str, password: str = "hydrantproof-test-pass"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
    return client


@pytest.fixture
def editor_user(db):
    return make_user("editor-1", "editor")


@pytest.fixture
def viewer_user(db):
    return make_user("viewer-1", "viewer")


@pytest.fixture
def admin_user(db):
    return make_user("admin-1", "admin")


@pytest.fixture
def api(db, editor_user):
    client = APIClient()
    return basic_auth(client, editor_user.username)


@pytest.fixture
def viewer_api(db, viewer_user):
    client = APIClient()
    return basic_auth(client, viewer_user.username)


@pytest.fixture
def admin_api(db, admin_user):
    client = APIClient()
    return basic_auth(client, admin_user.username)


@pytest.fixture
def anonymous_api(db):
    return APIClient()
