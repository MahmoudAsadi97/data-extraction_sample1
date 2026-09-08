"""Delivery: Excel, CSV, JSON and (optionally) Google Sheets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .excel import export_excel
from .flat import export_csv, export_json
from .gsheets import GoogleSheetsUnavailable, export_google_sheets

if TYPE_CHECKING:
    from ..pipeline import Pipeline

log = logging.getLogger(__name__)

__all__ = ["export_all", "export_excel", "export_csv", "export_json", "export_google_sheets", "GoogleSheetsUnavailable"]


def export_all(pipeline: Pipeline) -> dict[str, Path]:
    cfg = pipeline.config
    directory, basename = pipeline.output_paths()
    directory.mkdir(parents=True, exist_ok=True)
    project_info = cfg.project.model_dump()
    outputs: dict[str, Path] = {}
    formats = cfg.output.formats
    if "xlsx" in formats:
        outputs["xlsx"] = export_excel(directory / f"{basename}.xlsx", pipeline.records, pipeline.groups, pipeline.report,
                                       pipeline.schema, project_info, include_excluded=cfg.output.include_excluded)
    if "csv" in formats:
        outputs["csv"] = export_csv(directory / f"{basename}.csv", pipeline.records, pipeline.schema)
    if "json" in formats:
        outputs["json"] = export_json(directory / f"{basename}.json", pipeline.records, pipeline.groups, pipeline.report, pipeline.schema)
    gs = cfg.output.google_sheets
    if gs.enabled:
        try:
            url = export_google_sheets(pipeline.records, pipeline.groups, pipeline.schema,
                                       title=gs.title or cfg.project.title, spreadsheet_id=gs.spreadsheet_id, share_with=gs.share_with)
            pipeline.report.outputs["google_sheets"] = url
            outputs["google_sheets"] = Path(url)
        except GoogleSheetsUnavailable as exc:
            pipeline.report.warn(f"Google Sheets export skipped: {exc}")
        except Exception as exc:  # network / API errors must not lose the local files
            pipeline.report.warn(f"Google Sheets export failed: {exc}")
    return outputs
