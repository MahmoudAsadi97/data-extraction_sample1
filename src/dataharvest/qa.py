"""Quality audit of an *existing* spreadsheet (CSV/XLSX): missing, invalid and duplicate records.

This is the "check the database before delivery" step, usable on files that
did not come out of this tool - e.g. a list typed up by hand or received
from a client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import DuplicateGroup, Record, RecordStatus
from .processing.dedupe import dedupe
from .processing.validate import normalize_record, validate_record
from .processing.verify import assign_record_status, compute_completeness
from .report import RunReport
from .schema import Schema, apply_overrides, load_schema
from .sources.csv_import import AUTO_MAPPING, read_rows


@dataclass
class AuditResult:
    records: list[Record]
    groups: list[DuplicateGroup]
    report: RunReport
    schema: Schema
    column_map: dict[str, str]
    ignored_columns: list[str] = field(default_factory=list)

    @property
    def problems(self) -> int:
        return sum(len([f for f in r.flags if not f.startswith("note:")]) for r in self.records)

    @property
    def rows_with_problems(self) -> int:
        return sum(1 for r in self.records if any(not f.startswith("note:") for f in r.flags))


def map_columns(headers: list[str], schema: Schema, mapping: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Map spreadsheet headers to schema fields by explicit mapping, label, name or common aliases."""
    mapping = {k.strip().lower(): v for k, v in (mapping or {}).items()}
    for header, target in mapping.items():
        if header not in {h.strip().lower() for h in headers} or not schema.field(target):
            raise ValueError(f"Invalid column mapping: {header} -> {target}")
    by_label = {f.display.strip().lower(): f.name for f in schema.fields}
    by_name = {f.name.lower(): f.name for f in schema.fields}
    result: dict[str, str] = {}
    ignored: list[str] = []
    for header in headers:
        key = header.strip().lower()
        target = mapping.get(key) or by_label.get(key) or by_name.get(key) or AUTO_MAPPING.get(key)
        if target and schema.field(target) and target in result.values():
            raise ValueError(f"Multiple columns map to '{target}'; resolve the ambiguity before importing.")
        if target and schema.field(target):
            result[header] = target
        else:
            ignored.append(header)
    return result, ignored


def audit_file(path: str | Path, schema_ref: str | dict | None = "leads", *, country: str = "BE",
               mapping: dict[str, str] | None = None, name_similarity: int = 90,
               optional_fields: list[str] | None = None) -> AuditResult:
    path = Path(path)
    schema = load_schema(schema_ref)
    if optional_fields:
        schema = apply_overrides(schema, {name: {"required": False} for name in optional_fields})
    rows = read_rows(path)
    if not rows:
        raise ValueError(f"no data rows found in {path.name}")
    headers = list(rows[0].keys())
    column_map, ignored = map_columns(headers, schema, mapping)
    if not column_map:
        raise ValueError(f"none of the columns in {path.name} could be mapped to schema '{schema.name}' fields: {headers}")
    report = RunReport(project_name=path.stem, project_title=f"Quality audit of {path.name}")
    stage = report.start("load", len(rows))
    records: list[Record] = []
    prefix = "ROW"
    for i, row in enumerate(rows, start=2):
        values: dict[str, Any] = {column_map[h]: v for h, v in row.items() if h in column_map and v not in (None, "")}
        rec = Record(record_id=f"{prefix}-{i:04d}", source=path.name, source_id=f"row {i}", source_url="", raw={"row": i})
        for name, value in values.items():
            rec.set(name, value, source=path.name)
        records.append(rec)
    report.finish(stage, len(records), f"columns mapped: {len(column_map)}, ignored: {len(ignored)}")

    absent_required = [f.name for f in schema.required if f.name not in column_map.values()]
    if absent_required:
        labels = ", ".join(schema.field(n).display for n in absent_required if schema.field(n))
        report.warn(f"required column(s) not present in the file: {labels} - add them before delivery")
    stage = report.start("normalise & validate", len(records))
    problems = 0
    for rec in records:
        normalize_record(rec, schema, country)
        problems += len(validate_record(rec, schema, country))
    report.finish(stage, len(records), f"{problems} problem(s)")

    stage = report.start("duplicates", len(records))
    for rec in records:
        compute_completeness(rec, schema)
    groups = dedupe(records, schema, threshold=name_similarity, merge=False)
    report.finish(stage, len(records), f"{len(groups)} duplicate group(s)")

    for rec in records:
        compute_completeness(rec, schema)
        assign_record_status(rec, schema)
        if rec.status == RecordStatus.EXCLUDED:
            rec.status = RecordStatus.NEEDS_REVIEW  # nothing is dropped from an audit; everything is reported
    if ignored:
        report.warn("columns not part of the schema were ignored: " + ", ".join(ignored))
    report.summarise(records, groups, schema)
    return AuditResult(records=records, groups=groups, report=report, schema=schema, column_map=column_map, ignored_columns=ignored)
