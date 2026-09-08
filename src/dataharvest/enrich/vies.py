"""EU VIES VAT-number validation (official European Commission service, no key needed).

``GET https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{country}/vat/{number}``
returns whether the number exists and, for most member states, the registered
name and address. This turns a scraped VAT number into a *verified* legal
identity - and exposes numbers that belong to a different entity.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from ..http import HttpClient
from ..processing.normalize import is_valid_vat, normalize_vat

log = logging.getLogger(__name__)

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{country}/vat/{number}"
TRANSIENT_ERRORS = {"MS_MAX_CONCURRENT_REQ", "MS_UNAVAILABLE", "SERVICE_UNAVAILABLE", "TIMEOUT", "GLOBAL_MAX_CONCURRENT_REQ", "MS_MAX_CONCURRENT_REQ_TIME"}


@dataclass
class ViesResult:
    vat: str
    valid: bool | None  # None = could not be checked
    name: str = ""
    address: str = ""
    error: str = ""
    request_date: str = ""

    @property
    def checked(self) -> bool:
        return self.valid is not None

    @property
    def city(self) -> str:
        """Best-effort city from the registered address ('Street 5\\n8510 Kortrijk' -> 'Kortrijk')."""
        for line in reversed(self.address.splitlines()):
            parts = line.strip().split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                return parts[1].strip()
        return ""

    @property
    def postcode(self) -> str:
        for line in reversed(self.address.splitlines()):
            parts = line.strip().split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                return parts[0]
        return ""


class ViesClient:
    def __init__(self, http: HttpClient, delay: float = 1.0) -> None:
        self.http = http
        self.delay = delay
        self.cache: dict[str, ViesResult] = {}
        self.requests_made = 0
        self.unavailable_streak = 0

    def check(self, vat: str, country_hint: str = "BE") -> ViesResult:
        canonical = normalize_vat(vat, country_hint) or ""
        if canonical in self.cache:
            return self.cache[canonical]
        result = self._check(canonical)
        self.cache[canonical] = result
        return result

    def _check(self, vat: str) -> ViesResult:
        if not vat or len(vat) < 4:
            return ViesResult(vat=vat, valid=False, error="malformed")
        local = is_valid_vat(vat)
        if local is False:
            return ViesResult(vat=vat, valid=False, error="format/checksum invalid")
        if self.unavailable_streak >= 5:
            return ViesResult(vat=vat, valid=None, error="VIES unavailable (skipped after repeated failures)")
        country, number = vat[:2], vat[2:]
        url = VIES_URL.format(country=country, number=number)
        last_error = ""
        for attempt in range(3):
            self.requests_made += 1
            try:
                resp = self.http.get(url, timeout=20, min_delay=self.delay, use_cache=attempt == 0)
                data = resp.json() if resp.content else {}
            except (requests.RequestException, ValueError) as exc:
                last_error = f"request failed: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            wrappers = data.get("errorWrappers") or []
            user_error = (data.get("userError") or "").upper()
            error_code = (wrappers[0].get("error") if wrappers else "") or (user_error if user_error not in ("VALID", "INVALID", "") else "")
            if error_code and error_code.upper() in TRANSIENT_ERRORS:
                last_error = error_code
                time.sleep(2.0 * (attempt + 1))
                continue
            if error_code:
                self.unavailable_streak = 0
                return ViesResult(vat=vat, valid=None, error=error_code, request_date=data.get("requestDate", ""))
            self.unavailable_streak = 0
            valid = bool(data.get("isValid")) if "isValid" in data else (bool(data.get("valid")) if "valid" in data else None)
            name = (data.get("name") or "").strip()
            address = (data.get("address") or "").strip()
            if name == "---":
                name = ""
            if address == "---":
                address = ""
            return ViesResult(vat=vat, valid=valid, name=name, address=address, request_date=data.get("requestDate", ""))
        self.unavailable_streak += 1
        return ViesResult(vat=vat, valid=None, error=f"VIES not reachable ({last_error})")
