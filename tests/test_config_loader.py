from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from samplelaw.config.loader import load_plan, load_plan_text
from samplelaw.config.pipeline_loader import load_pipeline
from samplelaw.util.errors import ConfigError
from tests.helpers import BAD, EXAMPLES, plan_text

def test_load_plan_normalizes_and_fingerprints() -> None:
    plan = load_plan(EXAMPLES / "plans" / "plan_basic.yaml")
    assert plan.name == "demo-members-fixed"
    assert plan.mode == "fixed"
    assert plan.sampling.per_stratum == 4
    assert plan.strata_keys == ("channel",)
    assert len(plan.fingerprint) == 64
    assert load_plan(EXAMPLES / "plans" / "plan_basic.yaml").fingerprint == plan.fingerprint

def test_fingerprint_changes_with_semantics() -> None:
    first = load_plan_text(plan_text(seed=7))
    second = load_plan_text(plan_text(seed=8))
    assert first.fingerprint != second.fingerprint

def test_missing_seed_has_stable_code_and_line() -> None:
    with pytest.raises(ConfigError) as captured:
        load_plan(BAD / "bad_plan_missing_seed.yaml")
    assert captured.value.code == "PLAN-001"
    assert captured.value.path == "sampling.seed"
    assert captured.value.line == 6

def test_threshold_out_of_range() -> None:
    with pytest.raises(ConfigError) as captured:
        load_plan(BAD / "bad_plan_threshold.yaml")
    assert captured.value.code == "PLAN-007"

def test_mode_field_conflict() -> None:
    with pytest.raises(ConfigError) as captured:
        load_plan(BAD / "bad_plan_mode_conflict.yaml")
    assert captured.value.code == "PLAN-008"

def test_unknown_field_and_wrong_type() -> None:
    data = yaml.safe_load(plan_text())
    data["mystery"] = True
    with pytest.raises(ConfigError) as captured:
        load_plan_text(yaml.safe_dump(data))
    assert captured.value.code == "PLAN-013"
    bad_type = yaml.safe_load(plan_text())
    bad_type["sampling"]["seed"] = "seven"
    with pytest.raises(ConfigError) as captured:
        load_plan_text(yaml.safe_dump(bad_type))
    assert captured.value.code == "PLAN-002"

def test_bad_yaml_and_sort_order_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("sampling: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError) as captured:
        load_plan(path)
    assert captured.value.code == "PLAN-012"
    assert captured.value.line is not None

def test_pipeline_loads_resolved_paths() -> None:
    pipeline = load_pipeline(EXAMPLES / "pipeline.yaml")
    assert len(pipeline.jobs) == 3
    assert pipeline.report_formats == ("markdown", "json", "html")
    assert pipeline.jobs[0].plan_path.is_file()
    assert pipeline.jobs[0].source.path.is_file()
    assert pipeline.output_dir.name == "out"

def test_pipeline_missing_source_is_pipe_error(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(plan_text(), encoding="utf-8")
    pipeline_data = {
        "apiVersion": "samplelaw/v1",
        "kind": "Pipeline",
        "metadata": {"name": "demo-pipeline"},
        "jobs": [
            {
                "id": "one",
                "plan": plan_path.name,
                "source": {"type": "csv", "path": "missing.csv"},
            }
        ],
    }
    config = tmp_path / "pipeline.yaml"
    config.write_text(yaml.safe_dump(pipeline_data), encoding="utf-8")
    with pytest.raises(ConfigError) as captured:
        load_pipeline(config)
    assert captured.value.code == "PIPE-003"

def test_minimum_above_maximum_is_plan_011() -> None:
    with pytest.raises(ConfigError) as captured:
        load_plan_text(plan_text(min_per=5, max_per=3))
    assert captured.value.code == "PLAN-011"
    with pytest.raises(ConfigError) as captured:
        load_plan_text(plan_text(per_stratum=5, max_per=3))
    assert captured.value.code == "PLAN-011"
