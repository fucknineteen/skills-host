---
name: 强制分析规范
description: 强制分析输出格式规范。每次生成分析结论前必须先读取analyses.json，按ANALYSIS_SPEC.md模板逐项提取数据，禁止凭记忆或stdout输出。
---

# 强制分析规范（高约束执行版）

## SKILL ROLE CONTRACT（技能职责合同）

本 Skill 负责：

**分析结论生成前的数据核验。**

本 Skill 不负责：

- 行情分析逻辑
- 指标计算逻辑
- K线同步逻辑
- 三层复盘逻辑

上述逻辑由：

**加密货币纯分析 Skill** 负责执行。

本 Skill 仅负责：

**验证最终结论中的所有数据是否能够追溯到 analyses.json。**

---

## EXECUTION CONTRACT（最高优先级）

加载 Skill 不等于执行 Skill。

读取 Skill 不等于遵守 Skill。

每次生成分析结论前：

**必须完整执行本 Skill 规定的核验流程。**

**禁止：**

- 跳过核验
- 缩减核验
- 只核验部分数字
- 凭经验判断正确
- 凭stdout判断正确

若核验未完成：

**禁止输出分析结论。**

---

## GATE-1 数据源门禁

开始分析前必须执行：

```python
import json

with open('/root/.hermes/trade_review/analyses.json') as f:
    data = json.load(f)

for r in reversed(data):
    if r.get('coin') in ('BTCUSDT', 'ETHUSDT'):
        pass
```

必须成功读取：

`analyses.json`

否则：

**立即停止。**

禁止继续分析。

---

## GATE-2 数据来源合同

分析结论中的所有数字：

**必须来自：**

`analyses.json`

**允许来源：**

- analyses.json

**禁止来源：**

- stdout
- 历史结论
- 上轮对话
- Agent记忆
- 人工推测
- 默认值

若无法证明数字来源于 analyses.json：

**禁止输出。**

---

## DATA MAPPING CONTRACT（字段映射合同）

每一个数字必须满足：

```
数字 → 字段 → JSON路径 → 真值
```

四项同时成立。

否则：

视为未核验。

不得输出。

---

## 字段映射规则

以下字段必须严格使用指定映射。

**禁止：**

- 模糊匹配
- 猜测字段
- 使用同义字段
- 使用简写替代

**正确：** `kline_table.{TF}.close`　　　　**错误：** `C`

**正确：** `kline_table.{TF}.rsi`　　　　**错误：** `RSI`

**正确：** `kline_table.{TF}.macd_h`　　　**错误：** `MACD_h`

发现字段不匹配：

**立即停止。不得继续核验。**

---

## 字段映射表

| 结论字段 | 数据源字段 | 示例 |
|---------|-----------|------|
| 收盘价 | `kline_table.{TF}.last_close` | BTC 1D: last_close=62332.2 |
| RSI | `kline_table.{TF}.rsi` | BTC 4H RSI=35.8 |
| MACD_h | `kline_table.{TF}.macd_h` | BTC 4H MACD_h=-535.3 |
| ADX | `kline_table.{TF}.adx` | BTC 4H ADX=48.0 |
| %b | `kline_table.{TF}.pct_b` | 字段路径是 `bb.pct_b` |
| 形态 | `kline_table.{TF}.label` | BTC 4H label='十字星' |
| 趋势 | `kline_table.{TF}.trend` | BTC 1D trend='下降' |
| 共振 | `resonance` | BTC resonance='🟡分歧' |
| 支撑 | `levels_4h.lows` | BTC lows=[62222.2, 62253.6] |
| 阻力 | `levels_4h.highs` | BTC highs=[64650.0, 64480.0] |
| 威科夫 | `wyckoff_data.phase/confidence/detail` | BTC phase='Markup (Phase D→E)' |
| 订单流FR | `order_flow.funding_rate_pct` | BTC funding_rate_pct=-0.00018 |
| 订单流taker | `order_flow.taker_buy_ratio` | BTC taker_buy_ratio=1.044 |
| VP POC | `session_vp.poc` | BTC poc=62439.1 |
| VP VAH | `session_vp.vah` | BTC vah=62881.5 |
| VP VAL | `session_vp.val` | BTC val=62284.3 |
| FG | `macro_external.fg_actual` | FG=14 |
| FG标签 | `macro_external.fg_label` | Extreme Fear |
| DXY | `macro_external.dxy` | 100.83 |
| VIX | `macro_external.vix` | 16.8 |
| BTC.D | `macro_external.btc_dominance` | 56.1 |
| 10Y | `macro_external.yield10` | 4.45 |
| 仓位 | `position` | BTC='观望（等确认）' |
| 道氏 | `kline_table.{TF}.trend` 组合 | 1D=下降 4H=盘整 1H=上升 |
| 加速 | `accel` | steady |

