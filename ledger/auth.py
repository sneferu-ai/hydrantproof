"""Authentication surfaces, throttling and the JSON error contract.

Three authentication surfaces write ``LoginAttempt`` rows: the login form,
every HTTP Basic 401, and the Django admin login.  The throttle is
username-keyed and bounded (specification §4):

* 10 failures for one username within 15 minutes → 429 with ``Retry-After``.
* Valid credentials are always accepted during an active throttle window, up
  to 20 verifications per username per 15 minutes; beyond that cap 429 applies
  to all requests for that username — a documented residual DoS window bounded
  to 15 minutes.
* A successful login resets the username's window.  Successes are recorded
  when they change the throttle state (a first attempt in a window, or one
  after a failure); a repeated success with nothing in between is not
  re-inserted, so an authenticated polling client does not turn every API read
  into a database write.
* With ``TRUST_PROXY_HEADERS=true`` an additional per-IP dimension (50/15 min)
  activates; with the default ``false`` it is off, so a reverse-proxied
  deployment is never site-wide locked out by a shared proxy IP.
* Verification is bounded by a process-wide semaphore (maxsize 2, 5 s
  timeout); a busy verifier answers 429 with ``Retry-After`` rather than
  queueing unbounded hash CPU.
"""
from __future__ import annotations

import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.permissions import BasePermission

from .middleware import client_ip
from .models import LoginAttempt

WINDOW = timedelta(minutes=15)
FAILURE_LIMIT = 10
VERIFICATION_CAP = 20
IP_FAILURE_LIMIT = 50
SEMAPHORE_TIMEOUT_S = 5.0

_VERIFICATION_SEMAPHORE = threading.BoundedSemaphore(2)

MODE_OK = "ok"
MODE_CHALLENGE = "challenge"
MODE_BLOCKED = "blocked"


