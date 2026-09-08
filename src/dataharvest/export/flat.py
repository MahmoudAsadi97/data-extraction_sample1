"""CSV (Excel/Google-Sheets friendly) and JSON (full provenance) exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..models import DuplicateGroup, Record, RecordStatus
from ..report import RunReport
from ..schema import Schema
from .columns import build_columns, record_row


def export_csv(path: Path, records: list[Record], schema: Schema, *, delivered_only: bool = True) -> Path:
    columns = build_columns(schema)
    rows = [r for r in records if r.status != RecordStatus.EXCLUDED] if delivered_only else records
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:  # BOM so Excel detects UTF-8
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([c.header for c in columns])
        for rec in rows:
            writer.writerow(["" if v is None else (v.replace("\n", " | ") if isinstance(v, str) else v) for v in record_row(rec, columns)])
    return path


def export_json(path: Path, records: list[Record], groups: list[DuplicateGroup], report: RunReport, schema: Schema) -> Path:
    payload: dict[str, Any] = {
        "schema": schema.model_dump(),
        "report": report.to_dict(),
        "duplicate_groups": [g.__dict__ for g in groups],
        "records": [r.as_dict() for r in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
