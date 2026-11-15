"""Stratum coverage, rare-layer hit checks and simple share-deviation metrics."""

from __future__ import annotations

from dataclasses import dataclass

from samplelaw.config.schema import PlanSpec
from samplelaw.plan.sampling import eligible_rows
from samplelaw.plan.stratify import enumerate_strata, stratum_counts, stratum_label
from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import SamplingError

@dataclass(frozen=True)
class CoverageAlert:
    code: str
    stratum: str
    metric: str
    threshold: float | None
    observed: float
    message: str

@dataclass(frozen=True)
class StratumCoverage:
    label: str
    source_rows: int
    source_share: float
    sample_rows: int
    sample_share: float
    share_deviation: float
    rare: bool
    empty: bool
    covered: bool

@dataclass(frozen=True)
class CoverageResult:
    status: str
    coverage_rate: float
    strata: tuple[StratumCoverage, ...]
    rare_total: int
    rare_hit: int
    rare_missed: tuple[str, ...]
    max_abs_share_deviation: float
    weighted_abs_share_deviation: float
    max_share_threshold: float
    weighted_share_threshold: float
    alerts: tuple[CoverageAlert, ...]

def _rounded(value: float) -> float:
    return round(value, 6)

def verify_coverage(plan: PlanSpec, source: Dataset, sample: Dataset) -> CoverageResult:
    source.require_columns(plan.strata_keys, role="source")
    sample.require_columns(plan.strata_keys, role="sample")
    kept, _ = eligible_rows(source, plan)
    if not kept:
        raise SamplingError("SAMP-001", "no eligible source rows for coverage verification")
    eligible = Dataset.from_rows(
        source.name,
        source.columns,
        (row for _, row in kept),
        source_kind=source.source_kind,
    )
    summary = enumerate_strata(eligible, plan.strata_keys, plan.rare_threshold, plan.expected_levels)
    sample_counts = stratum_counts(sample.rows, plan.strata_keys)
    sample_total = len(sample.rows)
    details: list[StratumCoverage] = []
    alerts: list[CoverageAlert] = []
    for info in summary.strata:
        actual = sample_counts.get(info.key, 0)
        sample_share = actual / sample_total if sample_total else 0.0
        deviation = abs(sample_share - info.share)
        covered = actual > 0
        details.append(
            StratumCoverage(
                label=info.label,
                source_rows=info.count,
                source_share=_rounded(info.share),
                sample_rows=actual,
                sample_share=_rounded(sample_share),
                share_deviation=_rounded(deviation),
                rare=info.rare,
                empty=info.empty,
                covered=covered,
            )
        )
        if info.rare and not covered:
            alerts.append(
                CoverageAlert(
                    "COV-001",
                    info.label,
                    "rare_stratum_hit",
                    1.0,
                    0.0,
                    "rare stratum has no sampled row",
                )
            )
        if info.empty:
            alerts.append(
                CoverageAlert(
                    "COV-002",
                    info.label,
                    "empty_source_stratum",
                    0.0,
                    0.0,
                    "expected stratum has no eligible source rows",
                )
            )
        if not covered and not info.rare and not info.empty:
            alerts.append(
                CoverageAlert(
                    "COV-005",
                    info.label,
                    "stratum_covered",
                    1.0,
                    0.0,
                    "source stratum has no sampled row",
                )
            )
    covered_count = sum(1 for item in details if item.covered)
    coverage_rate = covered_count / len(details) if details else 0.0
    rare = [item for item in summary.rare if not item.empty]
    rare_total = len(rare)
    rare_missed = tuple(item.label for item in rare if sample_counts.get(item.key, 0) == 0)
    rare_hit = rare_total - len(rare_missed)
    max_abs = max((item.share_deviation for item in details), default=0.0)
    weighted = sum(item.source_share * item.share_deviation for item in details)
    max_abs = _rounded(max_abs)
    weighted = _rounded(weighted)
    if max_abs > plan.thresholds.max_share_deviation:
        alerts.append(
            CoverageAlert(
                "COV-003",
                "",
                "max_abs_share_deviation",
                plan.thresholds.max_share_deviation,
                max_abs,
                "largest stratum share deviation exceeds the plan threshold",
            )
        )
    if weighted > plan.thresholds.max_weighted_share_deviation:
        alerts.append(
            CoverageAlert(
                "COV-004",
                "",
                "weighted_abs_share_deviation",
                plan.thresholds.max_weighted_share_deviation,
                weighted,
                "source-share-weighted deviation exceeds the plan threshold",
            )
        )
    known_keys = {info.key for info in summary.strata}
    unexpected = sorted((stratum_label(key), key) for key in sample_counts if key not in known_keys)
    for label, key in unexpected:
        alerts.append(
            CoverageAlert(
                "COV-006",
                label,
                "unexpected_stratum",
                0.0,
                float(sample_counts[key]),
                "sample contains a stratum not present in the source",
            )
        )
    if sample_total == 0:
        alerts.insert(
            0,
            CoverageAlert("COV-000", "", "sample_rows", 1.0, 0.0, "persisted sample has no rows"),
        )
        status = "FAIL"
    elif alerts:
        status = "WARN"
    else:
        status = "PASS"
    alerts.sort(key=lambda item: (item.code, item.stratum))
    return CoverageResult(
        status=status,
        coverage_rate=_rounded(coverage_rate),
        strata=tuple(details),
        rare_total=rare_total,
        rare_hit=rare_hit,
        rare_missed=rare_missed,
        max_abs_share_deviation=max_abs,
        weighted_abs_share_deviation=weighted,
        max_share_threshold=plan.thresholds.max_share_deviation,
        weighted_share_threshold=plan.thresholds.max_weighted_share_deviation,
        alerts=tuple(alerts),
    )
