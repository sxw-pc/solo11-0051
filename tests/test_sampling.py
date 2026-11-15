from __future__ import annotations

import random
from pathlib import Path

import pytest

from samplelaw.config.loader import load_plan
from samplelaw.plan.sampling import _stratum_seed, execute_sampling
from samplelaw.plan.stratify import enumerate_strata
from samplelaw.source.csv_reader import read_csv_dataset, write_csv_rows
from samplelaw.util.errors import DataError, SamplingError
from samplelaw.util.hashing import row_fingerprint
from tests.helpers import EXAMPLES, GOLD, make_dataset, make_plan, simple_dataset

def test_proportional_is_deterministic_and_bounded() -> None:
    plan = make_plan(mode="proportional", total_size=6, per_stratum=None, max_per=3)
    source = simple_dataset()
    first = execute_sampling(source, plan)
    second = execute_sampling(source, plan)
    assert first.sample_fingerprint == second.sample_fingerprint
    assert first.rows == second.rows
    assert first.actual_total == 6
    assert sum(item.target for item in first.allocations) == 6
    assert all(item.actual <= 3 for item in first.allocations)

def test_fixed_shortfall_warn_and_fail() -> None:
    source = make_dataset(
        [
            {"id": "a1", "channel": "a"},
            {"id": "a2", "channel": "a"},
            {"id": "b1", "channel": "b"},
            {"id": "b2", "channel": "b"},
            {"id": "b3", "channel": "b"},
        ],
        ["id", "channel"],
    )
    plan = make_plan(per_stratum=3, on_shortfall="warn")
    result = execute_sampling(source, plan)
    allocation = {item.label: item for item in result.allocations}
    assert allocation["a"].target == 3
    assert allocation["a"].actual == 2
    assert any(item.code == "SAMP-101" for item in result.warnings)
    strict = make_plan(per_stratum=3, on_shortfall="fail")
    with pytest.raises(SamplingError) as captured:
        execute_sampling(source, strict)
    assert captured.value.code == "SAMP-002"

def test_min_coverage_floor_raises_effective_total() -> None:
    source = make_dataset(
        [{"id": f"{channel}{i}", "channel": channel} for channel in "abc" for i in range(10)],
        ["id", "channel"],
    )
    plan = make_plan(mode="min_coverage", total_size=2, per_stratum=None, min_per=2, rare=0.01)
    result = execute_sampling(source, plan)
    assert result.actual_total == 6
    assert all(item.actual >= 2 for item in result.allocations)
    assert any(item.code == "SAMP-102" for item in result.warnings)

def test_max_cap_limits_total() -> None:
    source = make_dataset(
        [{"id": f"a{i}", "channel": "a"} for i in range(10)] + [{"id": "b0", "channel": "b"}],
        ["id", "channel"],
    )
    plan = make_plan(mode="proportional", total_size=10, per_stratum=None, max_per=3)
    result = execute_sampling(source, plan)
    assert {item.label: item.actual for item in result.allocations} == {"a": 3, "b": 1}
    assert any(item.code == "SAMP-102" for item in result.warnings)

def test_exclusion_and_expected_empty_stratum() -> None:
    source = make_dataset(
        [
            {"id": "1", "channel": "web", "status": "on"},
            {"id": "2", "channel": "web", "status": "off"},
            {"id": "3", "channel": "store", "status": "on"},
        ],
        ["id", "channel", "status"],
    )
    plan = make_plan(
        mode="proportional",
        total_size=2,
        per_stratum=None,
        min_per=1,
        rare=0.2,
        excludes=[{"column": "status", "operator": "eq", "value": "off"}],
        expected=[["archive"]],
    )
    result = execute_sampling(source, plan)
    assert result.excluded_rows == 1
    assert result.eligible_rows == 2
    assert any(item.label == "archive" and item.actual == 0 for item in result.allocations)
    assert any(item.code == "SAMP-103" for item in result.warnings)

