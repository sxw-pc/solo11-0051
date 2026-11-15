# 抽样方案 DSL

samplelaw 方案是一份 UTF-8 YAML，API 版本固定为 `samplelaw/v1`，类型固定为 `SamplingPlan`。加载时会做结构校验、语义校验和规范化指纹计算；未知字段直接拒绝，避免拼写错误被静默忽略。

## 完整示例

```yaml
apiVersion: samplelaw/v1
kind: SamplingPlan
metadata:
  name: demo-events-min-coverage
  version: 1
  description: "最小覆盖 + 排除条款 + 预期空层"
sampling:
  mode: min_coverage
  seed: 41003
  total_size: 30
  min_per_stratum: 2
  max_per_stratum: 6
  on_shortfall: warn
strata:
  keys:
    - region
    - category
  rare_threshold: 0.08
  expected_levels:
    - [south, archive]
exclude:
  - column: status
    operator: eq
    value: invalid
fingerprint:
  columns:
    - event_id
    - region
    - category
    - status
    - channel
    - resolver
    - duration_ms
    - created_at
  order_sensitive: false
thresholds:
  max_share_deviation: 0.15
  max_weighted_share_deviation: 0.10
  drift: 0.20
deviance:
  dimensions:
    - channel
  min_baseline_rows: 2
```

## 顶层字段

| 字段 | 必填 | 类型 | 规则 |
| --- | --- | --- | --- |
| `apiVersion` | 是 | string | 必须为 `samplelaw/v1`，否则 `PLAN-003`。 |
| `kind` | 是 | string | 必须为 `SamplingPlan`，否则 `PLAN-003`。 |
| `metadata` | 是 | mapping | 只允许 `name` / `version` / `description`。 |
| `sampling` | 是 | mapping | 见下表。 |
| `strata` | 是 | mapping | 见下表。 |
| `exclude` | 否 | list | 排除条款，默认空。 |
| `fingerprint` | 否 | mapping | 行指纹与比较方向，默认使用源数据全部列、顺序无关。 |
| `thresholds` | 否 | mapping | 偏差阈值，有默认值。 |
| `deviance` | 否 | mapping | 偏离检测维度，默认不检测层内构成漂移。 |

`metadata.name` 必填且非空；`metadata.version` 默认 `1`，必须为正整数；`description` 默认空字符串。所有未知字段报 `PLAN-013`。

## sampling

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `mode` | 必填 | `proportional` / `fixed` / `min_coverage`。 |
| `seed` | 必填 | 整数，范围 `0..2^32-1`；只初始化局部随机实例。 |
| `total_size` | 条件必填 | `proportional`、`min_coverage` 必填，正整数；`fixed` 禁止出现。 |
| `per_stratum` | 条件必填 | `fixed` 必填，正整数；其他模式禁止出现。 |
| `min_per_stratum` | `1` | 最小层样本量，正整数。 |
| `max_per_stratum` | 无 | 最大层样本量，正整数；必须不小于 `min_per_stratum`。 |
| `on_shortfall` | `warn` | `warn` 表示取尽可用行并告警；`fail` 表示抛 `SAMP-002`。 |

互斥和越界规则：

- `fixed` + `total_size`、非 `fixed` + `per_stratum` 报 `PLAN-008`。
- 固定模式下 `per_stratum < min_per_stratum` 或 `> max_per_stratum` 报 `PLAN-011`。
- `min_per_stratum > max_per_stratum` 报 `PLAN-011`。
- 非法枚举报 `PLAN-004`；非整数或布尔伪装整数报 `PLAN-002`；越界报 `PLAN-005`。

## strata

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `keys` | 必填 | 非空字符串列表，必须唯一；按声明顺序构成层键。 |
| `rare_threshold` | `0.05` | 位于 `(0, 1]`；层源占比严格小于该值为稀有层。 |
| `expected_levels` | 空 | 需要核验“即使源数据缺失也必须关注”的层。 |

`expected_levels` 支持三种写法（层键数量必须匹配）：

