from __future__ import annotations

from samplelaw.plan.sampling import Allocation
from samplelaw.source.dataset import Dataset
from samplelaw.verify.deviance import (
    build_composition,
    composition_from_json,
    composition_to_json,
    detect_deviance,
)
from tests.helpers import make_plan

def _composition(gold: int, silver: int) -> dict:
    return {
        "strata": {
            "a": {
                "sample_rows": gold + silver,
                "dimensions": {"tier": {"gold": gold, "silver": silver}},
            }
        }
    }

def test_over_sampling_detected() -> None:
    plan = make_plan(per_stratum=2, max_per=5)
    allocation = Allocation(("a",), "a", source_rows=10, target=10, actual=10)
    result = detect_deviance(plan, [allocation], None)
    assert result.status == "WARN"
    assert result.alerts[0].code == "DEV-001"
    assert result.alerts[0].observed == 10

def test_under_sampling_detected() -> None:
    plan = make_plan(per_stratum=2, min_per=2)
    allocation = Allocation(("a",), "a", source_rows=5, target=2, actual=1)
    result = detect_deviance(plan, [allocation], None)
    assert result.status == "WARN"
    assert result.alerts[0].code == "DEV-002"
    assert result.alerts[0].threshold == 2

def test_composition_drift_detected() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2)
    result = detect_deviance(
        plan,
        [],
        _composition(9, 1),
        _composition(1, 9),
        "batch-demo-old",
    )
    assert result.status == "WARN"
    assert result.baseline_batch_id == "batch-demo-old"
    assert result.alerts[0].code == "DEV-003"
    assert result.alerts[0].observed == 0.8

def test_baseline_and_stable_composition() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2)
    baseline = detect_deviance(plan, [], _composition(5, 5))
    assert baseline.status == "BASELINE"
    assert baseline.alerts == ()
    stable = detect_deviance(plan, [], _composition(5, 5), _composition(5, 5), "batch-demo-old")
    assert stable.status == "PASS"
    assert stable.alerts == ()
    assert stable.drift_metrics[0]["observed"] == 0.0

def test_build_composition_and_json_roundtrip() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",))
    sample = Dataset.from_rows(
        "sample",
        ["id", "channel", "tier"],
        [
            {"id": "1", "channel": "a", "tier": "gold"},
            {"id": "2", "channel": "a", "tier": "silver"},
        ],
    )
    composition = build_composition(plan, sample)
    assert composition["strata"]["a"]["sample_rows"] == 2
    assert composition["strata"]["a"]["dimensions"]["tier"] == {
        "gold": 1,
        "silver": 1,
    }
    encoded = composition_to_json(composition)
    assert composition_from_json(encoded) == composition
    assert composition_to_json(composition) == encoded
    assert composition_from_json("not-json") is None