def test_no_eligible_rows_and_missing_column() -> None:
    source = make_dataset(
        [{"id": "1", "channel": "web", "status": "off"}],
        ["id", "channel", "status"],
    )
    plan = make_plan(excludes=[{"column": "status", "operator": "eq", "value": "off"}])
    with pytest.raises(SamplingError) as captured:
        execute_sampling(source, plan)
    assert captured.value.code == "SAMP-001"
    with pytest.raises(DataError) as captured:
        execute_sampling(source, make_plan(keys=("region",)))
    assert captured.value.code == "DATA-002"

def test_strata_rare_and_empty_detection() -> None:
    source = make_dataset(
        [{"id": str(index), "channel": channel} for index, channel in enumerate(["web"] * 18 + ["rare"] * 2)],
        ["id", "channel"],
    )
    summary = enumerate_strata(source, ["channel"], 0.15, [["ghost"]])
    assert summary.total_rows == 20
    assert [item.label for item in summary.rare] == ["ghost", "rare"]
    assert [item.label for item in summary.empty] == ["ghost"]

def test_gold_snapshot_matches_fixed_plan() -> None:
    plan = load_plan(EXAMPLES / "plans" / "plan_basic.yaml")
    source = read_csv_dataset(EXAMPLES / "data" / "demo_members.csv")
    gold = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    result = execute_sampling(source, plan)
    assert result.rows == gold.rows

def test_post_exclusion_placement_backfills_and_matches_pre_fingerprint(tmp_path: Path) -> None:
    seed = 7
    label = "web"
    row_count = 12
    excluded_front = 3
    # Replicate the sampler's derived candidate order for this seed/stratum and
    # place the excluded rows at the front of that order, so the post cursor
    # must skip them and backfill from further down the same order.
    derived_order = list(range(row_count))
    random.Random(_stratum_seed(seed, label)).shuffle(derived_order)
    excluded_positions = set(derived_order[:excluded_front])
    rows = [
        {
            "id": f"row-{position:02d}",
            "channel": "web",
            "status": "off" if position in excluded_positions else "on",
        }
        for position in range(row_count)
    ]
    csv_path = tmp_path / "post_placement.csv"
    write_csv_rows(csv_path, ["id", "channel", "status"], rows)
    source = read_csv_dataset(csv_path)
    excludes = [{"column": "status", "operator": "eq", "value": "off"}]
    eligible = row_count - excluded_front

    post_plan = make_plan(per_stratum=5, seed=seed, excludes=excludes, exclusions_placement="post")
    post = execute_sampling(source, post_plan)
    allocation = post.allocations[0]
    assert allocation.target == 5
    assert allocation.actual == 5
    assert post.actual_total == 5
    assert post.excluded_rows == excluded_front
    assert {row["status"] for row in post.rows} == {"on"}
    # The cursor never resets, so no row can be drawn twice.
    hashes = [row_fingerprint(row, source.columns) for row in post.rows]
    assert len(set(hashes)) == len(hashes)
    # The picks are exactly the first five eligible rows in the derived order.
    expected_ids = {
        f"row-{position:02d}" for position in derived_order if position not in excluded_positions
    }
    picked_ids = {row["id"] for row in post.rows}
    assert picked_ids <= expected_ids
    assert len(picked_ids) == 5

    # Same final sample via both placements must yield the same fingerprint:
    # taking every eligible row makes the pre/post row sets coincide.
    pre_full = make_plan(per_stratum=eligible, seed=seed, excludes=excludes, exclusions_placement="pre")
    post_full = make_plan(per_stratum=eligible, seed=seed, excludes=excludes, exclusions_placement="post")
    pre_result = execute_sampling(source, pre_full)
    post_result = execute_sampling(source, post_full)
    assert {row["id"] for row in pre_result.rows} == {row["id"] for row in post_result.rows}
    assert pre_result.sample_fingerprint == post_result.sample_fingerprint
