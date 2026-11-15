"""Self-contained HTML report: inline CSS, no scripts and no external assets."""

from __future__ import annotations

from html import escape

from samplelaw.report.model import ReportDocument, composition_table_rows

_CSS = """
:root { color-scheme: light; --ink:#19232d; --muted:#5c6b78; --line:#d7dee5;
  --bg:#f4f7f9; --card:#ffffff; --accent:#176f5e; --warn:#8a5a00; --fail:#9d2a2a; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:var(--bg); color:var(--ink);
  font:14px/1.55 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
main { max-width:1120px; margin:0 auto; }
header { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:24px 28px; margin-bottom:20px; }
h1 { margin:0 0 8px; font-size:26px; letter-spacing:.01em; }
h2 { margin:28px 0 10px; font-size:18px; }
p.meta { margin:0; color:var(--muted); }
.badge { display:inline-block; padding:3px 10px; border-radius:999px;
  font-weight:600; font-size:12px; background:#e7efed; color:var(--accent); }
.badge.WARN { background:#fff1d6; color:var(--warn); }
.badge.FAIL { background:#fbe4e4; color:var(--fail); }
section { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:18px 22px; margin-bottom:18px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:8px 9px; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; background:#f8fafb; }
tr:last-child td { border-bottom:0; }
code { font-family: Consolas, "SFMono-Regular", monospace; font-size:12px; }
.muted { color:var(--muted); }
.kv { width:auto; min-width:62%; }
@media (max-width:700px) { body { padding:14px; } section { overflow-x:auto; } }
"""

def _e(value: object) -> str:
    return escape(str(value), quote=True)

def _table(headers: list[str], rows: list[list[object]], css_class: str = "") -> str:
    head = "".join(f"<th>{_e(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    klass = f' class="{_e(css_class)}"' if css_class else ""
    return f"<table{klass}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

def render_html(document: ReportDocument) -> str:
    data = document.to_dict()
    status_class = document.overall_status if document.overall_status in {"WARN", "FAIL"} else ""
    coverage = document.coverage
    deviance = document.deviance
    replay = document.replay
    alert_rows = [
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
    ]
    warning_rows = [[item["code"], item["stratum"], item["message"]] for item in document.sampling_warnings]
    composition_rows = composition_table_rows(deviance.get("composition"))
    stratum_rows = [
        [
            item.label,
            item.source_rows,
            f"{item.source_share:.4f}",
            item.target_rows,
            item.sample_rows,
            f"{item.sample_share:.4f}",
            f"{item.share_deviation:.4f}",
            "yes" if item.rare else "no",
            "yes" if item.empty else "no",
        ]
        for item in document.strata
    ]
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>样律校准台核验报告</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body><main>",
        "<header>",
        "<h1>样律校准台核验报告</h1>",
        f'<p class="meta">批次 <code>{_e(document.batch_id)}</code> · '
        f'总体结论 <span class="badge {_e(status_class)}">{_e(document.overall_status)}</span></p>',
        "</header>",
        "<section><h2>批次信息</h2>",
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
            "kv",
        ),
        "</section>",
        "<section><h2>分层与抽样</h2>",
        _table(
            ["层", "源行数", "源占比", "目标量", "实得量", "样本占比", "占比偏差", "稀有层", "空层"],
            stratum_rows,
        ),
        "</section>",
        "<section><h2>重放核验</h2>",
        _table(
            ["项目", "值"],
            [
                ["结论", replay["status"]],
                ["说明", replay["message"]],
                ["期望行数", replay["expected_rows"]],
                ["实际行数", replay["actual_rows"]],
                ["缺失行数", replay["missing_rows"]],
                ["多出行数", replay["extra_rows"]],
                ["顺序不一致", "yes" if replay["order_mismatch"] else "no"],
            ],
            "kv",
        ),
        "</section>",
        "<section><h2>代表性核验</h2>",
        _table(
            ["指标", "值"],
            [
                ["结论", coverage["status"]],
                ["分层覆盖率", f"{coverage['coverage_rate']:.4f}"],
                ["稀有层", coverage["rare_total"]],
                ["稀有层命中", coverage["rare_hit"]],
                ["稀有层漏抽", ", ".join(coverage["rare_missed"]) or "none"],
                ["最大占比偏差", f"{coverage['max_abs_share_deviation']:.4f}"],
                ["加权占比偏差", f"{coverage['weighted_abs_share_deviation']:.4f}"],
                ["最大偏差阈值", f"{coverage['max_share_threshold']:.4f}"],
                ["加权偏差阈值", f"{coverage['weighted_share_threshold']:.4f}"],
            ],
            "kv",
        ),
        "</section>",
        "<section><h2>执行偏离</h2>",
        _table(
            ["项目", "值"],
            [
                ["结论", deviance["status"]],
                ["基线批次", deviance["baseline_batch_id"] or "none"],
                ["漂移阈值", f"{deviance['threshold']:.4f}"],
                ["告警数", len(deviance["alerts"])],
            ],
            "kv",
        ),
        "<h3>层内构成快照</h3>",
        _table(["层", "维度", "取值", "行数", "层样本行数"], composition_rows)
        if composition_rows
        else '<p class="muted">无构成快照。</p>',
        "</section>",
        "<section><h2>告警明细</h2>",
        _table(["类型", "代码", "层", "指标", "阈值", "实测值", "说明"], alert_rows)
        if alert_rows
        else '<p class="muted">无告警。</p>',
        "</section>",
        "<section><h2>抽样告警</h2>",
        _table(["代码", "层", "说明"], warning_rows) if warning_rows else '<p class="muted">无抽样告警。</p>',
        "</section>",
        "</main></body></html>",
        "",
    ]
    return "\n".join(parts)
