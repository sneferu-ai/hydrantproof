#!/usr/bin/env python3
"""Example 5 — script the HydrantProof API from Python (stdlib only).

A complete integration cycle: health check, registry read, hydraulics preview,
coverage run + poll, evidence pack + poll, stored-bytes download. Uses only
the standard library (`urllib`), so it runs anywhere Python 3.12 runs — no
requests, no SDK.

It creates NO ledger rows (no hydrant/test/inspection/parcel), so demo
accounts stay active; it does create CoverageRun/EvidencePack rows.

Setup:
    A running HydrantProof reachable at BASE.

Run:
    python docs/examples/05_python_client.py
    BASE=http://scratch:8000 HP_USER=admin HP_PASS=... python docs/examples/05_python_client.py

Expected output: "CLIENT CYCLE OK" with the pack's byte size and zip entry
list.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8000")
USER = os.environ.get("HP_USER", "demo-editor")
PASS = os.environ.get("HP_PASS", "hydrantproof-demo")

_TOKEN = base64.b64encode(f"{USER}:{PASS}".encode()).decode()


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | bytes]:
    """One authenticated API call; returns (status, parsed-or-bytes)."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Authorization", f"Basic {_TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type:
                return response.status, json.loads(payload)
            return response.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def poll(path: str, key: str = "status", timeout_s: int = 120) -> dict:
    """Poll an async job until COMPLETE/FAILED."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, body = call("GET", path)
        assert status == 200, (status, body)
        assert isinstance(body, dict)
        if body[key] in ("COMPLETE", "FAILED"):
            return body
        time.sleep(1)
    raise TimeoutError(f"{path} did not finish within {timeout_s}s")


def main() -> int:
    # 1. Health
    status, body = call("GET", "/healthz")
    assert status == 200, (status, body)
    assert isinstance(body, dict) and body["status"] == "ok", body
    print("health:", body)

    # 2. Registry read
    status, body = call("GET", "/api/v1/hydrants/")
    assert status == 200 and isinstance(body, dict)
    print(f"hydrants: {body['count']}")

    # 3. Hydraulics preview (creates nothing)
    status, body = call("POST", "/api/v1/compute-preview/", {
        "tested_on": "2026-10-03",
        "static_psi": "80.00",
        "residual_psi": "40.00",
        "outlets": [{"outlet_diameter_in": "2.50",
                     "outlet_coefficient": "0.90",
                     "pitot_psi": "28.00"}],
    })
    assert status == 200, (status, body)
    assert isinstance(body, dict)
    print("preview Q_F:", body["computed_total_gpm"], "Q_R:", body["rated_gpm_20"])
    assert body["computed_total_gpm"] == 887.9

    # 4. Coverage run (async)
    status, body = call("POST", "/api/v1/coverage-runs/", {"parcel_filter": None})
    assert status == 202, (status, body)
    assert isinstance(body, dict)
    run = poll(body["poll_url"])
    print("coverage:", run["status"], "| covered:", run["covered_count"],
          "/", run["protected_parcel_count"], "| bands:", run["credited_band_counts"])
    assert run["status"] == "COMPLETE"

    # 5. Evidence pack (async)
    status, body = call("POST", "/api/v1/evidence-packs/", {"run_id": run["id"]})
    assert status == 202, (status, body)
    assert isinstance(body, dict)
    pack = poll(body["poll_url"])
    print("pack:", pack["status"], "| bytes:", pack["byte_size"], "| pages:", pack["page_count"])
    assert pack["status"] == "COMPLETE"

    # 6. Download the stored zip and inspect it
    status, payload = call("GET", f"/api/v1/evidence-packs/{pack['id']}/zip/")
    assert status == 200 and isinstance(payload, bytes)
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert "pack.pdf" in names and "manifest.txt" in names
        print("zip entries:", names)

    print()
    print("CLIENT CYCLE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