---

## NUMERIC VERIFICATION CONTRACT（数字核验合同）

对于结论中的每一个数字：

必须执行：

- 步骤1 — 找到对应字段
- 步骤2 — 提取真实值
- 步骤3 — 比较结论值
- 步骤4 — 记录状态

**禁止跳步。**

---

## 状态标注合同

核验结果只能使用：

**✅ 精确匹配** — 数值一致，或允许误差范围内一致

**⚠️ 数值不一致** — 结论值 ≠ 数据源值

**❌ 找不到出处** — 数据源不存在对应字段

**禁止发明新的状态。**

---

## 舍入误差合同

允许 RSI / MACD_h / ADX 出现 **±0.5** 以内的舍入误差。

超出范围：视为不一致，必须标记 ⚠️

**%b 按照实际值核验，不得自动取整。**

---

## OUTPUT VERIFICATION CONTRACT（输出核验合同）

按币种分别输出：

- BTC
- ETH

核验结果。

**必须列出所有不一致项。**

**必须列出所有未核验项。**

**禁止只报告通过项。**

---

## SPECIAL TRAP CONTRACT（已知陷阱合同）

### 陷阱1 — regime_cache 共享

BTC 与 ETH 共用 `.regime_cache.json`，因此 **order_flow 数据相同**。

若出现 BTC FR ≠ ETH FR：

**立即判定核验失败。**

### 陷阱2 — 字段名

必须使用 `last_close` / `rsi` / `macd_h` / `adx` / `pct_b` / `label` / `trend`。

**禁止简写。**

### 陷阱3 — position 字段

BTC 与 ETH 允许不同。

必须分别检查。

**禁止复制。**

### 陷阱4 — calendar_events

仅显示前 3 条。

**禁止输出全部 10 条。**

### 陷阱5 — stdout ≠ JSON

`analysis_template.py` stdout 仅供运行参考，不是最终数据源。

若 stdout 与 JSON 冲突：

**必须以 JSON 为准。**

### 陷阱6 — K线形态

必须读取 `kline_patterns`。

**禁止依据 stdout 中出现的多个 LPS 推导「LPS三连」。**

若 JSON 未记录：不得输出。

### 陷阱7 — 价格来源

现价必须使用 `kline_table.1H.last_close`。

**禁止使用 stdout ticker 价。**

若两者不同：以 JSON 为准。

---

## TEMPLATE CONTRACT（模板合同）

最终分析输出必须严格遵守 `ANALYSIS_SPEC.md` 第6章模板。

**禁止：**

- 修改结构
- 删除字段
- 增加字段
- 调整顺序

**大币种（BTC/ETH/SOL）：** 必须使用标准分析模板

**山寨币：** 必须使用庄控分析模板

---

## EXECUTION DISCIPLINE（执行纪律）

开始输出前必须完成以下自检：

- [ ] analyses.json 已读取
- [ ] 所有数字已找到字段
- [ ] 所有数字已找到路径
- [ ] 所有数字已核验
- [ ] stdout 未作为数据来源
- [ ] 模板正确

全部为 YES：允许输出。

否则：**立即停止。**

---

## FAILURE CONTRACT（失败处理合同）

若发现任意一个数字无法满足：

```
数字 → 字段 → JSON路径 → 真值
```

完整映射关系，则该数字必须 **⚠️未核验** 或直接删除。

**禁止：**

- 猜测
- 补写
- 根据经验填写

---

## FINAL DELIVERY CONTRACT（最终交付合同）

最终分析结论中的每一个数字必须同时满足：

1. 来源于 analyses.json
2. 存在字段映射
3. 存在 JSON 路径
4. 与真值一致
5. 已完成核验

若任意条件不满足：

**禁止输出。** 立即返回 `⚠️未核验` 并终止交付。
