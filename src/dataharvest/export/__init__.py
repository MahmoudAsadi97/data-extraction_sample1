"""Delivery: Excel, CSV, JSON and (optionally) Google Sheets."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .excel import export_excel
from .flat import export_csv, export_json
from .gsheets import GoogleSheetsUnavailable, export_google_sheets

if TYPE_CHECKING:
    from ..pipeline import Pipeline

log = logging.getLogger(__name__)

__all__ = ["export_all", "export_excel", "export_csv", "export_json", "export_google_sheets", "GoogleSheetsUnavailable"]


def _write(pipeline: Pipeline, kind: str, path: Path, writer) -> Path | None:
    """Write one output; if the target is locked (open in Excel on Windows) fall back to a timestamped name."""
    try:
        return writer(path)
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now().strftime('%H%M%S')}{path.suffix}")
        pipeline.report.warn(f"{path.name} is locked (open in another program?) - written as {alt.name} instead")
        try:
            return writer(alt)
        except PermissionError as exc:
            pipeline.report.warn(f"{kind} export failed: {exc}")
            return None


def export_all(pipeline: Pipeline) -> dict[str, Path | str]:
    cfg = pipeline.config
    directory, basename = pipeline.output_paths()
    directory.mkdir(parents=True, exist_ok=True)
    project_info = cfg.project.model_dump()
    outputs: dict[str, Path] = {}
    formats = cfg.output.formats
    # plain-text formats first: they never fail on a file lock, so a run is never lost entirely
    if "json" in formats:
        outputs["json"] = _write(pipeline, "json", directory / f"{basename}.json",
                                 lambda path: export_json(path, pipeline.records, pipeline.groups, pipeline.report, pipeline.schema))
    if "csv" in formats:
        outputs["csv"] = _write(pipeline, "csv", directory / f"{basename}.csv",
                                lambda path: export_csv(path, pipeline.records, pipeline.schema))
    if "xlsx" in formats:
        outputs["xlsx"] = _write(pipeline, "xlsx", directory / f"{basename}.xlsx",
                                 lambda path: export_excel(path, pipeline.records, pipeline.groups, pipeline.report, pipeline.schema,
                                                           project_info, include_excluded=cfg.output.include_excluded))
    outputs = {k: v for k, v in outputs.items() if v is not None}
    gs = cfg.output.google_sheets
    if gs.enabled:
        try:
            url = export_google_sheets(pipeline.records, pipeline.groups, pipeline.schema,
                                       title=gs.title or cfg.project.title, spreadsheet_id=gs.spreadsheet_id, share_with=gs.share_with)
            pipeline.report.outputs["google_sheets"] = url
            outputs["google_sheets"] = url
        except GoogleSheetsUnavailable as exc:
            pipeline.report.warn(f"Google Sheets export skipped: {exc}")
        except Exception as exc:  # network / API errors must not lose the local files
            pipeline.report.warn(f"Google Sheets export failed: {exc}")
    return outputs
