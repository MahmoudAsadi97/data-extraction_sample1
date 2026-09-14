"""Customer workflow tests with real files, SQLite transactions and CLI entrypoints."""

from __future__ import annotations

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files

import pytest
from typer.testing import CliRunner

from dataharvest.cli import app
from dataharvest.workspace import ReviewConflict, Workspace


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "customer.sqlite3")


def audit(workspace, tmp_path, name="accounts_before.csv", project="Client A"):
    path = tmp_path / name
    path.write_bytes((files("dataharvest") / "examples" / name).read_bytes())
    return workspace.audit(path, project=project)


def test_reopen_snapshot_and_keep_evidence(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    original = workspace.load(rid)
    reopened = Workspace(workspace.path).load(rid)
    assert [r.as_dict() for r in original["records"]] == [r.as_dict() for r in reopened["records"]]
    assert len(reopened["input_sha256"]) == 64
    assert len(reopened["records"]) == 6


def test_compare_finds_changes_ignores_row_order(workspace, tmp_path):
    before = audit(workspace, tmp_path)
    after = audit(workspace, tmp_path, "accounts_after.csv")
    diff = workspace.compare(before, after)
    assert diff["added"] == ["AC-007"] and diff["removed"] == ["AC-006"]
    assert {r["key"] for r in diff["changed"]} == {"AC-001", "AC-002", "AC-005"}
    assert diff["unchanged"] == 2
    assert workspace.load(after)["reviews"] == {}  # approvals never transfer silently to new evidence


def test_review_roundtrip_preserves_verification_status(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    run = workspace.load(rid)
    rec = next(r for r in run["records"] if r.get("account_id") == "AC-003")
    evidence = rec.as_dict()
    assert workspace.review(rid, rec.record_id, "approved", reviewer="Operator") == 1
    assert next(r for r in workspace.load(rid)["records"] if r.record_id == rec.record_id).as_dict() == evidence
    rows = list(csv.DictReader(io.StringIO(workspace.approved_csv(rid).decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["account_id"] == "AC-003"
    assert rows[0]["verification_status"] == "UNVERIFIED"
    assert rows[0]["reviewer"] == "Operator"
    assert len(workspace.events(rid)) == 1
    workspace.review(rid, rec.record_id, "rejected", reviewer="Operator", note="Out of scope", expected_revision=1)
    assert len(workspace.events(rid)) == 2
    assert len(list(csv.reader(io.StringIO(workspace.approved_csv(rid).decode("utf-8-sig"))))) == 1


def test_invalid_fields_block_approval(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    rec = next(r for r in workspace.load(rid)["records"] if r.get("account_id") == "AC-002")
    with pytest.raises(ValueError, match="Correct and re-import"):
        workspace.review(rid, rec.record_id, "approved", reviewer="Operator", note="Reviewed")
    assert not workspace.events(rid)


def test_flag_acknowledgement_and_stale_review(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    rec = next(r for r in workspace.load(rid)["records"] if r.get("account_id") == "AC-001")
    with pytest.raises(ValueError, match="note is required"):
        workspace.review(rid, rec.record_id, "approved", reviewer="Operator")
    workspace.review(rid, rec.record_id, "approved", reviewer="Operator", note="Confirmed these are separate branches")
    with pytest.raises(ReviewConflict):
        workspace.review(rid, rec.record_id, "pending", reviewer="Operator", expected_revision=0)
    assert len(workspace.events(rid)) == 1


def test_parallel_decisions_have_one_winner(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    rec = workspace.load(rid)["records"][2]

    def decide(i):
        try:
            workspace.review(rid, rec.record_id, "pending", reviewer=f"Operator {i}", expected_revision=0)
            return "saved"
        except ReviewConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(decide, range(2))) == ["conflict", "saved"]
    assert len(workspace.events(rid)) == 1


def test_projects_cannot_be_compared(workspace, tmp_path):
    a = audit(workspace, tmp_path, project="A")
    b = audit(workspace, tmp_path, project="B")
    with pytest.raises(ValueError, match="same project"):
        workspace.compare(a, b)


@pytest.mark.parametrize("ids", [["A", "A"], ["A", ""]])
def test_ambiguous_identity_blocks_comparison(workspace, tmp_path, ids):
    path = tmp_path / "input.csv"
    path.write_text(f"Account ID,Company name\n{ids[0]},One\n{ids[1]},Two\n")
    rid = workspace.audit(path, project="P")
    with pytest.raises(ValueError, match="Comparison key"):
        workspace.compare(rid, rid)


def test_delete_cascades_reviews_and_other_runs_survive(workspace, tmp_path):
    rid = audit(workspace, tmp_path)
    other = audit(workspace, tmp_path)
    workspace.review(rid, workspace.load(rid)["records"][0].record_id, "pending", reviewer="Operator")
    workspace.delete_run(rid)
    with pytest.raises(ValueError, match="Run not found"):
        workspace.load(rid)
    assert len(workspace.list_runs()) == 1 and workspace.load(other)


def test_cli_demo_and_export_do_not_overwrite(tmp_path):
    db = str(tmp_path / "cli.sqlite3")
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "demo", "--db", db])
    assert result.exit_code == 0, result.output
    rid = json.loads(result.output)["after"]
    target = tmp_path / "approved.csv"
    args = ["workspace", "export", rid, "--db", db, "--out", str(target)]
    assert runner.invoke(app, args).exit_code == 0
    original = target.read_bytes()
    assert runner.invoke(app, args).exit_code == 2
    assert target.read_bytes() == original
