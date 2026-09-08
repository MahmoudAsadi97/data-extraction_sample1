"""Core data structures shared by every stage of the pipeline.

A :class:`Record` is one row of the final database. Every field value carries
its own provenance (where it came from) and verification status, so the
exported spreadsheet can show *why* a value is trusted - or why it is flagged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FieldStatus(str, Enum):
    """Verification status of a single field value."""

    VERIFIED = "verified"  # confirmed by an independent check or a second source
    UNVERIFIED = "unverified"  # present and well-formed, but only one source / not checked
    CONFLICT = "conflict"  # sources disagree - needs a human decision
    INVALID = "invalid"  # failed validation (bad format, dead link, VAT rejected, ...)
    MISSING = "missing"  # no value available

    @property
    def is_present(self) -> bool:
        return self is not FieldStatus.MISSING


class RecordStatus(str, Enum):
    """Overall status of a record, derived from its field statuses and flags."""

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    EXCLUDED = "EXCLUDED"  # unusable (e.g. no name) - kept for audit, not in the main sheet


@dataclass
class FieldValue:
    """A single value with provenance."""

    value: Any = None
    source: str = ""  # e.g. "osm", "website", "vies", "search:duckduckgo", "csv"
    status: FieldStatus = FieldStatus.MISSING
    note: str = ""
    candidates: list[str] = field(default_factory=list)  # other values seen for this field

    @property
    def is_empty(self) -> bool:
        return self.value is None or (isinstance(self.value, str) and not self.value.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "status": self.status.value,
            "note": self.note,
            "candidates": list(self.candidates),
        }


@dataclass
class RawRecord:
    """What a source extractor returns: untyped values plus provenance."""

    source: str
    values: dict[str, Any]
    source_id: str = ""
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)  # issues already known at extraction time


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_record_id(prefix: str = "REC") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class Record:
    """One row of the output database with full provenance."""

    record_id: str
    source: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    source_id: str = ""
    source_url: str = ""
    flags: list[str] = field(default_factory=list)
    status: RecordStatus = RecordStatus.UNVERIFIED
    completeness: float = 0.0
    duplicate_group: str = ""
    duplicate_of: str = ""  # record_id of the record this one was merged into
    extracted_at: str = field(default_factory=_now_iso)
    raw: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)  # free-form results of verification checks

    # ------------------------------------------------------------------ access helpers
    def get(self, name: str, default: Any = None) -> Any:
        fv = self.fields.get(name)
        if fv is None or fv.is_empty:
            return default
        return fv.value

    def field_status(self, name: str) -> FieldStatus:
        fv = self.fields.get(name)
        return fv.status if fv else FieldStatus.MISSING

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def set(
        self,
        name: str,
        value: Any,
        source: str,
        status: FieldStatus = FieldStatus.UNVERIFIED,
        note: str = "",
    ) -> FieldValue:
        """Set a field value, keeping previous distinct values as candidates."""
        previous = self.fields.get(name)
        candidates: list[str] = []
        if previous is not None:
            candidates = list(previous.candidates)
            if not previous.is_empty and str(previous.value) != str(value):
                candidates.append(str(previous.value))
        if value is None or (isinstance(value, str) and not value.strip()):
            status = FieldStatus.MISSING
        fv = FieldValue(value=value, source=source, status=status, note=note, candidates=candidates)
        self.fields[name] = fv
        return fv

    def mark(self, name: str, status: FieldStatus, note: str = "", append_note: bool = True) -> None:
        fv = self.fields.get(name)
        if fv is None:
            fv = FieldValue()
            self.fields[name] = fv
        fv.status = status
        if note:
            fv.note = f"{fv.note}; {note}" if (fv.note and append_note) else note

    def add_candidate(self, name: str, value: Any) -> None:
        fv = self.fields.setdefault(name, FieldValue())
        if value is None:
            return
        sval = str(value)
        if sval != str(fv.value) and sval not in fv.candidates:
            fv.candidates.append(sval)

    def flag(self, message: str) -> None:
        if message not in self.flags:
            self.flags.append(message)

    # ------------------------------------------------------------------ serialisation
    def values(self) -> dict[str, Any]:
        return {name: fv.value for name, fv in self.fields.items()}

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source": self.source,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "status": self.status.value,
            "completeness": self.completeness,
            "flags": list(self.flags),
            "duplicate_group": self.duplicate_group,
            "duplicate_of": self.duplicate_of,
            "extracted_at": self.extracted_at,
            "fields": {name: fv.as_dict() for name, fv in self.fields.items()},
            "checks": self.checks,
            "raw": self.raw,
        }

    @classmethod
    def from_raw(cls, raw: RawRecord, record_id: str | None = None) -> Record:
        rec = cls(
            record_id=record_id or new_record_id(),
            source=raw.source,
            source_id=raw.source_id,
            source_url=raw.source_url,
            raw=dict(raw.raw),
        )
        for name, value in raw.values.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            rec.set(name, value, source=raw.source, status=FieldStatus.UNVERIFIED)
        for message in raw.flags:
            rec.flag(message)
        return rec


@dataclass
class DuplicateGroup:
    """A cluster of records judged to describe the same entity."""

    group_id: str
    master_id: str
    member_ids: list[str]
    reason: str
    similarity: float
    merged: bool = True  # False = flagged as *possible* duplicate, not merged
