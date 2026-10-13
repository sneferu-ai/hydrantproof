# Vendored Leaflet — provenance and the 1.9.3 / 1.9.4 deviation

| Field | Value |
|---|---|
| Library | Leaflet (https://leafletjs.com) |
| Vendored version | **1.9.3** (the dist's own header reads `Leaflet 1.9.3`) |
| Files | `leaflet.js`, `leaflet.css`, `images/*`, `LICENSE` (BSD 2-Clause) |
| Distribution source | the `django-leaflet` 0.34.0 wheel on PyPI (`leaflet/static/leaflet/`), which ships the upstream Leaflet dist unmodified |
| Specified version | the specification asks for Leaflet 1.9.4 |

**Deviation, stated plainly:** the build environment used to construct this
product has no route to the internet and no PyPI package carries a Leaflet
1.9.4 dist (the canonical `django-leaflet` distribution vendors 1.9.3; no
other reachable package vendors 1.9.4). Rather than fabricate a version string
or fetch at runtime — the container is offline by contract — the product ships
the real, unmodified upstream 1.9.3 dist and records the deviation here, in
the README, and in IMPLEMENTATION_NOTES.md.

Nothing in the product depends on 1.9.4-only behavior: the map surface uses
`L.map`, `L.tileLayer`, `L.circleMarker`, `L.circle`, `L.control.scale`,
`L.geoJSON` and `L.tooltip`, all of which are stable across the 1.9.x line.
Upgrading is a drop-in file replacement of this directory (no code change).
