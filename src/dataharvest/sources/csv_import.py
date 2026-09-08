"""Import a seed list from CSV / Excel - e.g. a LinkedIn Sales Navigator or Apollo export,
a client-provided list, or names typed up during manual research.

The rows go through exactly the same normalisation, enrichment, verification
and duplicate detection as records extracted from the web.

Example::

    - type: csv_import
      path: data/input/seed_companies.csv
      mapping:                # CSV/Excel column -> schema field (optional; identical names map automatically)
        Company: company_name
        Web: website
        Tel: phone
      country: BE
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..models import RawRecord
from .base import BaseSource, SourceError, register

log = logging.getLogger(__name__)

# Common column names seen in LinkedIn / Apollo / CRM exports, mapped to schema fields.
AUTO_MAPPING = {
    "company": "company_name", "company name": "company_name", "organisation": "company_name",
    "organization": "company_name", "account name": "company_name", "name": "company_name",
    "website": "website", "web": "website", "url": "website", "domain": "website", "company website": "website",
    "email": "email", "e-mail": "email", "email address": "email",
    "phone": "phone", "telephone": "phone", "tel": "phone", "phone number": "phone", "company phone": "phone",
    "linkedin": "linkedin", "linkedin url": "linkedin", "company linkedin url": "linkedin", "linkedin_url": "linkedin",
    "facebook": "facebook", "facebook url": "facebook", "instagram": "instagram",
    "industry": "industry", "category": "category", "sector": "category",
    "city": "city", "town": "city", "postcode": "postcode", "postal code": "postcode", "zip": "postcode",
    "street": "street", "address": "street", "street address": "street", "country": "country",
    "vat": "vat_number", "vat number": "vat_number", "btw": "vat_number", "kbo": "vat_number",
    "enterprise number": "vat_number", "employees": "employees", "# employees": "employees",
    "founded": "founded", "founded year": "founded", "description": "description", "notes": "description",
}


def read_rows(path: Path, encoding: str = "utf-8-sig", sheet: str | None = None) -> list[dict[str, Any]]:
    """Read CSV/TSV/XLSX into a list of dicts keyed by header."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows_iter, [])]
        rows = []
        for row in rows_iter:
            if row is None or all(v in (None, "") for v in row):
                continue
            rows.append({header[i]: row[i] for i in range(min(len(header), len(row))) if header[i]})
        return rows
    with path.open("r", encoding=encoding, newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # type: ignore[assignment]
        reader = csv.DictReader(fh, dialect=dialect)
        return [{(k or "").strip(): v for k, v in row.items()} for row in reader if any((v or "").strip() for v in row.values() if isinstance(v, str))]


@register
class CsvImportSource(BaseSource):
    type = "csv_import"
    description = "Seed list from CSV/Excel (client list, LinkedIn/Apollo export, manual research) - enriched & verified like any other source."
    produces = "leads"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        raw_path = self.require_opt("path")
        path = self.project.resolve_path(raw_path)
        if not path.exists():
            raise SourceError(f"csv_import: file not found: {path}")
        mapping: dict[str, str] = {str(k).strip().lower(): v for k, v in (self.opt("mapping") or {}).items()}
        schema_names = set(self.project.schema_def.names)
        rows = read_rows(path, encoding=self.opt("encoding", "utf-8-sig"), sheet=self.opt("sheet"))
        log.info("csv_import: %d rows read from %s", len(rows), path.name)
        country = self.opt("country") or self.project.project.country
        unmapped: set[str] = set()
        for i, row in enumerate(rows, start=2):  # 2 = first data row in the spreadsheet
            values: dict[str, Any] = {}
            for column, value in row.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                key = column.strip().lower()
                target = mapping.get(key) or (column if column in schema_names else None) or AUTO_MAPPING.get(key)
                if target and target in schema_names:
                    values[target] = value
                else:
                    unmapped.add(column)
            if not values:
                continue
            values.setdefault("country", country)
            yield RawRecord(
                source=self.label,
                source_id=f"row {i}",
                source_url=path.name,
                values=values,
                raw={"row": i, "columns": row},
            )
            if limit and (i - 1) >= limit:
                break
        if unmapped:
            self.warn("columns ignored (no schema field): " + ", ".join(sorted(unmapped)))
