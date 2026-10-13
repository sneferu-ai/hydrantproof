"""Template context processors."""
from __future__ import annotations

from django.conf import settings

from . import constants as C


_NAV_SECTIONS = (
    ("/hydrants/", "hydrants"),
    ("/parcels/", "parcels"),
    ("/coverage/", "coverage"),
    ("/packs/", "packs"),
    ("/exports/", "exports"),
    ("/field/", "field"),
    ("/guide/", "guide"),
    ("/settings/", "settings"),
)


def nav_section(path: str) -> str:
    """Which nav entry the current path belongs to (presentation only)."""
    for prefix, section in _NAV_SECTIONS:
        if path.startswith(prefix):
            return section
    return "dashboard"


def banners(request):
    """Sample-data and basemap banners (both are required UI truths)."""
    sample_banner = False
    try:
        from .models import Hydrant

        sample_banner = Hydrant.objects.filter(is_sample_data=True).exists()
    except Exception:  # pragma: no cover - pre-migration/tests
        sample_banner = False
    return {
        "sample_data_banner": sample_banner,
        "map_tile_url": getattr(settings, "MAP_TILE_URL", ""),
        "map_tile_attribution": getattr(settings, "MAP_TILE_ATTRIBUTION", ""),
        "artifact_label": C.ARTIFACT_LABEL,
        "provisional_banner": C.PROVISIONAL_BANNER,
        "nav_section": nav_section(getattr(request, "path", "") or ""),
    }
