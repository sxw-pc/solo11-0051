"""Detect over-sampling, under-sampling and within-stratum composition drift."""

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

def deviance_alert_payload(alert: DevianceAlert) -> dict[str, Any]:
    return {
        "code": alert.code,
        "kind": alert.kind,
        "stratum": alert.stratum,
        "dimension": alert.dimension,
        "metric": alert.metric,
        "threshold": alert.threshold,
        "observed": alert.observed,
        "message": alert.message,
    }

def deviance_payload(result: DevianceResult) -> dict[str, Any]:
    """Serialize the deviance verdict exactly as it should be replayed from the ledger."""
    return {
        "status": result.status,
        "baseline_batch_id": result.baseline_batch_id,
        "alerts": [deviance_alert_payload(item) for item in result.alerts],
        "drift_metrics": [dict(item) for item in result.drift_metrics],
    }

def deviance_from_payload(value: str | None) -> DevianceResult | None:
    """Rebuild a deviance verdict from its persisted payload, or ``None`` when unavailable."""
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if not isinstance(status, str) or not status:
        return None
    baseline_raw = parsed.get("baseline_batch_id")
    baseline = baseline_raw if isinstance(baseline_raw, str) and baseline_raw else None
    alerts: list[DevianceAlert] = []
    for item in parsed.get("alerts") or ():
        if not isinstance(item, Mapping):
            continue
        try:
            alerts.append(
                DevianceAlert(
                    code=str(item["code"]),
                    kind=str(item.get("kind", "")),
                    stratum=str(item.get("stratum", "")),
                    dimension=str(item.get("dimension", "")),
                    metric=str(item.get("metric", "")),
                    threshold=float(item.get("threshold", 0.0)),
                    observed=float(item.get("observed", 0.0)),
                    message=str(item.get("message", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    metrics = tuple(dict(item) for item in parsed.get("drift_metrics") or () if isinstance(item, Mapping))
    return DevianceResult(status=status, baseline_batch_id=baseline, alerts=tuple(alerts), drift_metrics=metrics)

def deviance_from_ledger(deviance_status: str, deviance_json: str, alerts_json: str) -> DevianceResult:
    """Rebuild the verdict stored on a ledger batch.

    Rows written before deviance payloads were persisted only carry the
    status and the combined alert list; for those we expose the stored
    status/alerts and leave baseline and drift metrics empty rather than
    pretending this run's recomputation is the historical verdict.
    """
    restored = deviance_from_payload(deviance_json)
    if restored is not None:
        return DevianceResult(
            status=deviance_status or restored.status,
            baseline_batch_id=restored.baseline_batch_id,
            alerts=restored.alerts,
            drift_metrics=restored.drift_metrics,
        )
    alerts: list[DevianceAlert] = []
    try:
        parsed_alerts = json.loads(alerts_json or "[]")
    except (json.JSONDecodeError, TypeError):
        parsed_alerts = []
    for item in parsed_alerts if isinstance(parsed_alerts, list) else ():
        if not isinstance(item, Mapping) or item.get("type") != "DEVIANCE":
            continue
        try:
            alerts.append(
                DevianceAlert(
                    code=str(item["code"]),
                    kind=str(item.get("kind", "")),
                    stratum=str(item.get("stratum", "")),
                    dimension=str(item.get("dimension", "")),
                    metric=str(item.get("metric", "")),
                    threshold=float(item.get("threshold", 0.0)),
                    observed=float(item.get("observed", 0.0)),
                    message=str(item.get("message", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return DevianceResult(
        status=deviance_status,
        baseline_batch_id=None,
        alerts=tuple(alerts),
        drift_metrics=(),
    )

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

def _total_variation(current_counts: Mapping[str, int], previous_counts: Mapping[str, int]) -> float:
    current = _distribution(current_counts)
    previous = _distribution(previous_counts)
    return round(
        0.5 * sum(abs(current.get(key, 0.0) - previous.get(key, 0.0)) for key in set(current) | set(previous)), 6
    )

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
    if previous_composition is not None and composition is not None:
        current_strata = composition.get("strata", {})
        previous_strata = previous_composition.get("strata", {})
        for label in sorted(set(current_strata) & set(previous_strata)):
            current_entry = current_strata[label]
            previous_entry = previous_strata[label]
            current_rows = int(current_entry.get("sample_rows", 0))
            previous_rows = int(previous_entry.get("sample_rows", 0))
            if current_rows < plan.deviance.min_baseline_rows or previous_rows < plan.deviance.min_baseline_rows:
                continue
            for dimension in plan.deviance.dimensions:
                current_counts = current_entry.get("dimensions", {}).get(dimension, {})
                previous_counts = previous_entry.get("dimensions", {}).get(dimension, {})
                observed = _total_variation(current_counts, previous_counts)
                metric = {
                    "stratum": label,
                    "dimension": dimension,
                    "metric": "total_variation",
                    "threshold": plan.thresholds.drift,
                    "observed": observed,
                }
                drift_metrics.append(metric)
                if observed > plan.thresholds.drift:
                    alerts.append(
                        DevianceAlert(
                            code="DEV-003",
                            kind="COMPOSITION_DRIFT",
                            stratum=label,
                            dimension=dimension,
                            metric="total_variation",
                            threshold=plan.thresholds.drift,
                            observed=observed,
                            message="within-stratum dimension distribution moved beyond threshold",
                        )
                    )
    alerts.sort(key=lambda item: (item.code, item.stratum, item.dimension))
    baseline = previous_batch_id if previous_composition is not None else None
    if alerts:
        status = "WARN"
    elif previous_composition is None:
        status = "BASELINE"
    else:
        status = "PASS"
    return DevianceResult(
        status=status,
        baseline_batch_id=baseline,
        alerts=tuple(alerts),
        drift_metrics=tuple(drift_metrics),
    )
