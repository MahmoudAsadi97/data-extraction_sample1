"""Field schemas: what columns the final database has and how each is validated.

Built-in schemas live in ``dataharvest/schemas/*.yaml`` (``leads``, ``companies``,
``products``). A project file can reference one by name or define its own
``fields`` list inline.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

FieldType = Literal[
    "string",  # free text, single line
    "text",  # longer free text
    "email",
    "url",
    "phone",
    "postcode",
    "vat",  # VAT / enterprise number (checksum + VIES verification for BE)
    "number",
    "integer",
    "year",
    "date",
    "enum",
    "latitude",
    "longitude",
    "social",  # social profile URL (LinkedIn, Facebook, Instagram, ...)
]


class FieldDef(BaseModel):
    """One column of the output database."""

    name: str
    label: str | None = None
    type: FieldType = "string"
    required: bool = False
    key: bool = False  # participates in duplicate detection
    description: str = ""
    pattern: str | None = None
    enum: list[str] | None = None
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    width: int | None = None  # Excel column width hint
    verify: str = ""  # human-readable description of how the value is verified

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip()
        if not v or not v.replace("_", "").isalnum():
            raise ValueError(f"Field name must be alphanumeric/underscore: {v!r}")
        return v

    @property
    def display(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()

    @property
    def is_contact(self) -> bool:
        return self.type in ("email", "url", "phone")


class Schema(BaseModel):
    name: str = "custom"
    label: str = "Data"  # sheet name for the main data sheet
    entity: str = "record"  # what one row represents (e.g. "company", "product")
    fields: list[FieldDef]
    address_fields: list[str] = Field(default_factory=list)  # fields that together form an address

    @field_validator("fields")
    @classmethod
    def _unique(cls, v: list[FieldDef]) -> list[FieldDef]:
        seen: set[str] = set()
        for f in v:
            if f.name in seen:
                raise ValueError(f"Duplicate field name in schema: {f.name}")
            seen.add(f.name)
        if not v:
            raise ValueError("A schema needs at least one field")
        return v

    # -------------------------------------------------------------- helpers
    def field(self, name: str) -> FieldDef | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def required(self) -> list[FieldDef]:
        return [f for f in self.fields if f.required]

    def by_type(self, *types: str) -> list[FieldDef]:
        return [f for f in self.fields if f.type in types]

    @property
    def name_field(self) -> str | None:
        """The field that names the entity (first required string field, or 'company_name'/'name')."""
        for candidate in ("company_name", "name", "title"):
            if self.field(candidate):
                return candidate
        for f in self.fields:
            if f.required and f.type in ("string", "text"):
                return f.name
        return self.fields[0].name


def builtin_schema_names() -> list[str]:
    pkg = resources.files("dataharvest") / "schemas"
    return sorted(p.name[:-5] for p in pkg.iterdir() if p.name.endswith(".yaml"))


def load_schema(ref: str | dict[str, Any] | Path | None) -> Schema:
    """Load a schema by built-in name, file path, or inline mapping."""
    if ref is None:
        ref = "leads"
    if isinstance(ref, dict):
        return Schema(**ref)
    ref_str = str(ref)
    path = Path(ref_str)
    if path.suffix in (".yaml", ".yml") and path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return Schema(**yaml.safe_load(fh))
    pkg = resources.files("dataharvest") / "schemas" / f"{ref_str}.yaml"
    if pkg.is_file():
        with pkg.open("r", encoding="utf-8") as fh:
            return Schema(**yaml.safe_load(fh))
    raise FileNotFoundError(
        f"Unknown schema {ref_str!r}. Built-in schemas: {', '.join(builtin_schema_names())}"
    )


def apply_overrides(schema: Schema, overrides: dict[str, dict[str, Any]] | None) -> Schema:
    """Return a copy of ``schema`` with per-field attribute changes, e.g. ``{"category": {"required": False}}``."""
    if not overrides:
        return schema
    fields = []
    for f in schema.fields:
        changes = overrides.get(f.name)
        fields.append(f.model_copy(update=changes) if changes else f)
    unknown = sorted(set(overrides) - {f.name for f in schema.fields})
    if unknown:
        raise ValueError(f"schema_overrides refer to unknown field(s): {', '.join(unknown)}")
    return schema.model_copy(update={"fields": fields})
