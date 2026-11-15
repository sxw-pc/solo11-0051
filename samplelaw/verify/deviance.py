"""Detect over-sampling, under-sampling and within-stratum composition drift.

Composition drift compares the baseline and current batches only on the value
domain they share: values that appeared or disappeared are attributed as
domain changes, and the drift distance is computed after re-normalizing both
sides on the shared subset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from samplelaw.config.schema import PlanSpec
from samplelaw.plan.sampling import Allocation
from samplelaw.plan.stratify import stratum_key, stratum_label
from samplelaw.source.dataset import Dataset

Composition = dict[str, Any]

@dataclass(frozen=True)
class DevianceAlert:
    code: str
    kind: str
    stratum: str
    dimension: str
    metric: str
    threshold: float
    observed: float
    message: str

@dataclass(frozen=True)
class DevianceResult:
    status: str
    baseline_batch_id: str | None
    alerts: tuple[DevianceAlert, ...]
    drift_metrics: tuple[dict[str, Any], ...]
    domain_changes: tuple[dict[str, Any], ...] = ()

def build_composition(plan: PlanSpec, sample: Dataset) -> Composition:
    """Build the per-stratum dimension histogram stored in the ledger."""
    sample.require_columns(plan.strata_keys, role="sample")
    sample.require_columns(plan.deviance.dimensions, role="sample")
    strata: dict[str, Any] = {}
    for row in sample.rows:
        label = stratum_label(stratum_key(row, plan.strata_keys))
        entry = strata.setdefault(
            label, {"sample_rows": 0, "dimensions": {column: {} for column in plan.deviance.dimensions}}
        )
        entry["sample_rows"] += 1
        for column in plan.deviance.dimensions:
            value = row[column]
            counts = entry["dimensions"][column]
            counts[value] = counts.get(value, 0) + 1
    return {"strata": strata}

def composition_to_json(composition: Composition) -> str:
    return json.dumps(composition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def composition_from_json(value: str | None) -> Composition | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

def _distribution(counts: Mapping[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    if total == 0:
        return {}
    return {key: value / total for key, value in counts.items()}

def _domain_changes(
    label: str,
    dimension: str,
    current_counts: Mapping[str, int],
    previous_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Attribute values that appeared in or disappeared from the value domain."""
    current_distribution = _distribution(current_counts)
    previous_distribution = _distribution(previous_counts)
    changes: list[dict[str, Any]] = []
    for value in sorted(set(previous_counts) - set(current_counts)):
        changes.append(
            {
                "stratum": label,
                "dimension": dimension,
                "value": value,
                "change": "removed",
                "previous_share": round(previous_distribution.get(value, 0.0), 6),
                "current_share": None,
            }
        )
    for value in sorted(set(current_counts) - set(previous_counts)):
        changes.append(
            {
                "stratum": label,
                "dimension": dimension,
                "value": value,
                "change": "added",
                "previous_share": None,
                "current_share": round(current_distribution.get(value, 0.0), 6),
            }
        )
    return changes

def _common_subset_variation(
    current_counts: Mapping[str, int],
    previous_counts: Mapping[str, int],
    common_values: Sequence[str],
) -> float | None:
    """Total variation on the shared value domain after re-normalizing both sides."""
    if not common_values:
        return None
    if not sum(current_counts[value] for value in common_values):
        return None
    if not sum(previous_counts[value] for value in common_values):
        return None
    current = _distribution({value: current_counts[value] for value in common_values})
    previous = _distribution({value: previous_counts[value] for value in common_values})
    values = sorted(common_values)
    return round(0.5 * sum(abs(current[value] - previous[value]) for value in values), 6)

def detect_deviance(
    plan: PlanSpec,
    allocations: Sequence[Allocation],
    composition: Composition | None,
    previous_composition: Composition | None = None,
    previous_batch_id: str | None = None,
) -> DevianceResult:
    alerts: list[DevianceAlert] = []
    max_rows = plan.sampling.max_per_stratum
    for allocation in allocations:
        if max_rows is not None and allocation.actual > max_rows:
            alerts.append(
                DevianceAlert(
                    code="DEV-001",
                    kind="OVER_SAMPLE",
                    stratum=allocation.label,
                    dimension="",
                    metric="actual_rows",
                    threshold=float(max_rows),
                    observed=float(allocation.actual),
                    message="stratum sample exceeds the declared upper bound",
                )
            )
        if allocation.source_rows > 0 and allocation.actual < plan.sampling.min_per_stratum:
            alerts.append(
                DevianceAlert(
                    code="DEV-002",
                    kind="UNDER_SAMPLE",
                    stratum=allocation.label,
                    dimension="",
                    metric="actual_rows",
                    threshold=float(plan.sampling.min_per_stratum),
                    observed=float(allocation.actual),
                    message="stratum sample is below the declared lower bound",
                )
            )
    drift_metrics: list[dict[str, Any]] = []
    domain_changes: list[dict[str, Any]] = []
    if previous_composition is not None and composition is not None:
        current_strata = composition.get("strata", {})
        previous_strata = previous_composition.get("strata", {})
        for label in sorted(set(current_strata) & set(previous_strata)):
            current_entry = current_strata[label]
            previous_entry = previous_strata[label]
            current_rows = int(current_entry.get("sample_rows", 0))
            previous_rows = int(previous_entry.get("sample_rows", 0))
            # The small-sample guard uses the full stratum size, never the shared subset.
            rows_sufficient = (
                current_rows >= plan.deviance.min_baseline_rows
                and previous_rows >= plan.deviance.min_baseline_rows
            )
            for dimension in plan.deviance.dimensions:
                current_counts = current_entry.get("dimensions", {}).get(dimension, {})
                previous_counts = previous_entry.get("dimensions", {}).get(dimension, {})
                if not rows_sufficient:
                    continue
                domain_changes.extend(
                    _domain_changes(label, dimension, current_counts, previous_counts)
                )
                common_values = sorted(set(current_counts) & set(previous_counts))
                observed = _common_subset_variation(current_counts, previous_counts, common_values)
                metric: dict[str, Any] = {
                    "stratum": label,
                    "dimension": dimension,
                    "metric": "total_variation",
                    "threshold": plan.thresholds.drift,
                    "observed": observed,
                    "common_values": common_values,
                    "status": "NO_BASELINE" if observed is None else "PASS",
                }
                drift_metrics.append(metric)
                if observed is None:
                    continue
                if observed > plan.thresholds.drift:
                    metric["status"] = "DRIFT"
                    alerts.append(
                        DevianceAlert(
                            code="DEV-003",
                            kind="COMPOSITION_DRIFT",
                            stratum=label,
                            dimension=dimension,
                            metric="total_variation",
                            threshold=plan.thresholds.drift,
                            observed=observed,
                            message=(
                                "within-stratum dimension distribution moved beyond threshold "
                                "on the shared value domain"
                            ),
                        )
                    )
    alerts.sort(key=lambda item: (item.code, item.stratum, item.dimension))
    domain_changes.sort(key=lambda item: (item["stratum"], item["dimension"], item["value"]))
    baseline = previous_batch_id if previous_composition is not None else None
    if alerts:
        status = "WARN"
    elif previous_composition is None:
        status = "BASELINE"
    elif drift_metrics and all(item["status"] == "NO_BASELINE" for item in drift_metrics):
        status = "NO_BASELINE"
    else:
        status = "PASS"
    return DevianceResult(
        status=status,
        baseline_batch_id=baseline,
        alerts=tuple(alerts),
        drift_metrics=tuple(drift_metrics),
        domain_changes=tuple(domain_changes),
    )
