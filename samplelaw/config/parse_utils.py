"""Reusable structural validation helpers for plan and pipeline YAML."""

from __future__ import annotations

from typing import Any

from samplelaw.config.schema import EXCLUSION_OPERATORS, Exclusion
from samplelaw.util.errors import ValidationIssue

TOP_KEYS = {
    "apiVersion",
    "kind",
    "metadata",
    "sampling",
    "strata",
    "exclude",
    "fingerprint",
    "thresholds",
    "deviance",
}

def unknown_fields(mapping: Any, allowed: set[str], path: str, issues: list[ValidationIssue]) -> None:
    if isinstance(mapping, dict):
        for key in mapping:
            if key not in allowed:
                child = f"{path}.{key}" if path else str(key)
                issues.append(ValidationIssue("PLAN-013", child, f"unknown field: {key}"))

def as_mapping(value: Any, path: str, issues: list[ValidationIssue]) -> dict[str, Any]:
    if not isinstance(value, dict):
        issues.append(ValidationIssue("PLAN-002", path, "expected a mapping"))
        return {}
    return value

def as_text(value: Any, path: str, issues: list[ValidationIssue], default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue("PLAN-002", path, "expected a non-empty string"))
        return default
    return value.strip()

def as_integer(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    default: int | None = None,
    minimum: int = 0,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(ValidationIssue("PLAN-002", path, "expected an integer"))
        return default
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"[{minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        issues.append(ValidationIssue("PLAN-005", path, f"value must be {bound}"))
        return default
    return value

def as_ratio(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    default: float,
    *,
    positive: bool = False,
) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(ValidationIssue("PLAN-002", path, "expected a number"))
        return default
    number = float(value)
    lower_ok = number > 0 if positive else number >= 0
    if not lower_ok or number > 1:
        message = "value must be in (0, 1]" if positive else "value must be in [0, 1]"
        issues.append(ValidationIssue("PLAN-007", path, message))
        return default
    return number

def as_bool(value: Any, path: str, issues: list[ValidationIssue], default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        issues.append(ValidationIssue("PLAN-002", path, "expected a boolean"))
        return default
    return value

def as_string_list(
    value: Any,
    path: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if value is None:
        if required:
            issues.append(ValidationIssue("PLAN-001", path, "field is required"))
        return default
    if not isinstance(value, list):
        issues.append(ValidationIssue("PLAN-002", path, "expected a list of strings"))
        return default
    result: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            issues.append(ValidationIssue("PLAN-002", item_path, "expected a non-empty string"))
            continue
        result.append(item.strip())
    if required and not result:
        issues.append(ValidationIssue("PLAN-005", path, "at least one item is required"))
    return tuple(result)

def parse_expected_levels(
    value: Any, path: str, keys: tuple[str, ...], issues: list[ValidationIssue]
) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        issues.append(ValidationIssue("PLAN-002", path, "expected a list of levels"))
        return ()
    levels: list[tuple[str, ...]] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, dict):
            if set(item) != set(keys):
                issues.append(
                    ValidationIssue(
                        "PLAN-002",
                        item_path,
                        "mapping level must contain exactly the declared strata keys",
                    )
                )
                continue
            values = tuple(str(item[key]).strip() for key in keys)
        elif isinstance(item, list):
            if len(item) != len(keys):
                issues.append(
                    ValidationIssue(
                        "PLAN-002",
                        item_path,
                        "expected level must have one value per strata key",
                    )
                )
                continue
            values = tuple(str(part).strip() for part in item)
        elif isinstance(item, str) and len(keys) == 1:
            values = (item.strip(),)
        else:
            issues.append(ValidationIssue("PLAN-002", item_path, "invalid expected level"))
            continue
        if any(not part for part in values):
            issues.append(ValidationIssue("PLAN-002", item_path, "level values must be non-empty"))
            continue
        levels.append(values)
    if len(set(levels)) != len(levels):
        issues.append(ValidationIssue("PLAN-006", path, "expected levels must be unique"))
    return tuple(levels)

def parse_exclusions(value: Any, issues: list[ValidationIssue]) -> tuple[Exclusion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        issues.append(ValidationIssue("PLAN-002", "exclude", "expected a list"))
        return ()
    result: list[Exclusion] = []
    for index, item in enumerate(value):
        path = f"exclude[{index}]"
        if not isinstance(item, dict):
            issues.append(ValidationIssue("PLAN-009", path, "exclusion must be a mapping"))
            continue
        unknown_fields(item, {"column", "operator", "value"}, path, issues)
        column = as_text(item.get("column"), f"{path}.column", issues)
        operator = as_text(item.get("operator"), f"{path}.operator", issues, default="eq").lower()
        if operator not in EXCLUSION_OPERATORS:
            issues.append(ValidationIssue("PLAN-004", f"{path}.operator", f"unsupported operator: {operator}"))
            continue
        raw_value = item.get("value")
        if operator in ("eq", "neq"):
            if raw_value is None or isinstance(raw_value, (dict, list)):
                issues.append(ValidationIssue("PLAN-009", f"{path}.value", "expected a scalar value"))
                continue
            normalized: str | tuple[str, ...] = str(raw_value)
        else:
            if not isinstance(raw_value, list) or not raw_value:
                issues.append(ValidationIssue("PLAN-009", f"{path}.value", "expected a non-empty list"))
                continue
            normalized = tuple(str(part) for part in raw_value)
        if column:
            result.append(Exclusion(column=column, operator=operator, value=normalized))
    return tuple(result)
