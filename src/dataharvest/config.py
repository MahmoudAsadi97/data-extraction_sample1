"""Project configuration (the "client instructions" for one extraction job).

A project is a YAML file describing *what* to collect (schema), *where* from
(sources), which enrichment / verification steps to run and how to deliver
the result. See ``projects/*.yaml`` for complete, runnable examples.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from .schema import Schema, apply_overrides, load_schema

load_dotenv()  # .env in the working directory, if present


class ProjectInfo(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    country: str = "BE"  # default country for phone / postcode / VAT rules
    language: str = "en"
    client: str = ""
    requested_by: str = ""
    deadline: str = ""
    instructions: str = ""  # free text: the brief, as received

    @field_validator("deadline", "title", "client", "requested_by", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        return "" if v is None else (v if isinstance(v, str) else str(v))  # YAML turns 2026-10-01 into a date

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c in v for c in ' /\\:*?"<>|'):
            raise ValueError("project.name must be a simple slug (used in file names)")
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return v.strip().upper()


class SourceConfig(BaseModel):
    """Configuration of one extraction source. Extra keys are passed to the source."""

    model_config = {"extra": "allow"}

    type: str
    name: str | None = None  # label used in the output; defaults to type
    enabled: bool = True
    limit: int | None = None

    @property
    def label(self) -> str:
        return self.name or self.type

    def options(self) -> dict[str, Any]:
        """All source-specific options (everything except the generic keys)."""
        data = self.model_dump()
        for k in ("type", "name", "enabled", "limit"):
            data.pop(k, None)
        return data


class WebsiteEnrichment(BaseModel):
    enabled: bool = True
    max_pages_per_site: int = Field(default=3, ge=1, le=10)
    workers: int = Field(default=6, ge=1, le=32)
    timeout: float = 15.0
    respect_robots: bool = True


class SearchEnrichment(BaseModel):
    enabled: bool = True
    provider: Literal["auto", "duckduckgo", "google_cse", "off"] = "auto"
    max_queries: int = Field(default=40, ge=0)
    delay_seconds: float = 2.0


class ViesEnrichment(BaseModel):
    enabled: bool = True
    delay_seconds: float = 1.0


class ApolloEnrichment(BaseModel):
    enabled: bool = False  # needs APOLLO_API_KEY
    max_lookups: int = 50


class EnrichmentConfig(BaseModel):
    website: WebsiteEnrichment = WebsiteEnrichment()
    find_missing_websites: SearchEnrichment = SearchEnrichment()
    vies: ViesEnrichment = ViesEnrichment()
    apollo: ApolloEnrichment = ApolloEnrichment()


class VerificationConfig(BaseModel):
    website_liveness: bool = True
    email_mx: bool = True
    phone_format: bool = True


class DedupeConfig(BaseModel):
    enabled: bool = True
    name_similarity: int = Field(default=90, ge=50, le=100)
    merge: bool = True  # merge confident duplicates; False = only flag them


class GoogleSheetsConfig(BaseModel):
    enabled: bool = False
    title: str = ""
    share_with: list[str] = Field(default_factory=list)
    spreadsheet_id: str = ""


class OutputConfig(BaseModel):
    directory: str = "data/output"
    basename: str = ""
    formats: list[Literal["xlsx", "csv", "json"]] = Field(default_factory=lambda: ["xlsx", "csv", "json"])
    timestamp: bool = True  # append run date to file names
    include_excluded: bool = True  # list excluded records in the review sheet
    id_prefix: str = ""  # record id prefix, e.g. KOR -> KOR-0001 (default: derived from the project name)
    google_sheets: GoogleSheetsConfig = GoogleSheetsConfig()


class HttpConfig(BaseModel):
    user_agent: str = ""
    contact_email: str = ""
    timeout: float = 20.0
    cache: bool = True
    cache_dir: str = "data/cache"
    cache_expire_hours: int = 24 * 7
    min_delay_per_host: float = 0.5
    max_retries: int = 3


class ProjectConfig(BaseModel):
    project: ProjectInfo
    schema_ref: str | dict[str, Any] | None = Field(default="leads", alias="schema")
    schema_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)  # field name -> FieldDef attributes to change
    sources: list[SourceConfig]
    enrichment: EnrichmentConfig = EnrichmentConfig()
    verification: VerificationConfig = VerificationConfig()
    dedupe: DedupeConfig = DedupeConfig()
    output: OutputConfig = OutputConfig()
    http: HttpConfig = HttpConfig()
    max_records: int = 0  # 0 = unlimited

    model_config = {"populate_by_name": True}

    _schema_obj: Schema | None = None
    _path: Path | None = None

    @model_validator(mode="after")
    def _defaults(self) -> ProjectConfig:
        if not self.output.basename:
            self.output.basename = self.project.name
        if not self.project.title:
            self.project.title = self.project.name.replace("_", " ").title()
        if not self.sources:
            raise ValueError("A project needs at least one source")
        return self

    @property
    def schema_def(self) -> Schema:
        """The resolved field schema (built-in name, file next to the project, or inline)."""
        if self._schema_obj is None:
            ref = self.schema_ref
            if isinstance(ref, str) and self._path is not None:
                candidate = (self._path.parent / ref).resolve()
                if candidate.suffix in (".yaml", ".yml") and candidate.exists():
                    ref = str(candidate)
            self._schema_obj = apply_overrides(load_schema(ref), self.schema_overrides)
        return self._schema_obj

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def active_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    def resolve_path(self, p: str) -> Path:
        """Resolve a path relative to the project file's directory (or CWD)."""
        path = Path(p)
        if path.is_absolute():
            return path
        base = self._path.parent if self._path else Path.cwd()
        # project files live in <root>/projects/, so relative paths are resolved from <root>
        root = base.parent if base.name == "projects" else base
        return (root / path).resolve()


def load_project(path: str | Path) -> ProjectConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Project file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = ProjectConfig(**data)
    cfg._path = path.resolve()
    return cfg


def env(name: str, default: str = "") -> str:
    """Read an environment variable (after .env loading), stripped."""
    return (os.environ.get(name) or default).strip()
