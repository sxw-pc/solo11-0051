# 样律校准台（samplelaw）

**把「抽样方案是否合法、样本是否达标、执行是否偏离」这三件事收敛成一套可声明、可复现、可审计的本地核验流水线。**

samplelaw 面向从本地 CSV / SQLite 数据中抽样做评审、回归、对外演示、训练集构造或质量抽检的小团队。它不替用户决定业务目标，而是把抽样方案写成声明式 YAML，再用同一套规则重算、比对和留痕。

## 是 / 不是

**是：**

- 抽样方案的声明式定义与静态校验引擎。
- 抽样结果的确定性重放核验器：相同种子、规则与源数据，应得到同一批行。
- 代表性核验器：分层覆盖率、每层样本量达成、稀有层缺失、空层与占比偏差。
- 「方案 - 数据 - 样本」批处理工具，带本地 SQLite 台账和可归档的 Markdown / JSON / HTML 报告。

**不是：**

- 不是采集器、爬虫或外部数据源连接器。
- 不是通用统计推断库，不做置信区间、假设检验、方差分析或 Bootstrap。
- 不是 Web 后台、看板或仪表盘。
- 不是数据库同步、数仓版本或时间旅行工具。
- 不做脱敏、去重、主数据归并、字段量纲契约或指标口径比对。
- 不向任何远端系统写入数据；本地 SQLite 台账是唯一落库形态。

## 使用边界与合规声明

- 只对**本人拥有或已获明确授权**的本地数据使用。不要用它处理来源不明的数据。
- 运行期完全离线：不发起网络请求，不解析远端地址，不加载 CDN 字体、脚本或图片。
- 源 CSV / SQLite 只读；工具只写 `--config` 中声明的输出目录和本地 SQLite 台账。
- 仓库内所有人员、组织、站点、订单和事件均为 `demo-*` 合成数据，不含真实密钥、真实公司名、真实域名或真实可达目标。
- HTML 报告只含内联 CSS，可双击离线打开。可选 Flask 预览仅监听 `127.0.0.1`，只读 GET，不提供上传或写接口。
- 详细边界见 [docs/COMPLIANCE.md](docs/COMPLIANCE.md)。

## 快速开始

依赖：Python 3.10+、PyYAML、pytest；Flask 仅用于可选预览。

```bash
# 1. 安装（离线环境可跳过，直接在仓库根目录用已安装的 PyYAML/pytest 运行）
python -m pip install -e ".[dev]"

# 2. 生成合成示例数据（仓库已附带同一批数据，可安全重建）
python examples/generate_data.py

# 3. 跑完整流程：三次抽样、重放、覆盖/偏离核验、三态报告、SQLite 台账
python -m samplelaw run --config examples/pipeline.yaml

# 4. 跑测试（离线、无额外配置）
python -m pytest -q
```

全流程默认产物在 `out/`：

- `out/samples/<job>.csv`：确定性样本。
- `out/reports/<job>/report.{md,json,html}`：三态报告。
- `out/ledger.sqlite`：批次台账。

一次 `run` 是一个要么整体成立、要么整体不成立的业务单元：每个 job 先把样本写入带 job 与批次标识的临时文件（`out/samples/<job>.csv.<pid>.tmp`），回读该文件并按方案声明的指纹列顺序重算指纹，与内存抽样结果不一致即以 `SAMP-004` 中止；全部 job 核验通过后，台账行在同一个 SQLite 事务内写入、临时样本再原子替换正式路径。任一环节失败都会回滚本批的台账行、临时文件与已替换的正式样本，不会出现「有样本无台账」或「有台账无样本」，重跑成功时 `batch_id` 与干净运行完全一致。

单样本核验与台账查询：

```bash
python -m samplelaw verify --plan examples/plans/plan_basic.yaml \
  --source examples/data/demo_members.csv \
  --sample fixtures/gold_samples/plan_basic_sample.csv --format markdown

python -m samplelaw inspect --ledger out/ledger.sqlite --limit 20
```

可选本地只读预览（未安装 Flask 不影响 CLI 与测试）：

```bash
python -m pip install -e ".[preview]"
python -m samplelaw preview --dir out/reports
# 打开 127.0.0.1:8765，只读查看已生成 HTML
```

## 核心概念

