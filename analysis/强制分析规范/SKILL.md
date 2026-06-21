---
name: 强制分析规范
description: 强制分析输出格式规范。每次生成分析结论前必须先读取analyses.json，按ANALYSIS_SPEC.md模板逐项提取数据，禁止凭记忆或stdout输出。
---

# Analysis Strict Output

> 📌 `crypto-analysis-workflow` skill 是本规范的完整操作版（含前置条件、字段映射表、输出模板、三层复盘衔接）。本 skill 聚焦输出核验纪律和常见陷阱。

## 强制流程

**每次生成分析结论前必须执行以下步骤，不可跳过：**

### 1. 读取数据源
```python
import json
with open('/root/.hermes/trade_review/analyses.json') as f:
    data = json.load(f)
# 找最新BTC/ETH记录
for r in reversed(data):
    if r.get('coin') in ('BTCUSDT', 'ETHUSDT'):
        # 处理
```

### 2. 逐项提取并对照
对结论中的每个数字，从记录中提取对应字段：

| 结论字段 | 数据源字段 | 示例 |
|---------|-----------|------|
| 收盘价 | `kline_table.{TF}.close` 或 `ticker_price` | BTC 1D: kline_table.1D.close=62332.2 |
| RSI | `kline_table.{TF}.rsi` 或 `rsi_14`/`rsi_1h` | BTC 4H RSI=35.8 |
| MACD_h | `kline_table.{TF}.macd_h` 或 `macd_h_4h`/`macd_h_1h` | BTC 4H MACD_h=-535.3 |
| ADX | `kline_table.{TF}.adx` | BTC 4H ADX=48.0 |
| %b | `kline_table.{TF}.pct_b` | BTC 4H %b=15.0 |
| 形态 | `kline_table.{TF}.shape` | BTC 4H shape='十字星' |
| 趋势 | `kline_table.{TF}.trend` | BTC 1D trend='下降' |
| 共振 | `resonance` | BTC resonance='🟡分歧' |
| 支撑 | `support` | BTC support=[62222.2, 62253.6] |
| 阻力 | `resistance` | BTC resistance=[64650.0, 64480.0] |
| 威科夫 | `wyckoff_data.phase/confidence/detail` | BTC phase='Markup (Phase D->E)' confidence=60 |
| 订单流FR | `order_flow.funding_rate_pct` | BTC funding_rate_pct=-0.00018 |
| 订单流taker | `order_flow.taker_buy_ratio` | BTC taker_buy_ratio=1.044 |
| VP POC | `session_vp.POC` | BTC POC=62439.1 |
| VP VAH | `session_vp.VAH` | BTC VAH=62881.5 |
| VP VAL | `session_vp.VAL` | BTC VAL=62284.3 |
| FG | `macro_external.fg_actual` | FG=14 |
| FG标签 | `macro_external.fg_label` | Extreme Fear |
| DXY | `macro_external.dxy` | 100.83 |
| VIX | `macro_external.vix` | 16.8 |
| BTC.D | `macro_external.btc_dominance` | 56.1 |
| 10Y | `macro_external.yield10` | 4.45 |
| 仓位 | `position` | BTC='观望（等确认）' |
| 道氏 | `kline_table.{TF}.trend` 组合 | 1D=下降 4H=盘整 1H=上升 |
| 加速 | `accel` | steady |

### 3. 标注状态
- ✅ 精确匹配或四舍五入后一致
- ⚠️ 不一致（结论值≠数据源值）
- ❌ 找不到出处（数据源中无对应字段）

### 4. 报告
按币种分组输出核验结果，标注所有不一致项。

## 常见陷阱
1. **regime_cache 共享**: BTC和ETH共用同一份 `.regime_cache.json`，所以 order_flow 数据相同。结论中不应给不同币种写不同的FR值。
2. **字段名映射**: kline_table 用 `close`/`rsi`/`macd_h`/`adx`/`pct_b`/`shape`/`trend`，不是 `C`/`RSI`/`MACD_h` 等简写。
3. **四舍五入**: RSI/MACD_h/ADX 通常取整展示，但 `%b` 可能有小数。核对时允许 ±0.5 的舍入误差。
4. **position 字段**: BTC和ETH可能不同，需单独检查。
5. **calendar_events**: 只显示前3条（共10条），结论中列出全部10条会冗余。
6. **K线形态 stdout ≠ JSON**: `analysis_template.py` stdout 可能列出多个 LPS（如 LPS@08:00/12:00/16:00），但 `kline_pattern_times` 只存 LPS1/LPS2/LPS3 中实际检测到的那个（如 LPS3@16:00）。**分析结论和文案必须以 JSON 的 `kline_pattern_times` 为准**，不可凭 stdout 写「LPS三连」。
7. **价格来源**: stdout 的 ticker 价与 JSON `kline_table.1H.close` 可能差几十点。**分析结论中的现价必须用 JSON 的 1H close**，不用 stdout ticker 价。

## 输出模板

**必须严格按照 `ANALYSIS_SPEC.md` 第6章的输出模板格式**，不可自由发挥。
- 大币种（BTC/ETH/SOL）使用"标准分析"模板
- 山寨币使用"庄控分析"模板

## 关键约束
- 禁止从 stdout 记忆或上一轮对话中抄写数值
- 禁止凭印象写结论中的任何数字
- 如果结论中有任意一项无法从JSON中找到对应字段，该项必须标注⚠️未核验或直接删除
- ETH和BTC可能共用同一份order_flow（regime_cache共享），不可为不同币种写不同的FR值
- 生成结论前必须读取analyses.json，每个数字都从JSON中逐项提取
- LLM生成的结论必须经过"数据源→结论"逐项映射检查，不允许出现结论值与JSON值不一致的情况
