"""Stable report document and timestamp rules shared by all output formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

def composition_table_rows(composition: object) -> list[list[object]]:
    """Flatten a composition snapshot into stable per-value table rows."""
    if not isinstance(composition, dict):
        return []
    strata = composition.get("strata")
    if not isinstance(strata, dict):
        return []
    rows: list[list[object]] = []
    for label in sorted(strata):
        entry = strata[label]
        if not isinstance(entry, dict):
            continue
        sample_rows = entry.get("sample_rows", 0)
        dimensions = entry.get("dimensions")
        if not isinstance(dimensions, dict):
            continue
        for dimension in sorted(dimensions):
            counts = dimensions[dimension]
            if not isinstance(counts, dict):
                continue
            for value in sorted(counts):
                rows.append([label, dimension, value, counts[value], sample_rows])
    return rows

def utc_timestamp(value: str | datetime | None = None) -> str:
    """Return the single report timestamp format: ``YYYY-MM-DDTHH:MM:SSZ``."""
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
    elif isinstance(value, str):
        if _TIMESTAMP_RE.fullmatch(value):
            return value
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO-8601") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
    else:
        raise TypeError("timestamp must be a string, datetime or None")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

@dataclass(frozen=True)
class ReportStratum:
    label: str
    source_rows: int
    source_share: float
    target_rows: int
    sample_rows: int
    sample_share: float
    share_deviation: float
    rare: bool
    empty: bool
    covered: bool
    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_rows": self.source_rows,
            "source_share": self.source_share,
            "target_rows": self.target_rows,
            "sample_rows": self.sample_rows,
            "sample_share": self.sample_share,
            "share_deviation": self.share_deviation,
            "rare": self.rare,
            "empty": self.empty,
            "covered": self.covered,
        }

@dataclass(frozen=True)
class ReportDocument:
    generated_at: str
    batch_id: str
    overall_status: str
    plan_name: str
    plan_version: int
    plan_fingerprint: str
    source_name: str
    source_kind: str
    source_fingerprint: str
    sample_fingerprint: str
    mode: str
    seed: int
    requested_total: int
    sample_total: int
    excluded_rows: int
    eligible_rows: int
    strata: tuple[ReportStratum, ...]
    sampling_warnings: tuple[dict[str, Any], ...]
    replay: dict[str, Any]
    coverage: dict[str, Any]
    deviance: dict[str, Any]
    alerts: tuple[dict[str, Any], ...]
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "samplelaw.report/v1",
            "generated_at": self.generated_at,
            "batch_id": self.batch_id,
            "overall_status": self.overall_status,
            "plan": {
                "name": self.plan_name,
                "version": self.plan_version,
                "fingerprint": self.plan_fingerprint,
            },
            "source": {
                "name": self.source_name,
                "kind": self.source_kind,
                "fingerprint": self.source_fingerprint,
            },
            "sample": {
                "fingerprint": self.sample_fingerprint,
                "mode": self.mode,
                "seed": self.seed,
                "requested_rows": self.requested_total,
                "rows": self.sample_total,
                "excluded_rows": self.excluded_rows,
                "eligible_rows": self.eligible_rows,
            },
            "strata": [item.to_dict() for item in self.strata],
            "sampling": {"warnings": list(self.sampling_warnings)},
            "replay": self.replay,
            "coverage": self.coverage,
            "deviance": self.deviance,
            "alerts": list(self.alerts),
        }
