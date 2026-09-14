"""Smoke test for the Streamlit dashboard (skipped when streamlit is not installed)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pandas")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def test_dashboard_renders_without_running(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAHARVEST_WORKSPACE", str(tmp_path / "ui.sqlite3"))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("Company data" in t.value for t in at.title)
    assert any(b.label == "Load sample workspace" for b in at.button)
    assert any("Import your company list" in i.value for i in at.info)


def test_demo_review_and_comparison_workflow(tmp_path, monkeypatch):
    from dataharvest.workspace import Workspace

    db = tmp_path / "ui.sqlite3"
    monkeypatch.setenv("DATAHARVEST_WORKSPACE", str(db))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    next(b for b in at.button if b.label == "Load sample workspace").click().run()
    assert not at.exception, at.exception
    assert any(m.label == "Company records" and m.value == "6" for m in at.metric)
    assert any(m.label == "Changed" and m.value == "3" for m in at.metric)
    saved = at.selectbox[1].value
    workspace = Workspace(db)
    run = workspace.load(saved)
    rec = next(r for r in run["records"] if r.get("account_id") == "AC-003")
    next(s for s in at.selectbox if s.label == "Record to review").select(rec.record_id).run()
    next(t for t in at.text_input if t.label == "Reviewer name").set_value("Operator")
    next(s for s in at.selectbox if s.label == "Decision").select("approved")
    next(b for b in at.button if b.label == "Save decision").click().run()
    assert not at.exception, at.exception
    assert any(m.label == "Approved for export" and m.value == "1" for m in at.metric)
    assert workspace.load(saved)["reviews"][rec.record_id]["decision"] == "approved"
    assert any("AC-003" in line for line in workspace.approved_csv(saved).decode().splitlines())


def test_extraction_projects_remain_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAHARVEST_WORKSPACE", str(tmp_path / "ui.sqlite3"))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.radio[0].set_value("Extraction projects").run()
    assert not at.exception, at.exception
    assert at.sidebar.selectbox[0].options
    assert any(b.label == "Run extraction" for b in at.button)
