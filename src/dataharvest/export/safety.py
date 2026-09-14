"""Literal text for spreadsheet-oriented exports; JSON retains original values."""

from __future__ import annotations

from typing import Any


def csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    # Check before replacing newlines so leading control characters cannot hide a formula.
    probe = value.lstrip(" \t\r\n\ufeff")
    dangerous = probe.startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠")) or value.startswith(("\t", "\r", "\n"))
    text = value.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
    return "'" + text if dangerous else text
