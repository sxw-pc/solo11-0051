"""Small builders used by the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from samplelaw.config.loader import load_plan_text
from samplelaw.config.schema import PlanSpec
from samplelaw.source.dataset import Dataset

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
GOLD = ROOT / "fixtures" / "gold_samples"
BAD = ROOT / "fixtures" / "bad_cases"

def plan_text(
    *,
    name: str = "unit-plan",
    mode: str = "fixed",
    seed: int = 7,
    total_size: int | None = None,
    per_stratum: int | None = 2,
    min_per: int = 1,
    max_per: int | None = None,
    keys: Sequence[str] = ("channel",),
    rare: float = 0.1,
    excludes: Sequence[Mapping[str, Any]] = (),
    expected: Sequence[Sequence[str]] = (),
    order_sensitive: bool = False,
    on_shortfall: str = "warn",
    threshold_max: float = 0.2,
    threshold_weighted: float = 0.15,
    drift: float = 0.2,
    dimensions: Sequence[str] = (),
    fingerprint_columns: Sequence[str] | None = None,
    api_version: str = "samplelaw/v1",
) -> str:
    sampling: dict[str, Any] = {
        "mode": mode,
        "seed": seed,
        "min_per_stratum": min_per,
        "on_shortfall": on_shortfall,
    }
    if total_size is not None:
        sampling["total_size"] = total_size
    if per_stratum is not None:
        sampling["per_stratum"] = per_stratum
    if max_per is not None:
        sampling["max_per_stratum"] = max_per
    strata: dict[str, Any] = {"keys": list(keys), "rare_threshold": rare}
    if expected:
        strata["expected_levels"] = [list(level) for level in expected]
    data: dict[str, Any] = {
        "apiVersion": api_version,
        "kind": "SamplingPlan",
        "metadata": {"name": name, "version": 1},
        "sampling": sampling,
        "strata": strata,
        "exclude": list(excludes),
        "fingerprint": {"order_sensitive": order_sensitive},
        "thresholds": {
            "max_share_deviation": threshold_max,
            "max_weighted_share_deviation": threshold_weighted,
            "drift": drift,
        },
        "deviance": {"dimensions": list(dimensions), "min_baseline_rows": 1},
    }
    if fingerprint_columns:
        data["fingerprint"]["columns"] = list(fingerprint_columns)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

def make_plan(**kwargs: Any) -> PlanSpec:
    return load_plan_text(plan_text(**kwargs), origin="<unit-test>")

def make_dataset(rows: Iterable[Mapping[str, str]], columns: Sequence[str], name: str = "unit-source") -> Dataset:
    return Dataset.from_rows(name, columns, rows)

def simple_dataset() -> Dataset:
    rows = [
        {"id": f"demo-{index:03d}", "channel": channel, "tier": tier}
        for index, (channel, tier) in enumerate(
            [("web", "gold"), ("web", "silver"), ("store", "gold"), ("store", "silver")] * 5
        )
    ]
    return make_dataset(rows, ["id", "channel", "tier"])

def write_yaml(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path
