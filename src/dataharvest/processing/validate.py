"""Schema-driven validation: required fields, types, patterns, ranges, cross-field consistency.

Also used stand-alone by ``dataharvest validate`` to QA an existing spreadsheet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..models import FieldStatus, Record
from ..schema import FieldDef, Schema
from .normalize import (
    email_domain,
    is_valid_email,
    is_valid_postcode,
    is_valid_vat,
    normalize_value,
    phone_info,
    social_network,
    url_domain,
)

FREE_MAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "hotmail.be", "outlook.com", "outlook.be", "live.be", "live.com", "yahoo.com",
    "yahoo.fr", "icloud.com", "me.com", "telenet.be", "skynet.be", "proximus.be", "scarlet.be", "msn.com", "protonmail.com",
}


def normalize_record(record: Record, schema: Schema, country: str) -> None:
    """Normalise every field value in place according to its schema type."""
    for fdef in schema.fields:
        fv = record.fields.get(fdef.name)
        if fv is None or fv.is_empty:
            continue
        original = fv.value
        normalized = normalize_value(original, fdef.type, country=country, field_name=fdef.name)
        if normalized is None:
            fv.candidates.append(str(original))
            fv.value = None
            fv.status = FieldStatus.INVALID
            fv.note = (fv.note + "; " if fv.note else "") + f"could not interpret {fdef.display.lower()} value {str(original)[:60]!r}"
            record.flag(f"{fdef.display.lower()} value could not be interpreted: {str(original)[:60]!r}")
        else:
            fv.value = normalized


def validate_field(fdef: FieldDef, value: Any, country: str) -> str | None:
    """Return a problem description, or None when the value is acceptable."""
    if value is None:
        return None
    text = str(value)
    if fdef.max_length and len(text) > fdef.max_length:
        return f"longer than {fdef.max_length} characters"
    if fdef.pattern and not re.search(fdef.pattern, text):
        return f"does not match pattern {fdef.pattern}"
    if fdef.enum and text not in fdef.enum:
        return f"not one of {', '.join(fdef.enum)}"
    if fdef.type == "email" and not is_valid_email(text):
        return "invalid e-mail address"
    if fdef.type == "url" and not url_domain(text):
        return "invalid URL"
    if fdef.type == "social" and social_network(text) is None:
        return "not a recognised social-network profile URL"
    if fdef.type == "phone" and not phone_info(text, country).get("valid"):
        return "invalid phone number"
    if fdef.type == "postcode" and is_valid_postcode(text, country) is False:
        return f"invalid postcode for {country}"
    if fdef.type == "vat" and is_valid_vat(text) is False:
        return "invalid VAT number (format or checksum)"
    if fdef.type in ("number", "integer", "year", "latitude", "longitude"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "not a number"
        if fdef.min is not None and num < fdef.min:
            return f"below minimum {fdef.min:g}"
        if fdef.max is not None and num > fdef.max:
            return f"above maximum {fdef.max:g}"
    return None


def validate_record(record: Record, schema: Schema, country: str, *, ignore_missing: Iterable[str] = ()) -> list[str]:
    """Apply schema rules; marks invalid fields and returns the list of problems.

    ``ignore_missing`` lists required fields whose absence should not be flagged (e.g. a column that
    does not exist at all in an audited file - reported once instead of on every row).
    """
    problems: list[str] = []
    ignored = set(ignore_missing)
    for fdef in schema.fields:
        fv = record.fields.get(fdef.name)
        if fv is None or fv.is_empty:
            if fdef.required and fdef.name not in ignored:
                problems.append(f"missing required field: {fdef.display.lower()}")
                record.flag(f"missing required field: {fdef.display.lower()}")
            continue
        problem = validate_field(fdef, fv.value, country)
        if problem:
            problems.append(f"{fdef.display.lower()}: {problem}")
            record.mark(fdef.name, FieldStatus.INVALID, problem)
            record.flag(f"{fdef.display.lower()} {problem}")
    problems.extend(cross_field_checks(record, schema))
    return problems


def cross_field_checks(record: Record, schema: Schema) -> list[str]:
    """Consistency rules between fields (only notes/flags, no hard failures)."""
    problems: list[str] = []
    email = record.get("email")
    website = record.get("website")
    if email and website:
        edom = email_domain(email) or ""
        wdom = url_domain(website) or ""
        if edom and wdom and edom != wdom and not edom.endswith("." + wdom) and not wdom.endswith("." + edom) and edom not in FREE_MAIL_DOMAINS:
            if record.field_status("email") != FieldStatus.VERIFIED:
                msg = f"e-mail domain ({edom}) differs from website domain ({wdom}) - review"
                record.flag(msg)
                problems.append(msg)
            else:
                record.mark("email", FieldStatus.VERIFIED, f"note: e-mail domain differs from website domain ({wdom})")
    postcode = record.get("postcode")
    city = record.get("city")
    if schema.field("postcode") and postcode and not city:
        record.flag("note: postcode present but city missing")
    hours = record.get("opening_hours")
    if hours and re.search(r"\bclosed\b|gesloten|fermé", str(hours), re.IGNORECASE) and not re.search(r"\d", str(hours)[:8]):
        msg = "source marks the business as closed (opening hours) - review"
        record.flag(msg)
        problems.append(msg)
    return problems
