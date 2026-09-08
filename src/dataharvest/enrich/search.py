"""Web search used to find a company's website when the primary source has none.

Providers (chosen automatically):

* ``google_cse`` - Google Programmable Search JSON API (needs GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID; 100 free queries/day)
* ``duckduckgo`` - DuckDuckGo HTML endpoint, no key required (rate-limited; be polite)

A website found through search is *never* trusted blindly: it is stored as an
unverified candidate and confirmed only when the company name is found on the
page by the website enricher.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..config import env
from ..http import HttpClient
from ..processing.normalize import name_key, normalize_url, url_domain

log = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Directories, social networks, review sites, maps: never a company's *own* website.
DIRECTORY_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com", "pinterest.",
    "tripadvisor.", "yelp.", "google.", "openstreetmap.org", "wikipedia.org", "wikidata.org", "foursquare.com",
    "goudengids.be", "pagesdor.be", "pagesjaunes.", "goldenpages.", "yellowpages.", "trustpilot.", "restaurantguru.",
    "thefork.", "deliveroo.", "ubereats.", "takeaway.com", "just-eat.", "booking.com", "expedia.", "hotels.com",
    "kbo-bce.", "companyweb.be", "trendstop.", "bizzy.org", "staatsbladmonitor.be", "north-data.", "opencorporates.",
    "indeed.", "glassdoor.", "jobat.be", "vdab.be", "cylex", "infobel.", "telefoonboek.", "1207.be", "1307.be",
    "mapcarta.", "waze.com", "apple.com/maps", "bing.com", "duckduckgo.com", "amazon.", "bol.com", "marktplaats.",
    "2dehands.be", "immoweb.be", "zimmo.be", "unizo.be", "voka.be", "openingsuren", "openinghours", "cybo.com",
    "menu.", "quandoo.", "zomato.", "resengo.", "kortrijk.be", "visitkortrijk", "uitinvlaanderen.", "streetdir",
)


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str

    @property
    def domain(self) -> str:
        return url_domain(self.url) or ""


def is_directory(url: str) -> bool:
    dom = (url_domain(url) or "").lower()
    return any(marker in dom for marker in DIRECTORY_DOMAINS)


def parse_ddg_html(html_text: str) -> list[SearchHit]:
    soup = BeautifulSoup(html_text, "lxml")
    hits: list[SearchHit] = []
    for result in soup.select("div.result"):
        if "result--ad" in " ".join(result.get("class", [])):
            continue
        a = result.select_one("a.result__a") or result.select_one("a.result-link")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlsplit(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            href = unquote(target) if target else ""
        if not href:
            continue
        snippet_el = result.select_one(".result__snippet")
        hits.append(SearchHit(url=href, title=a.get_text(" ", strip=True), snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "", provider="duckduckgo"))
    if not hits:  # lite endpoint layout
        for a in soup.select("a.result-link"):
            href = a.get("href", "")
            if href.startswith("//"):
                href = "https:" + href
            hits.append(SearchHit(url=href, title=a.get_text(" ", strip=True), snippet="", provider="duckduckgo"))
    return hits


class WebSearch:
    def __init__(self, http: HttpClient, provider: str = "auto", delay: float = 2.0) -> None:
        self.http = http
        self.delay = delay
        self.queries = 0
        self.failures = 0
        self.disabled_reason = ""
        if provider == "auto":
            provider = "google_cse" if env("GOOGLE_CSE_API_KEY") and env("GOOGLE_CSE_ID") else "duckduckgo"
        self.provider = provider

    @property
    def available(self) -> bool:
        return self.provider != "off" and not self.disabled_reason

    def search(self, query: str, max_results: int = 8) -> list[SearchHit]:
        if not self.available:
            return []
        self.queries += 1
        try:
            if self.provider == "google_cse":
                hits = self._google_cse(query, max_results)
            else:
                hits = self._duckduckgo(query)
        except Exception as exc:
            self.failures += 1
            log.warning("web search failed for %r: %s", query, exc)
            if self.failures >= 3:
                self.disabled_reason = f"search provider '{self.provider}' failing repeatedly ({exc})"
            return []
        return hits[:max_results]

    def _duckduckgo(self, query: str) -> list[SearchHit]:
        headers = {"Referer": "https://html.duckduckgo.com/", "Accept": "text/html"}
        resp = self.http.get(DDG_HTML_URL, params={"q": query, "kl": "be-nl"}, headers=headers, min_delay=self.delay, timeout=20)
        if resp.status_code in (202, 403, 429) or "anomaly" in resp.url:
            raise RuntimeError(f"DuckDuckGo rate-limited the request (HTTP {resp.status_code})")
        resp.raise_for_status()
        hits = parse_ddg_html(resp.text)
        if not hits and re.search(r"no results|geen resultaten", resp.text, re.IGNORECASE):
            return []
        return hits

    def _google_cse(self, query: str, max_results: int) -> list[SearchHit]:
        params = {"key": env("GOOGLE_CSE_API_KEY"), "cx": env("GOOGLE_CSE_ID"), "q": query, "num": min(10, max_results)}
        data = self.http.get_json(GOOGLE_CSE_URL, params=params, min_delay=0.5, timeout=20)
        return [SearchHit(url=i.get("link", ""), title=i.get("title", ""), snippet=i.get("snippet", ""), provider="google_cse")
                for i in data.get("items", [])]

    # ------------------------------------------------------------------ high level
    def find_website(self, company_name: str, city: str | None = None, country: str | None = None) -> SearchHit | None:
        """Best candidate for the company's own website, or None."""
        parts = [f'"{company_name}"']
        if city:
            parts.append(city)
        query = " ".join(parts)
        hits = self.search(query)
        key = name_key(company_name)
        tokens = [t for t in key.split() if len(t) > 2]
        best: tuple[int, SearchHit] | None = None
        for hit in hits:
            url = normalize_url(hit.url)
            if not url or is_directory(url):
                continue
            hay = name_key(f"{hit.title} {hit.snippet} {hit.domain}")
            score = 0
            if key and key in hay:
                score += 3
            score += sum(1 for t in tokens if t in hay)
            compact = re.sub(r"[^a-z0-9]", "", key)
            if compact and compact in hit.domain.replace("-", "").replace(".", ""):
                score += 4
            if score == 0:
                continue
            if best is None or score > best[0]:
                best = (score, SearchHit(url=url, title=hit.title, snippet=hit.snippet, provider=hit.provider))
        return best[1] if best else None
