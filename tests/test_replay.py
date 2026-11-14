from __future__ import annotations

import pytest

from samplelaw.config.loader import load_plan
from samplelaw.plan.sampling import execute_sampling
from samplelaw.source.csv_reader import read_csv_dataset
from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import DataError
from samplelaw.verify.replay import DIVERGED, INSUFFICIENT_INPUT, MATCH, verify_replay
from tests.helpers import BAD, EXAMPLES, GOLD, make_plan, simple_dataset

def _basic() -> tuple[object, object]:
    return (
        load_plan(EXAMPLES / "plans" / "plan_basic.yaml"),
        read_csv_dataset(EXAMPLES / "data" / "demo_members.csv"),
    )

def test_replay_match_from_gold_snapshot() -> None:
    plan, source = _basic()
    sample = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    result = verify_replay(plan, source, sample)
    assert result.status == MATCH
    assert result.missing_count == 0
    assert result.extra_count == 0
    assert result.expected_fingerprint == result.actual_fingerprint

def test_replay_diverged_from_mutated_sample() -> None:
    plan, source = _basic()
    sample = read_csv_dataset(BAD / "sample_mutated.csv")
    result = verify_replay(plan, source, sample)
    assert result.status == DIVERGED
    assert result.missing_count == 1
    assert result.extra_count == 1
    assert any(item.missing or item.extra for item in result.stratum_diffs)

def test_replay_insufficient_input() -> None:
    plan, source = _basic()
    empty = Dataset.from_rows("empty-sample", source.columns, [])
    result = verify_replay(plan, source, empty)
    assert result.status == INSUFFICIENT_INPUT
    assert result.actual_count == 0
    assert "no rows" in result.message

def test_replay_order_sensitivity() -> None:
    source = simple_dataset()
    plan = make_plan(per_stratum=2, order_sensitive=True)
    generated = execute_sampling(source, plan)
    sample = Dataset.from_rows("reordered", source.columns, reversed(generated.rows))
    result = verify_replay(plan, source, sample)
    assert result.status == DIVERGED
    assert result.order_mismatch is True
    assert result.missing_count == 0 and result.extra_count == 0

def test_replay_missing_sample_column_is_data_error() -> None:
    plan, source = _basic()
    sample = Dataset.from_rows(
        "demo-sample", ["member_id", "region"], [{"member_id": "demo-m-0001", "region": "north"}]
    )
    with pytest.raises(DataError) as captured:
        verify_replay(plan, source, sample)
    assert captured.value.code == "DATA-002"
