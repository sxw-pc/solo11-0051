"""Declarative plan and pipeline data structures.

The loader performs structural checks; this module owns the normalized
objects and the canonical representation used to fingerprint a plan.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from samplelaw.util.errors import ValidationIssue
from samplelaw.util.hashing import canonical_json, sha256_text

API_VERSION = "samplelaw/v1"
API_VERSION_V1 = API_VERSION
API_VERSION_V2 = "samplelaw/v2"
SUPPORTED_API_VERSIONS = (API_VERSION_V1, API_VERSION_V2)
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
    api_version: str = API_VERSION_V1
    @property
    def mode(self) -> str:
        return self.sampling.mode
    @property
    def seed(self) -> int:
        return self.sampling.seed
    @property
    def semantic_key(self) -> str:
        """Version-agnostic equivalence key shared by semantically equal plans."""
        return semantic_group_key(self.canonical())
    def canonical(self) -> dict[str, Any]:
        """Return the versioned canonical representation used for fingerprinting."""
        base = self._canonical_v1()
        if self.api_version == API_VERSION_V2:
            return _canonical_v2(base)
        return base
    def _canonical_v1(self) -> dict[str, Any]:
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

def _canonical_v2(base: dict[str, Any]) -> dict[str, Any]:
    """Derive the v2 canonical form from a v1 canonical dict.

    v2 keeps the v1 shape but stably sorts the order-insensitive
    collections (``strata.expected_levels`` and ``exclude``), coerces the
    ratio fields to plain numbers and never carries ``origin``, so plans
    that only differ in declaration order hash to the same fingerprint.
    """
    canonical = copy.deepcopy(base)
    canonical["apiVersion"] = API_VERSION_V2
    canonical.pop("origin", None)
    strata = canonical["strata"]
    strata["expected_levels"] = sorted(
        (list(level) for level in strata["expected_levels"]), key=canonical_json
    )
    strata["rare_threshold"] = float(strata["rare_threshold"])
    canonical["exclude"] = sorted(canonical["exclude"], key=canonical_json)
    canonical["thresholds"] = {
        key: float(value) for key, value in canonical["thresholds"].items()
    }
    return canonical

def normalize_canonical(canonical: Mapping[str, Any]) -> dict[str, Any]:
    """Irreversibly normalize a canonical dict for equivalence comparison.

    Two canonicals are semantically equivalent iff their normalizations are
    equal. The transform drops the version marker and ``origin``, stably
    sorts ``strata.expected_levels`` and ``exclude`` and coerces ratio
    fields to floats; it cannot be inverted back to the source canonical.
    """
    normalized = copy.deepcopy(dict(canonical))
    normalized.pop("apiVersion", None)
    normalized.pop("origin", None)
    strata = normalized.get("strata")
    if isinstance(strata, dict):
        levels = strata.get("expected_levels")
        if isinstance(levels, list):
            strata["expected_levels"] = sorted(
                (list(level) for level in levels), key=canonical_json
            )
        if "rare_threshold" in strata:
            strata["rare_threshold"] = float(strata["rare_threshold"])
    exclude = normalized.get("exclude")
    if isinstance(exclude, list):
        normalized["exclude"] = sorted(exclude, key=canonical_json)
    thresholds = normalized.get("thresholds")
    if isinstance(thresholds, dict):
        normalized["thresholds"] = {
            key: float(value) for key, value in thresholds.items()
        }
    return normalized

def semantic_group_key(canonical: Mapping[str, Any]) -> str:
    """Equivalence-group key: fingerprint of the normalized canonical.

    The hash algorithm itself is unchanged; only the normalized
    representation is hashed, and no version flag is embedded in it.
    """
    return sha256_text(canonical_json(normalize_canonical(canonical)))

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
    allow_cross_version_baseline: bool = False
    errors_seen: tuple[ValidationIssue, ...] = field(default_factory=tuple)
