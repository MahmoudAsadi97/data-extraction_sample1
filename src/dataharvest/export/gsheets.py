"""Google Sheets delivery (optional).

Needs ``pip install gspread google-auth`` and a service-account key in the
``GOOGLE_SERVICE_ACCOUNT_JSON`` environment variable (a file path or the JSON
itself). See docs/GOOGLE_SHEETS_SETUP.md. Without credentials the CSV export
can be imported into Google Sheets manually (File > Import).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import env
from ..models import DuplicateGroup, Record, RecordStatus
from ..schema import Schema
from .columns import build_columns, record_row

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


class GoogleSheetsUnavailable(RuntimeError):
    pass


def _client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise GoogleSheetsUnavailable("gspread/google-auth are not installed: pip install 'dataharvest[gsheets]' (or pip install gspread google-auth)") from exc
    raw = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise GoogleSheetsUnavailable("GOOGLE_SERVICE_ACCOUNT_JSON is not set - see docs/GOOGLE_SHEETS_SETUP.md (or import the CSV manually)")
    if raw.lstrip().startswith("{"):
        info = json.loads(raw)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        path = Path(os.path.expanduser(raw))
        if not path.exists():
            raise GoogleSheetsUnavailable(f"service-account file not found: {path}")
        creds = Credentials.from_service_account_file(str(path), scopes=SCOPES)
    return gspread.authorize(creds)


def _to_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    return value


def export_google_sheets(records: list[Record], groups: list[DuplicateGroup], schema: Schema, *, title: str,
                         spreadsheet_id: str = "", share_with: list[str] | None = None) -> str:
    """Create/update a spreadsheet with the data, review and duplicates sheets. Returns its URL."""
    gc = _client()
    spreadsheet_id = spreadsheet_id or env("GOOGLE_SHEETS_ID")
    if spreadsheet_id:
        sh = gc.open_by_key(spreadsheet_id)
    else:
        sh = gc.create(title)
    columns = build_columns(schema)
    delivered = [r for r in records if r.status != RecordStatus.EXCLUDED]
    review = [r for r in records if r.status in (RecordStatus.NEEDS_REVIEW, RecordStatus.EXCLUDED) and not r.duplicate_of]
    name_field = schema.name_field or "company_name"

    sheets: dict[str, list[list[Any]]] = {
        schema.label[:99]: [[c.header for c in columns]] + [[_to_cell(v) for v in record_row(r, columns)] for r in delivered],
        "Needs Review": [["Record ID", "Status", "Name", "Review flags", "Source URL", "Resolution"]]
        + [[r.record_id, r.status.value, _to_cell(r.get(name_field)), "\n".join(r.flags), r.source_url, ""] for r in review],
        "Duplicates": [["Group", "Action", "Kept record", "Other records", "Reason", "Similarity %"]]
        + [[g.group_id, "merged" if g.merged else "possible - decide", g.master_id, ", ".join(m for m in g.member_ids if m != g.master_id), g.reason, round(g.similarity)] for g in groups],
    }
    existing = {ws.title: ws for ws in sh.worksheets()}
    first = True
    for name, values in sheets.items():
        rows, cols = max(len(values), 2), max(len(values[0]), 1)
        if name in existing:
            ws = existing[name]
            ws.clear()
            ws.resize(rows=rows, cols=cols)
        elif first and "Sheet1" in existing:
            ws = existing["Sheet1"]
            ws.update_title(name)
            ws.resize(rows=rows, cols=cols)
        else:
            ws = sh.add_worksheet(title=name, rows=rows, cols=cols)
        ws.update(values, value_input_option="RAW")
        ws.format("1:1", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                          "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47}})
        ws.freeze(rows=1)
        first = False
    for email in share_with or []:
        try:
            sh.share(email, perm_type="user", role="writer", notify=False)
        except Exception as exc:  # pragma: no cover - depends on Drive permissions
            log.warning("could not share with %s: %s", email, exc)
    return sh.url
