"""Declarative plan and pipeline data structures.

The loader performs structural checks; this module owns the normalized
objects and the canonical representation used to fingerprint a plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from samplelaw.util.errors import ValidationIssue

API_VERSION = "samplelaw/v1"
PLAN_KIND = "SamplingPlan"
PIPELINE_KIND = "Pipeline"

MODES = ("proportional", "fixed", "min_coverage")
SHORTFALL_POLICIES = ("warn", "fail")
EXCLUSION_OPERATORS = ("eq", "neq", "in", "not_in")
REPORT_FORMATS = ("markdown", "json", "html")
SOURCE_TYPES = ("csv", "sqlite")

@dataclass(frozen=True)
class SamplingSpec:
    mode: str
    seed: int
    total_size: int | None
    per_stratum: int | None
    min_per_stratum: int
    max_per_stratum: int | None
    on_shortfall: str

@dataclass(frozen=True)
class Exclusion:
    column: str
    operator: str
    value: str | tuple[str, ...]

@dataclass(frozen=True)
class Thresholds:
    max_share_deviation: float
    max_weighted_share_deviation: float
    drift: float

@dataclass(frozen=True)
class DevianceSpec:
    dimensions: tuple[str, ...]
    min_baseline_rows: int

@dataclass(frozen=True)
class PlanSpec:
    name: str
    version: int
    description: str
    sampling: SamplingSpec
    strata_keys: tuple[str, ...]
    expected_levels: tuple[tuple[str, ...], ...]
    rare_threshold: float
    exclusions: tuple[Exclusion, ...]
    fingerprint_columns: tuple[str, ...] | None
    order_sensitive: bool
    thresholds: Thresholds
    deviance: DevianceSpec
    origin: str = ""
    fingerprint: str = ""
    @property
    def mode(self) -> str:
        return self.sampling.mode
    @property
    def seed(self) -> int:
        return self.sampling.seed
    def canonical(self) -> dict[str, Any]:
        """Return a JSON-safe semantic representation with stable ordering."""
        def exclusion_value(value: str | tuple[str, ...]) -> Any:
            return list(value) if isinstance(value, tuple) else value
        return {
            "apiVersion": API_VERSION,
            "kind": PLAN_KIND,
            "metadata": {
                "name": self.name,
                "version": self.version,
                "description": self.description,
            },
            "sampling": {
                "mode": self.sampling.mode,
                "seed": self.sampling.seed,
                "total_size": self.sampling.total_size,
                "per_stratum": self.sampling.per_stratum,
                "min_per_stratum": self.sampling.min_per_stratum,
                "max_per_stratum": self.sampling.max_per_stratum,
                "on_shortfall": self.sampling.on_shortfall,
            },
            "strata": {
                "keys": list(self.strata_keys),
                "rare_threshold": self.rare_threshold,
                "expected_levels": [list(level) for level in self.expected_levels],
            },
            "exclude": [
                {
                    "column": item.column,
                    "operator": item.operator,
                    "value": exclusion_value(item.value),
                }
                for item in self.exclusions
            ],
            "fingerprint": {
                "columns": list(self.fingerprint_columns) if self.fingerprint_columns else None,
                "order_sensitive": self.order_sensitive,
            },
            "thresholds": {
                "max_share_deviation": self.thresholds.max_share_deviation,
                "max_weighted_share_deviation": self.thresholds.max_weighted_share_deviation,
                "drift": self.thresholds.drift,
            },
            "deviance": {
                "dimensions": list(self.deviance.dimensions),
                "min_baseline_rows": self.deviance.min_baseline_rows,
            },
        }

@dataclass(frozen=True)
class SourceSpec:
    kind: str
    path: Path
    table: str | None = None
    encoding: str | None = None
    name: str = ""

@dataclass(frozen=True)
class JobSpec:
    job_id: str
    plan_path: Path
    source: SourceSpec

@dataclass(frozen=True)
class PipelineSpec:
    name: str
    description: str
    output_dir: Path
    ledger_path: Path
    report_formats: tuple[str, ...]
    jobs: tuple[JobSpec, ...]
    config_path: Path
    errors_seen: tuple[ValidationIssue, ...] = field(default_factory=tuple)
