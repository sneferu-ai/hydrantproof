"""Sessions and request helpers."""
from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import logout

ABSOLUTE_SESSION_KEY = "_absolute_started_at"


class AbsoluteSessionMiddleware:
    """Enforce the 12 h absolute session cap on top of the 2 h idle timeout.

    ``SESSION_COOKIE_AGE`` (2 h) is refreshed by ``SESSION_SAVE_EVERY_REQUEST``;
    this middleware records the session's first authenticated request and logs
    the user out once the absolute cap elapses, regardless of activity.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.absolute_seconds = getattr(settings, "ABSOLUTE_SESSION_SECONDS", 12 * 60 * 60)

    def __call__(self, request):
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            started = request.session.get(ABSOLUTE_SESSION_KEY)
            now = time.time()
            if started is None:
                request.session[ABSOLUTE_SESSION_KEY] = now
            elif now - float(started) > self.absolute_seconds:
                logout(request)
        return self.get_response(request)


def client_ip(request) -> str | None:
    """Client IP from X-Forwarded-For only when proxy headers are trusted."""
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
