"""Polite HTTP client: retries, per-host rate limiting, robots.txt, on-disk cache.

Every network access in the toolkit goes through :class:`HttpClient` so that
behaviour (User-Agent, timeouts, caching, politeness) is consistent and easy
to switch off in tests.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .network import UnsafeURL, validate_public_url

log = logging.getLogger(__name__)

DEFAULT_UA = "DataHarvest/{version} (+https://github.com/MahmoudAsadi97/data-extraction_sample1{contact})"
MAX_BYTES = 3_000_000  # never read more than ~3 MB of a page
# Services whose transient errors are worth waiting for (see _build_session)
API_HOSTS = (
    "overpass-api.de", "overpass.kumi.systems", "overpass.private.coffee", "nominatim.openstreetmap.org",
    "query.wikidata.org", "www.wikidata.org", "ec.europa.eu", "html.duckduckgo.com", "lite.duckduckgo.com",
    "www.googleapis.com", "places.googleapis.com", "api.apollo.io", "dns.google", "sheets.googleapis.com",
)


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int | None = None
    final_url: str = ""
    text: str = ""
    content_type: str = ""
    error: str = ""
    from_cache: bool = False
    elapsed: float = 0.0
    blocked: bool = False  # access refused (robots.txt, bot challenge, rate limit) - not proof the site is dead
    dns_failure: bool = False  # the host name could not be resolved
    dns_temporary: bool = False  # ... because the local resolver failed (WSL/VPN), not because the domain is gone

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower() or self.text.lstrip()[:15].lower().startswith("<!doctype html")


class HttpClient:
    """A requests session with sane defaults for web research."""

    def __init__(
        self,
        *,
        user_agent: str = "",
        contact_email: str = "",
        timeout: float = 20.0,
        cache: bool = True,
        cache_dir: str | Path = "data/cache",
        cache_expire_hours: int = 24 * 7,
        min_delay_per_host: float = 0.5,
        max_retries: int = 3,
        respect_robots: bool = True,
    ) -> None:
        contact = f"; contact: {contact_email}" if contact_email else ""
        self.user_agent = user_agent or DEFAULT_UA.format(version=__version__, contact=contact)
        self.timeout = timeout
        self.min_delay_per_host = min_delay_per_host
        self.respect_robots = respect_robots
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

        self.session = self._build_session(cache, Path(cache_dir), cache_expire_hours, max_retries)
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8,fr;q=0.7",
            }
        )

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _build_session(cache: bool, cache_dir: Path, expire_hours: int, max_retries: int) -> requests.Session:
        session: requests.Session
        if cache:
            try:
                import requests_cache

                cache_dir.mkdir(parents=True, exist_ok=True)
                session = requests_cache.CachedSession(
                    str(cache_dir / "http_cache"),
                    backend="sqlite",
                    expire_after=expire_hours * 3600,
                    allowable_methods=("GET", "POST"),
                    allowable_codes=(200, 203, 300, 301, 302, 404, 410),
                    filter_fn=cacheable,
                    stale_if_error=False,
                )
            except Exception as exc:  # pragma: no cover - cache is optional
                log.warning("HTTP cache unavailable (%s); continuing without cache", exc)
                session = requests.Session()
        else:
            session = requests.Session()
        common = dict(
            allowed_methods=frozenset({"GET", "HEAD", "POST"}),
            respect_retry_after_header=False,  # never let one site's Retry-After (minutes/hours) stall a worker
            raise_on_status=False,  # hand the final 4xx/5xx response back instead of raising RetryError
        )
        # APIs we depend on get patient retries; arbitrary company websites get one quick retry at most,
        # otherwise a slow or dead site costs (timeout x retries x pages) per record.
        api_retry = Retry(total=max_retries, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504), **common)
        site_retry = Retry(total=1, connect=1, read=0, backoff_factor=0.5, status_forcelist=(502, 503, 504), **common)
        site_adapter = HTTPAdapter(max_retries=site_retry, pool_connections=20, pool_maxsize=40)
        api_adapter = HTTPAdapter(max_retries=api_retry, pool_connections=10, pool_maxsize=20)
        session.mount("https://", site_adapter)
        session.mount("http://", site_adapter)
        for host in API_HOSTS:  # longest prefix wins in requests, so these override the defaults above
            session.mount(f"https://{host}/", api_adapter)
        return session

    # ------------------------------------------------------------------ politeness
    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _wait_for_host(self, host: str, min_delay: float | None = None) -> None:
        delay = self.min_delay_per_host if min_delay is None else min_delay
        if delay <= 0:
            return
        lock = self._host_lock(host)
        with lock:
            last = self._last_request.get(host, 0.0)
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_request[host] = time.monotonic()

    def allowed_by_robots(self, url: str) -> bool:
        """Check the matching agent rule; unavailable robots files defer collection."""
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            cached = self._robots.get(host, "unset")
        if cached == "unset":
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                resp = self.fetch(f"{host}/robots.txt", timeout=min(self.timeout, 10), check_robots=False)
                if resp.status == 200 and resp.text:
                    rp.parse(resp.text.splitlines())
                elif resp.status in (404, 410):
                    rp = None
                else:
                    rp.parse(["User-agent: *", "Disallow: /"])
            except Exception:
                rp.parse(["User-agent: *", "Disallow: /"])
            with self._lock:
                self._robots[host] = rp
            cached = rp
        if cached is None:
            return True
        try:
            return cached.can_fetch(self.user_agent, url)
        except Exception:
            return False

    # ------------------------------------------------------------------ requests
    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None, min_delay: float | None = None, allow_redirects: bool = True,
            check_robots: bool = False, stream: bool = False, use_cache: bool = True) -> requests.Response:
        """Low-level GET with politeness. Raises requests exceptions."""
        for _ in range(6):
            validate_public_url(url)
            if check_robots and not self.allowed_by_robots(url):
                raise PermissionError(f"Blocked by robots.txt: {url}")
            self._wait_for_host(urlparse(url).netloc, min_delay)
            with self._cache_scope(use_cache):
                response = self.session.get(url, params=params, headers=headers, timeout=timeout or self.timeout,
                                            allow_redirects=False, stream=stream)
            if not allow_redirects or not response.is_redirect:
                return response
            target = urljoin(response.url, response.headers["Location"])
            response.close()
            if urlparse(target).netloc != urlparse(url).netloc:
                headers = None  # never forward caller API credentials to another origin
            url, params = target, None
        raise requests.TooManyRedirects("Redirect limit exceeded (5 hops)")

    def post(self, url: str, *, data=None, json=None, headers: dict | None = None,
             timeout: float | None = None, min_delay: float | None = None, use_cache: bool = True) -> requests.Response:
        validate_public_url(url)
        self._wait_for_host(urlparse(url).netloc, min_delay)
        with self._cache_scope(use_cache):
            response = self.session.post(url, data=data, json=json, headers=headers, timeout=timeout or self.timeout,
                                         allow_redirects=False)
        if response.is_redirect:
            response.close()
            raise UnsafeURL("API POST redirects are not followed")
        return response

    def _cache_scope(self, use_cache: bool):
        """Context manager that bypasses the on-disk cache for one request when asked."""
        disabler = getattr(self.session, "cache_disabled", None)
        if use_cache or disabler is None:
            return contextlib.nullcontext()
        return disabler()

    def fetch(self, url: str, *, check_robots: bool = True, timeout: float | None = None,
              min_delay: float | None = None, headers: dict | None = None) -> FetchResult:
        """Fetch a page and never raise: errors are reported in the result."""
        started = time.monotonic()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        try:
            validate_public_url(url)
            if check_robots and not self.allowed_by_robots(url):
                return FetchResult(url=url, ok=False, error="blocked by robots.txt", blocked=True)
            resp = self.get(url, timeout=timeout, min_delay=min_delay, headers=headers, stream=True, check_robots=check_robots)
            ctype = resp.headers.get("Content-Type", "")
            body = b""
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return FetchResult(url=url, ok=False, error="page exceeds 3 MB limit", blocked=True)
            finally:
                resp.close()
            text = decode_body(body, ctype)
            return FetchResult(
                url=url,
                ok=200 <= resp.status_code < 400,
                status=resp.status_code,
                final_url=resp.url,
                text=text,
                content_type=ctype,
                from_cache=bool(getattr(resp, "from_cache", False)),
                elapsed=time.monotonic() - started,
                blocked=resp.status_code in (401, 403, 429, 503) and _looks_like_challenge(resp, text),
            )
        except (UnsafeURL, PermissionError) as exc:
            return FetchResult(url=url, ok=False, error=str(exc), blocked=True)
        except requests.exceptions.SSLError as exc:
            return FetchResult(url=url, ok=False, error=f"ssl error: {_short(exc)}", elapsed=time.monotonic() - started)
        except requests.exceptions.ConnectionError as exc:
            message = str(exc)
            lowered = message.lower()
            if any(marker in lowered for marker in ("nameresolutionerror", "failed to resolve", "getaddrinfo", "name resolution", "nodename nor servname")):
                temporary = "Temporary failure" in message or "try again" in message.lower()
                return FetchResult(url=url, ok=False, error="dns: temporary failure in name resolution" if temporary else "dns: host name does not resolve",
                                   dns_failure=True, dns_temporary=temporary, elapsed=time.monotonic() - started)
            return FetchResult(url=url, ok=False, error=f"connection error: {_short(exc)}", elapsed=time.monotonic() - started)
        except requests.exceptions.Timeout:
            return FetchResult(url=url, ok=False, error="timeout", elapsed=time.monotonic() - started)
        except requests.exceptions.RequestException as exc:
            return FetchResult(url=url, ok=False, error=f"request failed: {_short(exc)}", elapsed=time.monotonic() - started)
        except Exception as exc:  # defensive: never let one page kill the run
            return FetchResult(url=url, ok=False, error=f"error: {_short(exc)}", elapsed=time.monotonic() - started)

    def get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                 timeout: float | None = None, min_delay: float | None = None, use_cache: bool = True):
        resp = self.get(url, params=params, headers=headers, timeout=timeout, min_delay=min_delay, use_cache=use_cache)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"charset=([A-Za-z0-9_\-]+)", re.IGNORECASE)


_CHALLENGE_RE = re.compile(r"cloudflare|captcha|access denied|just a moment|bot detection|rate limit|too many requests|attention required", re.IGNORECASE)


def _looks_like_challenge(resp: requests.Response, text: str) -> bool:
    if resp.status_code in (401, 429):
        return True
    server = (resp.headers.get("Server") or "").lower()
    return bool(_CHALLENGE_RE.search(text[:5000])) or "cloudflare" in server or "cf-ray" in {k.lower() for k in resp.headers}


def dns_healthy(hosts: tuple[str, ...] = ("overpass-api.de", "www.openstreetmap.org", "duckduckgo.com"), timeout: float = 5.0) -> bool:
    """True when at least one well-known host resolves - used to tell 'dead website' from 'broken local DNS'."""
    import socket

    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        for host in hosts:
            try:
                socket.getaddrinfo(host, 443)
                return True
            except OSError:
                continue
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def cacheable(response: requests.Response) -> bool:
    """requests-cache filter: never store answers that are really transient errors."""
    url = response.url or ""
    try:
        if "/api/interpreter" in url:  # Overpass answers HTTP 200 with a 'remark' on timeouts/overload
            data = response.json()
            remark = str(data.get("remark", ""))
            return not ("runtime error" in remark.lower() or (not data.get("elements") and remark))
        if "vies/rest-api" in url:  # VIES busy/unavailable answers come back as HTTP 200 too
            data = response.json()
            if data.get("errorWrappers"):
                return False
            return str(data.get("userError", "")).upper() in ("", "VALID", "INVALID")
    except ValueError:
        return False
    return True


def decode_body(body: bytes, content_type: str = "") -> str:
    """Decode a response body: header charset, then <meta charset>, then UTF-8, then Windows-1252."""
    candidates: list[str] = []
    m = _HEADER_CHARSET.search(content_type or "")
    if m:
        candidates.append(m.group(1))
    m2 = _META_CHARSET.search(body[:4096])
    if m2:
        candidates.append(m2.group(1).decode("ascii", "ignore"))
    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def _short(exc: BaseException, limit: int = 140) -> str:
    msg = str(exc).replace("\n", " ")
    if "Max retries exceeded" in msg and "(" in msg:
        # keep only the root cause part of urllib3's verbose message
        msg = msg[msg.rfind("(") :]
    return msg[:limit]
