"""OpenAPI extensions for the product's two authentication schemes.

The custom authenticators are plain subclasses of DRF's; without these
extensions drf-spectacular cannot resolve them and emits warnings while
leaving the security schemes undefined.
"""
from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BasicAuthExtension(OpenApiAuthenticationExtension):
    target_class = "ledger.auth.HydrantBasicAuthentication"
    name = "basicAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "basic",
            "description": "HTTP Basic, accepted only under /api/v1/ (CSRF-exempt).",
        }


class SessionAuthExtension(OpenApiAuthenticationExtension):
    target_class = "ledger.auth.SessionAuthenticationNoBrowsable"
    name = "cookieAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "Browser session; CSRF is enforced on unsafe methods.",
        }
