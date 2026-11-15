"""End-to-end pipeline orchestration for sampling, verification and reports.

A pipeline run is one business unit with an all-or-nothing outcome:

1. **Stage phase** — every job samples its source, writes the sample CSV to a
   job- and batch-scoped temporary file, reads that file back and verifies the
   persisted fingerprint against the in-memory sampling result. No official
   sample path, ledger row or report is touched in this phase.
2. **Commit phase** — only after every job has been staged and verified, the
   ledger rows are inserted inside one SQLite transaction and the staged CSVs
   are atomically swapped onto their official paths. Any failure here rolls
   the ledger transaction back and restores the previous sample files.
3. **Report phase** — reports are rendered only for a fully committed run.

A failure therefore never leaves an intermediate state with samples but no
ledger rows, ledger rows but no samples, or half-written ``.csv`` files.
"""

from __future__ import annotations

import glob
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
from samplelaw.source.csv_reader import (
    commit_staged_csv,
    read_csv_dataset,
    staging_csv_path,
    write_staged_csv,
)
from samplelaw.source.dataset import Dataset
from samplelaw.source.sqlite_reader import read_sqlite_dataset
from samplelaw.util.errors import ConfigError, SamplingError
from samplelaw.util.hashing import batch_fingerprint, rows_fingerprint, short_hash
from samplelaw.verify.coverage import CoverageResult, verify_coverage
from samplelaw.verify.deviance import (
    DevianceResult,
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

@dataclass(frozen=True)
class StagedJob:
    """Everything a job produced before the run-wide commit point."""
    job_id: str
    plan: PlanSpec
    source: Dataset
    sample: Dataset
    sample_path: Path
    staging_path: Path
    sampling: SamplingResult
    replay: ReplayResult
    coverage: CoverageResult
    deviance: DevianceResult
    document: ReportDocument

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

def _fingerprint_columns(plan: PlanSpec, source: Dataset) -> tuple[str, ...]:
    return plan.fingerprint_columns or source.columns

def verify_persisted_sample(
    plan: PlanSpec,
    source: Dataset,
    persisted_sample: Dataset,
    sampling: SamplingResult,
    *,
    staging_path: Path,
) -> None:
    """Recompute the sample fingerprint from the bytes that were persisted.

    The plan-declared fingerprint column order is used deliberately, not the
    on-disk CSV header order: a column reordering or value rewrite on disk must
    be detected even when the header set is unchanged.
    """
    columns = _fingerprint_columns(plan, source)
    persisted_sample.require_columns(columns, role="sample")
    persisted_fp = rows_fingerprint(
        persisted_sample.rows,
        columns,
        order_sensitive=plan.order_sensitive,
    )
    if persisted_fp != sampling.sample_fingerprint:
        raise SamplingError(
            "SAMP-004",
            (
                "persisted sample fingerprint does not match the in-memory "
                f"sampling result: expected {sampling.sample_fingerprint}, "
                f"read back {persisted_fp}"
            ),
            path=str(staging_path),
        )

def _cleanup_staging(samples_dir: Path) -> None:
    """Remove staging/rollback files left behind by an earlier interrupted run."""
    for pattern in ("*.tmp", "*.rollback"):
        for stale in glob.glob(str(samples_dir / pattern)):
            Path(stale).unlink(missing_ok=True)

def _stage_job(
    job: Any,
    *,
    samples_dir: Path,
    timestamp: str,
    ledger: SQLiteLedger,
) -> StagedJob:
    plan = load_plan(job.plan_path)
    source = load_source(job.source)
    sampling = execute_sampling(source, plan)
    sample_path = samples_dir / f"{job.job_id}.csv"
    staging_path = staging_csv_path(sample_path)
    write_staged_csv(staging_path, source.columns, sampling.rows)
    try:
        persisted_sample = read_csv_dataset(staging_path, name=f"{job.job_id}-sample")
        verify_persisted_sample(
            plan,
            source,
            persisted_sample,
            sampling,
            staging_path=staging_path,
        )
        replay = verify_replay(plan, source, persisted_sample)
        coverage = verify_coverage(plan, source, persisted_sample)
        composition = build_composition(plan, persisted_sample)
        candidate_fp = batch_fingerprint(
            plan.fingerprint, source.fingerprint(), sampling.sample_fingerprint
        )
        candidate_id = f"batch-{short_hash(candidate_fp, 16)}"
        previous = ledger.previous_for_plan(plan.fingerprint, exclude_batch_id=candidate_id)
        previous_composition = composition_from_json(previous.composition_json) if previous else None
        deviance = detect_deviance(
            plan,
            sampling.allocations,
            composition,
            previous_composition,
            previous.batch_id if previous else None,
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
    except BaseException:
        staging_path.unlink(missing_ok=True)
        raise
    return StagedJob(
        job_id=job.job_id,
        plan=plan,
        source=source,
        sample=persisted_sample,
        sample_path=sample_path,
        staging_path=staging_path,
        sampling=sampling,
        replay=replay,
        coverage=coverage,
        deviance=deviance,
        document=document,
    )

def _record_staged_job(ledger: SQLiteLedger, staged: StagedJob, timestamp: str) -> tuple[LedgerBatch, bool]:
    document = staged.document
    composition = build_composition(staged.plan, staged.sample)
    return ledger.record_batch(
        plan_fingerprint=staged.plan.fingerprint,
        plan_name=staged.plan.name,
        source_fingerprint=document.source_fingerprint,
        sample_fingerprint=document.sample_fingerprint,
        replay_status=staged.replay.status,
        coverage_status=staged.coverage.status,
        coverage_rate=staged.coverage.coverage_rate,
        deviance_status=staged.deviance.status,
        overall_status=document.overall_status,
        sample_count=document.sample_total,
        requested_count=document.requested_total,
        alerts=document.alerts,
        composition=composition,
        created_at=timestamp,
    )

def _commit_samples(staged_jobs: tuple[StagedJob, ...]) -> None:
    """Swap every staged CSV onto its official path, rolling back on failure."""
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for staged in staged_jobs:
            if staged.sample_path.exists():
                backup = staged.sample_path.with_name(f"{staged.sample_path.name}.rollback")
                staged.sample_path.replace(backup)
                backups[staged.sample_path] = backup
            commit_staged_csv(staged.staging_path, staged.sample_path)
            replaced.append(staged.sample_path)
    except BaseException:
        for target in reversed(replaced):
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            backup.replace(target)
        for staged in staged_jobs:
            staged.staging_path.unlink(missing_ok=True)
        raise
    for backup in backups.values():
        backup.unlink(missing_ok=True)

def run_pipeline(config_path: str | Path, *, generated_at: str | None = None) -> PipelineRunResult:
    pipeline = load_pipeline(config_path)
    timestamp = utc_timestamp(generated_at)
    samples_dir = pipeline.output_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_staging(samples_dir)
    staged_jobs: list[StagedJob] = []
    with SQLiteLedger(pipeline.ledger_path) as ledger:
        try:
            # Phase 1: every job is staged and verified before anything becomes visible.
            for job in pipeline.jobs:
                staged_jobs.append(
                    _stage_job(job, samples_dir=samples_dir, timestamp=timestamp, ledger=ledger)
                )
            # Phase 2: one ledger transaction plus atomic sample-file replacement.
            recorded: list[tuple[LedgerBatch, bool]] = []
            with ledger.transaction():
                for staged in staged_jobs:
                    recorded.append(_record_staged_job(ledger, staged, timestamp))
            _commit_samples(tuple(staged_jobs))
        except BaseException:
            for staged in staged_jobs:
                staged.staging_path.unlink(missing_ok=True)
            _cleanup_staging(samples_dir)
            raise
    # Phase 3: reports exist only for a fully committed run.
    jobs: list[JobResult] = []
    for staged, (ledger_batch, duplicate) in zip(staged_jobs, recorded):
        report_paths = write_reports(
            staged.document,
            pipeline.output_dir / "reports" / staged.job_id,
            pipeline.report_formats,
        )
        jobs.append(
            JobResult(
                job_id=staged.job_id,
                plan=staged.plan,
                source=staged.source,
                sample=staged.sample,
                sample_path=staged.sample_path,
                sampling=staged.sampling,
                replay=staged.replay,
                coverage=staged.coverage,
                deviance=staged.deviance,
                document=staged.document,
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
