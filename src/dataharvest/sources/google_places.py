"""Google Maps Platform - Places API (New), Text Search.

Requires ``GOOGLE_MAPS_API_KEY``. When the key is absent the project can use
``osm_overpass`` instead, which needs no key.

Example::

    - type: google_places
      text_query: "restaurants in Kortrijk, Belgium"
      included_type: restaurant      # optional
      max_results: 60                # up to 20 per page, paginated automatically
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

from ..config import env
from ..models import RawRecord
from .base import BaseSource, SourceError, register

log = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
        "places.internationalPhoneNumber", "places.nationalPhoneNumber", "places.websiteUri",
        "places.location", "places.businessStatus", "places.primaryType", "places.types",
        "places.rating", "places.userRatingCount", "places.regularOpeningHours", "places.googleMapsUri",
        "places.editorialSummary", "nextPageToken",
    ]
)


def place_to_values(place: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    comps = {}
    for comp in place.get("addressComponents") or []:
        for t in comp.get("types", []):
            comps.setdefault(t, comp.get("longText") or comp.get("shortText"))
    hours = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
    status = place.get("businessStatus")
    if status and status != "OPERATIONAL":
        flags.append(f"Google lists the business as {status.replace('_', ' ').lower()}")
    values = {
        "company_name": (place.get("displayName") or {}).get("text"),
        "category": (place.get("primaryType") or "").replace("_", " ") or None,
        "subcategory": ", ".join(t.replace("_", " ") for t in (place.get("types") or [])[:3] if t != place.get("primaryType")) or None,
        "street": comps.get("route"),
        "house_number": comps.get("street_number"),
        "postcode": comps.get("postal_code"),
        "city": comps.get("locality") or comps.get("postal_town"),
        "district": comps.get("sublocality") or comps.get("sublocality_level_1"),
        "country": (next((c.get("shortText") for c in place.get("addressComponents") or [] if "country" in c.get("types", [])), None)),
        "phone": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "opening_hours": "; ".join(hours) if hours else None,
        "description": (place.get("editorialSummary") or {}).get("text"),
        "latitude": (place.get("location") or {}).get("latitude"),
        "longitude": (place.get("location") or {}).get("longitude"),
    }
    return values, flags


@register
class GooglePlacesSource(BaseSource):
    type = "google_places"
    description = "Google Maps Places API (New) text search. Needs GOOGLE_MAPS_API_KEY (billing account)."
    requires_env = ("GOOGLE_MAPS_API_KEY",)
    produces = "leads"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        api_key = env("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise SourceError("google_places needs GOOGLE_MAPS_API_KEY (see .env.example). Use osm_overpass for a key-less alternative.")
        text_query = self.require_opt("text_query")
        max_results = int(self.opt("max_results", 60))
        if limit:
            max_results = min(max_results, limit)
        body: dict[str, Any] = {
            "textQuery": text_query,
            "pageSize": min(20, max_results),
            "languageCode": self.opt("language", self.project.project.language or "en"),
            "regionCode": self.opt("region", self.project.project.country),
        }
        if self.opt("included_type"):
            body["includedType"] = self.opt("included_type")
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK}
        count = 0
        page_token: str | None = None
        while count < max_results:
            if page_token:
                body["pageToken"] = page_token
                time.sleep(2)  # tokens become valid after a short delay
            try:
                resp = self.http.post(PLACES_URL, json=body, headers=headers, timeout=30)
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                raise SourceError(f"Google Places request failed: {exc}") from exc
            if resp.status_code != 200:
                message = (data.get("error") or {}).get("message", resp.text[:200])
                raise SourceError(f"Google Places API error {resp.status_code}: {message}")
            for place in data.get("places", []):
                values, flags = place_to_values(place)
                yield RawRecord(
                    source=self.label,
                    source_id=place.get("id", ""),
                    source_url=place.get("googleMapsUri", ""),
                    values=values,
                    raw={"rating": place.get("rating"), "user_rating_count": place.get("userRatingCount"),
                         "business_status": place.get("businessStatus"), "formatted_address": place.get("formattedAddress")},
                    flags=flags,
                )
                count += 1
                if count >= max_results:
                    break
            page_token = data.get("nextPageToken")
            if not page_token:
                break
