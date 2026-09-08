"""Base class and registry for extraction sources."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from ..config import ProjectConfig, SourceConfig
from ..http import HttpClient
from ..models import RawRecord

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """A source could not run (bad configuration, missing credentials, remote failure)."""


class BaseSource(ABC):
    """One place data is extracted from (an API, a website, a file)."""

    #: registry key used in project files (``type: osm_overpass``)
    type: str = ""
    #: human description shown by ``dataharvest sources``
    description: str = ""
    #: environment variables that must be set for the source to work
    requires_env: tuple[str, ...] = ()
    #: the schema this source produces values for (informational)
    produces: str = "leads"

    def __init__(self, config: SourceConfig, http: HttpClient, project: ProjectConfig) -> None:
        self.config = config
        self.options: dict[str, Any] = config.options()
        self.http = http
        self.project = project
        self.label = config.label
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ API
    @classmethod
    def availability(cls) -> tuple[bool, str]:
        """(available, reason). Sources needing API keys report what is missing."""
        from ..config import env

        missing = [name for name in cls.requires_env if not env(name)]
        if missing:
            return False, "missing environment variable(s): " + ", ".join(missing)
        return True, "ready"

    @abstractmethod
    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        """Yield raw records. Implementations should be lazy where possible."""

    def warn(self, message: str) -> None:
        log.warning("[%s] %s", self.label, message)
        self.warnings.append(message)

    def opt(self, name: str, default: Any = None) -> Any:
        return self.options.get(name, default)

    def require_opt(self, name: str) -> Any:
        if name not in self.options or self.options[name] in (None, ""):
            raise SourceError(f"source '{self.label}' needs the option '{name}'")
        return self.options[name]


_REGISTRY: dict[str, type[BaseSource]] = {}
_LOADED = False


def register(cls: type[BaseSource]) -> type[BaseSource]:
    if not cls.type:
        raise ValueError("source class needs a 'type'")
    _REGISTRY[cls.type] = cls
    return cls


def get_source_class(type_name: str) -> type[BaseSource]:
    _ensure_loaded()
    try:
        return _REGISTRY[type_name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise SourceError(f"unknown source type '{type_name}'. Known types: {known}") from None


def all_sources() -> dict[str, type[BaseSource]]:
    _ensure_loaded()
    return dict(sorted(_REGISTRY.items()))


def _ensure_loaded() -> None:
    """Import the built-in source modules so that they register themselves."""
    global _LOADED
    if _LOADED:
        return
    from . import apollo, csv_import, google_places, html_list, osm_overpass, wikidata  # noqa: F401

    _LOADED = True
