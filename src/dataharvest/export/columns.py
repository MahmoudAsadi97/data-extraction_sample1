"""Column layout shared by the Excel, CSV and Google Sheets exporters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Record
from ..processing.normalize import format_phone_display
from ..schema import FieldDef, Schema

CHECK_FIELDS = ("company_name", "name", "title", "website", "email", "phone", "vat_number")


@dataclass
class Column:
    key: str
    header: str
    width: int = 16
    kind: str = "value"  # value | status | system | check | reviewer
    field: FieldDef | None = None
    wrap: bool = False
    number_format: str | None = None
    hyperlink: bool = False


def build_columns(schema: Schema) -> list[Column]:
    cols: list[Column] = [Column("record_id", "Record ID", 11, "system")]
    for f in schema.fields:
        width = f.width or (40 if f.type == "text" else 18)
        cols.append(Column(f.name, f.display, width, "value", f, wrap=f.type == "text",
                           number_format={"latitude": "0.000000", "longitude": "0.000000", "number": "#,##0.00", "integer": "0", "year": "0"}.get(f.type),
                           hyperlink=f.type in ("url", "social", "email")))
    cols.append(Column("status", "Verification status", 20, "status"))
    cols.append(Column("flags", "Review flags", 55, "status", wrap=True))
    cols.append(Column("completeness", "Completeness %", 14, "status", number_format="0"))
    for name in CHECK_FIELDS:
        f = schema.field(name)
        if f is not None:
            cols.append(Column(f"check:{name}", f"{f.display} check", 34, "check", f, wrap=True))
    cols.append(Column("source", "Source", 14, "system"))
    cols.append(Column("source_url", "Source URL", 40, "system", hyperlink=True))
    cols.append(Column("duplicate_group", "Duplicate group", 14, "system"))
    cols.append(Column("extracted_at", "Extracted at (UTC)", 20, "system"))
    cols.append(Column("reviewer_decision", "Reviewer decision", 17, "reviewer"))
    cols.append(Column("reviewer_notes", "Reviewer notes", 30, "reviewer"))
    return cols


def cell_value(rec: Record, col: Column, *, country: str = "BE") -> Any:
    if col.kind == "value":
        value = rec.get(col.key)
        if col.field is not None and col.field.type == "phone" and value:
            return format_phone_display(str(value), country)  # "+32 56 20 55 88" instead of E.164
        return value
    if col.key == "record_id":
        return rec.record_id
    if col.key == "status":
        return rec.status.value
    if col.key == "flags":
        return "\n".join(rec.flags) if rec.flags else None
    if col.key == "completeness":
        return rec.completeness
    if col.key.startswith("check:"):
        name = col.key[6:]
        fv = rec.fields.get(name)
        if fv is None or (fv.is_empty and not fv.candidates and not fv.note):
            return "missing"
        text = fv.status.value
        if fv.note:
            text += f" - {fv.note}"
        if fv.candidates:
            text += f" | other values seen: {', '.join(fv.candidates[:3])}"
        return text
    if col.key == "source":
        return rec.source
    if col.key == "source_url":
        return rec.source_url or None
    if col.key == "duplicate_group":
        return rec.duplicate_group or None
    if col.key == "extracted_at":
        return rec.extracted_at
    return None


def record_row(rec: Record, columns: list[Column], *, country: str = "BE") -> list[Any]:
    return [cell_value(rec, c, country=country) for c in columns]
