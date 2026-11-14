"""YAML reading, source-line indexing and validation error helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from samplelaw.util.errors import ConfigError, ValidationIssue

@dataclass(frozen=True)
class YamlDocument:
    data: Any
    lines: dict[str, int]
    path: Path

def _walk(node: yaml.Node, path: str, lines: dict[str, int]) -> None:
    lines[path] = node.start_mark.line + 1
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = str(key_node.value)
            child = f"{path}.{key}" if path else key
            _walk(value_node, child, lines)
    elif isinstance(node, yaml.SequenceNode):
        for index, child_node in enumerate(node.value):
            _walk(child_node, f"{path}[{index}]", lines)

def read_yaml_document(
    path: str | Path,
    *,
    missing_code: str = "PLAN-014",
    parse_code: str = "PLAN-012",
) -> YamlDocument:
    resolved = Path(path)
    if not resolved.is_file():
        raise ConfigError(missing_code, f"configuration file not found: {resolved}", path=str(resolved))
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError("PLAN-014", f"cannot read configuration file: {exc}", path=str(resolved)) from exc
    try:
        data = yaml.safe_load(text)
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        raise ConfigError(parse_code, f"invalid YAML: {exc}", path=str(resolved), line=line) from exc
    lines: dict[str, int] = {}
    if root is not None:
        _walk(root, "", lines)
    return YamlDocument(data=data, lines=lines, path=resolved)

def annotate_issue(issue: ValidationIssue, document: YamlDocument) -> ValidationIssue:
    if issue.line is not None:
        return issue
    line = document.lines.get(issue.path)
    if line is None and issue.path:
        parts = issue.path.split(".")
        while parts and line is None:
            parts.pop()
            line = document.lines.get(".".join(parts))
    return issue.with_line(line or 1)

def raise_first(issues: list[ValidationIssue], document: YamlDocument) -> None:
    if not issues:
        return
    ordered = sorted(issues, key=lambda item: (item.line or 10**9, item.path, item.code))
    issue = annotate_issue(ordered[0], document)
    raise ConfigError(issue.code, issue.message, path=issue.path, line=issue.line)
