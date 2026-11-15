"""Duplicate-batch reruns must replay the ledger verdict, not a fresh one."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import yaml
import pytest

from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.runner import run_pipeline
from tests.helpers import plan_text

def _rows(prefix: str, tiers: list[tuple[str, str]], repeat: int = 3) -> list[dict[str, str]]:
    return [
        {"id": f"{prefix}-{index:02d}", "channel": channel, "tier": tier}
        for index, (channel, tier) in enumerate(tiers * repeat)
    ]

def _uniform(prefix: str, tier: str, size: int = 4) -> list[dict[str, str]]:
    return [
        {"id": f"{prefix}-{index:02d}", "channel": channel, "tier": tier}
        for index, channel in enumerate(["web", "store"] * (size // 2))
    ]

def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ["id", "channel", "tier"]
    lines = [",".join(columns)] + [",".join(row[column] for column in columns) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _pipeline(tmp_path: Path, plan_path: Path, sources: dict[str, list[dict[str, str]]]) -> Path:
    jobs = []
    for job_id, rows in sources.items():
        csv_path = tmp_path / f"{job_id}.csv"
        _write_csv(csv_path, rows)
        jobs.append(
            {
                "id": job_id,
                "plan": str(plan_path),
                "source": {"type": "csv", "path": str(csv_path), "name": job_id},
            }
        )
    config = {
        "apiVersion": "samplelaw/v1",
        "kind": "Pipeline",
        "metadata": {"name": "duplicate-pipeline"},
        "defaults": {
            "output_dir": str(tmp_path / "out"),
            "ledger": str(tmp_path / "out" / "ledger.sqlite"),
            "report_formats": ["json"],
        },
        "jobs": jobs,
    }
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path

def _plan(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(plan_text(keys=("channel",), dimensions=("tier",), **kwargs), encoding="utf-8")
    return path

def _ledger_rows(ledger_path: Path) -> dict[str, dict[str, object]]:
    with SQLiteLedger(ledger_path) as ledger:
        return {batch.batch_id: batch.to_dict() for batch in ledger.query_batches(limit=20)}

def _report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))

def _mutate_ledger(ledger_path: Path, sql: str, params: tuple[object, ...]) -> None:
    with closing(sqlite3.connect(str(ledger_path))) as connection:
        connection.execute(sql, params)
        connection.commit()

def _assert_report_matches_stored(report: dict[str, object], stored: dict[str, object]) -> None:
    deviance = report["deviance"]
    assert deviance["status"] == stored["deviance_status"]
    assert deviance["baseline_batch_id"] == stored["deviance"]["baseline_batch_id"]
    assert deviance["alerts"] == stored["deviance"]["alerts"]
    assert deviance["drift_metrics"] == stored["deviance"]["drift_metrics"]
    assert deviance["composition"] == stored["composition"]

def test_duplicate_ignores_newer_ledger_history(tmp_path: Path) -> None:
    """A duplicate replays its own row even if a newer batch is now the previous one."""
    plan = _plan(tmp_path, name="history-plan", per_stratum=2, max_per=None)
    source_a = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    source_b = _rows("b", [("web", "silver"), ("web", "gold"), ("store", "silver"), ("store", "gold")])
    config = _pipeline(tmp_path, plan, {"job-a": source_a, "job-b": source_b})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    job_a, job_b = first.jobs
    assert job_a.deviance.status == "BASELINE"
    assert job_b.deviance.status == "PASS"

    # Only job-a is submitted again. A naive rerun would pick job-b (the newest
    # same-plan batch) as its baseline and turn BASELINE into PASS.
    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    repeated = second.jobs[0]
    assert repeated.duplicate_batch is True
    assert repeated.ledger_batch.batch_id == job_a.ledger_batch.batch_id
    assert repeated.deviance.status == "BASELINE"
    assert repeated.deviance.baseline_batch_id is None
    report = _report(repeated.report_paths["json"])
    assert report["deviance"]["status"] == "BASELINE"
    assert report["deviance"]["baseline_batch_id"] is None
    assert report["deviance"]["drift_metrics"] == []
    # job-b is also a duplicate and keeps job-a as its baseline.
    assert second.jobs[1].deviance.status == "PASS"
    assert second.jobs[1].deviance.baseline_batch_id == job_a.ledger_batch.batch_id

def test_duplicate_rerun_report_deviance_matches_ledger(tmp_path: Path) -> None:
    """The same input twice: the second report must show the first run's verdict."""
    plan = _plan(tmp_path, name="shared-plan", per_stratum=2, max_per=None)
    source_rows = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    config = _pipeline(tmp_path, plan, {"job-a": source_rows})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    assert first.jobs[0].deviance.status == "BASELINE"
    assert first.jobs[0].document.deviance["status"] == "BASELINE"
    assert first.jobs[0].document.deviance["baseline_batch_id"] is None

    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    job = second.jobs[0]
    assert job.duplicate_batch is True
    assert job.ledger_batch.batch_id == first.jobs[0].ledger_batch.batch_id
    stored = _ledger_rows(second.ledger_path)[job.ledger_batch.batch_id]
    assert stored["created_at"] == "2026-11-14T00:00:00Z"
    _assert_report_matches_stored(_report(job.report_paths["json"]), stored)
    assert job.document.deviance["status"] == stored["deviance_status"]
    assert job.document.deviance["baseline_batch_id"] is None
    assert job.document.deviance["drift_metrics"] == []

