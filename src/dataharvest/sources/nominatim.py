"""Nominatim (OpenStreetMap geocoder) helper: resolve a place name to an OSM area.

Usage policy: max 1 request/second and a descriptive User-Agent - both are
enforced by :class:`HttpClient`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..http import HttpClient

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PREFERRED_TYPES = ("city", "town", "municipality", "village", "administrative", "county", "state")


@dataclass
class Area:
    name: str
    display_name: str
    osm_type: str
    osm_id: int
    lat: float
    lon: float
    bbox: tuple[float, float, float, float]  # south, north, west, east
    place_rank: int = 0
    address_type: str = ""

    @property
    def overpass_area_id(self) -> int | None:
        """Overpass area ids: relations + 3_600_000_000, ways + 2_400_000_000."""
        if self.osm_type == "relation":
            return 3_600_000_000 + self.osm_id
        if self.osm_type == "way":
            return 2_400_000_000 + self.osm_id
        return None


def resolve_area(http: HttpClient, name: str, country: str | None = None) -> Area | None:
    """Find the administrative boundary for a place name (e.g. 'Kortrijk', country 'BE')."""
    params = {"q": name, "format": "jsonv2", "limit": 10, "addressdetails": 0}
    if country:
        params["countrycodes"] = country.lower()
    try:
        results = http.get_json(NOMINATIM_URL, params=params, min_delay=1.0)
    except Exception as exc:
        log.warning("Nominatim lookup failed for %r: %s", name, exc)
        return None
    if not results:
        return None

    def score(item: dict) -> tuple[int, int, float]:
        is_boundary = item.get("category") == "boundary" and item.get("type") == "administrative"
        addr_type = item.get("addresstype", "")
        pref = PREFERRED_TYPES.index(addr_type) if addr_type in PREFERRED_TYPES else len(PREFERRED_TYPES)
        exact = str(item.get("name", "")).lower() == name.lower()
        return (0 if is_boundary else 1, pref - (1 if exact else 0), -float(item.get("importance", 0)))

    boundaries = [r for r in results if r.get("osm_type") in ("relation", "way")]
    if not boundaries:
        return None
    best = sorted(boundaries, key=score)[0]
    bbox = best.get("boundingbox") or [0, 0, 0, 0]
    return Area(
        name=best.get("name") or name,
        display_name=best.get("display_name", ""),
        osm_type=best["osm_type"],
        osm_id=int(best["osm_id"]),
        lat=float(best.get("lat", 0)),
        lon=float(best.get("lon", 0)),
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        place_rank=int(best.get("place_rank", 0) or 0),
        address_type=best.get("addresstype", ""),
    )
