"""HydrantProof settings.

Configuration truth is the environment (see packaging.json ``env_var_names``);
this module never invents a secret.  The Django secret key resolution chain is
``DJANGO_SECRET_KEY`` env -> ``AppSetting`` (persisted by ``manage.py
ensure_secret``) -> an ephemeral build-time key so that ``collectstatic`` works
during the image build.  See README "Production posture".
"""
from __future__ import annotations

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
# The constant below is the BUILD-TIME key: it lets collectstatic and
# bake_render_stack run during the image build without a database.  It must
# never serve traffic — ledger.secret_key adopts the DB-persisted key at the
# serving entry point (wsgi/asgi) and check_posture refuses to boot on the
# constant (see README "Production posture").
EPHEMERAL_SECRET_KEY = "ephemeral-build-time-key-collectstatic-only"
SECRET_KEY = env("DJANGO_SECRET_KEY") or EPHEMERAL_SECRET_KEY
USING_EPHEMERAL_SECRET_KEY = SECRET_KEY == EPHEMERAL_SECRET_KEY
DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", ["*"]) or ["*"]
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "ledger.middleware.AbsoluteSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hydrantproof.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ledger.context.banners",
            ],
        },
    },
]

WSGI_APPLICATION = "hydrantproof.wsgi.application"

# ---------------------------------------------------------------------------
# Database / storage
# ---------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgres://hydrantproof:hydrantproof@localhost:5432/hydrantproof"),
        conn_max_age=0,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_ROOT = Path(env("HYDRANTPROOF_MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = "/media/"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Auth / sessions / CSRF
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 7200  # 2 h idle; the 12 h absolute cap is middleware-enforced
SESSION_SAVE_EVERY_REQUEST = True
ABSOLUTE_SESSION_SECONDS = 12 * 60 * 60

COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_SECURE = COOKIE_SECURE

TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS", False)

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "ledger.auth.SessionAuthenticationNoBrowsable",
        "ledger.auth.HydrantBasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "ledger.auth.json_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "HydrantProof API",
    "DESCRIPTION": (
        "District water-supply records: hydrant registry, NFPA 291 flow-test ledger, "
        "inspections and deficiencies, readiness, coverage worksheet and evidence packs."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Paths are served exactly as routed (no prefix stripping): the published
    # contract matches the route table literally.
    "SCHEMA_PATH_PREFIX": None,
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---------------------------------------------------------------------------
# Product policy knobs read from the environment (see README)
# ---------------------------------------------------------------------------
MAP_TILE_URL = env("MAP_TILE_URL")
MAP_TILE_ATTRIBUTION = env("MAP_TILE_ATTRIBUTION")
ALERT_WEBHOOK_URL = env("ALERT_WEBHOOK_URL")
SEED_DEMO = env_bool("SEED_DEMO", True)
ALLOW_INSECURE_LOOPBACK = env_bool("ALLOW_INSECURE_LOOPBACK", False)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "ledger": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO"), "propagate": False},
    },
}
