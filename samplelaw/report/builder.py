"""Assemble a report document from verification results."""

from __future__ import annotations

from typing import Any, Mapping

from samplelaw.config.schema import PlanSpec
from samplelaw.plan.sampling import SamplingResult
from samplelaw.report.model import ReportDocument, ReportStratum, utc_timestamp
from samplelaw.source.dataset import Dataset
from samplelaw.util.hashing import batch_fingerprint, short_hash
from samplelaw.verify.coverage import CoverageResult
from samplelaw.verify.deviance import DevianceResult, deviance_alert_payload
from samplelaw.verify.replay import ReplayResult

def alerts_for_ledger(
    sampling: SamplingResult,
    replay: ReplayResult,
    coverage: CoverageResult,
    deviance: DevianceResult,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = [
        {
            "type": "SAMPLING",
            "code": item.code,
            "stratum": item.stratum,
            "message": item.message,
        }
        for item in sampling.warnings
    ]
    if replay.status != "MATCH":
        alerts.append(
            {
                "type": "REPLAY",
                "code": "REPLAY-002" if replay.status == "DIVERGED" else "REPLAY-001",
                "stratum": "",
                "message": replay.message,
            }
        )
    alerts.extend(
        {
            "type": "COVERAGE",
            "code": item.code,
            "stratum": item.stratum,
            "metric": item.metric,
            "threshold": item.threshold,
            "observed": item.observed,
            "message": item.message,
        }
        for item in coverage.alerts
    )
    alerts.extend(
        {
            "type": "DEVIANCE",
            **deviance_alert_payload(item),
        }
        for item in deviance.alerts
    )
    alerts.sort(key=lambda item: (str(item["type"]), str(item["code"]), str(item["stratum"])))
    return alerts

def build_report_document(
    *,
    plan: PlanSpec,
    source: Dataset,
    sampling: SamplingResult,
    replay: ReplayResult,
    coverage: CoverageResult,
    deviance: DevianceResult,
    generated_at: str | None,
    previous_batch_id: str | None,
    composition: Mapping[str, Any] | None = None,
) -> ReportDocument:
    timestamp = utc_timestamp(generated_at)
    source_fp = source.fingerprint()
    batch_fp = batch_fingerprint(plan.fingerprint, source_fp, sampling.sample_fingerprint)
    batch_id = f"batch-{short_hash(batch_fp, 16)}"
    coverage_by_label = {item.label: item for item in coverage.strata}
    allocation_by_label = {item.label: item for item in sampling.allocations}
    strata: list[ReportStratum] = []
    for label in sorted(set(coverage_by_label) | set(allocation_by_label)):
        cov = coverage_by_label.get(label)
        allocation = allocation_by_label.get(label)
        strata.append(
            ReportStratum(
                label=label,
                source_rows=cov.source_rows if cov else (allocation.source_rows if allocation else 0),
                source_share=cov.source_share if cov else 0.0,
                target_rows=allocation.target if allocation else 0,
                sample_rows=cov.sample_rows if cov else (allocation.actual if allocation else 0),
                sample_share=cov.sample_share if cov else 0.0,
                share_deviation=cov.share_deviation if cov else 0.0,
                rare=cov.rare if cov else False,
                empty=cov.empty if cov else False,
                covered=cov.covered if cov else False,
            )
        )
    alerts = alerts_for_ledger(sampling, replay, coverage, deviance)
    replay_dict = {
        "status": replay.status,
        "message": replay.message,
        "expected_rows": replay.expected_count,
        "actual_rows": replay.actual_count,
        "missing_rows": replay.missing_count,
        "extra_rows": replay.extra_count,
        "order_mismatch": replay.order_mismatch,
        "expected_fingerprint": replay.expected_fingerprint,
        "actual_fingerprint": replay.actual_fingerprint,
        "strata": [
            {
                "label": item.label,
                "expected_rows": item.expected,
                "actual_rows": item.actual,
                "missing_rows": item.missing,
                "extra_rows": item.extra,
            }
            for item in replay.stratum_diffs
        ],
    }
    coverage_dict = {
        "status": coverage.status,
        "coverage_rate": coverage.coverage_rate,
        "rare_total": coverage.rare_total,
        "rare_hit": coverage.rare_hit,
        "rare_missed": list(coverage.rare_missed),
        "max_abs_share_deviation": coverage.max_abs_share_deviation,
        "weighted_abs_share_deviation": coverage.weighted_abs_share_deviation,
        "max_share_threshold": coverage.max_share_threshold,
        "weighted_share_threshold": coverage.weighted_share_threshold,
        "alerts": [
            {
                "code": item.code,
                "stratum": item.stratum,
                "metric": item.metric,
                "threshold": item.threshold,
                "observed": item.observed,
                "message": item.message,
            }
            for item in coverage.alerts
        ],
    }
    deviance_dict = {
        "status": deviance.status,
        "baseline_batch_id": deviance.baseline_batch_id or previous_batch_id,
        "threshold": plan.thresholds.drift,
        "alerts": [deviance_alert_payload(item) for item in deviance.alerts],
        "drift_metrics": list(deviance.drift_metrics),
        "composition": composition,
    }
    return ReportDocument(
        generated_at=timestamp,
        batch_id=batch_id,
        overall_status=sampling_status(replay, coverage, deviance, bool(sampling.warnings)),
        plan_name=plan.name,
        plan_version=plan.version,
        plan_fingerprint=plan.fingerprint,
        source_name=source.name,
        source_kind=source.source_kind,
        source_fingerprint=source_fp,
        sample_fingerprint=sampling.sample_fingerprint,
        mode=plan.mode,
        seed=plan.seed,
        requested_total=sampling.requested_total,
        sample_total=sampling.actual_total,
        excluded_rows=sampling.excluded_rows,
        eligible_rows=sampling.eligible_rows,
        strata=tuple(strata),
        sampling_warnings=tuple(
            {"code": item.code, "stratum": item.stratum, "message": item.message} for item in sampling.warnings
        ),
        replay=replay_dict,
        coverage=coverage_dict,
        deviance=deviance_dict,
        alerts=tuple(alerts),
    )

def sampling_status(
    replay: ReplayResult,
    coverage: CoverageResult,
    deviance: DevianceResult,
    sampling_warnings: bool = False,
) -> str:
    if replay.status != "MATCH" or coverage.status == "FAIL":
        return "FAIL"
    if coverage.status != "PASS" or deviance.status == "WARN" or sampling_warnings:
        return "WARN"
    return "PASS"
