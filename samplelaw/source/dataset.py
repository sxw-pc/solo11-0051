"""Unified in-memory dataset model plus local type inference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

from samplelaw.util.errors import DataError
from samplelaw.util.hashing import dataset_fingerprint, row_fingerprint

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+(?:Z)?)?$")

@dataclass(frozen=True)
class Dataset:
    """An ordered, immutable-looking table held entirely in memory."""
    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    source_kind: str = "memory"
    source_path: str = ""
    encoding: str = ""
    @classmethod
    def from_rows(
        cls,
        name: str,
        columns: Sequence[str],
        rows: Iterable[Mapping[str, str]],
        *,
        source_kind: str = "memory",
        source_path: str = "",
        encoding: str = "",
    ) -> "Dataset":
        normalized_columns = tuple(columns)
        if len(set(normalized_columns)) != len(normalized_columns):
            raise DataError("DATA-004", "dataset contains duplicate column names", path=name)
        if any(not column for column in normalized_columns):
            raise DataError("DATA-004", "dataset contains an empty column name", path=name)
        normalized_rows: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            missing = [column for column in normalized_columns if column not in row]
            if missing:
                raise DataError(
                    "DATA-002",
                    f"row {index + 1} is missing columns: {', '.join(missing)}",
                    path=name,
                )
            normalized_rows.append({column: str(row[column]) for column in normalized_columns})
        return cls(
            name=name,
            columns=normalized_columns,
            rows=tuple(normalized_rows),
            source_kind=source_kind,
            source_path=source_path,
            encoding=encoding,
        )
    def require_columns(self, columns: Sequence[str], *, role: str = "dataset") -> None:
        missing = [column for column in columns if column not in self.columns]
        if missing:
            raise DataError(
                "DATA-002",
                f"{role} is missing columns: {', '.join(missing)}",
                path=self.name,
            )
    def fingerprint(self) -> str:
        return dataset_fingerprint(self.columns, self.rows)
    def row_hashes(self, columns: Sequence[str]) -> list[str]:
        return [row_fingerprint(row, columns) for row in self.rows]

def _is_iso_date(value: str) -> bool:
    text = value.strip()
    if not _DATE_RE.fullmatch(text):
        return False
    date_part = text[:10]
    try:
        date.fromisoformat(date_part)
    except ValueError:
        return False
    if len(text) > 10:
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
    return True

def _infer_one(values: Sequence[str]) -> str:
    present = [value.strip() for value in values if value.strip()]
    if not present:
        return "string"
    if all(_INT_RE.fullmatch(value) for value in present):
        return "integer"
    if all(_FLOAT_RE.fullmatch(value) for value in present):
        return "float"
    if all(_is_iso_date(value) for value in present):
        return "date"
    return "string"

def infer_column_types(dataset: Dataset) -> dict[str, str]:
    """Infer integer/float/date/string for each column, ignoring empty cells."""
    result: dict[str, str] = {}
    for column in dataset.columns:
        values = [row[column] for row in dataset.rows]
        result[column] = _infer_one(values)
    return result

def primary_key_candidates(dataset: Dataset) -> list[str]:
    """Single-column candidates: non-empty in every row and globally unique."""
    candidates: list[str] = []
    for column in dataset.columns:
        values = [row[column] for row in dataset.rows]
        if values and all(value != "" for value in values) and len(values) == len(set(values)):
            candidates.append(column)
    return candidates

def filter_columns(dataset: Dataset, columns: Sequence[str]) -> Dataset:
    dataset.require_columns(columns)
    return Dataset.from_rows(
        dataset.name,
        columns,
        ({column: row[column] for column in columns} for row in dataset.rows),
        source_kind=dataset.source_kind,
        source_path=dataset.source_path,
        encoding=dataset.encoding,
    )
