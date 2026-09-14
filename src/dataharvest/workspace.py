"""Durable local audit snapshots, review decisions and explicit-key comparisons.

One database belongs to one trusted operator/customer. It is not a multi-tenant
authorization boundary. All writes are transactional; decisions never alter evidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .export.safety import csv_cell
from .models import FieldStatus, FieldValue, Record, RecordStatus
from .qa import AuditResult, audit_file
from .schema import Schema


class ReviewConflict(ValueError):
    """A review was changed since the operator last loaded it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def restore_record(payload: dict) -> Record:
    data = dict(payload)
    data["status"] = RecordStatus(data["status"])
    data["fields"] = {name: FieldValue(**{**value, "status": FieldStatus(value["status"])})
                      for name, value in data["fields"].items()}
    return Record(**data)


def approval_blockers(record: Record, schema: Schema) -> list[str]:
    blockers = [f"Missing required field: {f.display}" for f in schema.required if not record.has(f.name)]
    blockers.extend(f"{name}: {value.status.value}" for name, value in record.fields.items()
                    if value.status in (FieldStatus.INVALID, FieldStatus.CONFLICT))
    if record.duplicate_of or record.status == RecordStatus.EXCLUDED:
        blockers.append("Record is excluded or merged")
    return blockers


class Workspace:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported workspace version {version}; upgrade the application.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, project TEXT NOT NULL, created_at TEXT NOT NULL,
                    input_name TEXT NOT NULL, input_sha256 TEXT NOT NULL, schema_json TEXT NOT NULL,
                    report_json TEXT NOT NULL, records_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    record_id TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('pending','approved','rejected')),
                    reviewer TEXT NOT NULL, note TEXT NOT NULL, revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(run_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    record_id TEXT NOT NULL, decision TEXT NOT NULL, reviewer TEXT NOT NULL,
                    note TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_project ON runs(project, created_at);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_audit(self, result: AuditResult, *, project: str, input_name: str, input_sha256: str) -> str:
        if not project.strip() or len(project) > 120:
            raise ValueError("Project name must contain 1–120 characters.")
        ids = [r.record_id for r in result.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate record IDs in snapshot.")
        run_id = uuid.uuid4().hex
        report = result.report.to_dict()
        report["column_map"] = result.column_map
        report["ignored_columns"] = result.ignored_columns
        report["duplicate_groups_detail"] = [g.__dict__ for g in result.groups]
        with self._connect() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                       (run_id, project.strip(), _now(), Path(input_name).name, input_sha256,
                        _json(result.schema.model_dump()), _json(report), _json([r.as_dict() for r in result.records])))
        return run_id

    def audit(self, path: str | Path, *, project: str, schema_ref: str | dict = "accounts",
              country: str = "BE", mapping: dict[str, str] | None = None) -> str:
        path = Path(path)
        # Parse and hash the same immutable bytes, even if the original file changes during the audit.
        import tempfile

        from .sources.csv_import import MAX_INPUT_BYTES

        with path.open("rb") as source:
            content = source.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise ValueError("Input exceeds the 20 MiB limit.")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / path.name
            snapshot.write_bytes(content)
            result = audit_file(snapshot, schema_ref, country=country, mapping=mapping)
        result.report.settings.update({"country": country, "offline": True})
        return self.save_audit(result, project=project, input_name=path.name,
                               input_sha256=hashlib.sha256(content).hexdigest())

    def list_runs(self, project: str | None = None) -> list[dict]:
        with self._connect() as db:
            query = "SELECT run_id,project,created_at,input_name,input_sha256 FROM runs"
            rows = db.execute(query + (" WHERE project=?" if project else "") + " ORDER BY created_at DESC",
                              (project,) if project else ()).fetchall()
        return [dict(row) for row in rows]

    def load(self, run_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Run not found in this workspace.")
            reviews = db.execute("SELECT * FROM reviews WHERE run_id=?", (run_id,)).fetchall()
        run = dict(row)
        run["schema"] = Schema.model_validate_json(run.pop("schema_json"))
        run["report"] = json.loads(run.pop("report_json"))
        run["records"] = [restore_record(r) for r in json.loads(run.pop("records_json"))]
        run["reviews"] = {r["record_id"]: dict(r) for r in reviews}
        return run

    def review(self, run_id: str, record_id: str, decision: str, *, reviewer: str, note: str = "",
               expected_revision: int = 0) -> int:
        if decision not in ("pending", "approved", "rejected"):
            raise ValueError("Choose pending, approved or rejected.")
        reviewer, note = reviewer.strip(), note.strip()
        if not reviewer or len(reviewer) > 120 or len(note) > 4000:
            raise ValueError("Provide a reviewer name (max 120 characters) and a note of at most 4,000 characters.")
        run = self.load(run_id)
        record = next((r for r in run["records"] if r.record_id == record_id), None)
        if record is None:
            raise ValueError("Record not found in this run.")
        if decision == "approved":
            blockers = approval_blockers(record, run["schema"])
            if blockers:
                raise ValueError("Correct and re-import before approval: " + "; ".join(blockers))
            if record.flags and not note:
                raise ValueError("A note is required to acknowledge the record's review flags.")
        if decision == "rejected" and not note:
            raise ValueError("A rejection reason is required.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM reviews WHERE run_id=? AND record_id=?", (run_id, record_id)).fetchone()
            revision = current[0] if current else 0
            if revision != expected_revision:
                raise ReviewConflict("This decision changed in another session. Reload and review the latest decision.")
            revision += 1
            stamp = _now()
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?)",
                       (run_id, record_id, decision, reviewer, note, revision, stamp))
            db.execute("INSERT INTO events(run_id,record_id,decision,reviewer,note,revision,created_at) VALUES (?,?,?,?,?,?,?)",
                       (run_id, record_id, decision, reviewer, note, revision, stamp))
        return revision

    def events(self, run_id: str) -> list[dict]:
        self.load(run_id)
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY event_id", (run_id,))]

    def approved_csv(self, run_id: str) -> bytes:
        run = self.load(run_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        headers = ["record_id", *run["schema"].names, "verification_status", "reviewer", "review_note", "reviewed_at"]
        writer.writerow([csv_cell(h) for h in headers])
        for record in run["records"]:
            decision = run["reviews"].get(record.record_id, {})
            if decision.get("decision") != "approved" or approval_blockers(record, run["schema"]):
                continue
            writer.writerow([csv_cell(v) for v in [record.record_id, *[record.get(n) for n in run["schema"].names],
                             record.status.value, decision["reviewer"], decision["note"], decision["updated_at"]]])
        return output.getvalue().encode("utf-8-sig")

    def compare(self, before_id: str, after_id: str, *, key: str = "account_id") -> dict:
        before, after = self.load(before_id), self.load(after_id)
        if before["project"] != after["project"]:
            raise ValueError("Compare runs from the same project.")
        if before["schema"].model_dump() != after["schema"].model_dump():
            raise ValueError("Schemas differ; compare runs audited with the same schema.")
        if key not in before["schema"].names:
            raise ValueError("Comparison key is not in the schema.")

        def index(run):
            rows = {}
            for record in run["records"]:
                value = record.get(key)
                if value is None or record.field_status(key) in (FieldStatus.INVALID, FieldStatus.CONFLICT):
                    raise ValueError(f"Comparison key '{key}' must be present and valid on every record.")
                value = str(value).strip()
                if value in rows:
                    raise ValueError(f"Comparison key '{key}' is not unique. Use a stable customer ID.")
                rows[value] = record
            return rows

        old, new = index(before), index(after)
        changed = []
        unchanged = 0
        for identity in sorted(old.keys() & new.keys()):
            changes = []
            for name in before["schema"].names:
                a, b = old[identity].get(name), new[identity].get(name)
                sa, sb = old[identity].field_status(name).value, new[identity].field_status(name).value
                if a != b or sa != sb:
                    changes.append({"field": name, "before": a, "after": b, "status_before": sa, "status_after": sb})
            if changes:
                changed.append({"key": identity, "fields": changes})
            else:
                unchanged += 1
        return {"before": before_id, "after": after_id, "key": key, "added": sorted(new.keys() - old.keys()),
                "removed": sorted(old.keys() - new.keys()), "changed": changed, "unchanged": unchanged}

    def delete_run(self, run_id: str) -> None:
        """Delete a local snapshot and its decisions for operator-controlled retention."""
        with self._connect() as db:
            if db.execute("DELETE FROM runs WHERE run_id=?", (run_id,)).rowcount != 1:
                raise ValueError("Run not found in this workspace.")
