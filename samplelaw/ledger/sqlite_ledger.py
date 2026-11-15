"""Local SQLite ledger for batches, fingerprints, verdicts and alerts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from samplelaw.util.errors import LedgerError
from samplelaw.util.hashing import batch_fingerprint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
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
CREATE INDEX IF NOT EXISTS idx_batches_plan ON batches(plan_fingerprint);
CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);
"""

@dataclass(frozen=True)
class LedgerBatch:
    batch_id: str
    batch_fingerprint: str
    plan_fingerprint: str
    plan_name: str
    source_fingerprint: str
    sample_fingerprint: str
    replay_status: str
    coverage_status: str
    coverage_rate: float
    deviance_status: str
    overall_status: str
    sample_count: int
    requested_count: int
    alerts_json: str
    composition_json: str
    created_at: str
    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_fingerprint": self.batch_fingerprint,
            "plan_fingerprint": self.plan_fingerprint,
            "plan_name": self.plan_name,
            "source_fingerprint": self.source_fingerprint,
            "sample_fingerprint": self.sample_fingerprint,
            "replay_status": self.replay_status,
            "coverage_status": self.coverage_status,
            "coverage_rate": self.coverage_rate,
            "deviance_status": self.deviance_status,
            "overall_status": self.overall_status,
            "sample_count": self.sample_count,
            "requested_count": self.requested_count,
            "alerts": json.loads(self.alerts_json),
            "created_at": self.created_at,
        }

def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

class SQLiteLedger:
    """A single-file ledger; all public writes stay inside this SQLite file."""
    def __init__(self, path: str | Path = "out/ledger.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(str(self.path))
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(_SCHEMA)
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.commit()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-001", f"cannot open ledger: {exc}", path=str(self.path)) from exc
    def __enter__(self) -> "SQLiteLedger":
        return self
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-003", f"cannot close ledger: {exc}", path=str(self.path)) from exc
    def record_batch(
        self,
        *,
        plan_fingerprint: str,
        plan_name: str,
        source_fingerprint: str,
        sample_fingerprint: str,
        replay_status: str,
        coverage_status: str,
        coverage_rate: float,
        deviance_status: str,
        overall_status: str,
        sample_count: int,
        requested_count: int,
        alerts: Sequence[Mapping[str, Any]],
        composition: Mapping[str, Any] | None,
        created_at: str,
    ) -> tuple[LedgerBatch, bool]:
        fingerprint = batch_fingerprint(plan_fingerprint, source_fingerprint, sample_fingerprint)
        batch_id = f"batch-{fingerprint[:16]}"
        existing = self.get_batch(batch_id)
        if existing is not None:
            return existing, True
        try:
            self._connection.execute(
                """
                INSERT INTO batches (
                    batch_id, batch_fingerprint, plan_fingerprint, plan_name,
                    source_fingerprint, sample_fingerprint, replay_status,
                    coverage_status, coverage_rate, deviance_status, overall_status,
                    sample_count, requested_count, alerts_json, composition_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    fingerprint,
                    plan_fingerprint,
                    plan_name,
                    source_fingerprint,
                    sample_fingerprint,
                    replay_status,
                    coverage_status,
                    float(coverage_rate),
                    deviance_status,
                    overall_status,
                    int(sample_count),
                    int(requested_count),
                    _json(list(alerts)),
                    _json(dict(composition or {"strata": {}})),
                    created_at,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError:
            existing = self.get_batch(batch_id)
            if existing is not None:
                return existing, True
            raise
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-002", f"cannot write ledger batch: {exc}", path=str(self.path)) from exc
        batch = self.get_batch(batch_id)
        if batch is None:
            raise LedgerError("LEDGER-002", "ledger write vanished", path=str(self.path))
        return batch, False
    def get_batch(self, batch_id: str) -> LedgerBatch | None:
        try:
            row = self._connection.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-004", f"cannot read ledger: {exc}", path=str(self.path)) from exc
        return _row_to_batch(row) if row is not None else None
    def query_batches(self, *, plan_fingerprint: str | None = None, limit: int = 20) -> list[LedgerBatch]:
        if limit < 1:
            raise LedgerError("LEDGER-005", "limit must be positive", path=str(self.path))
        sql = "SELECT * FROM batches"
        params: list[Any] = []
        if plan_fingerprint:
            sql += " WHERE plan_fingerprint = ?"
            params.append(plan_fingerprint)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        try:
            rows = self._connection.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-004", f"cannot query ledger: {exc}", path=str(self.path)) from exc
        return [_row_to_batch(row) for row in rows]
    def previous_for_plan(self, plan_fingerprint: str, *, exclude_batch_id: str | None = None) -> LedgerBatch | None:
        sql = "SELECT * FROM batches WHERE plan_fingerprint = ?"
        params: list[Any] = [plan_fingerprint]
        if exclude_batch_id:
            sql += " AND batch_id != ?"
            params.append(exclude_batch_id)
        sql += " ORDER BY rowid DESC LIMIT 1"
        try:
            row = self._connection.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError("LEDGER-004", f"cannot query previous batch: {exc}", path=str(self.path)) from exc
        return _row_to_batch(row) if row is not None else None

def _row_to_batch(row: sqlite3.Row) -> LedgerBatch:
    return LedgerBatch(
        batch_id=str(row["batch_id"]),
        batch_fingerprint=str(row["batch_fingerprint"]),
        plan_fingerprint=str(row["plan_fingerprint"]),
        plan_name=str(row["plan_name"]),
        source_fingerprint=str(row["source_fingerprint"]),
        sample_fingerprint=str(row["sample_fingerprint"]),
        replay_status=str(row["replay_status"]),
        coverage_status=str(row["coverage_status"]),
        coverage_rate=float(row["coverage_rate"]),
        deviance_status=str(row["deviance_status"]),
        overall_status=str(row["overall_status"]),
        sample_count=int(row["sample_count"]),
        requested_count=int(row["requested_count"]),
        alerts_json=str(row["alerts_json"]),
        composition_json=str(row["composition_json"]),
        created_at=str(row["created_at"]),
    )
