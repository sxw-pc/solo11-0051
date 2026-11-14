"""Human-readable Markdown report with deterministic table ordering."""

from __future__ import annotations

from samplelaw.report.model import ReportDocument

def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")

def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    return "\n".join(lines)

def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

def render_markdown(document: ReportDocument) -> str:
    data = document.to_dict()
    lines: list[str] = [
        "# 样律校准台核验报告",
        "",
        f"> 批次 `{document.batch_id}` · 总体结论 **{document.overall_status}**",
        "",
        "## 批次信息",
        "",
        _table(
            ["项目", "值"],
            [
                ["生成时间", document.generated_at],
                ["方案名称", document.plan_name],
                ["方案版本", document.plan_version],
                ["方案指纹", document.plan_fingerprint],
                ["源数据", f"{document.source_name} ({document.source_kind})"],
                ["源数据指纹", document.source_fingerprint],
                ["样本指纹", document.sample_fingerprint],
                ["抽样模式", document.mode],
                ["随机种子", document.seed],
                ["目标样本量", document.requested_total],
                ["实际样本量", document.sample_total],
                ["排除行数", document.excluded_rows],
                ["可抽行数", document.eligible_rows],
            ],
        ),
        "",
        "## 分层与抽样",
        "",
        _table(
            [
                "层",
                "源行数",
                "源占比",
                "目标量",
                "实得量",
                "样本占比",
                "占比偏差",
                "稀有层",
                "空层",
            ],
            [
                [
                    item.label,
                    item.source_rows,
                    f"{item.source_share:.4f}",
                    item.target_rows,
                    item.sample_rows,
                    f"{item.sample_share:.4f}",
                    f"{item.share_deviation:.4f}",
                    _yes_no(item.rare),
                    _yes_no(item.empty),
                ]
                for item in document.strata
            ],
        ),
        "",
        "## 重放核验",
        "",
        _table(
            ["项目", "值"],
            [
                ["结论", document.replay["status"]],
                ["说明", document.replay["message"]],
                ["期望行数", document.replay["expected_rows"]],
                ["实际行数", document.replay["actual_rows"]],
                ["缺失行数", document.replay["missing_rows"]],
                ["多出行数", document.replay["extra_rows"]],
                ["顺序不一致", _yes_no(bool(document.replay["order_mismatch"]))],
            ],
        ),
        "",
        "## 代表性核验",
        "",
        _table(
            ["指标", "值"],
            [
                ["结论", document.coverage["status"]],
                ["分层覆盖率", f"{document.coverage['coverage_rate']:.4f}"],
                ["稀有层", document.coverage["rare_total"]],
                ["稀有层命中", document.coverage["rare_hit"]],
                ["稀有层漏抽", ", ".join(document.coverage["rare_missed"]) or "none"],
                ["最大占比偏差", f"{document.coverage['max_abs_share_deviation']:.4f}"],
                [
                    "加权占比偏差",
                    f"{document.coverage['weighted_abs_share_deviation']:.4f}",
                ],
                [
                    "最大偏差阈值",
                    f"{document.coverage['max_share_threshold']:.4f}",
                ],
                [
                    "加权偏差阈值",
                    f"{document.coverage['weighted_share_threshold']:.4f}",
                ],
            ],
        ),
        "",
        "## 执行偏离",
        "",
        _table(
            ["项目", "值"],
            [
                ["结论", document.deviance["status"]],
                ["基线批次", document.deviance["baseline_batch_id"] or "none"],
                ["漂移阈值", f"{document.deviance['threshold']:.4f}"],
                ["告警数", len(document.deviance["alerts"])],
            ],
        ),
        "",
        "## 告警明细",
        "",
    ]
    if data["alerts"]:
        lines.append(
            _table(
                ["类型", "代码", "层", "指标", "阈值", "实测值", "说明"],
                [
                    [
                        item.get("type", ""),
                        item.get("code", ""),
                        item.get("stratum", ""),
                        item.get("metric", ""),
                        item.get("threshold", ""),
                        item.get("observed", ""),
                        item.get("message", ""),
                    ]
                    for item in data["alerts"]
                ],
            )
        )
    else:
        lines.append("无告警。")
    lines.extend(["", "## 抽样告警", ""])
    if document.sampling_warnings:
        lines.append(
            _table(
                ["代码", "层", "说明"],
                [[item["code"], item["stratum"], item["message"]] for item in document.sampling_warnings],
            )
        )
    else:
        lines.append("无抽样告警。")
    lines.append("")
    return "\n".join(lines)
