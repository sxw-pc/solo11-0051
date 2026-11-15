"""Static legality rules for a loaded sampling plan.

These checks are intentionally about the plan alone. Whether a column exists
or a stratum is too small depends on data and belongs to runtime verification.
"""

from __future__ import annotations

from samplelaw.config.schema import EXCLUSION_PLACEMENTS, MODES, PlanSpec, SHORTFALL_POLICIES
from samplelaw.util.errors import ValidationIssue

def validate_plan_semantics(plan: PlanSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    sampling = plan.sampling
    if sampling.mode not in MODES:
        issues.append(ValidationIssue("PLAN-004", "sampling.mode", f"unsupported sampling mode: {sampling.mode}"))
    if sampling.on_shortfall not in SHORTFALL_POLICIES:
        issues.append(
            ValidationIssue(
                "PLAN-004",
                "sampling.on_shortfall",
                f"on_shortfall must be one of {SHORTFALL_POLICIES}",
            )
        )
    if plan.exclusions_placement not in EXCLUSION_PLACEMENTS:
        issues.append(
            ValidationIssue(
                "PLAN-004",
                "exclusions_placement",
                f"exclusions_placement must be one of {EXCLUSION_PLACEMENTS}",
            )
        )
    if sampling.mode == "fixed":
        if sampling.per_stratum is None:
            issues.append(ValidationIssue("PLAN-001", "sampling.per_stratum", "fixed mode requires per_stratum"))
        if sampling.total_size is not None:
            issues.append(
                ValidationIssue(
                    "PLAN-008",
                    "sampling.total_size",
                    "total_size is mutually exclusive with fixed mode",
                )
            )
    else:
        if sampling.total_size is None:
            issues.append(
                ValidationIssue(
                    "PLAN-001",
                    "sampling.total_size",
                    f"{sampling.mode} mode requires total_size",
                )
            )
        if sampling.per_stratum is not None:
            issues.append(
                ValidationIssue(
                    "PLAN-008",
                    "sampling.per_stratum",
                    "per_stratum is only valid in fixed mode",
                )
            )
    if sampling.min_per_stratum < 1:
        issues.append(ValidationIssue("PLAN-005", "sampling.min_per_stratum", "min_per_stratum must be at least 1"))
    if sampling.max_per_stratum is not None:
        if sampling.max_per_stratum < 1:
            issues.append(ValidationIssue("PLAN-005", "sampling.max_per_stratum", "max_per_stratum must be at least 1"))
        if sampling.max_per_stratum < sampling.min_per_stratum:
            issues.append(
                ValidationIssue(
                    "PLAN-011",
                    "sampling.max_per_stratum",
                    "max_per_stratum must be greater than or equal to min_per_stratum",
                )
            )
        if (
            sampling.mode == "fixed"
            and sampling.per_stratum is not None
            and sampling.per_stratum > sampling.max_per_stratum
        ):
            issues.append(
                ValidationIssue(
                    "PLAN-011",
                    "sampling.per_stratum",
                    "per_stratum must not exceed max_per_stratum",
                )
            )
    if (
        sampling.mode == "fixed"
        and sampling.per_stratum is not None
        and sampling.per_stratum < sampling.min_per_stratum
    ):
        issues.append(
            ValidationIssue(
                "PLAN-011",
                "sampling.per_stratum",
                "per_stratum must not be below min_per_stratum",
            )
        )
    if sampling.total_size is not None and sampling.total_size < 1:
        issues.append(ValidationIssue("PLAN-005", "sampling.total_size", "total_size must be positive"))
    if not plan.strata_keys:
        issues.append(ValidationIssue("PLAN-005", "strata.keys", "at least one key is required"))
    if len(set(plan.strata_keys)) != len(plan.strata_keys):
        issues.append(ValidationIssue("PLAN-006", "strata.keys", "strata keys must be unique"))
    if not 0 < plan.rare_threshold <= 1:
        issues.append(ValidationIssue("PLAN-007", "strata.rare_threshold", "rare_threshold must be in (0, 1]"))
    if len(set(plan.expected_levels)) != len(plan.expected_levels):
        issues.append(ValidationIssue("PLAN-006", "strata.expected_levels", "expected levels must be unique"))
    for index, level in enumerate(plan.expected_levels):
        if len(level) != len(plan.strata_keys):
            issues.append(
                ValidationIssue(
                    "PLAN-002",
                    f"strata.expected_levels[{index}]",
                    "expected level must have one value per strata key",
                )
            )
    if plan.fingerprint_columns is not None:
        if not plan.fingerprint_columns:
            issues.append(
                ValidationIssue(
                    "PLAN-005",
                    "fingerprint.columns",
                    "fingerprint.columns must not be empty when provided",
                )
            )
        if len(set(plan.fingerprint_columns)) != len(plan.fingerprint_columns):
            issues.append(
                ValidationIssue(
                    "PLAN-006",
                    "fingerprint.columns",
                    "fingerprint columns must be unique",
                )
            )
    if len(set(plan.deviance.dimensions)) != len(plan.deviance.dimensions):
        issues.append(ValidationIssue("PLAN-006", "deviance.dimensions", "deviance dimensions must be unique"))
    if plan.deviance.min_baseline_rows < 1:
        issues.append(
            ValidationIssue(
                "PLAN-005",
                "deviance.min_baseline_rows",
                "min_baseline_rows must be at least 1",
            )
        )
    for name, value in (
        ("thresholds.max_share_deviation", plan.thresholds.max_share_deviation),
        ("thresholds.max_weighted_share_deviation", plan.thresholds.max_weighted_share_deviation),
        ("thresholds.drift", plan.thresholds.drift),
    ):
        if not 0 <= value <= 1:
            issues.append(ValidationIssue("PLAN-007", name, "threshold must be in [0, 1]"))
    return issues

def assert_plan_legal(plan: PlanSpec) -> None:
    issues = validate_plan_semantics(plan)
    if issues:
        first = issues[0]
        from samplelaw.util.errors import ConfigError
        raise ConfigError(first.code, first.message, path=first.path, line=first.line)
