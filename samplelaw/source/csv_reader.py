"""Local CSV reader with explicit encoding fallback, plus a stable atomic writer."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import DataError

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
_STAGING_SUFFIX = ".tmp"

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

def _render_csv(columns: Sequence[str], rows: Iterable[Mapping[str, str]]) -> bytes:
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
    return buffer.getvalue().encode("utf-8")

def _write_bytes(path: Path, payload: bytes) -> None:
    try:
        path.write_bytes(payload)
    except OSError as exc:
        raise DataError("DATA-004", f"cannot write CSV: {exc}", path=str(path)) from exc

def write_csv_rows(
    path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, str]],
) -> Path:
    """Write deterministic UTF-8 CSV with LF newlines and no BOM.

    The payload is first rendered into a sibling temporary file and then
    atomically swapped into place with :func:`os.replace`, so a reader never
    observes a half-written file.
    """
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    staging_path = staging_csv_path(resolved)
    _write_bytes(staging_path, _render_csv(columns, rows))
    try:
        os.replace(staging_path, resolved)
    except OSError as exc:
        staging_path.unlink(missing_ok=True)
        raise DataError("DATA-004", f"cannot replace CSV: {exc}", path=str(resolved)) from exc
    return resolved

def staging_csv_path(final_path: str | Path) -> Path:
    """Return the sibling temporary path used while staging ``final_path``."""
    resolved = Path(final_path)
    return resolved.with_name(f"{resolved.name}.{os.getpid()}{_STAGING_SUFFIX}")

def write_staged_csv(
    staging_path: str | Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, str]],
) -> Path:
    """Render a CSV to a temporary staging file without touching any target."""
    resolved = Path(staging_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(resolved, _render_csv(columns, rows))
    return resolved

def commit_staged_csv(staging_path: str | Path, final_path: str | Path) -> Path:
    """Atomically move a staged CSV onto its final path.

    ``os.replace`` is atomic on the local filesystem and overwrites any
    previous sample, so reruns can replace an earlier successful output
    without an intermediate missing-file state.
    """
    source = Path(staging_path)
    target = Path(final_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError as exc:
        raise DataError("DATA-004", f"cannot replace CSV: {exc}", path=str(target)) from exc
    return target
