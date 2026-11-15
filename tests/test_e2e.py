from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.runner import run_pipeline
from samplelaw.source.csv_reader import read_csv_dataset
from tests.helpers import EXAMPLES, GOLD

def _blocked(*args: object, **kwargs: object) -> None:
    raise AssertionError("network access is forbidden in samplelaw")

def test_full_pipeline_offline_and_auditable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    examples = tmp_path / "examples"
    shutil.copytree(EXAMPLES, examples)
    result = run_pipeline(examples / "pipeline.yaml", generated_at="2026-11-14T00:00:00Z")
    assert result.overall_status == "WARN"
    assert len(result.jobs) == 3
    assert result.output_dir == tmp_path / "out"
    for job in result.jobs:
        assert job.replay.status == "MATCH"
        assert job.sample_path.is_file()
        assert set(job.report_paths) == {"markdown", "json", "html"}
        for path in job.report_paths.values():
            assert path.is_file() and path.stat().st_size > 0
    members = read_csv_dataset(result.output_dir / "samples" / "members-fixed.csv")
    gold = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    assert members.rows == gold.rows
    report = json.loads((result.output_dir / "reports" / "events-guarded" / "report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "samplelaw.report/v1"
    assert report["replay"]["status"] == "MATCH"
    assert report["plan"]["fingerprint"] == result.jobs[2].plan.fingerprint
    assert any(
        item["stratum"] == "south | archive" and item["code"] == "COV-002" for item in report["coverage"]["alerts"]
    )
    html = (result.output_dir / "reports" / "members-fixed" / "report.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html
    with SQLiteLedger(result.ledger_path) as ledger:
        batches = ledger.query_batches(limit=10)
        assert len(batches) == 3
        assert {batch.sample_count for batch in batches} == {16, 30}

def test_pipeline_rerun_is_duplicate_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _blocked)
    examples = tmp_path / "examples"
    shutil.copytree(EXAMPLES, examples)
    first = run_pipeline(examples / "pipeline.yaml", generated_at="2026-11-14T00:00:00Z")
    second = run_pipeline(examples / "pipeline.yaml", generated_at="2026-11-14T01:00:00Z")
    assert all(job.duplicate_batch for job in second.jobs)
    assert [job.ledger_batch.batch_id for job in first.jobs] == [job.ledger_batch.batch_id for job in second.jobs]
    with SQLiteLedger(second.ledger_path) as ledger:
        assert len(ledger.query_batches(limit=10)) == 3
