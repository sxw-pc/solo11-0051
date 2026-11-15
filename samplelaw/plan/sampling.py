"""Deterministic execution of proportional, fixed and min-coverage plans."""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Mapping

from samplelaw.config.schema import Exclusion, PlanSpec
from samplelaw.plan.stratify import (
    StratumInfo,
    enumerate_strata,
    stratum_counts,
    stratum_key,
    stratum_label,
)
from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import SamplingError
from samplelaw.util.hashing import canonical_json, rows_fingerprint, sha256_text

@dataclass(frozen=True)
class SamplingWarning:
    code: str
    stratum: str
    message: str

@dataclass(frozen=True)
class Allocation:
    key: tuple[str, ...]
    label: str
    source_rows: int
    target: int
    actual: int
    empty: bool = False

@dataclass(frozen=True)
class SampledRow:
    index: int
    row: dict[str, str]
    stratum_key: tuple[str, ...]

@dataclass(frozen=True)
class SamplingResult:
    plan: PlanSpec
    sampled_rows: tuple[SampledRow, ...]
    allocations: tuple[Allocation, ...]
    warnings: tuple[SamplingWarning, ...]
    excluded_rows: int
    eligible_rows: int
    requested_total: int
    sample_fingerprint: str
    @property
    def rows(self) -> tuple[dict[str, str], ...]:
        return tuple(item.row for item in self.sampled_rows)
    @property
    def actual_total(self) -> int:
        return len(self.sampled_rows)

def _matches(row: Mapping[str, str], rule: Exclusion) -> bool:
    value = row.get(rule.column, "")
    if rule.operator == "eq":
        return value == rule.value
    if rule.operator == "neq":
        return value != rule.value
    values = tuple(rule.value) if isinstance(rule.value, tuple) else (rule.value,)
    if rule.operator == "in":
        return value in values
    if rule.operator == "not_in":
        return value not in values
    raise SamplingError("SAMP-003", f"unknown exclusion operator: {rule.operator}")

def eligible_rows(dataset: Dataset, plan: PlanSpec) -> tuple[list[tuple[int, dict[str, str]]], int]:
    for rule in plan.exclusions:
        dataset.require_columns([rule.column], role="source")
    kept: list[tuple[int, dict[str, str]]] = []
    excluded = 0
    for index, row in enumerate(dataset.rows):
        if any(_matches(row, rule) for rule in plan.exclusions):
            excluded += 1
        else:
            kept.append((index, row))
    return kept, excluded

def _shortfall(plan: PlanSpec, key: tuple[str, ...], source_rows: int, required: int) -> SamplingWarning:
    message = f"only {source_rows} eligible rows available, required {required}"
    if plan.sampling.on_shortfall == "fail":
        raise SamplingError("SAMP-002", message, path=stratum_label(key))
    return SamplingWarning("SAMP-101", stratum_label(key), message)

def _stratum_seed(seed: int, label: str) -> int:
    """Derive a deterministic per-stratum seed from the plan seed and stratum name."""
    return int(sha256_text(canonical_json({"seed": seed, "stratum": label}))[:16], 16)

def _draw_post(
    candidates: list[tuple[int, dict[str, str]]],
    target: int,
    plan: PlanSpec,
    label: str,
) -> list[tuple[int, dict[str, str]]]:
    """Walk the stratum's derived stable order with a single cursor.

    Rows hitting an exclusion rule are dropped in place and the cursor keeps
    advancing to backfill the seat, so excluded rows never occupy a final
    seat. The cursor is never reset: each candidate is visited at most once,
    which keeps the picked rows duplicate-free. If the stratum runs out of
    continuable candidates before reaching ``target``, the rows gathered so
    far are kept (shortfall handling already ran during target computation).
    """
    if target <= 0 or not candidates:
        return []
    ordered = list(candidates)
    random.Random(_stratum_seed(plan.seed, label)).shuffle(ordered)
    picked: list[tuple[int, dict[str, str]]] = []
    for index, row in ordered:
        if any(_matches(row, rule) for rule in plan.exclusions):
            continue
        picked.append((index, row))
        if len(picked) >= target:
            break
    return picked

def _distribute_extras(
    sizes: dict[tuple[str, ...], int],
    floors: dict[tuple[str, ...], int],
    caps: dict[tuple[str, ...], int],
    extra_total: int,
) -> dict[tuple[str, ...], int]:
    extras = {key: 0 for key in sizes}
    heap: list[tuple[float, tuple[str, ...]]] = []
    for key in sorted(sizes):
        if floors[key] < caps[key]:
            heapq.heappush(heap, (-float(sizes[key]), key))
    while extra_total > 0 and heap:
        _, key = heapq.heappop(heap)
        extras[key] += 1
        extra_total -= 1
        if floors[key] + extras[key] < caps[key]:
            heapq.heappush(heap, (-float(sizes[key]) / (extras[key] + 1), key))
    if extra_total:
        raise SamplingError("SAMP-003", "allocation failed to consume the requested sample size")
    return extras

