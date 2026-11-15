from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.util.errors import LedgerError
from samplelaw.util.hashing import batch_fingerprint

def _record(
    ledger: SQLiteLedger,
    *,
    plan_fp: str = "plan-demo",
    source_fp: str = "source-demo",
    sample_fp: str = "sample-demo",
    created_at: str = "2026-11-14T00:00:00Z",
):
    return ledger.record_batch(
        plan_fingerprint=plan_fp,
        plan_name="demo-plan",
        source_fingerprint=source_fp,
        sample_fingerprint=sample_fp,
        replay_status="MATCH",
        coverage_status="PASS",
        coverage_rate=1.0,
        deviance_status="BASELINE",
        overall_status="PASS",
        sample_count=10,
        requested_count=10,
        alerts=[{"type": "COVERAGE", "code": "COV-000", "message": "demo"}],
        composition={"strata": {"a": {"sample_rows": 10, "dimensions": {}}}},
        deviance={
            "status": "BASELINE",
            "baseline_batch_id": None,
            "alerts": [],
            "drift_metrics": [],
        },
        created_at=created_at,
    )

def test_ledger_write_query_and_duplicate(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.sqlite"
    with SQLiteLedger(ledger_path) as ledger:
        batch, duplicate = _record(ledger)
        assert duplicate is False
        assert batch.batch_id.startswith("batch-")
        assert ledger.get_batch(batch.batch_id) == batch
        again, duplicate = _record(ledger)
        assert duplicate is True
        assert again.batch_id == batch.batch_id
        assert len(ledger.query_batches(limit=10)) == 1
        assert batch.to_dict()["alerts"][0]["code"] == "COV-000"
        assert batch.to_dict()["deviance"]["status"] == "BASELINE"
        assert batch.to_dict()["composition"]["strata"]["a"]["sample_rows"] == 10

def test_previous_batch_and_plan_filter(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        first, _ = _record(ledger, sample_fp="sample-one", created_at="2026-11-14T00:00:00Z")
        second, _ = _record(ledger, sample_fp="sample-two", created_at="2026-11-14T01:00:00Z")
        other, _ = _record(
            ledger,
            plan_fp="plan-other",
            source_fp="source-other",
            sample_fp="sample-other",
            created_at="2026-11-14T02:00:00Z",
        )
        previous = ledger.previous_for_plan("plan-demo", exclude_batch_id=second.batch_id)
        assert previous is not None and previous.batch_id == first.batch_id
        filtered = ledger.query_batches(plan_fingerprint="plan-demo", limit=10)
        assert {item.batch_id for item in filtered} == {first.batch_id, second.batch_id}
        assert other.batch_id not in {item.batch_id for item in filtered}
        assert ledger.get_batch("missing-batch") is None

def test_legacy_ledger_without_deviance_column_migrates(tmp_path: Path) -> None:
    ledger_path = tmp_path / "legacy.sqlite"
    fingerprint = batch_fingerprint("plan-demo", "source-demo", "sample-demo")
    batch_id = f"batch-{fingerprint[:16]}"
    with sqlite3.connect(str(ledger_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY,
                batch_fingerprint TEXT NOT NULL UNIQUE,
                plan_fingerprint TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                sample_fingerprint TEXT NOT NULL,
                replay_status TEXT NOT NULL,
                coverage_status TEXT NOT NULL,
                coverage_rate REAL NOT NULL,
                deviance_status TEXT NOT NULL,
                overall_status TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                requested_count INTEGER NOT NULL,
                alerts_json TEXT NOT NULL,
                composition_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO batches (
                batch_id, batch_fingerprint, plan_fingerprint, plan_name,
                source_fingerprint, sample_fingerprint, replay_status, coverage_status,
                coverage_rate, deviance_status, overall_status, sample_count,
                requested_count, alerts_json, composition_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                fingerprint,
                "plan-demo",
                "demo-plan",
                "source-demo",
                "sample-demo",
                "MATCH",
                "PASS",
                1.0,
                "BASELINE",
                "PASS",
                10,
                10,
                "[]",
                '{"strata":{}}',
                "2026-11-14T00:00:00Z",
            ),
        )
        connection.commit()
    with SQLiteLedger(ledger_path) as ledger:
        batch = ledger.get_batch(batch_id)
        assert batch is not None
        assert batch.deviance_status == "BASELINE"
        assert batch.deviance_json == ""
        assert batch.to_dict()["deviance"] is None
        # A legacy duplicate is still recognised after the migration.
        again, duplicate = _record(ledger)
        assert duplicate is True
        assert again.batch_id == batch_id
        assert again.deviance_json == ""

def test_invalid_query_limit(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        with pytest.raises(LedgerError) as captured:
            ledger.query_batches(limit=0)
        assert captured.value.code == "LEDGER-005"