def test_duplicate_rerun_reuses_composition_snapshot(tmp_path: Path) -> None:
    """A duplicate batch presents the row's composition snapshot, not this run's."""
    plan = _plan(tmp_path, name="snapshot-plan", per_stratum=2, max_per=None)
    source_rows = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    config = _pipeline(tmp_path, plan, {"job-a": source_rows})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    batch_id = first.jobs[0].ledger_batch.batch_id
    assert first.jobs[0].document.deviance["composition"]["strata"]["web"]["sample_rows"] == 2

    # Replace the persisted snapshot for this batch identity, emulating an
    # earlier row whose recorded composition differs from today's recomputation.
    replacement = {
        "strata": {
            "web": {"sample_rows": 2, "dimensions": {"tier": {"bronze": 1, "gold": 1}}},
            "store": {"sample_rows": 2, "dimensions": {"tier": {"bronze": 1, "silver": 1}}},
        }
    }
    _mutate_ledger(
        first.ledger_path,
        "UPDATE batches SET composition_json = ? WHERE batch_id = ?",
        (json.dumps(replacement, sort_keys=True), batch_id),
    )

    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    job = second.jobs[0]
    assert job.duplicate_batch is True
    report = _report(job.report_paths["json"])
    assert report["deviance"]["composition"] == replacement
    stored = _ledger_rows(second.ledger_path)[batch_id]
    assert stored["composition"] == replacement
    _assert_report_matches_stored(report, stored)

def test_non_duplicate_batch_still_uses_previous_as_baseline(tmp_path: Path) -> None:
    """A new batch for the same plan compares against the most recent batch."""
    plan = _plan(tmp_path, name="chain-plan", per_stratum=2, max_per=None)
    source_a = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    source_b = _rows("b", [("web", "silver"), ("web", "gold"), ("store", "silver"), ("store", "gold")])
    config = _pipeline(tmp_path, plan, {"job-a": source_a, "job-b": source_b})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    job_a, job_b = first.jobs
    assert job_a.duplicate_batch is False and job_a.deviance.status == "BASELINE"
    assert job_b.duplicate_batch is False
    assert job_b.deviance.status == "PASS"
    assert job_b.deviance.baseline_batch_id == job_a.ledger_batch.batch_id
    assert job_b.document.deviance["baseline_batch_id"] == job_a.ledger_batch.batch_id
    assert job_b.document.deviance["drift_metrics"]

    # A rerun keeps job-a as BASELINE and job-b on job-a as its baseline.
    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    assert all(job.duplicate_batch for job in second.jobs)
    assert second.jobs[0].deviance.status == "BASELINE"
    assert second.jobs[0].document.deviance["baseline_batch_id"] is None
    assert second.jobs[1].deviance.status == "PASS"
    assert second.jobs[1].deviance.baseline_batch_id == job_a.ledger_batch.batch_id
    assert second.jobs[1].document.deviance["baseline_batch_id"] == job_a.ledger_batch.batch_id

