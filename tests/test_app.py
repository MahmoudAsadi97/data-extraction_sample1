"""Smoke test for the Streamlit dashboard (skipped when streamlit is not installed)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pandas")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def test_dashboard_renders_without_running():
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("DataHarvest" in t.value for t in at.title)
    assert at.sidebar.selectbox[0].options  # project files are listed
    assert at.button[0].label == "Run extraction"
    assert any("Choose a project" in i.value for i in at.info)
