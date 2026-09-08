"""Polite HTTP client: retries, per-host rate limiting, robots.txt, on-disk cache.

Every network access in the toolkit goes through :class:`HttpClient` so that
behaviour (User-Agent, timeouts, caching, politeness) is consistent and easy
to switch off in tests.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

log = logging.getLogger(__name__)

DEFAULT_UA = "DataHarvest/{version} (+https://github.com/MahmoudAsadi97/data-extraction_sample1{contact})"
MAX_BYTES = 3_000_000  # never read more than ~3 MB of a page


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
                    stale_if_error=True,
                )
            except Exception as exc:  # pragma: no cover - cache is optional
                log.warning("HTTP cache unavailable (%s); continuing without cache", exc)
                session = requests.Session()
        else:
            session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD", "POST"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=40)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
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
        """Check robots.txt for the URL (cached per host). Failures are treated as *allowed*."""
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            cached = self._robots.get(host, "unset")
        if cached == "unset":
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                resp = self.session.get(f"{host}/robots.txt", timeout=min(self.timeout, 10))
                if resp.status_code == 200 and resp.text:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None
            except Exception:
                rp = None
            with self._lock:
                self._robots[host] = rp
            cached = rp
        if cached is None:
            return True
        try:
            return cached.can_fetch(self.user_agent, url) or cached.can_fetch("*", url)
        except Exception:
            return True

    # ------------------------------------------------------------------ requests
    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            timeout: float | None = None, min_delay: float | None = None, allow_redirects: bool = True,
            check_robots: bool = False, stream: bool = False) -> requests.Response:
        """Low-level GET with politeness. Raises requests exceptions."""
        if check_robots and not self.allowed_by_robots(url):
            raise PermissionError(f"Blocked by robots.txt: {url}")
        self._wait_for_host(urlparse(url).netloc, min_delay)
        return self.session.get(url, params=params, headers=headers, timeout=timeout or self.timeout,
                                allow_redirects=allow_redirects, stream=stream)

    def post(self, url: str, *, data=None, json=None, headers: dict | None = None,
             timeout: float | None = None, min_delay: float | None = None) -> requests.Response:
        self._wait_for_host(urlparse(url).netloc, min_delay)
        return self.session.post(url, data=data, json=json, headers=headers, timeout=timeout or self.timeout)

    def fetch(self, url: str, *, check_robots: bool = True, timeout: float | None = None,
              min_delay: float | None = None, headers: dict | None = None) -> FetchResult:
        """Fetch a page and never raise: errors are reported in the result."""
        started = time.monotonic()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        try:
            if check_robots and not self.allowed_by_robots(url):
                return FetchResult(url=url, ok=False, error="blocked by robots.txt")
            resp = self.get(url, timeout=timeout, min_delay=min_delay, headers=headers, stream=True)
            ctype = resp.headers.get("Content-Type", "")
            body = b""
            for chunk in resp.iter_content(chunk_size=65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    break
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
            )
        except requests.exceptions.SSLError as exc:
            return FetchResult(url=url, ok=False, error=f"ssl error: {_short(exc)}", elapsed=time.monotonic() - started)
        except requests.exceptions.ConnectionError as exc:
            return FetchResult(url=url, ok=False, error=f"connection error: {_short(exc)}", elapsed=time.monotonic() - started)
        except requests.exceptions.Timeout:
            return FetchResult(url=url, ok=False, error="timeout", elapsed=time.monotonic() - started)
        except requests.exceptions.RequestException as exc:
            return FetchResult(url=url, ok=False, error=f"request failed: {_short(exc)}", elapsed=time.monotonic() - started)
        except Exception as exc:  # defensive: never let one page kill the run
            return FetchResult(url=url, ok=False, error=f"error: {_short(exc)}", elapsed=time.monotonic() - started)

    def get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None,
                 timeout: float | None = None, min_delay: float | None = None):
        resp = self.get(url, params=params, headers=headers, timeout=timeout, min_delay=min_delay)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"charset=([A-Za-z0-9_\-]+)", re.IGNORECASE)


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
