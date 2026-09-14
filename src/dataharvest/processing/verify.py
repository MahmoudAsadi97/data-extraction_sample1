"""Verification checks that do not need a full page fetch: e-mail domains (MX),
phone numbers, VAT checksums - plus the rules that turn per-field statuses
into one overall record status.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from ..http import HttpClient
from ..models import FieldStatus, Record, RecordStatus
from ..schema import Schema
from .normalize import email_domain, is_valid_email, phone_info

log = logging.getLogger(__name__)

DOH_URL = "https://dns.google/resolve"


@dataclass
class MxResult:
    domain: str
    deliverable: bool | None  # True = MX or A record found, False = domain cannot receive mail, None = unknown
    detail: str = ""


class MailDomainChecker:
    """MX lookup with a DNS-over-HTTPS fallback and a per-domain cache (thread-safe)."""

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http
        self.cache: dict[str, MxResult] = {}
        self._lock = threading.Lock()
        self.dns_failures = 0

    def check(self, domain: str) -> MxResult:
        domain = (domain or "").lower().strip(".")
        if not domain:
            return MxResult(domain, False, "no domain")
        with self._lock:
            if domain in self.cache:
                return self.cache[domain]
        result = self._resolve(domain)
        with self._lock:
            self.cache[domain] = result
        return result

    def _resolve(self, domain: str) -> MxResult:
        try:
            import dns.resolver

            resolver = dns.resolver.Resolver()
            resolver.lifetime = 5.0
            try:
                answers = resolver.resolve(domain, "MX")
                hosts = sorted(str(r.exchange).rstrip(".") for r in answers)
                if hosts == [""]:
                    return MxResult(domain, False, "domain explicitly refuses mail (Null MX)")
                if hosts and hosts != [""]:
                    return MxResult(domain, True, f"MX: {hosts[0]}")
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                pass
            except dns.resolver.NoNameservers:
                raise RuntimeError("resolver returned SERVFAIL/REFUSED") from None  # transient: fall back to DoH
            try:
                resolver.resolve(domain, "A")
                return MxResult(domain, True, "no MX record, but domain resolves (A record)")
            except dns.resolver.NXDOMAIN:
                return MxResult(domain, False, "domain does not exist (NXDOMAIN)")
            except dns.resolver.NoAnswer:
                return MxResult(domain, False, "no MX and no A record")
            except dns.resolver.NoNameservers:
                raise RuntimeError("resolver returned SERVFAIL/REFUSED") from None
        except ImportError:
            pass
        except Exception as exc:  # timeouts, no network
            self.dns_failures += 1
            log.debug("DNS lookup failed for %s: %s", domain, exc)
        return self._resolve_doh(domain)

    def _resolve_doh(self, domain: str) -> MxResult:
        if self.http is None:
            return MxResult(domain, None, "DNS unavailable")
        try:
            data = self.http.get_json(DOH_URL, params={"name": domain, "type": "MX"}, timeout=8, min_delay=0.1)
            status = data.get("Status")
            if status == 3:
                return MxResult(domain, False, "domain does not exist (NXDOMAIN via DoH)")
            if status != 0:
                return MxResult(domain, None, f"DNS lookup unavailable (DoH status {status})")
            mx = [a for a in data.get("Answer", []) if a.get("type") == 15]
            if any(str(a.get("data", "")).strip() == "0 ." for a in mx):
                return MxResult(domain, False, "domain explicitly refuses mail (Null MX via DoH)")
            if mx:
                return MxResult(domain, True, "MX found (DoH)")
            data_a = self.http.get_json(DOH_URL, params={"name": domain, "type": "A"}, timeout=8, min_delay=0.1)
            if data_a.get("Status") not in (0, 3):
                return MxResult(domain, None, "DNS address lookup unavailable (DoH)")
            if any(a.get("type") in (1, 28) for a in data_a.get("Answer", [])):
                return MxResult(domain, True, "no MX record, but domain resolves (DoH)")
            return MxResult(domain, False, "no MX and no A record (DoH)")
        except Exception as exc:
            return MxResult(domain, None, f"DNS unavailable ({str(exc)[:60]})")


# --------------------------------------------------------------------------- field checks


def verify_email_field(record: Record, checker: MailDomainChecker | None) -> None:
    fv = record.fields.get("email")
    if fv is None or fv.is_empty:
        return
    email = str(fv.value)
    if not is_valid_email(email):
        record.mark("email", FieldStatus.INVALID, "invalid e-mail syntax")
        record.flag("e-mail has an invalid format")
        return
    if checker is None:
        return
    mx = checker.check(email_domain(email) or "")
    record.checks["email_domain"] = {"domain": mx.domain, "deliverable": mx.deliverable, "detail": mx.detail}
    if mx.deliverable is False:
        record.mark("email", FieldStatus.INVALID, f"mail domain check failed: {mx.detail}")
        record.flag(f"e-mail domain cannot receive mail ({mx.detail})")
    elif mx.deliverable is True and fv.status == FieldStatus.UNVERIFIED:
        record.mark("email", FieldStatus.UNVERIFIED, "domain accepts mail; address itself not confirmed by a second source")
    elif mx.deliverable is None and fv.status == FieldStatus.UNVERIFIED:
        record.mark("email", FieldStatus.UNVERIFIED, "mail-domain check unavailable")


def verify_phone_field(record: Record, region: str) -> None:
    fv = record.fields.get("phone")
    if fv is None or fv.is_empty:
        return
    info = phone_info(str(fv.value), region)
    record.checks["phone"] = info
    if not info.get("valid"):
        record.mark("phone", FieldStatus.INVALID, info.get("reason", "invalid number"))
        record.flag("phone number is not valid for its region")
        return
    fv.note = (fv.note + "; " if fv.note else "") + f"{info['type']} number ({info['region']})"


# --------------------------------------------------------------------------- record status


def assign_record_status(record: Record, schema: Schema, *, ignore_missing: Iterable[str] = ()) -> RecordStatus:
    """Derive the overall status from field statuses, required fields and flags.

    ``ignore_missing``: required fields that may be absent without forcing NEEDS_REVIEW
    (used by the audit when a whole column is missing from the file).
    """
    name_field = schema.name_field
    if record.duplicate_of:
        record.status = RecordStatus.EXCLUDED
        return record.status
    if name_field and not record.has(name_field):
        record.status = RecordStatus.EXCLUDED
        record.flag(f"missing {schema.field(name_field).display.lower() if schema.field(name_field) else name_field} - record unusable")
        return record.status

    statuses = {name: fv.status for name, fv in record.fields.items() if not fv.is_empty}
    invalid = [n for n, s in statuses.items() if s == FieldStatus.INVALID]
    conflicts = [n for n, s in statuses.items() if s == FieldStatus.CONFLICT]
    verified = [n for n, s in statuses.items() if s == FieldStatus.VERIFIED]
    ignored = set(ignore_missing)
    missing_required = [f.name for f in schema.required if not record.has(f.name) and f.name not in ignored]
    review_flags = [f for f in record.flags if not f.startswith("note:")]

    if conflicts or invalid or missing_required or any("review" in f.lower() or "duplicate" in f.lower() for f in review_flags):
        record.status = RecordStatus.NEEDS_REVIEW
    else:
        contact_fields = [f.name for f in schema.fields if f.type in ("email", "url", "phone", "vat")]
        contact_verified = [n for n in verified if n in contact_fields]
        name_verified = name_field in verified
        if len(contact_verified) >= 2 or (contact_verified and name_verified):
            record.status = RecordStatus.VERIFIED
        elif verified:
            record.status = RecordStatus.PARTIALLY_VERIFIED
        else:
            record.status = RecordStatus.UNVERIFIED
    return record.status


def compute_completeness(record: Record, schema: Schema) -> float:
    """Share of schema fields with a value (required fields count double)."""
    total = 0
    filled = 0
    for f in schema.fields:
        weight = 2 if f.required else 1
        total += weight
        if record.has(f.name):
            filled += weight
    record.completeness = round(100.0 * filled / total, 1) if total else 0.0
    return record.completeness
