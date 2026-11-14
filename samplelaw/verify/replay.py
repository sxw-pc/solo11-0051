"""Recompute a sample from plan + source and compare it with a persisted sample."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from samplelaw.config.schema import PlanSpec
from samplelaw.plan.sampling import SamplingError, execute_sampling
from samplelaw.plan.stratify import stratum_key, stratum_label
from samplelaw.source.dataset import Dataset
from samplelaw.util.hashing import row_fingerprint, rows_fingerprint

MATCH = "MATCH"
DIVERGED = "DIVERGED"
INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"

@dataclass(frozen=True)
class StratumDiff:
    label: str
    expected: int
    actual: int
    missing: int
    extra: int

@dataclass(frozen=True)
class ReplayResult:
    status: str
    expected_fingerprint: str
    actual_fingerprint: str
    expected_count: int
    actual_count: int
    missing_count: int
    extra_count: int
    order_mismatch: bool
    stratum_diffs: tuple[StratumDiff, ...]
    message: str

def _counter_for(rows: tuple[dict[str, str], ...], columns: tuple[str, ...]) -> Counter[str]:
    return Counter(row_fingerprint(row, columns) for row in rows)

def _stratum_diff(
    expected: tuple[dict[str, str], ...],
    actual: tuple[dict[str, str], ...],
    plan: PlanSpec,
    columns: tuple[str, ...],
) -> tuple[StratumDiff, ...]:
    expected_counts: Counter[str] = Counter()
    actual_counts: Counter[str] = Counter()
    expected_hashes: dict[str, Counter[str]] = {}
    actual_hashes: dict[str, Counter[str]] = {}
    for row in expected:
        label = stratum_label(stratum_key(row, plan.strata_keys))
        expected_counts[label] += 1
        expected_hashes.setdefault(label, Counter())[row_fingerprint(row, columns)] += 1
    for row in actual:
        label = stratum_label(stratum_key(row, plan.strata_keys))
        actual_counts[label] += 1
        actual_hashes.setdefault(label, Counter())[row_fingerprint(row, columns)] += 1
    diffs: list[StratumDiff] = []
    for label in sorted(set(expected_counts) | set(actual_counts)):
        expected_map = expected_hashes.get(label, Counter())
        actual_map = actual_hashes.get(label, Counter())
        diffs.append(
            StratumDiff(
                label=label,
                expected=expected_counts[label],
                actual=actual_counts[label],
                missing=sum((expected_map - actual_map).values()),
                extra=sum((actual_map - expected_map).values()),
            )
        )
    return tuple(diffs)

def verify_replay(plan: PlanSpec, source: Dataset, sample: Dataset) -> ReplayResult:
    columns = plan.fingerprint_columns or source.columns
    source.require_columns(columns, role="source")
    sample.require_columns(columns, role="sample")
    sample.require_columns(plan.strata_keys, role="sample")
    if not source.rows or not sample.rows:
        return ReplayResult(
            status=INSUFFICIENT_INPUT,
            expected_fingerprint="",
            actual_fingerprint=rows_fingerprint(sample.rows, columns, order_sensitive=plan.order_sensitive),
            expected_count=0,
            actual_count=len(sample.rows),
            missing_count=0,
            extra_count=0,
            order_mismatch=False,
            stratum_diffs=(),
            message="source or persisted sample has no rows",
        )
    try:
        generated = execute_sampling(source, plan)
    except SamplingError as exc:
        if exc.code == "SAMP-001":
            return ReplayResult(
                status=INSUFFICIENT_INPUT,
                expected_fingerprint="",
                actual_fingerprint=rows_fingerprint(sample.rows, columns, order_sensitive=plan.order_sensitive),
                expected_count=0,
                actual_count=len(sample.rows),
                missing_count=0,
                extra_count=0,
                order_mismatch=False,
                stratum_diffs=(),
                message=f"cannot recompute expected sample: {exc.message}",
            )
        raise
    expected_rows = generated.rows
    expected_counter = _counter_for(expected_rows, columns)
    actual_counter = _counter_for(sample.rows, columns)
    missing = sum((expected_counter - actual_counter).values())
    extra = sum((actual_counter - expected_counter).values())
    expected_sequence = [row_fingerprint(row, columns) for row in expected_rows]
    actual_sequence = [row_fingerprint(row, columns) for row in sample.rows]
    order_mismatch = plan.order_sensitive and expected_sequence != actual_sequence
    diffs = _stratum_diff(expected_rows, sample.rows, plan, columns)
    matched = missing == 0 and extra == 0 and not order_mismatch
    detail = "sample matches deterministic replay"
    if not matched:
        detail = f"replay diverged: {missing} missing row(s), {extra} extra row(s)"
        if order_mismatch:
            detail += "; row order differs but plan requires ordered comparison"
    return ReplayResult(
        status=MATCH if matched else DIVERGED,
        expected_fingerprint=rows_fingerprint(expected_rows, columns, order_sensitive=plan.order_sensitive),
        actual_fingerprint=rows_fingerprint(sample.rows, columns, order_sensitive=plan.order_sensitive),
        expected_count=len(expected_rows),
        actual_count=len(sample.rows),
        missing_count=missing,
        extra_count=extra,
        order_mismatch=order_mismatch,
        stratum_diffs=diffs,
        message=detail,
    )
