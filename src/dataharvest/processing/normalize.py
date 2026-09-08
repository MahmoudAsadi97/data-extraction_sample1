"""Normalisation of raw values into one consistent format per field type.

Consistent formatting is what makes a database usable: one phone format,
lower-case e-mails, canonical URLs, VAT numbers without dots, and so on.
Everything here is pure (no network) and unit-tested.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

# --------------------------------------------------------------------------- text

_WS = re.compile(r"\s+")
LEGAL_FORMS = (
    "bvba", "bv", "nv", "sa", "sprl", "srl", "cv", "cvba", "comm.v", "commv", "vof", "snc", "vzw", "asbl",
    "ltd", "limited", "plc", "gmbh", "ag", "inc", "inc.", "llc", "corp", "corporation", "co", "company",
    "b.v.", "n.v.", "s.a.", "s.p.r.l.", "s.r.l.", "ebvba", "eenmanszaak",
)
_LEGAL_RE = re.compile(r"\b(" + "|".join(re.escape(x) for x in LEGAL_FORMS) + r")\b\.?", re.IGNORECASE)


def clean_text(value: Any, *, max_length: int | None = None) -> str | None:
    """Trim, collapse whitespace, drop control characters. None for empty."""
    if value is None:
        return None
    text = str(value)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    text = _WS.sub(" ", text).strip()
    if not text:
        return None
    if max_length and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_name(value: Any) -> str | None:
    """Clean a company / product name for display (keeps original casing)."""
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("’", "'").replace("`", "'").strip(" -–|,;:")
    return text or None


def name_key(value: Any) -> str:
    """Aggressive normalisation used for matching: lower-case, no accents, no legal forms, no punctuation."""
    text = clean_text(value) or ""
    text = strip_accents(text).lower()
    text = _LEGAL_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------- e-mail

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}$")


def normalize_email(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.strip("<>\"' ,;")
    if text.lower().startswith("mailto:"):
        text = text[7:]
    text = text.split("?", 1)[0].strip()
    text = text.replace(" [at] ", "@").replace("(at)", "@").replace("[at]", "@")
    text = text.replace(" [dot] ", ".").replace("[dot]", ".").replace("(dot)", ".")
    text = text.replace(" ", "")
    if "@" not in text:
        return None
    local, _, domain = text.rpartition("@")
    email = f"{local}@{domain.lower()}"
    return email


def is_valid_email(value: str | None) -> bool:
    if not value:
        return False
    if ".." in value or value.startswith(".") or value.endswith("."):
        return False
    return bool(EMAIL_RE.match(value))


def email_domain(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    return value.rsplit("@", 1)[1].lower()


# --------------------------------------------------------------------------- URLs

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref"}


def normalize_url(value: Any) -> str | None:
    """Canonical URL: scheme added, host lower-cased, tracking params and fragments dropped."""
    text = clean_text(value)
    if not text:
        return None
    text = text.strip("<>\"' ,;")
    if text.lower().startswith("mailto:") or "@" in text.split("/")[0] and not text.lower().startswith("http"):
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        text = "https://" + text.lstrip("/")
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = parts.hostname
    if not host or "." not in host:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    netloc = host.lower()
    if parts.port and not (scheme == "http" and parts.port == 80) and not (scheme == "https" and parts.port == 443):
        netloc = f"{netloc}:{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_domain(value: str | None) -> str | None:
    """Registrable-ish domain used for matching: host without a leading 'www.'."""
    if not value:
        return None
    try:
        host = urlsplit(value if "://" in value else "https://" + value).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


# --------------------------------------------------------------------------- phone


def normalize_phone(value: Any, region: str = "BE") -> str | None:
    """Return the number in E.164 (+3256205588) or None if it cannot be parsed."""
    text = clean_text(value)
    if not text:
        return None
    text = text.split(";")[0].split("/")[0] if text.count("+") > 1 or ";" in text else text
    text = text.strip()
    if text.lower().startswith("tel:"):
        text = text[4:]
    try:
        num = phonenumbers.parse(text, region.upper() if region else None)
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(num):
        return None
    return phonenumbers.format_number(num, PhoneNumberFormat.E164)


def phone_info(value: str | None, region: str = "BE") -> dict[str, Any]:
    """Validate a phone number and describe it (valid / type / national format)."""
    if not value:
        return {"valid": False, "reason": "missing"}
    try:
        num = phonenumbers.parse(value, region.upper() if region else None)
    except NumberParseException as exc:
        return {"valid": False, "reason": f"unparseable ({exc._msg})"}
    valid = phonenumbers.is_valid_number(num)
    ntype = phonenumbers.number_type(num)
    type_name = {
        PhoneNumberType.MOBILE: "mobile",
        PhoneNumberType.FIXED_LINE: "fixed",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed/mobile",
        PhoneNumberType.TOLL_FREE: "toll-free",
        PhoneNumberType.PREMIUM_RATE: "premium",
        PhoneNumberType.VOIP: "voip",
    }.get(ntype, "other" if valid else "unknown")
    return {
        "valid": valid,
        "type": type_name,
        "e164": phonenumbers.format_number(num, PhoneNumberFormat.E164),
        "international": phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL),
        "national": phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL),
        "region": phonenumbers.region_code_for_number(num) or "",
        "reason": "" if valid else "not a valid number for its region",
    }


def format_phone_display(value: str | None, region: str = "BE") -> str | None:
    info = phone_info(value, region)
    return info.get("international") if info.get("valid") else value


# --------------------------------------------------------------------------- postcode

POSTCODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "BE": re.compile(r"^[1-9]\d{3}$"),
    "NL": re.compile(r"^[1-9]\d{3} ?[A-Z]{2}$"),
    "FR": re.compile(r"^\d{5}$"),
    "DE": re.compile(r"^\d{5}$"),
    "LU": re.compile(r"^L?-?\d{4}$"),
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$"),
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
}


def normalize_postcode(value: Any, country: str = "BE") -> str | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.upper().replace("B-", "").strip()
    if country.upper() == "NL":
        text = re.sub(r"^(\d{4})\s*([A-Z]{2})$", r"\1 \2", text)
    return text


def is_valid_postcode(value: str | None, country: str = "BE") -> bool | None:
    """True/False when a pattern is known for the country, None when unknown."""
    if not value:
        return False
    pattern = POSTCODE_PATTERNS.get(country.upper())
    if pattern is None:
        return None
    return bool(pattern.match(value))


# --------------------------------------------------------------------------- VAT / enterprise numbers

VAT_PATTERNS: dict[str, re.Pattern[str]] = {
    "BE": re.compile(r"^BE[01]\d{9}$"),
    "NL": re.compile(r"^NL\d{9}B\d{2}$"),
    "FR": re.compile(r"^FR[A-Z0-9]{2}\d{9}$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "LU": re.compile(r"^LU\d{8}$"),
}


def normalize_vat(value: Any, country: str = "BE") -> str | None:
    """Canonical VAT id, e.g. 'BE0629985405' (no spaces/dots)."""
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"[\s.\-/]", "", text.upper())
    for prefix in ("BTW", "TVA", "VAT", "KBO", "BCE", "MWST", "USTID"):
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip(":")
    if not re.match(r"^[A-Z]{2}", text):
        text = country.upper() + text
    if text.startswith("BE") and len(text) == 11 and text[2:].isdigit():
        text = "BE0" + text[2:]  # 9-digit legacy format
    return text


def is_valid_vat(value: str | None) -> bool | None:
    """Check the format and (for BE) the modulo-97 checksum. None when the country is unknown."""
    if not value or len(value) < 4:
        return False
    country = value[:2]
    pattern = VAT_PATTERNS.get(country)
    if pattern is None:
        return None
    if not pattern.match(value):
        return False
    if country == "BE":
        digits = value[2:]
        return (97 - int(digits[:8]) % 97) == int(digits[8:])
    return True


# --------------------------------------------------------------------------- social profiles

SOCIAL_HOSTS = {
    "linkedin": ("linkedin.com",),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com",),
    "twitter": ("twitter.com", "x.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
}
_SOCIAL_JUNK = re.compile(
    r"/(sharer|share|intent|login|policies|privacy|help|dialog|plugins|hashtag|explore|home|search|legal|tr\?|"
    r"about|terms|settings|reel/|watch|groups/feed)", re.IGNORECASE,
)


def social_network(url: str | None) -> str | None:
    host = url_domain(url) if url else None
    if not host:
        return None
    for network, hosts in SOCIAL_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return network
    return None


def normalize_social(value: Any, network: str) -> str | None:
    """Turn a handle or URL into a canonical profile URL for the given network."""
    text = clean_text(value)
    if not text:
        return None
    text = text.strip("@ ")
    base = {
        "linkedin": "https://www.linkedin.com/company/",
        "facebook": "https://www.facebook.com/",
        "instagram": "https://www.instagram.com/",
        "twitter": "https://x.com/",
        "youtube": "https://www.youtube.com/",
        "tiktok": "https://www.tiktok.com/@",
    }.get(network, "https://")
    if "." in text and ("/" in text or text.lower().startswith(("http", "www"))):
        url = normalize_url(text)
        if not url or social_network(url) != network:
            return None
        if _SOCIAL_JUNK.search(url):
            return None
        path = urlsplit(url).path
        if network == "linkedin" and not re.match(r"^/(company|in|school|showcase)/[^/]+", path):
            return None
        if path in ("", "/"):
            return None
        return url
    if not re.match(r"^[A-Za-z0-9._\-]+$", text):
        return None
    return base + text


# --------------------------------------------------------------------------- numbers, dates, coordinates

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\u00a0", " ").strip()
    text = re.sub(r"(?<=\d) (?=\d{3}\b)", "", text)  # "3 600" -> "3600"
    if "," in text and "." in text:  # both separators present: the last one is the decimal separator
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # a single comma followed by exactly three digits is a thousands separator ("1,234"), otherwise a decimal ("1,50")
        if re.search(r"\d,\d{3}(?!\d)", text) and not re.search(r",\d{1,2}$", text):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def normalize_integer(value: Any) -> int | None:
    num = normalize_number(value)
    return int(round(num)) if num is not None else None


def normalize_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.year
    m = re.search(r"(1[5-9]\d{2}|20\d{2}|21\d{2})", str(value))
    return int(m.group(1)) if m else None


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 4] if "T" in fmt else text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def normalize_coordinate(value: Any, kind: str) -> float | None:
    num = normalize_number(value)
    if num is None:
        return None
    limit = 90 if kind == "latitude" else 180
    return round(num, 6) if -limit <= num <= limit else None


# --------------------------------------------------------------------------- dispatcher


def normalize_value(value: Any, ftype: str, *, country: str = "BE", field_name: str = "") -> Any:
    """Normalise ``value`` according to a schema field type. Returns None when unusable."""
    if value is None:
        return None
    if ftype == "string":
        return normalize_name(value) if field_name in ("company_name", "name", "title", "legal_name") else clean_text(value)
    if ftype == "text":
        return clean_text(value)
    if ftype == "email":
        return normalize_email(value)
    if ftype == "url":
        return normalize_url(value)
    if ftype == "phone":
        return normalize_phone(value, country)
    if ftype == "postcode":
        return normalize_postcode(value, country)
    if ftype == "vat":
        return normalize_vat(value, country)
    if ftype == "number":
        return normalize_number(value)
    if ftype == "integer":
        return normalize_integer(value)
    if ftype == "year":
        return normalize_year(value)
    if ftype == "date":
        return normalize_date(value)
    if ftype == "enum":
        return clean_text(value)
    if ftype in ("latitude", "longitude"):
        return normalize_coordinate(value, ftype)
    if ftype == "social":
        network = social_network(str(value)) or field_name
        return normalize_social(value, network) if network in SOCIAL_HOSTS else normalize_url(value)
    return clean_text(value)