def test_duplicate_with_historical_drift_alerts_replays_stored_alerts(tmp_path: Path) -> None:
    """Drift alerts recorded for the batch are replayed verbatim on a duplicate."""
    plan = _plan(tmp_path, name="warn-plan", per_stratum=2, max_per=None, drift=0.05)
    source_a = _uniform("a", "gold")
    source_b = _uniform("b", "silver")
    config = _pipeline(tmp_path, plan, {"job-a": source_a, "job-b": source_b})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    job_b = first.jobs[1]
    assert job_b.deviance.status == "WARN"
    assert {item.code for item in job_b.deviance.alerts} == {"DEV-003"}
    stored_before = _ledger_rows(first.ledger_path)[job_b.ledger_batch.batch_id]
    stored_alerts = stored_before["alerts"]
    stored_deviance = stored_before["deviance"]
    assert stored_deviance["status"] == "WARN"
    assert stored_deviance["baseline_batch_id"] == first.jobs[0].ledger_batch.batch_id
    assert {item["code"] for item in stored_deviance["alerts"]} == {"DEV-003"}
    assert len(stored_deviance["drift_metrics"]) == 2
    assert any(item["type"] == "DEVIANCE" and item["code"] == "DEV-003" for item in stored_alerts)

    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    job = second.jobs[1]
    assert job.duplicate_batch is True
    assert job.ledger_batch.created_at == "2026-11-14T00:00:00Z"
    report = _report(job.report_paths["json"])
    _assert_report_matches_stored(report, stored_before)
    assert report["deviance"]["status"] == "WARN"
    assert len(report["deviance"]["alerts"]) == len(stored_deviance["alerts"]) == 2
    # The ledger row is untouched: same created_at and full alert list.
    stored_after = _ledger_rows(second.ledger_path)[job.ledger_batch.batch_id]
    assert stored_after["alerts"] == stored_alerts
    assert list(report["alerts"]) == list(first.jobs[1].document.alerts)

def test_concurrent_insert_still_renders_stored_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the row appears between our lookup and our write, re-render from the row."""
    plan = _plan(tmp_path, name="race-plan", per_stratum=2, max_per=None)
    source_rows = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    config = _pipeline(tmp_path, plan, {"job-a": source_rows})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    batch_id = first.jobs[0].ledger_batch.batch_id

    # Emulate a row whose recorded verdict differs from what this run recomputes.
    stored_verdict = {
        "status": "WARN",
        "baseline_batch_id": "batch-historical",
        "alerts": [
            {
                "code": "DEV-003",
                "kind": "COMPOSITION_DRIFT",
                "stratum": "web",
                "dimension": "tier",
                "metric": "total_variation",
                "threshold": 0.2,
                "observed": 0.9,
                "message": "within-stratum dimension distribution moved beyond threshold",
            }
        ],
        "drift_metrics": [
            {"stratum": "web", "dimension": "tier", "metric": "total_variation", "threshold": 0.2, "observed": 0.9}
        ],
    }
    _mutate_ledger(
        first.ledger_path,
        "UPDATE batches SET deviance_status = ?, deviance_json = ? WHERE batch_id = ?",
        ("WARN", json.dumps(stored_verdict, sort_keys=True), batch_id),
    )

    original_get_batch = SQLiteLedger.get_batch
    calls = {"count": 0}

    def hide_first_lookup(self: SQLiteLedger, looked_up_id: str):
        # Hide the existing row from the runner's pre-check only; the insert then
        # loses the race inside record_batch and returns the stored row.
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_get_batch(self, looked_up_id)

    monkeypatch.setattr(SQLiteLedger, "get_batch", hide_first_lookup)
    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")

    job = second.jobs[0]
    assert job.duplicate_batch is True
    report = _report(job.report_paths["json"])
    assert report["deviance"]["status"] == "WARN"
    assert report["deviance"]["baseline_batch_id"] == "batch-historical"
    assert report["deviance"]["alerts"] == stored_verdict["alerts"]
    assert report["deviance"]["drift_metrics"] == stored_verdict["drift_metrics"]
    assert job.deviance.status == "WARN"
    assert first.jobs[0].deviance.status == "BASELINE"

def test_legacy_ledger_row_without_deviance_payload_is_replayed(tmp_path: Path) -> None:
    """Rows written before deviance payloads existed still replay status and alerts."""
    plan = _plan(tmp_path, name="legacy-plan", per_stratum=2, max_per=None)
    source_rows = _rows("a", [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")])
    config = _pipeline(tmp_path, plan, {"job-a": source_rows})
    first = run_pipeline(config, generated_at="2026-11-14T00:00:00Z")
    batch_id = first.jobs[0].ledger_batch.batch_id
    _mutate_ledger(
        first.ledger_path,
        "UPDATE batches SET deviance_json = '' WHERE batch_id = ?",
        (batch_id,),
    )

    second = run_pipeline(config, generated_at="2026-11-14T01:00:00Z")
    job = second.jobs[0]
    assert job.duplicate_batch is True
    report = _report(job.report_paths["json"])
    stored = _ledger_rows(second.ledger_path)[batch_id]
    assert report["deviance"]["status"] == stored["deviance_status"] == "BASELINE"
    assert report["deviance"]["baseline_batch_id"] is None
    assert report["deviance"]["alerts"] == []
    assert report["deviance"]["drift_metrics"] == []
    # The composition snapshot came from the row, not this run's recomputation.
    assert report["deviance"]["composition"] == stored["composition"]