class ThrottleExceeded(exceptions.APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many authentication attempts. Try again later."

    def __init__(self, detail=None, retry_after: int = 900):
        super().__init__(detail or self.default_detail)
        self.retry_after = max(1, int(retry_after))
        # DRF's exception handler emits Retry-After from ``exc.wait``.
        self.wait = self.retry_after

    def get_headers(self):
        return {"Retry-After": str(self.retry_after)}


def record_login_attempt(request, username: str, *, surface: str, success: bool) -> None:
    LoginAttempt.objects.create(
        username=(username or "")[:150],
        surface=surface,
        success=success,
        ip=client_ip(request) if request is not None else None,
    )


def _window_start(now):
    return now - WINDOW


def _attempts_since_last_success(username: str, now):
    """Failures and verifications since the last success (window-scoped)."""
    qs = LoginAttempt.objects.filter(username=username, at__gte=_window_start(now))
    last_success = (
        LoginAttempt.objects.filter(username=username, success=True, at__gte=_window_start(now))
        .order_by("-at")
        .first()
    )
    if last_success is not None:
        qs = qs.filter(at__gt=last_success.at)
    failures = qs.filter(success=False).count()
    verifications = qs.count()
    return failures, verifications


def _success_needs_recording(username: str, now) -> bool:
    """Record a success only when it changes the throttle state.

    A success that repeats an already-recorded success (nothing in between)
    adds no information — but an INSERT per authenticated request turns every
    read-only API call into a database write, and a scripted 1 Hz integration
    client would write ~86,000 LoginAttempt rows per user per day.  A success
    after a failure still lands (it resets the window), as does the first
    attempt of a window.
    """
    latest = (
        LoginAttempt.objects.filter(username=username, at__gte=_window_start(now))
        .order_by("-at", "-id")
        .first()
    )
    return latest is None or not latest.success


def throttle_mode(username: str, ip: str | None, now=None) -> tuple[str, int]:
    """Return ``(mode, retry_after_seconds)`` for the next authentication try."""
    now = now or timezone.now()
    failures, verifications = _attempts_since_last_success(username or "", now)
    if verifications >= VERIFICATION_CAP:
        return MODE_BLOCKED, int(WINDOW.total_seconds())
    if ip and getattr(settings, "TRUST_PROXY_HEADERS", False):
        ip_failures = LoginAttempt.objects.filter(
            ip=ip, success=False, at__gte=_window_start(now)
        ).count()
        if ip_failures >= IP_FAILURE_LIMIT:
            return MODE_BLOCKED, int(WINDOW.total_seconds())
    if failures >= FAILURE_LIMIT:
        return MODE_CHALLENGE, int(WINDOW.total_seconds())
    return MODE_OK, 0


def acquire_verification_slot(timeout: float = SEMAPHORE_TIMEOUT_S):
    """Bound concurrent password verification; ``False`` means busy."""
    return _VERIFICATION_SEMAPHORE.acquire(timeout=timeout)


def release_verification_slot() -> None:
    try:
        _VERIFICATION_SEMAPHORE.release()
    except ValueError:  # pragma: no cover - defensive
        pass


def authenticate_with_throttle(request, username: str, password: str, *, surface: str):
    """Verify credentials under the throttle.  Returns a User or raises.

    Raises :class:`ThrottleExceeded` (429) when the username is blocked, when
    the verifier is saturated, or when a throttled window sees another failure.
    """
    from django.contrib.auth import authenticate

    mode, retry_after = throttle_mode(username, client_ip(request) if request else None)
    if mode == MODE_BLOCKED:
        raise ThrottleExceeded(retry_after=retry_after)
    if not acquire_verification_slot():
        raise ThrottleExceeded("Authentication verifier is busy; retry shortly.", retry_after=30)
    try:
        user = authenticate(request=request, username=username, password=password)
    finally:
        release_verification_slot()

    if user is not None and user.is_active:
        # The window-resetting success is recorded; a repeat success with no
        # intervening attempt is not (see ``_success_needs_recording``).
        if _success_needs_recording(username, timezone.now()):
            record_login_attempt(request, username, surface=surface, success=True)
        return user

    record_login_attempt(request, username, surface=surface, success=False)
    if mode == MODE_CHALLENGE:
        # A throttled window treats any failure as 429 (Retry-After), never 401.
        raise ThrottleExceeded(retry_after=retry_after)
    return None


class SessionAuthenticationNoBrowsable(SessionAuthentication):
    """Session auth with CSRF enforced on unsafe methods (DRF default retained)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        return result


class HydrantBasicAuthentication(BasicAuthentication):
    """HTTP Basic, accepted only under ``/api/v1/``, CSRF-exempt.

    Every 401 writes a ``LoginAttempt`` row (surface=basic) and the username
    throttle applies before the password is verified.
    """

    def authenticate(self, request):
        if not request.path.startswith("/api/v1/"):
            return None
        return super().authenticate(request)

    def authenticate_credentials(self, userid, password, request=None):
        if not userid or not password:
            raise exceptions.AuthenticationFailed("Invalid username or password.")
        user = authenticate_with_throttle(
            request, userid, password, surface=LoginAttempt.SURFACE_BASIC
        )
        if user is None:
            # Generic message: never disclose whether the username exists.
            raise exceptions.AuthenticationFailed("Invalid username or password.")
        return (user, None)

    def authenticate_header(self, request):
        return 'Basic realm="hydrantproof"'


def json_exception_handler(exc, context):
    """DRF handler: anonymous JSON is 401 with the pinned detail string."""
    from rest_framework.views import exception_handler

    response = exception_handler(exc, context)
    if response is None:
        return None
    if response.status_code == status.HTTP_403_FORBIDDEN and isinstance(
        exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)
    ):
        response.status_code = status.HTTP_401_UNAUTHORIZED
        response.data = {"detail": "Authentication required."}
    if isinstance(exc, exceptions.NotAuthenticated):
        response.status_code = status.HTTP_401_UNAUTHORIZED
        response.data = {"detail": "Authentication required."}
    return response


def user_can_write(user) -> bool:
    """``editor``+ writes; ``viewer`` reads only; anonymous never."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=("editor", "admin")).exists()


def user_is_admin(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name="admin").exists()


class CanWrite(BasePermission):
    """``editor``+ may write; ``viewer`` is read-only."""

    message = "Your role is read-only."

    def has_permission(self, request, view):
        return user_can_write(request.user)


class IsAdmin(BasePermission):
    """``admin`` only: users, settings, purge, restore, outbox drain, media delete."""

    message = "Administrator role required."

    def has_permission(self, request, view):
        return user_is_admin(request.user)
