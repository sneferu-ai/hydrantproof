"""Authentication surfaces, the bounded throttle, sessions, and /healthz."""
from __future__ import annotations

import base64
import pytest
from django.test import Client
from rest_framework.test import APIClient

from ledger.auth import (
    FAILURE_LIMIT,
    MODE_BLOCKED,
    MODE_CHALLENGE,
    MODE_OK,
    VERIFICATION_CAP,
    throttle_mode,
)
from ledger.health import compute_health
from ledger.models import LoginAttempt

pytestmark = pytest.mark.django_db


def basic(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
    return client


class TestBasicAuthSurface:
    def test_basic_failure_writes_a_login_attempt(self, editor_user):
        client = basic(editor_user.username, "wrong-password")
        assert client.get("/api/v1/hydrants/").status_code == 401
        attempt = LoginAttempt.objects.filter(surface=LoginAttempt.SURFACE_BASIC).first()
        assert attempt is not None
        assert attempt.success is False
        assert attempt.username == editor_user.username

    def test_basic_success_records_success(self, editor_user):
        client = basic(editor_user.username, "hydrantproof-test-pass")
        assert client.get("/api/v1/hydrants/").status_code == 200
        assert LoginAttempt.objects.filter(success=True).exists()

    def test_basic_is_not_accepted_outside_api_v1(self, editor_user):
        client = basic(editor_user.username, "hydrantproof-test-pass")
        response = client.get("/dashboard/")
        assert response.status_code == 302  # anonymous HTML redirects to login

    def test_throttled_username_failure_answers_429_with_retry_after(self, editor_user):
        for _ in range(FAILURE_LIMIT):
            LoginAttempt.objects.create(username=editor_user.username, surface="basic", success=False)
        client = basic(editor_user.username, "wrong-password")
        response = client.get("/api/v1/hydrants/")
        assert response.status_code == 429
        assert int(response["Retry-After"]) > 0

    def test_valid_credentials_are_still_accepted_inside_the_throttle_window(self, editor_user):
        for _ in range(FAILURE_LIMIT):
            LoginAttempt.objects.create(username=editor_user.username, surface="basic", success=False)
        client = basic(editor_user.username, "hydrantproof-test-pass")
        assert client.get("/api/v1/hydrants/").status_code == 200
        # The success resets the username's window.
        assert LoginAttempt.objects.filter(username=editor_user.username, success=True).exists()
        mode, _ = throttle_mode(editor_user.username, None)
        assert mode == MODE_OK

    def test_beyond_the_verification_cap_everything_is_429(self, editor_user):
        for _ in range(VERIFICATION_CAP):
            LoginAttempt.objects.create(username=editor_user.username, surface="basic", success=False)
        mode, _ = throttle_mode(editor_user.username, None)
        assert mode == MODE_BLOCKED
        client = basic(editor_user.username, "hydrantproof-test-pass")
        assert client.get("/api/v1/hydrants/").status_code == 429

    def test_ip_dimension_is_off_unless_proxy_headers_are_trusted(self, editor_user, settings):
        settings.TRUST_PROXY_HEADERS = False
        for _ in range(60):
            LoginAttempt.objects.create(username="other", surface="basic", success=False, ip="10.0.0.9")
        mode, _ = throttle_mode(editor_user.username, "10.0.0.9")
        assert mode == MODE_OK

        settings.TRUST_PROXY_HEADERS = True
        mode, _ = throttle_mode(editor_user.username, "10.0.0.9")
        assert mode == MODE_BLOCKED


class TestCreateUserCommand:
    """The documented account path (test_plan step 21 / §18 README)."""

    def test_createuser_creates_a_grouped_account(self):
        from django.contrib.auth.models import User
        from django.core.management import call_command

        call_command("createuser", "field-op", "--role", "editor",
                     "--password", "Q7-tundra-lantern-93")
        user = User.objects.get(username="field-op")
        assert user.groups.filter(name="editor").exists()
        assert user.check_password("Q7-tundra-lantern-93")


class TestLoginForm:
    def test_get_returns_html_with_a_csrf_token(self, db):
        response = Client().get("/login/")
        assert response.status_code == 200
        assert "csrfmiddlewaretoken" in response.content.decode()

    def test_valid_post_redirects_to_dashboard(self, editor_user):
        client = Client()
        page = client.get("/login/")
        token = page.cookies["csrftoken"].value
        response = client.post(
            "/login/",
            {"username": editor_user.username, "password": "hydrantproof-test-pass",
             "csrfmiddlewaretoken": token},
        )
        assert response.status_code == 302
        assert response["Location"] == "/dashboard/"

    def test_invalid_post_returns_200_with_an_error_and_records_the_attempt(self, editor_user):
        client = Client()
        page = client.get("/login/")
        token = page.cookies["csrftoken"].value
        response = client.post(
            "/login/",
            {"username": editor_user.username, "password": "nope", "csrfmiddlewaretoken": token},
        )
        assert response.status_code == 200
        assert "Invalid username or password" in response.content.decode()
        assert LoginAttempt.objects.filter(surface=LoginAttempt.SURFACE_FORM, success=False).exists()

    def test_logout_ends_the_session(self, editor_user):
        client = Client()
        page = client.get("/login/")
        token = page.cookies["csrftoken"].value
        client.post("/login/", {"username": editor_user.username,
                                "password": "hydrantproof-test-pass",
                                "csrfmiddlewaretoken": token})
        assert client.get("/dashboard/").status_code == 200
        client.post("/logout/", {"csrfmiddlewaretoken": token})
        assert client.get("/dashboard/").status_code == 302


class TestAbsoluteSession:
    def test_session_expires_after_the_absolute_cap(self, editor_user):
        client = Client()
        page = client.get("/login/")
        token = page.cookies["csrftoken"].value
        client.post("/login/", {"username": editor_user.username,
                                "password": "hydrantproof-test-pass",
                                "csrfmiddlewaretoken": token})
        session = client.session
        session["_absolute_started_at"] = 0  # the epoch: far past the 12 h cap
        session.save()
        assert client.get("/dashboard/").status_code == 302


class TestHealthz:
    def test_health_ok_with_migrations_applied(self):
        status, payload = compute_health()
        assert status == 200
        assert payload["status"] == "ok"
        assert payload["checks"] == {"database": "reachable", "migrations": "applied"}

    def test_endpoint_is_anonymous(self, db):
        response = Client().get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["database"] == "reachable"

    def test_cache_control_header_is_five_seconds(self, db):
        response = Client().get("/healthz")
        assert response["Cache-Control"] == "max-age=5"


# ---------------------------------------------------------------------------
# Repair-round regressions (native static review BUG-004/013)
# ---------------------------------------------------------------------------
class TestAdminLoginSurface:
    """Every admin-surface outcome is recorded, successes included."""

    def _staff(self):
        from django.contrib.auth.models import User

        return User.objects.create_superuser("admin-surface", password="hydrantproof-test-pass")

    def test_admin_login_success_is_recorded(self):
        staff = self._staff()
        client = Client()
        response = client.post(
            "/admin/login/",
            {"username": staff.username, "password": "hydrantproof-test-pass"},
        )
        assert response.status_code == 302
        attempt = LoginAttempt.objects.filter(surface=LoginAttempt.SURFACE_ADMIN).first()
        assert attempt is not None
        assert attempt.success is True
        assert attempt.username == staff.username

    def test_admin_login_failure_is_recorded(self):
        self._staff()
        client = Client()
        response = client.post("/admin/login/",
                               {"username": "admin-surface", "password": "wrong-password"})
        assert response.status_code == 200
        assert LoginAttempt.objects.filter(
            surface=LoginAttempt.SURFACE_ADMIN, success=False
        ).exists()

    def test_non_staff_valid_credentials_are_recorded_as_a_failure(self):
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user(username="plain-editor", password="hydrantproof-test-pass")
        Group.objects.get_or_create(name="editor")
        user.groups.add(Group.objects.get(name="editor"))
        client = Client()
        response = client.post(
            "/admin/login/",
            {"username": "plain-editor", "password": "hydrantproof-test-pass"},
        )
        assert response.status_code == 200  # the admin form refuses non-staff
        attempt = LoginAttempt.objects.filter(surface=LoginAttempt.SURFACE_ADMIN).first()
        assert attempt is not None
        assert attempt.success is False

    def test_admin_success_resets_the_username_window(self):
        staff = self._staff()
        for _ in range(FAILURE_LIMIT):
            LoginAttempt.objects.create(username=staff.username, surface="admin", success=False)
        assert throttle_mode(staff.username, None)[0] == MODE_CHALLENGE
        client = Client()
        response = client.post(
            "/admin/login/",
            {"username": staff.username, "password": "hydrantproof-test-pass"},
        )
        assert response.status_code == 302
        assert throttle_mode(staff.username, None)[0] == MODE_OK


class TestBoundedSuccessRecording:
    """An authenticated polling client must not turn every read into a write."""

    def test_repeated_basic_success_does_not_grow_login_attempts(self, editor_user):
        client = basic(editor_user.username, "hydrantproof-test-pass")
        for _ in range(6):
            assert client.get("/api/v1/hydrants/").status_code == 200
        assert LoginAttempt.objects.filter(username=editor_user.username).count() == 1

    def test_a_success_after_a_failure_is_recorded(self, editor_user):
        assert basic(editor_user.username, "wrong-password").get(
            "/api/v1/hydrants/"
        ).status_code == 401
        assert basic(editor_user.username, "hydrantproof-test-pass").get(
            "/api/v1/hydrants/"
        ).status_code == 200
        assert LoginAttempt.objects.filter(
            username=editor_user.username, success=True
        ).exists()

    def test_a_failure_after_a_success_is_still_recorded(self, editor_user):
        good = basic(editor_user.username, "hydrantproof-test-pass")
        assert good.get("/api/v1/hydrants/").status_code == 200
        assert basic(editor_user.username, "wrong-password").get(
            "/api/v1/hydrants/"
        ).status_code == 401
        assert LoginAttempt.objects.filter(
            username=editor_user.username, success=False
        ).exists()
