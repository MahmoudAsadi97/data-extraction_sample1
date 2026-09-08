"""Run report: what was done, how many records at each stage, what needs attention."""

from __future__ import annotations

import json
import platform
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .models import DuplicateGroup, Record, RecordStatus
from .schema import Schema


@dataclass
class Stage:
    name: str
    started: float
    finished: float | None = None
    input_count: int = 0
    output_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return (self.finished or time.monotonic()) - self.started


@dataclass
class RunReport:
    project_name: str
    project_title: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    stages: list[Stage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    field_completeness: dict[str, float] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)
    verification_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    duplicate_groups: int = 0
    records_total: int = 0
    records_delivered: int = 0

    # ------------------------------------------------------------------ stages
    def start(self, name: str, input_count: int = 0) -> Stage:
        stage = Stage(name=name, started=time.monotonic(), input_count=input_count)
        self.stages.append(stage)
        return stage

    def finish(self, stage: Stage, output_count: int | None = None, note: str | None = None) -> None:
        stage.finished = time.monotonic()
        if output_count is not None:
            stage.output_count = output_count
        if note:
            stage.notes.append(note)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # ------------------------------------------------------------------ statistics
    def summarise(self, records: list[Record], groups: list[DuplicateGroup], schema: Schema) -> None:
        delivered = [r for r in records if r.status != RecordStatus.EXCLUDED]
        self.records_total = len(records)
        self.records_delivered = len(delivered)
        self.status_counts = {s.value: 0 for s in RecordStatus}
        for r in records:
            self.status_counts[r.status.value] += 1
        self.field_completeness = {
            f.name: round(100.0 * sum(1 for r in delivered if r.has(f.name)) / len(delivered), 1) if delivered else 0.0
            for f in schema.fields
        }
        flags: Counter[str] = Counter()
        for r in records:
            for fl in r.flags:
                flags[_generalise_flag(fl)] += 1
        self.flag_counts = dict(flags.most_common(25))
        ver: dict[str, dict[str, int]] = {}
        for f in schema.fields:
            if f.type in ("email", "url", "phone", "vat", "string") and f.name in ("company_name", "email", "website", "phone", "vat_number"):
                counts: Counter[str] = Counter(r.field_status(f.name).value for r in delivered)
                ver[f.name] = dict(counts)
        self.verification_counts = ver
        self.duplicate_groups = len(groups)
        self.sources = dict(Counter(r.source for r in records))

    # ------------------------------------------------------------------ output
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": f"DataHarvest {__version__}",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "project": {"name": self.project_name, "title": self.project_title},
            "started_at": self.started_at,
            "records_total": self.records_total,
            "records_delivered": self.records_delivered,
            "status_counts": self.status_counts,
            "sources": self.sources,
            "duplicate_groups": self.duplicate_groups,
            "field_completeness_pct": self.field_completeness,
            "verification_counts": self.verification_counts,
            "flag_counts": self.flag_counts,
            "stages": [
                {"name": s.name, "seconds": round(s.seconds, 2), "input": s.input_count, "output": s.output_count, "notes": s.notes}
                for s in self.stages
            ],
            "warnings": self.warnings,
            "settings": self.settings,
            "outputs": self.outputs,
        }

    def to_markdown(self) -> str:
        d = self.to_dict()
        lines = [f"# Run report - {self.project_title}", ""]
        lines.append(f"- Project: `{self.project_name}`  ")
        lines.append(f"- Started: {self.started_at}  ")
        lines.append(f"- Tool: {d['tool']} on Python {d['python']}  ")
        lines.append(f"- Records extracted: **{self.records_total}**, delivered: **{self.records_delivered}** "
                     f"(excluded/merged: {self.records_total - self.records_delivered})  ")
        lines.append(f"- Duplicate groups: {self.duplicate_groups}")
        lines += ["", "## Record status", "", "| Status | Count |", "|---|---|"]
        for k, v in self.status_counts.items():
            lines.append(f"| {k} | {v} |")
        lines += ["", "## Sources", "", "| Source | Records |", "|---|---|"]
        for k, v in self.sources.items():
            lines.append(f"| {k} | {v} |")
        if self.verification_counts:
            lines += ["", "## Verification of key fields (delivered records)", "", "| Field | verified | unverified | conflict | invalid | missing |", "|---|---|---|---|---|---|"]
            for name, counts in self.verification_counts.items():
                lines.append(f"| {name} | " + " | ".join(str(counts.get(s, 0)) for s in ("verified", "unverified", "conflict", "invalid", "missing")) + " |")
        lines += ["", "## Field completeness (delivered records)", "", "| Field | Filled |", "|---|---|"]
        for k, v in self.field_completeness.items():
            lines.append(f"| {k} | {v:.0f}% |")
        if self.flag_counts:
            lines += ["", "## Most common review flags", "", "| Flag | Records |", "|---|---|"]
            for k, v in self.flag_counts.items():
                lines.append(f"| {k} | {v} |")
        lines += ["", "## Pipeline stages", "", "| Stage | In | Out | Seconds | Notes |", "|---|---|---|---|---|"]
        for s in d["stages"]:
            lines.append(f"| {s['name']} | {s['input']} | {s['output']} | {s['seconds']} | {'; '.join(s['notes'])} |")
        if self.warnings:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in self.warnings]
        if self.outputs:
            lines += ["", "## Output files", ""] + [f"- {k}: `{v}`" for k, v in self.outputs.items()]
        return "\n".join(lines) + "\n"

    def write(self, directory: Path, basename: str) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        md = directory / f"{basename}_report.md"
        js = directory / f"{basename}_report.json"
        md.write_text(self.to_markdown(), encoding="utf-8")
        js.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return md, js


def _generalise_flag(flag: str) -> str:
    """Collapse record-specific details so flags can be counted ('duplicate of REC-123' -> 'duplicate of <id>')."""
    import re

    text = re.sub(r"REC-[A-Z0-9]+", "<id>", flag)
    text = re.sub(r"DUP-\d+", "<group>", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r":.*$", "", text)
    text = re.sub(r"'[^']*'", "'…'", text)
    return text.strip(" -")
