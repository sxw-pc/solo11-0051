"""Stable JSON report rendering."""

from __future__ import annotations

import json

from samplelaw.report.model import ReportDocument

def render_json(document: ReportDocument) -> str:
    return json.dumps(document.to_dict(), ensure_ascii=False, indent=2) + "\n"
