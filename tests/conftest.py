"""Shared fixtures: offline HTTP client, recorded API responses, project configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dataharvest.config import ProjectConfig, load_project
from dataharvest.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def http() -> HttpClient:
    """An HTTP client with caching, politeness delays and robots checks switched off (for mocked tests)."""
    client = HttpClient(cache=False, min_delay_per_host=0, respect_robots=False, max_retries=0, timeout=5)
    yield client
    client.close()


@pytest.fixture
def leads_project(tmp_path: Path) -> ProjectConfig:
    """A minimal OSM-based leads project written to a temp folder (outputs go there too)."""
    cfg = {
        "project": {"name": "test_leads", "title": "Test leads", "country": "BE"},
        "schema": "leads",
        "sources": [{"type": "osm_overpass", "name": "osm", "area": "Kortrijk", "country": "BE", "tags": ["amenity=restaurant", "amenity=cafe"]}],
        "enrichment": {
            "website": {"enabled": True, "max_pages_per_site": 2, "workers": 2},
            "find_missing_websites": {"enabled": True, "provider": "duckduckgo", "max_queries": 5, "delay_seconds": 0},
            "vies": {"enabled": True, "delay_seconds": 0},
        },
        "output": {"directory": str(tmp_path / "out"), "timestamp": False},
        "http": {"cache": False, "min_delay_per_host": 0},
    }
    path = tmp_path / "projects" / "test_leads.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return load_project(path)


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture(autouse=True)
def fixture_dns(request, monkeypatch):
    """Recorded HTTP tests use deterministic public DNS; network tests use real DNS."""
    if request.node.get_closest_marker("network") is None:
        monkeypatch.setattr("dataharvest.network._resolve", lambda host, port: ["93.184.216.34"])
