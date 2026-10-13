"""``render_stack_version`` — computable at runtime, no ``docker inspect``.

``manage.py bake_render_stack`` runs at build time and writes
``/app/render_stack.json`` = ``{base_image_digest, python_version,
requirements_sha256, installed_freeze_sha256}`` where the digest is grepped
from the Dockerfile's ``FROM`` line (the single source of the pin — no
duplicated string).  At runtime the version is the SHA-256 of that file's
canonical JSON.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_PATH = Path("/app/render_stack.json")
_FROM_RE = re.compile(r"^FROM\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def base_image_digest(dockerfile: Path | None = None) -> str:
    dockerfile = dockerfile or _find_dockerfile()
    if dockerfile is None or not dockerfile.exists():
        return "unknown"
    match = _FROM_RE.search(dockerfile.read_text())
    if not match:
        return "unknown"
    reference = match.group(1)
    if "@" in reference:
        return reference.split("@", 1)[1]
    return reference


def _find_dockerfile() -> Path | None:
    for candidate in (Path("/app/Dockerfile"), Path.cwd() / "Dockerfile",
                      Path(__file__).resolve().parent.parent / "Dockerfile"):
        if candidate.exists():
            return candidate
    return None


def requirements_sha256(requirements: Path | None = None) -> str:
    requirements = requirements or (Path(__file__).resolve().parent.parent / "requirements.txt")
    if not requirements.exists():
        return "unknown"
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def installed_freeze_sha256() -> str:
    try:
        output = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=120, check=False,
        ).stdout
    except Exception:  # pragma: no cover - pip unavailable
        output = ""
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


def build_payload() -> dict:
    return {
        "base_image_digest": base_image_digest(),
        "python_version": platform.python_version(),
        "requirements_sha256": requirements_sha256(),
        "installed_freeze_sha256": installed_freeze_sha256(),
    }


def write_render_stack(path: Path | None = None) -> Path:
    path = Path(path) if path else DEFAULT_PATH
    payload = build_payload()
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return path


def render_stack_version(path: Path | None = None) -> str:
    """SHA-256 of the frozen file's canonical JSON (or a labelled unknown)."""
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return "unknown"
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return "unknown"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
