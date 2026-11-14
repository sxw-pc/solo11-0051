"""Deterministic stratum enumeration and rare/empty layer detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from samplelaw.source.dataset import Dataset

SEPARATOR = " | "

@dataclass(frozen=True)
class StratumInfo:
    key: tuple[str, ...]
    label: str
    count: int
    share: float
    rare: bool
    empty: bool
    expected: bool

@dataclass(frozen=True)
class StrataSummary:
    strata: tuple[StratumInfo, ...]
    total_rows: int
    rare: tuple[StratumInfo, ...]
    empty: tuple[StratumInfo, ...]
    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.strata)

def stratum_key(row: Mapping[str, str], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(row.get(key, "")) for key in keys)

def stratum_label(key: Sequence[str]) -> str:
    return SEPARATOR.join(key)

def stratum_counts(rows: Sequence[Mapping[str, str]], keys: Sequence[str]) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    for row in rows:
        key = stratum_key(row, keys)
        counts[key] = counts.get(key, 0) + 1
    return counts

def enumerate_strata(
    dataset: Dataset,
    keys: Sequence[str],
    rare_threshold: float,
    expected_levels: Sequence[Sequence[str]] = (),
) -> StrataSummary:
    dataset.require_columns(keys, role="source")
    counts = stratum_counts(dataset.rows, keys)
    expected = {tuple(level) for level in expected_levels}
    all_keys = sorted(set(counts) | expected)
    total = sum(counts.values())
    strata: list[StratumInfo] = []
    for key in all_keys:
        count = counts.get(key, 0)
        share = (count / total) if total else 0.0
        strs = StratumInfo(
            key=key,
            label=stratum_label(key),
            count=count,
            share=share,
            rare=share < rare_threshold,
            empty=count == 0,
            expected=key in expected,
        )
        strata.append(strs)
    rare = tuple(item for item in strata if item.rare)
    empty = tuple(item for item in strata if item.empty)
    return StrataSummary(strata=tuple(strata), total_rows=total, rare=rare, empty=empty)
