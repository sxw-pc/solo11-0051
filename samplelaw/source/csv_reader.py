"""Local CSV reader with explicit encoding fallback, plus a stable writer."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import DataError

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")

def _decode(raw: bytes, encoding: str | None) -> tuple[str, str]:
    candidates = (encoding,) if encoding else _ENCODINGS
    for candidate in candidates:
        try:
            return raw.decode(candidate), candidate
        except (LookupError, UnicodeDecodeError):
            continue
    attempted = ", ".join(str(item) for item in candidates)
    raise DataError(
        "DATA-003",
        f"cannot decode CSV using {attempted}; save it as UTF-8, UTF-8-SIG or GBK",
    )

def read_csv_dataset(
    path: str | Path,
    *,
    name: str | None = None,
    encoding: str | None = None,
) -> Dataset:
    resolved = Path(path)
    if not resolved.is_file():
        raise DataError("DATA-004", f"CSV file not found: {resolved}", path=str(resolved))
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise DataError("DATA-004", f"cannot read CSV: {exc}", path=str(resolved)) from exc
    if not raw.strip():
        raise DataError("DATA-001", "CSV source is empty", path=str(resolved))
    text, used_encoding = _decode(raw, encoding)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        columns = tuple(reader.fieldnames or ())
        if not columns:
            raise DataError("DATA-001", "CSV source has no header", path=str(resolved))
        if any(not column.strip() for column in columns):
            raise DataError("DATA-004", "CSV header contains an empty name", path=str(resolved))
        if len(set(columns)) != len(columns):
            raise DataError("DATA-004", "CSV header contains duplicate names", path=str(resolved))
        rows: list[dict[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise DataError(
                    "DATA-004",
                    f"row {line_number} has more fields than the header",
                    path=str(resolved),
                    line=line_number,
                )
            rows.append({column: (row.get(column) or "") for column in columns})
    except csv.Error as exc:
        raise DataError("DATA-004", f"malformed CSV: {exc}", path=str(resolved)) from exc
    if not rows:
        raise DataError("DATA-001", "CSV source has no data rows", path=str(resolved))
    return Dataset.from_rows(
        name or resolved.stem,
        columns,
        rows,
        source_kind="csv",
        source_path=str(resolved),
        encoding=used_encoding,
    )

def write_csv_rows(
    path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, str]],
) -> Path:
    """Write deterministic UTF-8 CSV with LF newlines and no BOM."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    try:
        resolved.write_bytes(buffer.getvalue().encode("utf-8"))
    except OSError as exc:
        raise DataError("DATA-004", f"cannot write CSV: {exc}", path=str(resolved)) from exc
    return resolved
