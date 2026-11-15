from __future__ import annotations

import json
from pathlib import Path

import pytest

from samplelaw.report.model import utc_timestamp
from samplelaw.report.builder import build_report_document
from samplelaw.report.render_html import render_html
from samplelaw.report.render_json import render_json
from samplelaw.report.render_markdown import render_markdown
from samplelaw.report.writer import render_report, write_reports
from samplelaw.plan.sampling import execute_sampling
from samplelaw.runner import verify_sample
from samplelaw.config.loader import load_plan
from samplelaw.source.csv_reader import read_csv_dataset
from samplelaw.util.errors import ReportError
from samplelaw.verify.coverage import verify_coverage
from samplelaw.verify.deviance import detect_deviance
from samplelaw.verify.replay import verify_replay
from tests.helpers import EXAMPLES, GOLD

FIXED_TIME = "2026-11-14T00:00:00Z"

def _document():
    plan = load_plan(EXAMPLES / "plans" / "plan_basic.yaml")
    source = read_csv_dataset(EXAMPLES / "data" / "demo_members.csv")
    sample = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    return verify_sample(plan, source, sample, generated_at=FIXED_TIME)[-1]

def test_json_field_order_and_byte_stability() -> None:
    document = _document()
    first = render_json(document)
    second = render_json(document)
    assert first == second
    parsed = json.loads(first)
    assert list(parsed)[:6] == [
        "schema",
        "generated_at",
        "batch_id",
        "overall_status",
        "plan",
        "source",
    ]
    assert parsed["generated_at"] == FIXED_TIME
    assert parsed["replay"]["status"] == "MATCH"
    assert parsed["sample"]["rows"] == 16

def test_markdown_has_sections_and_lf() -> None:
    content = render_markdown(_document())
    assert "# 样律校准台核验报告" in content
    assert "## 重放核验" in content
    assert "## 代表性核验" in content
    assert "MATCH" in content
    assert "\r" not in content
    assert content.endswith("\n")

def test_html_is_inline_and_has_no_external_resources() -> None:
    content = render_html(_document())
    assert "<style>" in content
    assert "http://" not in content
    assert "https://" not in content
    assert "<script" not in content
    assert "<link" not in content
    assert 'src="' not in content
    assert "分层与抽样" in content

def test_write_reports_is_repeatable(tmp_path: Path) -> None:
    document = _document()
    first = write_reports(document, tmp_path, ("markdown", "json", "html"))
    bytes_first = {fmt: path.read_bytes() for fmt, path in first.items()}
    second = write_reports(document, tmp_path, ("markdown", "json", "html"))
    assert set(first) == {"markdown", "json", "html"}
    for fmt in first:
        assert first[fmt] == second[fmt]
        assert second[fmt].read_bytes() == bytes_first[fmt]
    assert json.loads(bytes_first["json"].decode("utf-8"))["batch_id"] == document.batch_id

def _deviance_document():
    plan = load_plan(EXAMPLES / "plans" / "plan_basic.yaml")
    source = read_csv_dataset(EXAMPLES / "data" / "demo_members.csv")
    sample = read_csv_dataset(GOLD / "plan_basic_sample.csv")
    result = detect_deviance(
        plan,
        [],
        {"strata": {"a": {"sample_rows": 10, "dimensions": {"tier": {"gold": 7, "silver": 2, "bronze": 1}}}}},
        {"strata": {"a": {"sample_rows": 10, "dimensions": {"tier": {"gold": 6, "silver": 3, "platinum": 1}}}}},
        "batch-demo-old",
    )
    return build_report_document(
        plan=plan,
        source=source,
        sampling=execute_sampling(source, plan),
        replay=verify_replay(plan, source, sample),
        coverage=verify_coverage(plan, source, sample),
        deviance=result,
        generated_at=FIXED_TIME,
        previous_batch_id="batch-demo-old",
    )

def test_report_exposes_domain_changes_and_drift_order() -> None:
    document = _deviance_document()
    data = document.to_dict()
    deviance = data["deviance"]
    assert list(deviance)[-2:] == ["drift_metrics", "domain_changes"]
    assert deviance["drift_metrics"][0]["common_values"] == ["gold", "silver"]
    assert deviance["drift_metrics"][0]["observed"] == 0.111111
    assert deviance["domain_changes"] == [
        {
            "stratum": "a",
            "dimension": "tier",
            "value": "bronze",
            "change": "added",
            "previous_share": None,
            "current_share": 0.1,
        },
        {
            "stratum": "a",
            "dimension": "tier",
            "value": "platinum",
            "change": "removed",
            "previous_share": 0.1,
            "current_share": None,
        },
    ]
    json_text = render_json(document)
    assert render_json(document) == json_text
    markdown = render_markdown(document)
    assert "### 漂移指标（共同取值域）" in markdown
    assert "### 取值域变化" in markdown
    assert "platinum" in markdown and "bronze" in markdown
    html = render_html(document)
    assert "取值域变化" in html and "platinum" in html

def test_unknown_report_format_and_timestamp_rules() -> None:
    document = _document()
    with pytest.raises(ReportError) as captured:
        render_report(document, "pdf")
    assert captured.value.code == "REPORT-001"
    assert utc_timestamp("2026-11-14T08:00:00+08:00") == FIXED_TIME
    with pytest.raises(ValueError):
        utc_timestamp("not-a-date")