| 概念 | 含义 |
| --- | --- |
| 方案（Plan） | 声明分层键、抽样模式、种子、样本量上下界、排除条款、阈值和指纹列。加载后计算 sha256 方案指纹。 |
| 层（Stratum） | 由一个或多个分层键的值组成的组合，例如 `region + tier`。层名按 `a \| b` 形式稳定排序显示。 |
| 稀有层 | 占可抽源数据比例低于 `strata.rare_threshold` 的层。稀有层被抽到是代表性达标的重要信号。 |
| 空层 | 方案 `expected_levels` 中声明但源数据没有可抽行的层；核验时单列告警，不静默忽略。 |
| 样本指纹 | 按方案声明列集合逐行生成指纹，再按比较方向汇总成 sha256。相同样本得到相同指纹。 |
| 重放核验 | 用同一源数据、方案和种子重新执行抽样，与落盘样本逐行指纹比对，结论为 `MATCH` / `DIVERGED` / `INSUFFICIENT_INPUT`。 |
| 代表性与偏离 | 覆盖率、稀有层命中、占比偏差；相对上一批同方案批次的层内构成漂移，以及超抽/漏抽。 |
| 批次 | 方案指纹 + 源数据指纹 + 样本指纹的规范化哈希，重复批次可被台账识别并复用。 |

## 三种抽样模式

| 模式 | 必填字段 | 分配规则 | 不足时 |
| --- | --- | --- | --- |
| `proportional` | `total_size` | 按每层源行数比例分配；同时受 `min_per_stratum` 与 `max_per_stratum` 约束。 | 层内不足按 `on_shortfall` 告警或失败；总量受容量/上限约束时告警。 |
| `fixed` | `per_stratum` | 每个非空层固定抽 `per_stratum` 条，不按比例。 | 层内不足按 `on_shortfall` 告警取尽或失败；报告保留目标量与实得量差异。 |
| `min_coverage` | `total_size` | 先为每层保证 `min_per_stratum`，剩余席位按层规模比例分配，再受上限约束。 | 最小覆盖总和高于目标量时自动抬高实际总量并告警；容量不足按策略处理。 |

三种模式都只使用方案中的 `seed` 初始化局部 `random.Random` 实例，按层名稳定排序后执行。随机结果跨机器复现，不依赖进程级随机状态。模式专属字段互斥；`min_per_stratum > max_per_stratum` 或固定量越界会被静态规则拒绝。

## 报告字段与台账

### 报告

每份报告都从同一份 `ReportDocument` 渲染，字段与顺序固定：

- `schema`、`generated_at`（固定 `YYYY-MM-DDTHH:MM:SSZ`）、`batch_id`、`overall_status`。
- `plan`：`name`、`version`、`fingerprint`。
- `source`：`name`、`kind`、`fingerprint`。
- `sample`：`fingerprint`、`mode`、`seed`、`requested_rows`、`rows`、`excluded_rows`、`eligible_rows`。
- `strata[]`：层名、源行数/占比、目标量、实得量、样本占比、占比偏差、稀有/空层/命中标记。
- `sampling.warnings[]`、`replay`、`coverage`、`deviance`、`alerts[]`。

JSON 使用稳定字段顺序、UTF-8 与 LF；Markdown 的表格行顺序稳定；HTML 内联 CSS、无脚本、无外链。相同文档重复渲染得到相同字节。

### 台账表 `batches`

| 列 | 说明 |
| --- | --- |
| `batch_id` | `batch-` + 批次指纹前 16 位，主键。 |
| `batch_fingerprint` | 方案 / 源 / 样本指纹的规范化 sha256，唯一。 |
| `plan_fingerprint`、`plan_name` | 方案身份。 |
| `source_fingerprint`、`sample_fingerprint` | 源数据与样本身份。 |
| `replay_status`、`coverage_status`、`coverage_rate`、`deviance_status` | 各项核验结论。 |
| `overall_status` | `PASS` / `WARN` / `FAIL`。 |
| `sample_count`、`requested_count` | 实得量 / 目标量。 |
| `alerts_json`、`composition_json` | 告警明细与层内维度构成快照，供下一批偏离检测。 |
| `created_at` | 固定 UTC ISO-8601。 |

重复批次返回已有记录并标记 `duplicate`，不会重复写入。查询入口：

```bash
python -m samplelaw inspect --ledger out/ledger.sqlite --plan-fingerprint <sha256>
python -m samplelaw inspect --ledger out/ledger.sqlite --batch-id batch-xxxxxxxxxxxxxxxx
```

## 错误码速查

错误码、触发条件与处置建议见 [docs/ERROR_CODES.md](docs/ERROR_CODES.md)。方案 DSL 的完整字段与边界规则见 [docs/PLAN_DSL.md](docs/PLAN_DSL.md)。常见前缀：

