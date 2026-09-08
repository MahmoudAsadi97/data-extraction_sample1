"""Apollo.io - B2B company database (search + enrichment). Requires ``APOLLO_API_KEY``.

Search example::

    - type: apollo
      locations: ["Kortrijk, Belgium"]
      keywords: ["software", "IT services"]     # q_organization_keyword_tags
      employee_ranges: ["1,10", "11,50"]
      max_results: 100

The same module also provides :func:`enrich_by_domain`, used by the pipeline
to complete records (LinkedIn URL, industry, size) when the key is available.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import requests

from ..config import env
from ..http import HttpClient
from ..models import RawRecord
from .base import BaseSource, SourceError, register

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_companies/search"
ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"


def organization_to_values(org: dict[str, Any]) -> dict[str, Any]:
    phone = org.get("primary_phone") or {}
    return {
        "company_name": org.get("name"),
        "industry": org.get("industry"),
        "category": org.get("industry"),
        "description": org.get("short_description"),
        "founded": org.get("founded_year"),
        "employees": org.get("estimated_num_employees"),
        "street": org.get("street_address") or org.get("raw_address"),
        "postcode": org.get("postal_code"),
        "city": org.get("city"),
        "country": org.get("country"),
        "phone": phone.get("number") if isinstance(phone, dict) else phone,
        "website": org.get("website_url") or (f"https://{org['primary_domain']}" if org.get("primary_domain") else None),
        "linkedin": org.get("linkedin_url"),
        "facebook": org.get("facebook_url"),
    }


def _headers(api_key: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "Cache-Control": "no-cache", "x-api-key": api_key}


def enrich_by_domain(http: HttpClient, domain: str, api_key: str | None = None) -> dict[str, Any] | None:
    """Look up one organisation by domain. Returns mapped values or None."""
    api_key = api_key or env("APOLLO_API_KEY")
    if not api_key or not domain:
        return None
    try:
        resp = http.get(ENRICH_URL, params={"domain": domain}, headers=_headers(api_key), timeout=30, min_delay=0.6)
        if resp.status_code != 200:
            log.warning("Apollo enrich %s -> HTTP %s", domain, resp.status_code)
            return None
        org = resp.json().get("organization")
    except (requests.RequestException, ValueError) as exc:
        log.warning("Apollo enrich failed for %s: %s", domain, exc)
        return None
    return organization_to_values(org) if org else None


@register
class ApolloSource(BaseSource):
    type = "apollo"
    description = "Apollo.io company search (B2B database). Needs APOLLO_API_KEY. LinkedIn-style firmographics."
    requires_env = ("APOLLO_API_KEY",)
    produces = "companies"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        api_key = env("APOLLO_API_KEY")
        if not api_key:
            raise SourceError("apollo needs APOLLO_API_KEY (see .env.example). Use csv_import with a manual/LinkedIn list as an alternative.")
        max_results = int(self.opt("max_results", 100))
        if limit:
            max_results = min(max_results, limit)
        per_page = min(100, max_results)
        body: dict[str, Any] = {"page": 1, "per_page": per_page}
        if self.opt("locations"):
            body["organization_locations"] = list(self.opt("locations"))
        if self.opt("keywords"):
            body["q_organization_keyword_tags"] = list(self.opt("keywords"))
        if self.opt("employee_ranges"):
            body["organization_num_employees_ranges"] = list(self.opt("employee_ranges"))
        if self.opt("name"):
            body["q_organization_name"] = self.opt("name")
        count = 0
        while count < max_results:
            try:
                resp = self.http.post(SEARCH_URL, json=body, headers=_headers(api_key), timeout=30, min_delay=0.6)
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                raise SourceError(f"Apollo request failed: {exc}") from exc
            if resp.status_code != 200:
                raise SourceError(f"Apollo API error {resp.status_code}: {str(data)[:200]}")
            orgs = data.get("organizations") or data.get("accounts") or []
            if not orgs:
                break
            for org in orgs:
                yield RawRecord(
                    source=self.label,
                    source_id=str(org.get("id", "")),
                    source_url=org.get("linkedin_url") or org.get("website_url") or "",
                    values=organization_to_values(org),
                    raw={"apollo_id": org.get("id"), "domain": org.get("primary_domain")},
                )
                count += 1
                if count >= max_results:
                    break
            pagination = data.get("pagination") or {}
            if body["page"] >= int(pagination.get("total_pages", 1) or 1):
                break
            body["page"] += 1
