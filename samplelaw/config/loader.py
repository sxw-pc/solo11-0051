"""Load a SamplingPlan YAML document and validate its structure."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from samplelaw.config.parse_utils import (
    TOP_KEYS,
    as_bool,
    as_integer,
    as_mapping,
    as_ratio,
    as_string_list,
    as_text,
    parse_exclusions,
    parse_expected_levels,
    unknown_fields,
)
from samplelaw.config.schema import (
    API_VERSION,
    MODES,
    PLAN_KIND,
    SHORTFALL_POLICIES,
    DevianceSpec,
    PlanSpec,
    SamplingSpec,
    Thresholds,
)
from samplelaw.config.yamlutil import YamlDocument, _walk, raise_first, read_yaml_document
from samplelaw.plan.rules import validate_plan_semantics
from samplelaw.util.errors import ConfigError, ValidationIssue
from samplelaw.util.hashing import plan_fingerprint

def _build_plan(data: dict[str, Any], document: YamlDocument) -> PlanSpec:
    issues: list[ValidationIssue] = []
    unknown_fields(data, TOP_KEYS, "", issues)
    if as_text(data.get("apiVersion"), "apiVersion", issues) != API_VERSION:
        issues.append(ValidationIssue("PLAN-003", "apiVersion", f"expected {API_VERSION!r}"))
    if as_text(data.get("kind"), "kind", issues) != PLAN_KIND:
        issues.append(ValidationIssue("PLAN-003", "kind", f"expected {PLAN_KIND!r}"))
    metadata = as_mapping(data.get("metadata", {}), "metadata", issues)
    unknown_fields(metadata, {"name", "version", "description"}, "metadata", issues)
    name = as_text(metadata.get("name"), "metadata.name", issues)
    if "name" not in metadata:
        issues.append(ValidationIssue("PLAN-001", "metadata.name", "field is required"))
    version = as_integer(metadata.get("version"), "metadata.version", issues, default=1, minimum=1)
    description = as_text(metadata.get("description"), "metadata.description", issues)
    sampling_raw = as_mapping(data.get("sampling", {}), "sampling", issues)
    unknown_fields(
        sampling_raw,
        {
            "mode",
            "seed",
            "total_size",
            "per_stratum",
            "min_per_stratum",
            "max_per_stratum",
            "on_shortfall",
        },
        "sampling",
        issues,
    )
    mode = as_text(sampling_raw.get("mode"), "sampling.mode", issues).lower()
    if mode and mode not in MODES:
        issues.append(ValidationIssue("PLAN-004", "sampling.mode", f"unsupported mode: {mode}"))
    if "mode" not in sampling_raw:
        issues.append(ValidationIssue("PLAN-001", "sampling.mode", "field is required"))
    seed = as_integer(
        sampling_raw.get("seed"),
        "sampling.seed",
        issues,
        default=None,
        minimum=0,
        maximum=2**32 - 1,
    )
    if "seed" not in sampling_raw:
        issues.append(ValidationIssue("PLAN-001", "sampling.seed", "field is required"))
    total_size = as_integer(sampling_raw.get("total_size"), "sampling.total_size", issues, minimum=1)
    per_stratum = as_integer(sampling_raw.get("per_stratum"), "sampling.per_stratum", issues, minimum=1)
    minimum = as_integer(
        sampling_raw.get("min_per_stratum"),
        "sampling.min_per_stratum",
        issues,
        default=1,
        minimum=1,
    )
    maximum = as_integer(sampling_raw.get("max_per_stratum"), "sampling.max_per_stratum", issues, minimum=1)
    on_shortfall = as_text(sampling_raw.get("on_shortfall"), "sampling.on_shortfall", issues, default="warn").lower()
    if on_shortfall not in SHORTFALL_POLICIES:
        issues.append(ValidationIssue("PLAN-004", "sampling.on_shortfall", f"unsupported policy: {on_shortfall}"))
    strata_raw = as_mapping(data.get("strata", {}), "strata", issues)
    unknown_fields(strata_raw, {"keys", "rare_threshold", "expected_levels"}, "strata", issues)
    keys = as_string_list(strata_raw.get("keys"), "strata.keys", issues, required=True)
    if "keys" not in strata_raw:
        issues.append(ValidationIssue("PLAN-001", "strata.keys", "field is required"))
    if len(set(keys)) != len(keys):
        issues.append(ValidationIssue("PLAN-006", "strata.keys", "strata keys must be unique"))
    rare_threshold = as_ratio(
        strata_raw.get("rare_threshold"),
        "strata.rare_threshold",
        issues,
        default=0.05,
        positive=True,
    )
    expected_levels = parse_expected_levels(strata_raw.get("expected_levels"), "strata.expected_levels", keys, issues)
    exclusions = parse_exclusions(data.get("exclude"), issues)
    fingerprint_raw = as_mapping(data.get("fingerprint", {}), "fingerprint", issues)
    unknown_fields(fingerprint_raw, {"columns", "order_sensitive"}, "fingerprint", issues)
    fingerprint_columns = as_string_list(fingerprint_raw.get("columns"), "fingerprint.columns", issues)
    if fingerprint_raw.get("columns") is not None and not fingerprint_columns:
        issues.append(ValidationIssue("PLAN-005", "fingerprint.columns", "fingerprint.columns must not be empty"))
    if len(set(fingerprint_columns)) != len(fingerprint_columns):
        issues.append(ValidationIssue("PLAN-006", "fingerprint.columns", "columns must be unique"))
    order_sensitive = as_bool(
        fingerprint_raw.get("order_sensitive"),
        "fingerprint.order_sensitive",
        issues,
        default=False,
    )
    thresholds_raw = as_mapping(data.get("thresholds", {}), "thresholds", issues)
    unknown_fields(
        thresholds_raw,
        {"max_share_deviation", "max_weighted_share_deviation", "drift"},
        "thresholds",
        issues,
    )
    thresholds = Thresholds(
        max_share_deviation=as_ratio(
            thresholds_raw.get("max_share_deviation"),
            "thresholds.max_share_deviation",
            issues,
            default=0.15,
        ),
        max_weighted_share_deviation=as_ratio(
            thresholds_raw.get("max_weighted_share_deviation"),
            "thresholds.max_weighted_share_deviation",
            issues,
            default=0.10,
        ),
        drift=as_ratio(thresholds_raw.get("drift"), "thresholds.drift", issues, default=0.20),
    )
    deviance_raw = as_mapping(data.get("deviance", {}), "deviance", issues)
    unknown_fields(deviance_raw, {"dimensions", "min_baseline_rows"}, "deviance", issues)
    dimensions = as_string_list(deviance_raw.get("dimensions"), "deviance.dimensions", issues)
    if len(set(dimensions)) != len(dimensions):
        issues.append(ValidationIssue("PLAN-006", "deviance.dimensions", "dimensions must be unique"))
    min_baseline_rows = as_integer(
        deviance_raw.get("min_baseline_rows"),
        "deviance.min_baseline_rows",
        issues,
        default=1,
        minimum=1,
    )
    if issues:
        raise_first(issues, document)
    assert seed is not None and version is not None and minimum is not None
    assert min_baseline_rows is not None
    plan = PlanSpec(
        name=name,
        version=version,
        description=description,
        sampling=SamplingSpec(
            mode=mode,
            seed=seed,
            total_size=total_size,
            per_stratum=per_stratum,
            min_per_stratum=minimum,
            max_per_stratum=maximum,
            on_shortfall=on_shortfall,
        ),
        strata_keys=keys,
        expected_levels=expected_levels,
        rare_threshold=rare_threshold,
        exclusions=exclusions,
        fingerprint_columns=fingerprint_columns or None,
        order_sensitive=order_sensitive,
        thresholds=thresholds,
        deviance=DevianceSpec(dimensions=dimensions, min_baseline_rows=min_baseline_rows),
        origin=str(document.path),
    )
    semantic_issues = validate_plan_semantics(plan)
    if semantic_issues:
        raise_first(semantic_issues, document)
    return replace(plan, fingerprint=plan_fingerprint(plan.canonical()))

def load_plan(path: str | Path) -> PlanSpec:
    document = read_yaml_document(path)
    if not isinstance(document.data, dict):
        raise_first([ValidationIssue("PLAN-002", "", "plan document must be a mapping")], document)
    return _build_plan(document.data, document)

def load_plan_text(text: str, origin: str = "<memory>") -> PlanSpec:
    """Load a plan from text; useful for focused tests and embedding."""
    try:
        data = yaml.safe_load(text)
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        raise ConfigError("PLAN-012", f"invalid YAML: {exc}", path=origin, line=line) from exc
    lines: dict[str, int] = {}
    if root is not None:
        _walk(root, "", lines)
    document = YamlDocument(data=data, lines=lines, path=Path(origin))
    if not isinstance(data, dict):
        raise_first([ValidationIssue("PLAN-002", "", "plan must be a mapping")], document)
    return _build_plan(data, document)