- `PLAN-*`：方案结构、类型、枚举、阈值与互斥字段。
- `PIPE-*`：流水线配置与路径。
- `DATA-*`：本地 CSV / SQLite 读取、编码与列结构。
- `SAMP-*`：抽样执行、容量与最小覆盖告警。
- `COV-*`、`DEV-*`：覆盖/代表性和执行偏离告警。
- `LEDGER-*`、`REPORT-*`、`CLI-*`：台账、报告与命令行。

## 目录结构与模块职责

```text
samplelaw/
├─ README.md
├─ pyproject.toml
├─ samplelaw/
│  ├─ cli.py                  # run / verify / inspect / preview
│  ├─ runner.py               # 批量任务编排、样本落盘、报告与台账串联
│  ├─ preview.py              # 可选 Flask 只读预览（惰性导入）
│  ├─ config/                 # YAML 读取、schema、行号定位、流水线加载
│  ├─ source/                 # Dataset、CSV 编码自适应、SQLite 只读读取
│  ├─ plan/                   # 分层、稀有/空层、确定性抽样、静态规则
│  ├─ verify/                 # replay、coverage、deviance
│  ├─ report/                 # 统一报告模型、Markdown/JSON/HTML 渲染
│  ├─ ledger/                 # 本地 SQLite 台账
│  └─ util/                   # 哈希、错误类型、日志
├─ examples/
│  ├─ pipeline.yaml
│  ├─ generate_data.py        # 合成数据重建
│  ├─ plans/
│  └─ data/
├─ fixtures/
│  ├─ gold_samples/           # 确定性回归快照
│  └─ bad_cases/              # 坏方案与坏样本
├─ docs/
└─ tests/
```

核心模块职责：

- `config`：校验字段、互斥关系、范围与路径；给出节点路径和可用行号；计算方案指纹。
- `source`：统一 `Dataset`；类型推断、主键候选、行/数据集指纹；CSV 支持 UTF-8 / UTF-8-SIG / GBK，SQLite 使用只读 URI。
- `plan`：稳定层枚举、稀有层与预期空层、三种抽样模式、执行告警、静态合法性规则。
- `verify`：重放比对；覆盖率和占比偏差；超抽、漏抽和层内构成漂移。
- `report` / `ledger`：稳定报告；批次、方案/源/样本指纹、告警与构成快照留痕。

## 明确不实现

- 不做网络数据源连接、采集、爬虫或表单填报。
- 不做通用统计推断（置信区间、假设检验、方差分析、Bootstrap 推断）。
- 不做脱敏、数据去重或主数据合并。
- 不做数据仓库版本管理或时间旅行查询。
- 不做字段量纲契约或指标口径比对。
- 不做机器学习、建模或特征工程。
- 不做多用户、鉴权、RBAC 或在线协作。
- 不向远端系统写入；SQLite 台账是唯一落库形态。
- 不做 PDF / Excel 导出。

## 常见问题

**CSV 编码不一致怎么办？**

读取器依次尝试 UTF-8-SIG、UTF-8、GBK；都失败时报 `DATA-003`。建议统一另存为 UTF-8，或在流水线 source 中显式指定 `encoding`。

**列缺失 / 列名拼错怎么办？**

源数据缺少分层键、指纹列或排除列时报 `DATA-002`。先用 `inspect` 或本地 CSV 表头核对列名；方案 DSL 不做模糊匹配。

**重放结果 `DIVERGED` 怎么排查？**

1. 确认源数据没有被改写（比较源指纹）。
2. 确认样本文件未被人工改动（比较缺失行数与多出行数）。
3. 确认方案种子、分层键、排除条款和指纹列未变化。
4. 若方案声明 `order_sensitive: true`，行顺序也会参与比较，查看 `order_mismatch`。

**重放报 `INSUFFICIENT_INPUT`？**

源数据或落盘样本没有数据行，或排除后没有可抽行。检查空表、排除条件和样本文件是否完整。

**为什么 `deviance` 是 `BASELINE`？**

同一方案首次入库没有上一批可比，构成漂移以基线记录落库；再次运行同方案的新批次时会对比上一批。完全相同的批次会被台账识别为重复批次。

**没有 Flask 能用吗？**

能。CLI 的 `run` / `verify` / `inspect` 与全部测试不依赖 Flask；`preview` 子命令才会惰性导入 Flask。
