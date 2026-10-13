"""``/healthz`` — anonymous, cached 5 s, honest about which check failed."""
from __future__ import annotations

import time

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse

CACHE_TTL_SECONDS = 5
_cache: dict = {"at": 0.0, "status": 503, "payload": None}


def _check_database() -> str:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "reachable"
    except Exception as exc:  # pragma: no cover - exercised with the db stopped
        return f"unreachable: {type(exc).__name__}"


def _check_migrations() -> str:
    try:
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return "applied" if not plan else f"pending: {len(plan)}"
    except Exception as exc:  # pragma: no cover
        return f"unknown: {type(exc).__name__}"


def compute_health() -> tuple[int, dict]:
    checks = {"database": _check_database(), "migrations": _check_migrations()}
    healthy = checks["database"] == "reachable" and checks["migrations"] == "applied"
    if healthy:
        return 200, {"status": "ok", "checks": checks}
    failing = [name for name, value in checks.items() if not _is_ok(name, value)]
    return 503, {"status": "unavailable", "checks": checks, "failing": failing}


def _is_ok(name: str, value: str) -> bool:
    return value == ("reachable" if name == "database" else "applied")


def healthz(request):
    """200 only after migrations are applied and the database answers SELECT 1."""
    now = time.monotonic()
    if now - _cache["at"] > CACHE_TTL_SECONDS or _cache["payload"] is None:
        status, payload = compute_health()
        _cache.update({"at": now, "status": status, "payload": payload})
    response = JsonResponse(_cache["payload"], status=_cache["status"])
    response["Cache-Control"] = f"max-age={CACHE_TTL_SECONDS}"
    return response
