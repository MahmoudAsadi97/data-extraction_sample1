"""OpenStreetMap via the Overpass API - the key-less alternative to Google Maps Places.

Returns every business matching the requested OSM tags inside an area
(a municipality resolved through Nominatim, or an explicit bounding box).

Example project source::

    - type: osm_overpass
      area: Kortrijk
      country: BE
      tags: ["amenity=restaurant", "amenity=cafe"]
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

from ..models import RawRecord
from .base import BaseSource, SourceError, register
from .nominatim import Area, resolve_area

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

# OSM tag -> schema field. First match wins, so the more specific "contact:*" keys come first.
TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "phone": ("contact:phone", "phone", "contact:mobile", "mobile"),
    "email": ("contact:email", "email"),
    "website": ("contact:website", "website", "url"),
    "facebook": ("contact:facebook",),
    "instagram": ("contact:instagram",),
    "linkedin": ("contact:linkedin",),
    "vat_number": ("ref:vatin", "ref:BE:KBO", "ref:vat"),
    "opening_hours": ("opening_hours",),
    "description": ("description",),
    "street": ("addr:street",),
    "house_number": ("addr:housenumber",),
    "postcode": ("addr:postcode",),
    "city": ("addr:city",),
    "district": ("addr:suburb", "addr:district"),
    "country": ("addr:country",),
}
CATEGORY_KEYS = ("amenity", "shop", "office", "craft", "tourism", "leisure", "healthcare", "industrial")
SUBCATEGORY_KEYS = ("cuisine", "office", "craft", "healthcare:speciality", "shop", "brand")


def parse_tag_filter(expr: str) -> str:
    """'amenity=restaurant' -> '["amenity"="restaurant"]', 'office' -> '["office"]', 'shop=*' -> '["shop"]'."""
    expr = expr.strip()
    if "=" not in expr:
        return f'["{expr}"]'
    key, value = (p.strip() for p in expr.split("=", 1))
    if value in ("*", ""):
        return f'["{key}"]'
    if "|" in value:
        return f'["{key}"~"^({value})$"]'
    return f'["{key}"="{value}"]'


def build_query(tag_filters: list[str], *, area_id: int | None = None, area_name: str | None = None,
                bbox: tuple[float, float, float, float] | None = None, timeout: int = 90) -> str:
    if not tag_filters:
        raise SourceError("osm_overpass needs at least one tag filter, e.g. 'amenity=restaurant'")
    selectors = "".join(parse_tag_filter(t) for t in tag_filters) if len(tag_filters) == 1 else None
    if area_id:
        scope = f"area({area_id})->.searchArea;"
        where = "(area.searchArea)"
    elif area_name:
        safe = area_name.replace('"', '\\"')
        scope = f'area["name"="{safe}"]["boundary"="administrative"]->.searchArea;'
        where = "(area.searchArea)"
    elif bbox:
        south, west, north, east = bbox
        scope = ""
        where = f"({south},{west},{north},{east})"
    else:
        raise SourceError("osm_overpass needs an 'area' (place name) or a 'bbox'")
    if selectors:
        union = f"nwr{selectors}{where};"
    else:
        union = "".join(f"nwr{parse_tag_filter(t)}{where};" for t in tag_filters)
    return f"[out:json][timeout:{timeout}];\n{scope}\n(\n{union}\n);\nout center tags;"


def element_to_values(el: dict[str, Any], tag_filters: list[str]) -> dict[str, Any]:
    tags: dict[str, str] = el.get("tags") or {}
    values: dict[str, Any] = {}
    name = tags.get("name") or tags.get("name:nl") or tags.get("name:en") or tags.get("name:fr") or tags.get("brand")
    values["company_name"] = name
    if tags.get("official_name") and tags.get("official_name") != name:
        values["legal_name"] = tags["official_name"]
    for field_name, keys in TAG_FIELDS.items():
        for key in keys:
            if tags.get(key):
                values[field_name] = tags[key]
                break
    # category = the tag we searched for; subcategory = cuisine/brand/etc.
    category, subcategory = None, None
    for key in CATEGORY_KEYS:
        if tags.get(key):
            category = tags[key]
            for sub in SUBCATEGORY_KEYS:
                if sub != key and tags.get(sub):
                    subcategory = tags[sub]
                    break
            break
    values["category"] = (category or "").replace("_", " ") or None
    values["subcategory"] = (subcategory or "").replace("_", " ").replace(";", ", ") or None
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    values["latitude"], values["longitude"] = lat, lon
    return values


@register
class OsmOverpassSource(BaseSource):
    type = "osm_overpass"
    description = "Businesses/places from OpenStreetMap (Overpass API). Free, no API key. Alternative to Google Maps."
    produces = "leads"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        tag_filters = self.opt("tags") or []
        if isinstance(tag_filters, str):
            tag_filters = [tag_filters]
        timeout = int(self.opt("timeout", 90))
        area_id: int | None = None
        area_name: str | None = None
        bbox = self.opt("bbox")
        area_meta: Area | None = None

        if bbox:
            bbox = tuple(float(x) for x in bbox)
        else:
            area_opt = self.opt("area")
            country = self.opt("country") or self.project.project.country
            if isinstance(area_opt, dict):
                area_name = area_opt.get("name")
                country = area_opt.get("country", country)
            else:
                area_name = str(area_opt) if area_opt else None
            if not area_name:
                raise SourceError("osm_overpass needs 'area' (place name) or 'bbox'")
            area_meta = resolve_area(self.http, area_name, country)
            if area_meta and area_meta.overpass_area_id:
                area_id = area_meta.overpass_area_id
                log.info("Resolved area %r -> %s (%s/%s)", area_name, area_meta.display_name, area_meta.osm_type, area_meta.osm_id)
            else:
                self.warn(f"could not resolve '{area_name}' via Nominatim; falling back to a name-based area query")

        query = build_query(tag_filters, area_id=area_id, area_name=None if area_id else area_name, bbox=bbox, timeout=timeout)
        data = self._run_query(query, timeout)
        elements = data.get("elements", [])
        log.info("Overpass returned %d elements", len(elements))
        count = 0
        for el in elements:
            if not el.get("tags"):
                continue
            values = element_to_values(el, tag_filters)
            if not values.get("country") and area_meta is not None:
                values["country"] = (self.opt("country") or self.project.project.country)
            if not values.get("city") and area_meta is not None and area_meta.address_type in ("city", "town", "village", "municipality"):
                values["city"] = area_meta.name
            yield RawRecord(
                source=self.label,
                source_id=f"{el['type']}/{el['id']}",
                source_url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
                values=values,
                raw={"osm_type": el["type"], "osm_id": el["id"], "tags": el.get("tags", {})},
            )
            count += 1
            if limit and count >= limit:
                break

    # ------------------------------------------------------------------ internals
    def _run_query(self, query: str, timeout: int) -> dict[str, Any]:
        last_error: Exception | None = None
        endpoints = self.opt("endpoints") or OVERPASS_ENDPOINTS
        for attempt, endpoint in enumerate(endpoints):
            try:
                resp = self.http.post(endpoint, data={"data": query}, timeout=timeout + 15, min_delay=1.0)
                if resp.status_code in (429, 504) and attempt < len(endpoints) - 1:
                    log.warning("Overpass %s answered %s; trying next mirror", endpoint, resp.status_code)
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                remark = str(data.get("remark", ""))
                if "runtime error" in remark.lower() or (not data.get("elements") and remark):
                    log.warning("Overpass %s: %s", endpoint, remark)
                    last_error = RuntimeError(remark)
                    time.sleep(2)
                    continue  # timeout / overload on this mirror - try the next one
                return data
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning("Overpass request to %s failed: %s", endpoint, exc)
                time.sleep(1)
        raise SourceError(f"all Overpass endpoints failed: {last_error}")
