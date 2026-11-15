"""End-to-end pipeline orchestration for sampling, verification and reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from samplelaw.config.loader import load_plan
from samplelaw.config.pipeline_loader import load_pipeline
from samplelaw.config.schema import PlanSpec, SourceSpec
from samplelaw.ledger.sqlite_ledger import LedgerBatch, SQLiteLedger
from samplelaw.plan.sampling import SamplingResult, execute_sampling
from samplelaw.report.builder import build_report_document
from samplelaw.report.model import ReportDocument, utc_timestamp
from samplelaw.report.writer import write_reports
from samplelaw.source.csv_reader import read_csv_dataset, write_csv_rows
from samplelaw.source.dataset import Dataset
from samplelaw.source.sqlite_reader import read_sqlite_dataset
from samplelaw.util.errors import ConfigError
from samplelaw.util.hashing import batch_fingerprint, short_hash
from samplelaw.verify.coverage import CoverageResult, verify_coverage
from samplelaw.verify.deviance import (
    DevianceResult,
    IsolatedBaseline,
    build_composition,
    composition_from_json,
    detect_deviance,
)
from samplelaw.verify.replay import ReplayResult, verify_replay

@dataclass(frozen=True)
class JobResult:
    job_id: str
    plan: PlanSpec
    source: Dataset
    sample: Dataset
    sample_path: Path
    sampling: SamplingResult
    replay: ReplayResult
    coverage: CoverageResult
    deviance: DevianceResult
    document: ReportDocument
    report_paths: dict[str, Path]
    ledger_batch: LedgerBatch
    duplicate_batch: bool

@dataclass(frozen=True)
class PipelineRunResult:
    generated_at: str
    output_dir: Path
    ledger_path: Path
    jobs: tuple[JobResult, ...]
    @property
    def overall_status(self) -> str:
        if any(job.document.overall_status == "FAIL" for job in self.jobs):
            return "FAIL"
        if any(job.document.overall_status == "WARN" for job in self.jobs):
            return "WARN"
        return "PASS"

def load_source(spec: SourceSpec) -> Dataset:
    if spec.kind == "csv":
        return read_csv_dataset(spec.path, name=spec.name, encoding=spec.encoding)
    if spec.kind == "sqlite":
        return read_sqlite_dataset(spec.path, table=spec.table, name=spec.name)
    raise ConfigError("PIPE-004", f"unsupported source type: {spec.kind}")

def verify_sample(
    plan: PlanSpec,
    source: Dataset,
    sample: Dataset,
    *,
    generated_at: str | None = None,
) -> tuple[SamplingResult, ReplayResult, CoverageResult, DevianceResult, ReportDocument]:
    sampling = execute_sampling(source, plan)
    replay = verify_replay(plan, source, sample)
    coverage = verify_coverage(plan, source, sample)
    composition = build_composition(plan, sample)
    deviance = detect_deviance(plan, sampling.allocations, composition)
    document = build_report_document(
        plan=plan,
        source=source,
        sampling=sampling,
        replay=replay,
        coverage=coverage,
        deviance=deviance,
        generated_at=generated_at,
        previous_batch_id=None,
    )
    return sampling, replay, coverage, deviance, document

def _find_baseline(
    ledger: SQLiteLedger,
    plan: PlanSpec,
    candidate_id: str,
    *,
    allow_cross_version: bool,
) -> tuple[LedgerBatch | None, IsolatedBaseline | None, bool]:
    """Locate the baseline batch for deviance.

    Same-version baselines are always eligible. A batch from another
    fingerprint version is only used when it falls in the same semantic
    equivalence group *and* cross-version baselines are explicitly enabled;
    otherwise it is reported back as an isolated baseline (DEV-008).
    """
    previous = ledger.previous_for_plan(
        plan.fingerprint,
        fingerprint_version=plan.api_version,
        exclude_batch_id=candidate_id,
    )
    if previous is not None:
        return previous, None, False
    equivalent = ledger.previous_equivalent(
        plan.semantic_key,
        exclude_fingerprint_version=plan.api_version,
        exclude_batch_id=candidate_id,
    )
    if equivalent is None:
        return None, None, False
    if allow_cross_version:
        return equivalent, None, True
    return (
        None,
        IsolatedBaseline(
            batch_id=equivalent.batch_id,
            fingerprint_version=equivalent.fingerprint_version,
        ),
        False,
    )

def run_pipeline(
    config_path: str | Path,
    *,
    generated_at: str | None = None,
    allow_cross_version_baseline: bool = False,
) -> PipelineRunResult:
    pipeline = load_pipeline(config_path)
    allow_cross_version = allow_cross_version_baseline or pipeline.allow_cross_version_baseline
    timestamp = utc_timestamp(generated_at)
    pipeline.output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[JobResult] = []
    with SQLiteLedger(pipeline.ledger_path) as ledger:
        for job in pipeline.jobs:
            plan = load_plan(job.plan_path)
            source = load_source(job.source)
            sampling = execute_sampling(source, plan)
            sample_path = pipeline.output_dir / "samples" / f"{job.job_id}.csv"
            write_csv_rows(sample_path, source.columns, sampling.rows)
            persisted_sample = read_csv_dataset(sample_path, name=f"{job.job_id}-sample")
            replay = verify_replay(plan, source, persisted_sample)
            coverage = verify_coverage(plan, source, persisted_sample)
            composition = build_composition(plan, persisted_sample)
            candidate_fp = batch_fingerprint(plan.fingerprint, source.fingerprint(), sampling.sample_fingerprint)
            candidate_id = f"batch-{short_hash(candidate_fp, 16)}"
            ledger.backfill_semantic_key(plan.fingerprint, plan.semantic_key)
            previous, isolated_baseline, cross_version = _find_baseline(
                ledger,
                plan,
                candidate_id,
                allow_cross_version=allow_cross_version,
            )
            previous_composition = composition_from_json(previous.composition_json) if previous else None
            deviance = detect_deviance(
                plan,
                sampling.allocations,
                composition,
                previous_composition,
                previous.batch_id if previous else None,
                isolated_baseline=isolated_baseline,
                cross_version_baseline=cross_version,
            )
            document = build_report_document(
                plan=plan,
                source=source,
                sampling=sampling,
                replay=replay,
                coverage=coverage,
                deviance=deviance,
                generated_at=timestamp,
                previous_batch_id=previous.batch_id if previous else None,
            )
            report_paths = write_reports(
                document,
                pipeline.output_dir / "reports" / job.job_id,
                pipeline.report_formats,
            )
            ledger_batch, duplicate = ledger.record_batch(
                plan_fingerprint=plan.fingerprint,
                plan_name=plan.name,
                source_fingerprint=document.source_fingerprint,
                sample_fingerprint=document.sample_fingerprint,
                replay_status=replay.status,
                coverage_status=coverage.status,
                coverage_rate=coverage.coverage_rate,
                deviance_status=deviance.status,
                overall_status=document.overall_status,
                sample_count=document.sample_total,
                requested_count=document.requested_total,
                alerts=document.alerts,
                composition=composition,
                created_at=timestamp,
                fingerprint_version=plan.api_version,
                semantic_key=plan.semantic_key,
            )
            jobs.append(
                JobResult(
                    job_id=job.job_id,
                    plan=plan,
                    source=source,
                    sample=persisted_sample,
                    sample_path=sample_path,
                    sampling=sampling,
                    replay=replay,
                    coverage=coverage,
                    deviance=deviance,
                    document=document,
                    report_paths=report_paths,
                    ledger_batch=ledger_batch,
                    duplicate_batch=duplicate,
                )
            )
    return PipelineRunResult(
        generated_at=timestamp,
        output_dir=pipeline.output_dir,
        ledger_path=pipeline.ledger_path,
        jobs=tuple(jobs),
    )

def pipeline_summary(result: PipelineRunResult) -> dict[str, Any]:
    return {
        "generated_at": result.generated_at,
        "overall_status": result.overall_status,
        "output_dir": str(result.output_dir),
        "ledger": str(result.ledger_path),
        "jobs": [
            {
                "id": job.job_id,
                "plan": job.plan.name,
                "plan_fingerprint": job.plan.fingerprint,
                "batch_id": job.ledger_batch.batch_id,
                "overall_status": job.document.overall_status,
                "replay_status": job.replay.status,
                "coverage_status": job.coverage.status,
                "deviance_status": job.deviance.status,
                "sample_rows": job.document.sample_total,
                "duplicate_batch": job.duplicate_batch,
                "sample": str(job.sample_path),
                "reports": {fmt: str(path) for fmt, path in sorted(job.report_paths.items())},
            }
            for job in result.jobs
        ],
    }
