"""Company-website enrichment: fetch the homepage (+ contact/about pages) and
extract e-mails, phone numbers, social profiles, VAT numbers and the page title.

Also acts as the *website liveness check* (one fetch serves both purposes)
and checks whether the company name actually appears on the site.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

import phonenumbers
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from ..http import FetchResult, HttpClient
from ..processing.normalize import (
    email_domain,
    is_valid_email,
    is_valid_vat,
    name_key,
    normalize_email,
    normalize_phone,
    normalize_social,
    normalize_url,
    normalize_vat,
    social_network,
    url_domain,
)

log = logging.getLogger(__name__)

CONTACT_LINK_RE = re.compile(
    r"contact|contacteer|kontakt|over[\s-]*ons|about|wie[\s-]*zijn[\s-]*we|impressum|colofon|legal|mentions[\s-]*l[ée]gales|"
    r"algemene[\s-]*voorwaarden|privacy|team|bereikbaarheid|info|reserv|praktisch",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
OBFUSCATED_EMAIL_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s@\s|&#64;)\s*([A-Za-z0-9.\-]+)\s*(?:\[dot\]|\(dot\)|\.)\s*([A-Za-z]{2,24})",
    re.IGNORECASE,
)
EMAIL_JUNK_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|css|js|pdf)$|example\.|@sentry|wixpress|@2x|@3x|^\d+x\d*@|noreply|no-reply|"
    r"@(domain|email|mail|yourdomain|company|site)\.(com|be|nl)$|@[0-9.]+$|^(email|mail|name|user|info)@(domain|example|email)\b",
    re.IGNORECASE,
)
GENERIC_LOCALPARTS = ("info", "contact", "hello", "hallo", "welkom", "office", "mail", "admin", "sales", "reservatie", "reservaties", "reservations", "booking")
# Belgian enterprise / VAT numbers: "BE 0629.985.405", "BTW BE0629985405", "Ondernemingsnummer 0629 985 405"
VAT_CONTEXT_RE = re.compile(
    r"(?:\bBE\s?[01]\d{3}[.\s]?\d{3}[.\s]?\d{3}\b)|"
    r"(?:(?:BTW|TVA|VAT|KBO|BCE|RPR|RPM|ondernemingsnummer|ondernemingsnr|enterprise\s+number|company\s+number|"
    r"num[ée]ro\s+d.entreprise|btw[\s-]*nummer|btw[\s-]*nr|vat[\s-]*number)\W{0,25}(?:BE\s?)?([01]\d{3}[.\s]?\d{3}[.\s]?\d{3})\b)",
    re.IGNORECASE,
)
SHARE_LINK_RE = re.compile(r"sharer|/share|intent/|/dialog/|plugins/|/tr\?", re.IGNORECASE)


@dataclass
class SiteExtraction:
    url: str
    ok: bool
    status: int | None = None
    final_url: str = ""
    error: str = ""
    title: str = ""
    description: str = ""
    pages_fetched: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    vat_numbers: list[str] = field(default_factory=list)
    name_match: bool | None = None
    name_similarity: int = 0
    text_sample: str = ""
    from_cache: bool = False
    blocked: bool = False
    note: str = ""  # informational (e.g. reached over http instead of https)

    @property
    def liveness(self) -> str:
        if self.ok:
            if self.final_url and url_domain(self.final_url) != url_domain(self.url):
                return "redirected"
            return "live"
        if self.blocked:
            return "blocked"  # robots.txt, bot challenge or rate limit: could not be checked, not proven dead
        if self.status and 400 <= self.status < 600:
            return "dead"
        return "unreachable"


def _clean_html_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return html.unescape(text)


def extract_emails(soup: BeautifulSoup, text: str) -> list[str]:
    found: list[str] = []
    for a in soup.select('a[href^="mailto:"]'):
        href = unquote(a.get("href", ""))
        for part in href[7:].split(","):
            email = normalize_email(part)
            if email:
                found.append(email)
    for m in EMAIL_RE.finditer(text):
        found.append(m.group(0).lower())
    for m in OBFUSCATED_EMAIL_RE.finditer(text):
        found.append(f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower())
    seen: set[str] = set()
    result = []
    for email in found:
        email = email.strip(".,;:")
        if email in seen or not is_valid_email(email) or EMAIL_JUNK_RE.search(email):
            continue
        seen.add(email)
        result.append(email)
    return result


def extract_phones(soup: BeautifulSoup, text: str, region: str) -> list[str]:
    found: list[str] = []
    for a in soup.select('a[href^="tel:"]'):
        num = normalize_phone(unquote(a.get("href", ""))[4:], region)
        if num:
            found.append(num)
    try:
        for match in phonenumbers.PhoneNumberMatcher(text[:200_000], region.upper(), leniency=phonenumbers.Leniency.VALID):
            found.append(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164))
    except Exception:  # pragma: no cover - matcher is defensive already
        pass
    seen: set[str] = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def extract_socials(soup: BeautifulSoup, page_url: str) -> dict[str, list[str]]:
    """All social profile links per network, in page order (share/intent links skipped)."""
    socials: dict[str, list[str]] = {}
    for a in soup.select("a[href]"):
        href = urljoin(page_url, a.get("href", "").strip())
        network = social_network(href)
        if not network or SHARE_LINK_RE.search(href):
            continue
        canonical = normalize_social(href, network)
        if canonical and canonical not in socials.setdefault(network, []):
            socials[network].append(canonical)
    return socials


def pick_social(candidates: list[str], company_name: str | None) -> str | None:
    """Prefer the profile whose handle resembles the company name (a footer often links the web agency too)."""
    if not candidates:
        return None
    key = name_key(company_name or "").replace(" ", "")
    if not key:
        return candidates[0]

    def score(url: str) -> int:
        handle = re.sub(r"[^a-z0-9]", "", urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower())
        return int(fuzz.partial_ratio(key, handle)) if handle else 0

    best = max(candidates, key=score)
    return best if score(best) >= 60 else candidates[0]


def extract_vat_numbers(text: str, country: str) -> list[str]:
    found: list[str] = []
    for m in VAT_CONTEXT_RE.finditer(text):
        raw = m.group(1) or m.group(0)
        vat = normalize_vat(raw, country)
        if vat and is_valid_vat(vat) and vat not in found:
            found.append(vat)
    return found


def _candidate_pages(soup: BeautifulSoup, base_url: str, max_pages: int) -> list[str]:
    base_domain = url_domain(base_url)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if url_domain(full) != base_domain:
            continue
        normalized = normalize_url(full)
        if not normalized or normalized in seen or normalized == normalize_url(base_url):
            continue
        label = f"{a.get_text(' ', strip=True)} {urlsplit(full).path}"
        if CONTACT_LINK_RE.search(label):
            score = 3 if re.search(r"contact|kontakt", label, re.IGNORECASE) else 1
            scored.append((score, normalized))
            seen.add(normalized)
    scored.sort(key=lambda t: -t[0])
    return [u for _, u in scored[: max(0, max_pages - 1)]]


def name_matches(company_name: str | None, title: str, text: str) -> tuple[bool, int]:
    """Does the company name appear on the page? Returns (match, similarity 0-100)."""
    key = name_key(company_name or "")
    if not key:
        return False, 0
    haystack = name_key(f"{title} {text[:5000]}")
    if key in haystack:
        return True, 100
    score = int(fuzz.partial_ratio(key, haystack[:20000])) if len(key) >= 4 else 0
    return score >= 90, score


class WebsiteEnricher:
    def __init__(self, http: HttpClient, *, country: str = "BE", max_pages: int = 3, timeout: float = 15.0,
                 respect_robots: bool = True) -> None:
        self.http = http
        self.country = country
        self.max_pages = max_pages
        self.timeout = timeout
        self.respect_robots = respect_robots

    def analyse(self, url: str, company_name: str | None = None) -> SiteExtraction:
        first: FetchResult = self.http.fetch(url, check_robots=self.respect_robots, timeout=self.timeout)
        if not first.ok and not first.blocked and first.status is None and url.lower().startswith("https://"):
            # many small-business sites still have no TLS: a connection/SSL failure on https is retried on http
            retry = self.http.fetch("http://" + url[8:], check_robots=self.respect_robots, timeout=self.timeout)
            if retry.ok:
                retry.error = "https not available - reached over plain http"
                first = retry
        result = SiteExtraction(url=url, ok=first.ok, status=first.status, final_url=first.final_url or url,
                                error=first.error, from_cache=first.from_cache, blocked=first.blocked)
        http_note = first.error if first.ok and first.error else ""
        result.error = ""
        if first.ok and not first.text.strip():
            result.error = "empty response"
        if not first.ok or not first.is_html:
            if not first.ok:
                result.error = first.error
            elif not first.is_html:
                result.error = f"not an HTML page ({first.content_type or 'unknown type'})"
            return result
        result.error = ""
        result.note = http_note
        pages = [(result.final_url, first.text)]
        soup = BeautifulSoup(first.text, "lxml")
        for extra in _candidate_pages(soup, result.final_url, self.max_pages):
            fetched = self.http.fetch(extra, check_robots=self.respect_robots, timeout=self.timeout)
            if fetched.ok and fetched.is_html:
                pages.append((fetched.final_url or extra, fetched.text))
        title_tag = soup.find("title")
        result.title = (title_tag.get_text(" ", strip=True) if title_tag else "")[:200]
        meta = soup.find("meta", attrs={"name": re.compile("^description$", re.IGNORECASE)}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if meta and meta.get("content"):
            result.description = str(meta.get("content")).strip()[:500]
        all_text = []
        social_candidates: dict[str, list[str]] = {}
        for page_url, page_html in pages:
            page_soup = soup if page_url == result.final_url else BeautifulSoup(page_html, "lxml")
            result.pages_fetched.append(page_url)
            text = _clean_html_text(page_soup)
            all_text.append(text)
            for email in extract_emails(page_soup, text):
                if email not in result.emails:
                    result.emails.append(email)
            for phone in extract_phones(page_soup, text, self.country):
                if phone not in result.phones:
                    result.phones.append(phone)
            for network, links in extract_socials(page_soup, page_url).items():
                known = social_candidates.setdefault(network, [])
                known.extend(link for link in links if link not in known)
            for vat in extract_vat_numbers(text, self.country):
                if vat not in result.vat_numbers:
                    result.vat_numbers.append(vat)
        joined = " ".join(all_text)
        result.text_sample = joined[:3000]
        for network, links in social_candidates.items():
            chosen = pick_social(links, company_name)
            if chosen:
                result.socials[network] = chosen
        result.emails = rank_emails(result.emails, url_domain(result.final_url))
        if company_name:
            result.name_match, result.name_similarity = name_matches(company_name, result.title, joined)
        return result


def rank_emails(emails: list[str], site_domain: str | None) -> list[str]:
    """Own-domain addresses first, generic mailboxes (info@, contact@) before personal ones."""

    def score(email: str) -> tuple[int, int, str]:
        dom = email_domain(email) or ""
        own = 0 if site_domain and (dom == site_domain or dom.endswith("." + site_domain) or site_domain.endswith("." + dom)) else 1
        generic = 0 if email.split("@")[0].lower() in GENERIC_LOCALPARTS else 1
        return (own, generic, email)

    return sorted(dict.fromkeys(emails), key=score)