def _proportional_targets(
    plan: PlanSpec,
    summary: list[StratumInfo],
    sizes: dict[tuple[str, ...], int],
    warnings: list[SamplingWarning],
) -> dict[tuple[str, ...], int]:
    sampling = plan.sampling
    assert sampling.total_size is not None
    floors: dict[tuple[str, ...], int] = {}
    caps: dict[tuple[str, ...], int] = {}
    for info in summary:
        key = info.key
        size = sizes[key]
        if size == 0:
            floors[key] = caps[key] = 0
            warnings.append(SamplingWarning("SAMP-103", info.label, "expected stratum has no eligible source rows"))
            continue
        floor = min(sampling.min_per_stratum, size)
        if size < sampling.min_per_stratum:
            warnings.append(_shortfall(plan, key, size, sampling.min_per_stratum))
        floors[key] = floor
        caps[key] = min(size, sampling.max_per_stratum or size)
    floor_sum = sum(floors.values())
    cap_sum = sum(caps.values())
    effective = max(sampling.total_size, floor_sum)
    effective = min(effective, cap_sum)
    if effective != sampling.total_size:
        reason = (
            "minimum coverage raised total size"
            if effective > sampling.total_size
            else ("source capacity or max_per_stratum capped total size")
        )
        warnings.append(
            SamplingWarning(
                "SAMP-102",
                "",
                f"requested {sampling.total_size}, executing {effective}: {reason}",
            )
        )
    extras = _distribute_extras(sizes, floors, caps, effective - floor_sum)
    return {key: floors[key] + extras[key] for key in sizes}

def _fixed_targets(
    plan: PlanSpec,
    summary: list[StratumInfo],
    sizes: dict[tuple[str, ...], int],
    warnings: list[SamplingWarning],
) -> dict[tuple[str, ...], int]:
    assert plan.sampling.per_stratum is not None
    targets: dict[tuple[str, ...], int] = {}
    for info in summary:
        key = info.key
        size = sizes[key]
        if size == 0:
            targets[key] = 0
            warnings.append(SamplingWarning("SAMP-103", info.label, "expected stratum has no eligible source rows"))
            continue
        targets[key] = plan.sampling.per_stratum
        if size < plan.sampling.per_stratum:
            warnings.append(_shortfall(plan, key, size, plan.sampling.per_stratum))
    return targets

def execute_sampling(dataset: Dataset, plan: PlanSpec) -> SamplingResult:
    stratum_columns = plan.strata_keys
    fingerprint_columns = plan.fingerprint_columns or dataset.columns
    dataset.require_columns(stratum_columns, role="source")
    dataset.require_columns(fingerprint_columns, role="source")
    kept, excluded = eligible_rows(dataset, plan)
    if not kept:
        raise SamplingError("SAMP-001", "no eligible rows remain after exclusions", path=dataset.name)
    eligible_dataset = Dataset.from_rows(
        dataset.name,
        dataset.columns,
        (row for _, row in kept),
        source_kind=dataset.source_kind,
    )
    summary = enumerate_strata(
        eligible_dataset,
        stratum_columns,
        plan.rare_threshold,
        plan.expected_levels,
    )
    counts = stratum_counts((row for _, row in kept), stratum_columns)
    sizes = {info.key: counts.get(info.key, 0) for info in summary.strata}
    warnings: list[SamplingWarning] = []
    if plan.mode == "fixed":
        targets = _fixed_targets(plan, list(summary.strata), sizes, warnings)
    else:
        targets = _proportional_targets(plan, list(summary.strata), sizes, warnings)
    grouped: dict[tuple[str, ...], list[tuple[int, dict[str, str]]]] = {}
    if plan.exclusions_placement == "post":
        # Post placement keeps excluded rows in the candidate pool; the
        # per-stratum cursor drops them during the draw instead.
        pool = enumerate(dataset.rows)
    else:
        pool = iter(kept)
    for index, row in pool:
        grouped.setdefault(stratum_key(row, stratum_columns), []).append((index, row))
    rng = random.Random(plan.seed)
    sampled: list[SampledRow] = []
    allocations: list[Allocation] = []
    for info in summary.strata:
        candidates = grouped.get(info.key, [])
        target = targets[info.key]
        if plan.exclusions_placement == "post":
            picked = _draw_post(candidates, target, plan, info.label)
        else:
            picked = rng.sample(candidates, min(target, len(candidates))) if candidates and target else []
        actual = len(picked)
        for index, row in sorted(picked, key=lambda item: item[0]):
            sampled.append(SampledRow(index=index, row=row, stratum_key=info.key))
        allocations.append(
            Allocation(
                key=info.key,
                label=info.label,
                source_rows=sizes[info.key],
                target=target,
                actual=actual,
                empty=sizes[info.key] == 0,
            )
        )
    if not sampled:
        raise SamplingError("SAMP-001", "sampling produced zero rows", path=dataset.name)
    sample_fp = rows_fingerprint(
        (item.row for item in sampled),
        fingerprint_columns,
        order_sensitive=plan.order_sensitive,
    )
    warnings.sort(key=lambda item: (item.code, item.stratum))
    return SamplingResult(
        plan=plan,
        sampled_rows=tuple(sampled),
        allocations=tuple(allocations),
        warnings=tuple(warnings),
        excluded_rows=excluded,
        eligible_rows=len(kept),
        requested_total=sum(item.target for item in allocations),
        sample_fingerprint=sample_fp,
    )
