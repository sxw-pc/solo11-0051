from __future__ import annotations

import pytest

from samplelaw.config.loader import load_plan
from samplelaw.source.csv_reader import read_csv_dataset
from samplelaw.source.dataset import Dataset
from samplelaw.util.errors import DataError
from samplelaw.verify.coverage import verify_coverage
from tests.helpers import EXAMPLES, GOLD, make_dataset, make_plan

def test_coverage_passes_for_gold_sample() -> None:
    plan = load_plan(EXAMPLES / "plans" / "plan_basic.yaml")
    source = read_csv_dataset(EXAMPLES / "data" / "demo_members.csv")
    sample = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    result = verify_coverage(plan, source, sample)
    assert result.status == "PASS"
    assert result.coverage_rate == 1.0
    assert result.rare_total == 1 and result.rare_hit == 1

def test_rare_stratum_miss_emits_alert() -> None:
    source = make_dataset(
        [{"id": f"a{i}", "channel": "a"} for i in range(10)]
        + [{"id": "b0", "channel": "b"}, {"id": "b1", "channel": "b"}],
        ["id", "channel"],
    )
    sample = Dataset.from_rows("sample", ["id", "channel"], [{"id": f"a{i}", "channel": "a"} for i in range(4)])
    plan = make_plan(mode="proportional", total_size=4, per_stratum=None, max_per=10, rare=0.2)
    result = verify_coverage(plan, source, sample)
    assert result.status == "WARN"
    assert "b" in result.rare_missed
    assert any(item.code == "COV-001" for item in result.alerts)

def test_expected_empty_stratum_is_reported() -> None:
    source = make_dataset(
        [{"id": "a0", "channel": "a"}, {"id": "b0", "channel": "b"}],
        ["id", "channel"],
    )
    sample = Dataset.from_rows(
        "sample",
        ["id", "channel"],
        [{"id": "a0", "channel": "a"}, {"id": "b0", "channel": "b"}],
    )
    plan = make_plan(
        per_stratum=1,
        expected=[["ghost"]],
        rare=0.1,
    )
    result = verify_coverage(plan, source, sample)
    assert result.coverage_rate == pytest.approx(2 / 3, abs=0.000001)
    assert any(item.code == "COV-002" for item in result.alerts)
    assert any(item.empty for item in result.strata)

def test_share_deviation_thresholds_and_empty_sample() -> None:
    source = make_dataset(
        [{"id": f"a{i}", "channel": "a"} for i in range(5)] + [{"id": f"b{i}", "channel": "b"} for i in range(5)],
        ["id", "channel"],
    )
    plan = make_plan(
        mode="proportional",
        total_size=5,
        per_stratum=None,
        max_per=5,
        rare=0.1,
        threshold_max=0.1,
        threshold_weighted=0.05,
    )
    sample = Dataset.from_rows("sample", ["id", "channel"], [{"id": f"a{i}", "channel": "a"} for i in range(5)])
    result = verify_coverage(plan, source, sample)
    codes = {item.code for item in result.alerts}
    assert {"COV-003", "COV-004"}.issubset(codes)
    assert result.max_abs_share_deviation == 0.5
    empty = Dataset.from_rows("empty", ["id", "channel"], [])
    failed = verify_coverage(plan, source, empty)
    assert failed.status == "FAIL"
    assert any(item.code == "COV-000" for item in failed.alerts)

def test_coverage_missing_sample_column() -> None:
    plan = make_plan()
    source = make_dataset([{"id": "1", "channel": "web"}], ["id", "channel"])
    sample = Dataset.from_rows("sample", ["id"], [{"id": "1"}])
    with pytest.raises(DataError) as captured:
        verify_coverage(plan, source, sample)
    assert captured.value.code == "DATA-002"
