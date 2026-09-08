"""Wikidata SPARQL source - company profiles from the world's largest open knowledge base.

Example::

    - type: wikidata
      headquarters_in: Q1113      # West Flanders (any place item: municipality, province, country)
      instance_of: Q4830453       # business (subclasses included)
      limit: 500

Or supply a complete custom ``query`` (SPARQL) whose variable names match
schema field names.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import requests

from ..models import RawRecord
from .base import BaseSource, SourceError, register

log = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"

DEFAULT_QUERY = """
SELECT ?company ?companyLabel
       (SAMPLE(?legal_name) AS ?legal_name)
       (SAMPLE(?website) AS ?website)
       (SAMPLE(?inception) AS ?inception)
       (SAMPLE(?dissolved) AS ?dissolved)
       (GROUP_CONCAT(DISTINCT ?industryLabel; separator="; ") AS ?industries)
       (SAMPLE(?hqLabel) AS ?hq)
       (SAMPLE(?employees) AS ?employees)
       (SAMPLE(?vat) AS ?vat)
       (SAMPLE(?linkedin) AS ?linkedin)
       (SAMPLE(?phone) AS ?phone)
       (SAMPLE(?email) AS ?email)
       (SAMPLE(?description) AS ?description)
WHERE {
  ?company wdt:P31/wdt:P279* wd:%(instance_of)s ;
           wdt:P159 ?hq .
  ?hq wdt:P131* wd:%(place)s .
  OPTIONAL { ?company wdt:P856 ?website }
  OPTIONAL { ?company wdt:P571 ?inception }
  OPTIONAL { ?company wdt:P576 ?dissolved }
  OPTIONAL { ?company wdt:P1448 ?legal_name }
  OPTIONAL { ?company wdt:P452 ?industry . ?industry rdfs:label ?industryLabel FILTER(LANG(?industryLabel) = "en") }
  OPTIONAL { ?company wdt:P1128 ?employees }
  OPTIONAL { ?company wdt:P3608 ?vat }
  OPTIONAL { ?company wdt:P4264 ?linkedin }
  OPTIONAL { ?company wdt:P1329 ?phone }
  OPTIONAL { ?company wdt:P968 ?email }
  OPTIONAL { ?company schema:description ?description FILTER(LANG(?description) = "en") }
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "%(languages)s" .
    ?company rdfs:label ?companyLabel .
    ?hq rdfs:label ?hqLabel .
  }
}
GROUP BY ?company ?companyLabel
LIMIT %(limit)d
"""


def binding_value(binding: dict[str, Any], name: str) -> str | None:
    cell = binding.get(name)
    if not cell:
        return None
    value = cell.get("value")
    return value if value not in (None, "") else None


def binding_to_values(b: dict[str, Any]) -> tuple[dict[str, Any], str, list[str]]:
    """Map one SPARQL result row to schema values. Returns (values, qid, flags)."""
    flags: list[str] = []
    uri = binding_value(b, "company") or ""
    qid = uri.rsplit("/", 1)[-1]
    label = binding_value(b, "companyLabel")
    if not label or label == qid:
        label = None
        flags.append("no readable name in source (label missing)")
    inception = binding_value(b, "inception")
    dissolved = binding_value(b, "dissolved")
    linkedin = binding_value(b, "linkedin")
    email = binding_value(b, "email")
    values: dict[str, Any] = {
        "company_name": label,
        "legal_name": binding_value(b, "legal_name"),
        "industry": (binding_value(b, "industries") or "").strip("; ") or None,
        "description": binding_value(b, "description"),
        "founded": inception[:4] if inception else None,
        "employees": binding_value(b, "employees"),
        "city": binding_value(b, "hq"),
        "website": binding_value(b, "website"),
        "vat_number": binding_value(b, "vat"),
        "linkedin": f"https://www.linkedin.com/company/{linkedin}" if linkedin and "/" not in linkedin else linkedin,
        "phone": binding_value(b, "phone"),
        "email": email[7:] if email and email.lower().startswith("mailto:") else email,
    }
    if dissolved:
        values["status_note"] = f"dissolved {dissolved[:10]}"
        flags.append(f"source marks the company as dissolved ({dissolved[:10]})")
    return values, qid, flags


@register
class WikidataSource(BaseSource):
    type = "wikidata"
    description = "Company profiles from Wikidata (SPARQL). Free, no API key. Good for registers of larger companies."
    produces = "companies"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        query = self.opt("query")
        max_rows = int(self.opt("limit_rows", 1000))
        if not query:
            place = self.opt("headquarters_in")
            if not place:
                raise SourceError("wikidata needs 'headquarters_in' (a Wikidata Q-id such as Q1113) or a custom 'query'")
            query = DEFAULT_QUERY % {
                "instance_of": self.opt("instance_of", "Q4830453"),
                "place": place,
                "languages": self.opt("languages", "en,nl,fr,de"),
                "limit": max_rows,
            }
        rows = self._run(query)
        log.info("Wikidata returned %d rows", len(rows))
        country = self.opt("country") or self.project.project.country
        count = 0
        for b in rows:
            values, qid, flags = binding_to_values(b)
            if not values.get("country"):
                values["country"] = country
            yield RawRecord(
                source=self.label,
                source_id=qid,
                source_url=f"https://www.wikidata.org/wiki/{qid}",
                values=values,
                raw={"binding": {k: v.get("value") for k, v in b.items()}},
                flags=flags,
            )
            count += 1
            if limit and count >= limit:
                break

    def _run(self, query: str) -> list[dict[str, Any]]:
        headers = {"Accept": "application/sparql-results+json"}
        try:
            resp = self.http.get(SPARQL_URL, params={"query": query, "format": "json"}, headers=headers,
                                 timeout=120, min_delay=1.0)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"Wikidata query failed: {exc}") from exc
        return data.get("results", {}).get("bindings", [])
