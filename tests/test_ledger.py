from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from samplelaw.ledger.sqlite_ledger import SQLiteLedger
from samplelaw.util.errors import LedgerError

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

def test_invalid_query_limit(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        with pytest.raises(LedgerError) as captured:
            ledger.query_batches(limit=0)
        assert captured.value.code == "LEDGER-005"

def test_transaction_commits_all_rows_together(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        with ledger.transaction():
            ledger.record_batch(
                plan_fingerprint="plan-a",
                plan_name="demo-a",
                source_fingerprint="src-a",
                sample_fingerprint="sample-a",
                replay_status="MATCH",
                coverage_status="PASS",
                coverage_rate=1.0,
                deviance_status="BASELINE",
                overall_status="PASS",
                sample_count=1,
                requested_count=1,
                alerts=(),
                composition=None,
                created_at="2026-11-14T00:00:00Z",
            )
            ledger.record_batch(
                plan_fingerprint="plan-b",
                plan_name="demo-b",
                source_fingerprint="src-b",
                sample_fingerprint="sample-b",
                replay_status="MATCH",
                coverage_status="PASS",
                coverage_rate=1.0,
                deviance_status="BASELINE",
                overall_status="PASS",
                sample_count=1,
                requested_count=1,
                alerts=(),
                composition=None,
                created_at="2026-11-14T00:00:00Z",
            )
        assert len(ledger.query_batches(limit=10)) == 2

def test_transaction_rolls_back_every_row_on_failure(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        with pytest.raises(sqlite3.Error):
            with ledger.transaction():
                _record(ledger, sample_fp="sample-one")
                # A malformed write inside the same transaction must undo the first.
                ledger._connection.execute(
                    "INSERT INTO batches (batch_id) VALUES (?)", ("broken",)
                )
        assert ledger.query_batches(limit=10) == []
        # The connection is usable again after rollback.
        batch, duplicate = _record(ledger, sample_fp="sample-one")
        assert duplicate is False and batch.batch_id.startswith("batch-")

def test_nested_transactions_share_one_commit(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        with ledger.transaction():
            _record(ledger, sample_fp="sample-one")
            with ledger.transaction():
                _record(ledger, sample_fp="sample-two")
            # Still uncommitted from the inner context's point of view.
        assert len(ledger.query_batches(limit=10)) == 2

def test_record_batch_outside_transaction_still_autocommits(tmp_path: Path) -> None:
    with SQLiteLedger(tmp_path / "ledger.sqlite") as ledger:
        batch, _ = _record(ledger)
    with SQLiteLedger(tmp_path / "ledger.sqlite") as reopened:
        assert reopened.get_batch(batch.batch_id) == batch
