"""Render and write reports as stable UTF-8 LF files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from samplelaw.report.model import ReportDocument
from samplelaw.report.render_html import render_html
from samplelaw.report.render_json import render_json
from samplelaw.report.render_markdown import render_markdown
from samplelaw.util.errors import ReportError

_RENDERERS: dict[str, tuple[str, Callable[[ReportDocument], str]]] = {
    "markdown": ("report.md", render_markdown),
    "json": ("report.json", render_json),
    "html": ("report.html", render_html),
}

def render_report(document: ReportDocument, fmt: str) -> str:
    if fmt not in _RENDERERS:
        raise ReportError("REPORT-001", f"unsupported report format: {fmt}")
    return _RENDERERS[fmt][1](document)

def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    staging_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        staging_path.write_bytes(payload)
        os.replace(staging_path, path)
    except OSError as exc:
        staging_path.unlink(missing_ok=True)
        raise ReportError("REPORT-002", f"cannot write report: {exc}", path=str(path)) from exc

def write_reports(document: ReportDocument, output_dir: str | Path, formats: tuple[str, ...]) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for fmt in formats:
        if fmt not in _RENDERERS:
            raise ReportError("REPORT-001", f"unsupported report format: {fmt}")
        filename, renderer = _RENDERERS[fmt]
        path = directory / filename
        _atomic_write_bytes(path, renderer(document).encode("utf-8"))
        written[fmt] = path
    return written
