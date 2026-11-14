"""Deterministic fingerprints used across sampling, replay and the ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def row_fingerprint(row: Mapping[str, str], columns: Sequence[str]) -> str:
    """Fingerprint one row using the declared column order and exact values."""
    payload = [[column, str(row.get(column, ""))] for column in columns]
    return sha256_text(canonical_json(payload))

def rows_fingerprint(
    rows: Iterable[Mapping[str, str]],
    columns: Sequence[str],
    *,
    order_sensitive: bool = False,
) -> str:
    hashes = [row_fingerprint(row, columns) for row in rows]
    if not order_sensitive:
        hashes.sort()
    return sha256_text(canonical_json(hashes))

def dataset_fingerprint(columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> str:
    hashes = [row_fingerprint(row, columns) for row in rows]
    return sha256_text(canonical_json({"columns": list(columns), "rows": hashes}))

def batch_fingerprint(plan_fingerprint: str, source_fingerprint: str, sample_fingerprint: str) -> str:
    return sha256_text(
        canonical_json(
            {
                "plan": plan_fingerprint,
                "source": source_fingerprint,
                "sample": sample_fingerprint,
            }
        )
    )

def plan_fingerprint(plan_canonical: Mapping[str, object]) -> str:
    return sha256_text(canonical_json(plan_canonical))

def short_hash(value: str, length: int = 12) -> str:
    if length < 4:
        raise ValueError("length must be at least 4")
    return value[:length]
