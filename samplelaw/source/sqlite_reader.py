"""Read SQLite tables through a read-only connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import DataError

def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'

def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]

def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def read_sqlite_dataset(
    path: str | Path,
    *,
    table: str | None = None,
    name: str | None = None,
) -> Dataset:
    resolved = Path(path)
    if not resolved.is_file():
        raise DataError("DATA-004", f"SQLite file not found: {resolved}", path=str(resolved))
    uri = resolved.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DataError("DATA-004", f"cannot open SQLite source: {exc}", path=str(resolved)) from exc
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = _table_names(connection)
        chosen = table
        if chosen is None:
            if len(tables) != 1:
                raise DataError(
                    "DATA-002",
                    "table must be specified unless the database has exactly one table",
                    path=str(resolved),
                )
            chosen = tables[0]
        if chosen not in tables:
            raise DataError("DATA-004", f"SQLite table not found: {chosen}", path=str(resolved))
        try:
            cursor = connection.execute(f"SELECT * FROM {_quote_identifier(chosen)}")
            columns = tuple(str(item[0]) for item in cursor.description or ())
            raw_rows = cursor.fetchall()
        except sqlite3.Error as exc:
            raise DataError("DATA-004", f"cannot read SQLite table {chosen}: {exc}", path=str(resolved)) from exc
    finally:
        connection.close()
    if not columns:
        raise DataError("DATA-001", "SQLite table has no columns", path=str(resolved))
    if len(set(columns)) != len(columns) or any(not column for column in columns):
        raise DataError("DATA-004", "SQLite table has invalid columns", path=str(resolved))
    if not raw_rows:
        raise DataError("DATA-001", "SQLite table has no data rows", path=str(resolved))
    rows = [{column: _to_text(value) for column, value in zip(columns, raw_row)} for raw_row in raw_rows]
    return Dataset.from_rows(
        name or chosen,
        columns,
        rows,
        source_kind="sqlite",
        source_path=f"{resolved}#{chosen}",
    )
