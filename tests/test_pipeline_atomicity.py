"""Atomicity tests: a pipeline run either fully commits or leaves no trace."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from samplelaw import runner as runner_module
from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.plan.sampling import execute_sampling
from samplelaw.runner import run_pipeline
from samplelaw.source import csv_reader as csv_reader_module
from samplelaw.source.csv_reader import (
    read_csv_dataset,
    write_staged_csv,
)
from samplelaw.util.errors import SamplingError
from tests.helpers import EXAMPLES

JOB_IDS = ("members-fixed", "orders-proportional", "events-guarded")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    shutil.copytree(EXAMPLES, root / "examples")
    return root


def _pipeline_config(workspace: Path) -> Path:
    return workspace / "examples" / "pipeline.yaml"


def _samples_dir(workspace: Path) -> Path:
    return workspace / "out" / "samples"


def _ledger_path(workspace: Path) -> Path:
    return workspace / "out" / "ledger.sqlite"


def _run_ok(workspace: Path) -> object:
    return run_pipeline(_pipeline_config(workspace), generated_at="2026-11-14T00:00:00Z")


def _no_csv_half_products(workspace: Path) -> None:
    samples = _samples_dir(workspace)
    leftovers = [
        path
        for path in samples.rglob("*")
        if path.is_file()
        and (path.name.endswith(".tmp") or path.name.endswith(".rollback"))
    ]
    assert leftovers == []


def test_stale_staging_and_rollback_files_are_swept(workspace: Path) -> None:
    samples = _samples_dir(workspace)
    samples.mkdir(parents=True)
    (samples / "members-fixed.csv.4242.tmp").write_text("stale", encoding="utf-8")
    (samples / "orders-proportional.csv.rollback").write_text("stale", encoding="utf-8")

    _run_ok(workspace)

    _no_csv_half_products(workspace)
    assert (samples / "members-fixed.csv").is_file()


def _ledger_batch_ids(workspace: Path) -> set[str]:
    if not _ledger_path(workspace).exists():
        return set()
    with SQLiteLedger(_ledger_path(workspace)) as ledger:
        return {batch.batch_id for batch in ledger.query_batches(limit=50)}


def _ledger_row_count(workspace: Path) -> int:
    if not _ledger_path(workspace).exists():
        return 0
    with SQLiteLedger(_ledger_path(workspace)) as ledger:
        return len(ledger.query_batches(limit=50))


def test_successful_run_commits_all_samples_reports_and_ledger_rows(workspace: Path) -> None:
    result = _run_ok(workspace)
    assert len(result.jobs) == 3
    for job in result.jobs:
        assert job.sample_path.is_file()
        for report_path in job.report_paths.values():
            assert report_path.is_file()
    assert len(_ledger_batch_ids(workspace)) == 3
    _no_csv_half_products(workspace)


def test_stage_failure_leaves_no_samples_no_ledger_and_no_half_products(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_execute = execute_sampling
    failing_job = "orders-proportional"

    def flaky_execute(dataset, plan):  # type: ignore[no-untyped-def]
        result = real_execute(dataset, plan)
        if plan.name == "demo-orders-proportional":
            raise SamplingError("SAMP-001", "injected stage failure", path=failing_job)
        return result

    monkeypatch.setattr(runner_module, "execute_sampling", flaky_execute)
    with pytest.raises(SamplingError) as captured:
        _run_ok(workspace)
    assert captured.value.code == "SAMP-001"

    # No official samples and no ledger rows: the run never reached the commit phase.
    assert not _samples_dir(workspace).exists() or not any(_samples_dir(workspace).glob("*.csv"))
    assert _ledger_row_count(workspace) == 0
    _no_csv_half_products(workspace)
    # Reports must not exist for a run that never committed.
    assert not (workspace / "out" / "reports").exists()


def test_stage_failure_keeps_previous_committed_samples_intact(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _run_ok(workspace)
    expected_rows = {
        job.job_id: read_csv_dataset(job.sample_path).rows for job in first.jobs
    }
    expected_ids = _ledger_batch_ids(workspace)

    real_execute = execute_sampling

    def flaky_execute(dataset, plan):  # type: ignore[no-untyped-def]
        result = real_execute(dataset, plan)
        if plan.name == "demo-orders-proportional":
            raise SamplingError("SAMP-001", "injected rerun failure", path="orders-proportional")
        return result

    monkeypatch.setattr(runner_module, "execute_sampling", flaky_execute)
    with pytest.raises(SamplingError):
        _run_ok(workspace)

    # The previously committed samples and ledger rows survive untouched.
    for job_id in JOB_IDS:
        sample_path = _samples_dir(workspace) / f"{job_id}.csv"
        assert read_csv_dataset(sample_path).rows == expected_rows[job_id]
    assert _ledger_batch_ids(workspace) == expected_ids
    _no_csv_half_products(workspace)


def test_rerun_after_failure_reproduces_clean_run_batch_ids(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _run_ok(workspace)
    clean_ids = [job.ledger_batch.batch_id for job in clean.jobs]

    # Wipe the outputs and force the first attempt to fail mid-stage.
    shutil.rmtree(workspace / "out")
    real_execute = execute_sampling
    attempts = {"count": 0}

    def fail_once(dataset, plan):  # type: ignore[no-untyped-def]
        result = real_execute(dataset, plan)
        if plan.name == "demo-orders-proportional":
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise SamplingError("SAMP-001", "injected first-attempt failure")
        return result

    monkeypatch.setattr(runner_module, "execute_sampling", fail_once)
    with pytest.raises(SamplingError):
        _run_ok(workspace)
    assert _ledger_row_count(workspace) == 0
    _no_csv_half_products(workspace)

    recovered = _run_ok(workspace)
    recovered_ids = [job.ledger_batch.batch_id for job in recovered.jobs]
    assert recovered_ids == clean_ids


def test_persisted_fingerprint_mismatch_aborts_with_samp_004(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tamper_after_write(staging_path, columns, rows):  # type: ignore[no-untyped-def]
        written = write_staged_csv(staging_path, columns, rows)
        if Path(staging_path).name.startswith("events-guarded.csv."):
            text = Path(staging_path).read_text(encoding="utf-8")
            lines = text.split("\n")
            # Rewrite the first data row's event id: the row set changes but
            # the header (column order) stays exactly the same.
            for index, line in enumerate(lines):
                if line and index == 1:
                    fields = line.split(",")
                    fields[0] = f"tampered-{index}"
                    lines[index] = ",".join(fields)
                    break
            Path(staging_path).write_text("\n".join(lines), encoding="utf-8")
        return written

    monkeypatch.setattr(runner_module, "write_staged_csv", tamper_after_write)
    with pytest.raises(SamplingError) as captured:
        _run_ok(workspace)
    assert captured.value.code == "SAMP-004"
    assert _ledger_row_count(workspace) == 0
    assert not any(_samples_dir(workspace).glob("*.csv"))
    _no_csv_half_products(workspace)


def test_ledger_commit_failure_rolls_back_rows_and_samples(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_record = SQLiteLedger.record_batch
    calls = {"count": 0}

    def fail_on_second_batch(self, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 2:
            raise sqlite3.OperationalError("injected ledger failure")
        return real_record(self, **kwargs)

    monkeypatch.setattr(SQLiteLedger, "record_batch", fail_on_second_batch)
    with pytest.raises(sqlite3.OperationalError):
        _run_ok(workspace)

    # The first INSERT lived in the same transaction and must have rolled back.
    with SQLiteLedger(_ledger_path(workspace)) as ledger:
        assert ledger.query_batches(limit=50) == []
    # No official sample was swapped in during the aborted commit.
    assert not any(_samples_dir(workspace).glob("*.csv"))
    _no_csv_half_products(workspace)


def test_sample_swap_failure_restores_backups_and_rolls_back_ledger(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a clean run so official samples exist and must be restored on failure.
    first = _run_ok(workspace)
    original_bytes = {
        path: path.read_bytes() for path in _samples_dir(workspace).glob("*.csv")
    }
    original_ids = _ledger_batch_ids(workspace)

    real_commit = csv_reader_module.commit_staged_csv
    swap_calls = {"count": 0}

    def fail_second_swap(staging_path, final_path):  # type: ignore[no-untyped-def]
        swap_calls["count"] += 1
        if swap_calls["count"] == 2:
            raise OSError("injected swap failure")
        return real_commit(staging_path, final_path)

    monkeypatch.setattr(runner_module, "commit_staged_csv", fail_second_swap)
    with pytest.raises(OSError):
        _run_ok(workspace)

    # Ledger: no new rows beyond the original run (duplicate-safe rerun semantics).
    assert _ledger_batch_ids(workspace) == original_ids
    # Official samples are byte-identical to the pre-failure run.
    for path, payload in original_bytes.items():
        assert path.read_bytes() == payload
    _no_csv_half_products(workspace)
    # And a subsequent clean rerun succeeds with stable batch ids.
    recovered = _run_ok(workspace)
    assert [job.ledger_batch.batch_id for job in recovered.jobs] == [
        job.ledger_batch.batch_id for job in first.jobs
    ]
