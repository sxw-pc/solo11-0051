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

def _tier_composition(counts: dict[str, int]) -> dict:
    return {
        "strata": {
            "a": {
                "sample_rows": sum(counts.values()),
                "dimensions": {"tier": dict(counts)},
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
    assert result.domain_changes == ()

def test_domain_change_attributed_and_common_subset_compared() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2)
    # Baseline {a,b,c} = 60/30/10 and current {a,b,d} = 70/20/10.
    # The shared a/b sub-domain re-normalizes to 70/90 vs 60/90, distance 0.1111.
    previous = _tier_composition({"a": 60, "b": 30, "c": 10})
    current = _tier_composition({"a": 70, "b": 20, "d": 10})
    result = detect_deviance(plan, [], current, previous, "batch-demo-old")
    assert result.status == "PASS"
    assert result.alerts == ()
    assert result.drift_metrics[0]["observed"] == 0.111111
    assert result.drift_metrics[0]["common_values"] == ["a", "b"]
    changes = {item["value"]: item for item in result.domain_changes}
    assert changes["c"]["change"] == "removed"
    assert changes["c"]["previous_share"] == 0.1
    assert changes["c"]["current_share"] is None
    assert changes["d"]["change"] == "added"
    assert changes["d"]["current_share"] == 0.1
    assert changes["d"]["previous_share"] is None
    assert result.drift_metrics[0]["status"] == "PASS"

def test_domain_change_does_not_mask_real_drift() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2)
    # Comparing the full domains would give 0.15 and stay under the threshold;
    # on the shared a/b domain the shift is 0.25 and must still alert.
    previous = _tier_composition({"a": 50, "b": 50})
    current = _tier_composition({"a": 60, "b": 20, "d": 20})
    result = detect_deviance(plan, [], current, previous, "batch-demo-old")
    assert result.drift_metrics[0]["common_values"] == ["a", "b"]
    assert result.drift_metrics[0]["observed"] == 0.25
    assert result.status == "WARN"
    assert result.alerts[0].code == "DEV-003"

def test_empty_common_subset_is_no_baseline() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2)
    previous = _tier_composition({"a": 5, "b": 5})
    current = _tier_composition({"c": 5, "d": 5})
    result = detect_deviance(plan, [], current, previous, "batch-demo-old")
    assert result.status == "NO_BASELINE"
    assert result.alerts == ()
    assert result.drift_metrics[0]["observed"] is None
    assert result.drift_metrics[0]["status"] == "NO_BASELINE"
    assert result.drift_metrics[0]["common_values"] == []
    changes = [(item["change"], item["value"]) for item in result.domain_changes]
    assert changes == [("removed", "a"), ("removed", "b"), ("added", "c"), ("added", "d")]

def test_min_baseline_rows_uses_full_stratum_size() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2, min_baseline_rows=10)
    previous = _tier_composition({"a": 6, "b": 4, "c": 1})
    current = _tier_composition({"a": 3, "b": 7, "d": 1})
    result = detect_deviance(plan, [], current, previous, "batch-demo-old")
    # Both full strata are >= 10 rows, so the comparison runs even though
    # individual common values hold fewer rows.
    assert result.drift_metrics[0]["observed"] == 0.3
    assert result.status == "WARN"

    small_plan = make_plan(per_stratum=2, dimensions=("tier",), drift=0.2, min_baseline_rows=12)
    small = detect_deviance(small_plan, [], current, previous, "batch-demo-old")
    assert small.drift_metrics == ()
    assert small.status == "PASS"

def test_drift_metrics_order_is_stable() -> None:
    plan = make_plan(per_stratum=2, dimensions=("tier", "channel", "region"), drift=0.2)
    previous = {
        "strata": {
            "b": {
                "sample_rows": 10,
                "dimensions": {
                    "tier": {"gold": 5, "silver": 5},
                    "channel": {"web": 5, "store": 5},
                    "region": {"north": 5, "south": 5},
                },
            },
            "a": {
                "sample_rows": 10,
                "dimensions": {
                    "tier": {"gold": 5, "silver": 5},
                    "channel": {"web": 5, "store": 5},
                    "region": {"north": 5, "south": 5},
                },
            },
        }
    }
    current = previous
    first = detect_deviance(plan, [], current, previous, "batch-demo-old")
    second = detect_deviance(plan, [], current, previous, "batch-demo-old")
    key = [(item["stratum"], item["dimension"]) for item in first.drift_metrics]
    assert key == [("a", "tier"), ("a", "channel"), ("a", "region"), ("b", "tier"), ("b", "channel"), ("b", "region")]
    assert [item["observed"] for item in first.drift_metrics] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert first.drift_metrics == second.drift_metrics
    assert first.domain_changes == second.domain_changes

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
