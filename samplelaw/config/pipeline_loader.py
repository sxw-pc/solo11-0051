"""Load the multi-job pipeline configuration used by ``samplelaw run``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from samplelaw.config.schema import (
    API_VERSION,
    PIPELINE_KIND,
    REPORT_FORMATS,
    SOURCE_TYPES,
    JobSpec,
    PipelineSpec,
    SourceSpec,
)
from samplelaw.config.yamlutil import YamlDocument, raise_first, read_yaml_document
from samplelaw.util.errors import ValidationIssue

_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

def _unknown(mapping: dict[str, Any], allowed: set[str], path: str, issues: list[ValidationIssue]) -> None:
    for key in mapping:
        if key not in allowed:
            child = f"{path}.{key}" if path else str(key)
            issues.append(ValidationIssue("PIPE-007", child, f"unknown field: {key}"))

def _mapping(value: Any, path: str, issues: list[ValidationIssue]) -> dict[str, Any]:
    if not isinstance(value, dict):
        issues.append(ValidationIssue("PIPE-002", path, "expected a mapping"))
        return {}
    return value

def _text(value: Any, path: str, issues: list[ValidationIssue], *, required: bool = True) -> str:
    if value is None:
        if required:
            issues.append(ValidationIssue("PIPE-001", path, "field is required"))
        return ""
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue("PIPE-002", path, "expected a non-empty string"))
        return ""
    return value.strip()

def _resolve(base: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()

def _build_pipeline(data: dict[str, Any], document: YamlDocument) -> PipelineSpec:
    issues: list[ValidationIssue] = []
    _unknown(data, {"apiVersion", "kind", "metadata", "defaults", "jobs"}, "", issues)
    if _text(data.get("apiVersion"), "apiVersion", issues) != API_VERSION:
        issues.append(ValidationIssue("PIPE-004", "apiVersion", f"expected {API_VERSION!r}"))
    if _text(data.get("kind"), "kind", issues) != PIPELINE_KIND:
        issues.append(ValidationIssue("PIPE-004", "kind", f"expected {PIPELINE_KIND!r}"))
    metadata = _mapping(data.get("metadata", {}), "metadata", issues)
    _unknown(metadata, {"name", "description"}, "metadata", issues)
    name = _text(metadata.get("name"), "metadata.name", issues)
    description = _text(metadata.get("description"), "metadata.description", issues, required=False)
    base = document.path.parent
    defaults = _mapping(data.get("defaults", {}), "defaults", issues)
    _unknown(defaults, {"output_dir", "ledger", "report_formats"}, "defaults", issues)
    output_raw = _text(defaults.get("output_dir"), "defaults.output_dir", issues, required=False) or "out"
    ledger_raw = _text(defaults.get("ledger"), "defaults.ledger", issues, required=False)
    output_dir = _resolve(base, output_raw)
    ledger_path = _resolve(base, ledger_raw) if ledger_raw else output_dir / "ledger.sqlite"
    formats_raw = defaults.get("report_formats")
    if formats_raw is None:
        report_formats = REPORT_FORMATS
    elif not isinstance(formats_raw, list) or not formats_raw:
        issues.append(ValidationIssue("PIPE-005", "defaults.report_formats", "expected a non-empty list"))
        report_formats = REPORT_FORMATS
    else:
        normalized: list[str] = []
        for index, item in enumerate(formats_raw):
            path = f"defaults.report_formats[{index}]"
            if not isinstance(item, str) or item not in REPORT_FORMATS:
                issues.append(ValidationIssue("PIPE-004", path, f"report format must be one of {REPORT_FORMATS}"))
                continue
            if item in normalized:
                issues.append(ValidationIssue("PIPE-006", path, f"duplicate report format: {item}"))
            normalized.append(item)
        report_formats = tuple(normalized or REPORT_FORMATS)
    jobs_raw = data.get("jobs")
    jobs: list[JobSpec] = []
    if jobs_raw is None:
        issues.append(ValidationIssue("PIPE-001", "jobs", "field is required"))
        jobs_raw = []
    if not isinstance(jobs_raw, list):
        issues.append(ValidationIssue("PIPE-002", "jobs", "expected a list"))
        jobs_raw = []
    if not jobs_raw:
        issues.append(ValidationIssue("PIPE-005", "jobs", "at least one job is required"))
    seen_ids: set[str] = set()
    for index, raw_job in enumerate(jobs_raw):
        path = f"jobs[{index}]"
        if not isinstance(raw_job, dict):
            issues.append(ValidationIssue("PIPE-002", path, "job must be a mapping"))
            continue
        _unknown(raw_job, {"id", "plan", "source"}, path, issues)
        job_id = _text(raw_job.get("id"), f"{path}.id", issues)
        if job_id and not _JOB_ID.fullmatch(job_id):
            issues.append(ValidationIssue("PIPE-005", f"{path}.id", "id must match letters, digits, '_' or '-'"))
        if job_id in seen_ids:
            issues.append(ValidationIssue("PIPE-006", f"{path}.id", f"duplicate job id: {job_id}"))
        seen_ids.add(job_id)
        plan_raw = _text(raw_job.get("plan"), f"{path}.plan", issues)
        plan_path = _resolve(base, plan_raw) if plan_raw else base / "__missing_plan__"
        if plan_raw and not plan_path.is_file():
            issues.append(ValidationIssue("PIPE-003", f"{path}.plan", f"plan file not found: {plan_path}"))
        source = _parse_source(raw_job.get("source"), f"{path}.source", base, issues)
        jobs.append(JobSpec(job_id=job_id, plan_path=plan_path, source=source))
    if issues:
        raise_first(issues, document)
    return PipelineSpec(
        name=name,
        description=description,
        output_dir=output_dir,
        ledger_path=ledger_path,
        report_formats=report_formats,
        jobs=tuple(jobs),
        config_path=document.path,
    )

def _parse_source(value: Any, path: str, base: Path, issues: list[ValidationIssue]) -> SourceSpec:
    raw = _mapping(value, path, issues)
    _unknown(raw, {"type", "path", "table", "encoding", "name"}, path, issues)
    kind = _text(raw.get("type"), f"{path}.type", issues).lower()
    if kind and kind not in SOURCE_TYPES:
        issues.append(ValidationIssue("PIPE-004", f"{path}.type", f"type must be one of {SOURCE_TYPES}"))
    path_raw = _text(raw.get("path"), f"{path}.path", issues)
    source_path = _resolve(base, path_raw) if path_raw else base / "__missing_source__"
    if path_raw and not source_path.is_file():
        issues.append(ValidationIssue("PIPE-003", f"{path}.path", f"source file not found: {source_path}"))
    table = _text(raw.get("table"), f"{path}.table", issues, required=False) or None
    if kind == "sqlite" and not table:
        issues.append(ValidationIssue("PIPE-001", f"{path}.table", "sqlite source requires table"))
    if kind == "csv" and table:
        issues.append(ValidationIssue("PIPE-005", f"{path}.table", "table is only valid for sqlite"))
    encoding = _text(raw.get("encoding"), f"{path}.encoding", issues, required=False) or None
    source_name = _text(raw.get("name"), f"{path}.name", issues, required=False)
    return SourceSpec(
        kind=kind,
        path=source_path,
        table=table,
        encoding=encoding,
        name=source_name or source_path.name,
    )

def load_pipeline(path: str | Path) -> PipelineSpec:
    document = read_yaml_document(path, missing_code="PIPE-003", parse_code="PIPE-008")
    if not isinstance(document.data, dict):
        raise_first([ValidationIssue("PIPE-002", "", "pipeline document must be a mapping")], document)
    return _build_pipeline(document.data, document)