```yaml
strata:
  keys: [region, tier]
  expected_levels:
    - [south, gold]       # 列表形式
    - {region: west, tier: platinum}  # 映射形式
```

单键方案也允许 `- legacy`。重复层或键数量不符分别报 `PLAN-006` / `PLAN-002`。预期空层不会伪造样本，只会在抽样、覆盖率和报告中告警。

## exclude

排除条款按“任一命中即排除”组合，全部在分层和抽样前执行。

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `column` | 必填 | 非空列名；源数据不存在时报 `DATA-002`。 |
| `operator` | `eq` | `eq` / `neq` / `in` / `not_in`。 |
| `value` | 必填 | `eq` / `neq` 为标量；`in` / `not_in` 为非空列表。值统一按字符串精确比较。 |

格式错误报 `PLAN-009`，未知操作符报 `PLAN-004`。注意：CSV 中 `"1"` 与数值 `1` 都按文本 `"1"` 比较；不做数值或日期转换。

## fingerprint

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `columns` | 源数据全部列 | 非空且唯一的列名列表；用于逐行指纹、样本指纹和重放比较。 |
| `order_sensitive` | `false` | `false` 时按指纹多重集比较；`true` 时行顺序也必须一致。 |

显式空列表报 `PLAN-005`，重复列报 `PLAN-006`。行指纹基于声明列和精确字符串值，使用规范化 JSON 的 sha256。

## thresholds

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `max_share_deviation` | `0.15` | 单层源占比与样本占比差绝对值的上限，`[0, 1]`。 |
| `max_weighted_share_deviation` | `0.10` | 以源占比为权重的占比偏差上限，`[0, 1]`。 |
| `drift` | `0.20` | 层内维度分布的总变差距离阈值，`[0, 1]`。 |

越界报 `PLAN-007`。覆盖率会分别检查最大偏差和加权偏差；偏离检测只用 `drift`。

## deviance

| 字段 | 默认 | 规则 |
| --- | --- | --- |
| `dimensions` | 空 | 用于层内构成漂移检测的列名列表，必须唯一；为空则不检测漂移。 |
| `min_baseline_rows` | `1` | 当前层与基线层都至少达到该样本行数才比较构成。 |

缺失维度列报 `DATA-002`。构成快照随批次写入台账，下一批同方案时读取比较；无上一批时状态为 `BASELINE`。完全相同的批次（方案、源数据和样本指纹都一致）会被台账识别为重复批次，其报告直接呈现首次落库的偏离结论与构成快照，不重新计算漂移。

## 抽样分配语义

1. 校验分层键、指纹列和排除列存在。
2. 应用排除条款，得到可抽行；若为空报 `SAMP-001`。
3. 按排序后的层键枚举层，并合并 `expected_levels` 中的空层。
4. 计算每层目标量：
   - `fixed`：目标为 `per_stratum`，容量不足按 `on_shortfall` 处理。
   - `proportional`：目标总量按比例分配，先满足层最小量，再按层规模分配剩余席位，并受层上限与容量约束。
   - `min_coverage`：语义与比例模式相同，但最小覆盖是首要约束；当 `total_size` 小于层最小量总和时，实际总量被抬高并产生 `SAMP-102` 告警。
5. 使用 `seed` 实例化一个局部 `random.Random`，按层名稳定顺序抽样。
6. 每层输出 `target` 与 `actual`；固定模式容量不足时两者可见差异。
7. 按 `fingerprint.columns` 与 `order_sensitive` 计算样本指纹。

## 规范化与指纹

方案指纹是规范化 JSON 的 sha256。规范化包含所有语义字段（元数据、抽样、分层、排除、阈值、偏离维度）且键顺序固定；未知字段不会进入指纹。修改种子、阈值、排除条款或比较方向都会改变指纹。

## 相关文档

- 错误码与处置：[ERROR_CODES.md](ERROR_CODES.md)
- 合规边界：[COMPLIANCE.md](COMPLIANCE.md)
- 批量流水线格式见仓库根目录 `examples/pipeline.yaml` 与 README。
