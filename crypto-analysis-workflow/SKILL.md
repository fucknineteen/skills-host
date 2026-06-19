---
name: crypto-analysis-workflow
description: 加密货币纯分析工作流。步骤：同步K线→执行analysis_template.py→从analyses.json逐项提取数据→按分析报告模板v5.0输出→标注来源。与大币种(标准分析模板)和山寨币(庄控分析模板)两套输出模板独立。
---

# 加密货币分析步骤

> 最后更新：2026-06-20
> 适用范围：BTC / ETH / SOL / DOGE
> 核心脚本：`analysis_template.py` → 写入 `analyses.json`
> 模板来源：`Obsidian/2-分析框架/分析报告模板v5.0.md`

---

## 一、前置条件

### 1.1 K线数据必须最新

分析前需确保 `okx_klines.db` 中有目标币种的最新K线：

```bash
cd /root/.hermes/trade_review
python3 monitor_and_sync.py BTC ETH SOL DOGE
```

**要求**：
- 检查数据完整性：逐币种、逐周期（5m/15m/30m/1H/4H/1D/1W）确认无缺口
- 检查数据质量：零量/平盘/负价应报告
- 确认同步日志无异常

> ⚠️ 1W周线：OKX周日收盘后才返回完整蜡烛。周中出现缺口属正常，等周日收盘后自动补齐。

**自动化**：cron 每小时自动同步（`sync_klines_cron.sh`），手动分析前建议再跑一次确认最新。

### 1.2 jq CLI 必须就绪

`analysis_template.py` 内部通过 `jq` 命令查询DB。若未安装：`apt install jq`

---

## 二、执行分析

### 2.1 运行分析脚本

```bash
cd /root/.hermes/trade_review
python3 analysis_template.py BTC ETH
```

**支持币种**：BTC / ETH / SOL / DOGE（可一次分析多个）

**什么是"纯分析" vs "发动态"**：本条命令只做纯技术分析，不生成社交文案。发动态需走 `publish_social.py`（详见 `social-posting-workflow` skill）。

### 2.2 脚本内部做的事（按顺序）

| 步骤 | 操作 | 数据源 |
|------|------|--------|
| ① | 同步最新K线 → DB | OKX API |
| ② | 计算技术指标 | DB K线 |
| ③ | 威科夫阶段检测 | 价格+量+结构 |
| ④ | K线形态检测（Spring/UTAD/SOS/LPS/SC） | 价格模式 |
| ⑤ | Volume Profile（POC/VAH/VAL） | DB K线 |
| ⑥ | 订单流（费率/多空比/买卖比） | OKX API + regime_cache |
| ⑦ | 道氏方向（1D/4H/1H） | 收盘价序列 |
| ⑧ | 共振评级（🟢偏强/🟡分歧/🔴偏弱） | 多指标综合 |
| ⑨ | 宏观环境（FG/DXY/VIX/10Y/BTC.D） | 金十MCP + alt.me + Yahoo |
| ⑩ | 金十财经日历 | 金十MCP |
| ⑪ | 写入 `analyses.json` | 本地文件 |

### 2.3 输出产物

| 产物 | 位置 | 格式 | 说明 |
|------|------|------|------|
| K线数据库 | `okx_klines.db` | SQLite | 5m/15m/30m/1H/4H/1D/1W |
| 分析缓存 | `analyses.json` | JSON数组 | 每次分析追加一条记录（同日同币覆盖） |
| 宏观缓存 | `.regime_cache.json` | JSON | 复用于BTC/ETH，3小时有效期 |
| 终端输出 | stdout | 文本 | 关键指标汇总（供快速浏览） |

---

## 三、生成分析结论（LLM 输出给用户）

### 3.1 强制流程（不可跳过）

```
① 读取 analyses.json → 找最新 BTC/ETH/SOL 记录
② 逐项提取数据 → 对照字段映射表
③ 按模板输出 → 大币种用标准分析模板，山寨币用庄控分析模板
④ 标注来源 → 每个数字可追溯到 analyses.json 字段
```

### 3.2 字段映射（结论 ← analyses.json）

| 结论中的内容 | 数据源字段 | 备注 |
|-------------|-----------|------|
| 现价 | `kline_table.1H.close` 或 `ticker_price` | **不用 stdout ticker 价** |
| TF收盘价 | `kline_table.{TF}.close` | close 字段名是 `last_close` |
| TF RSI | `kline_table.{TF}.rsi` 或 `rsi_14`/`rsi_1h` | |
| TF MACD_h | `kline_table.{TF}.macd_h` | |
| TF ADX | `kline_table.{TF}.adx` | |
| TF %b | `kline_table.{TF}.pct_b` | 字段路径是 `bb.pct_b` |
| TF K线形态 | `kline_table.{TF}.shape` | 字段名是 `label` |
| 道氏方向 | `kline_table.{TF}.trend` 组合 | |
| 共振 | `resonance` | 🟢偏强/🟡分歧/🔴偏弱 |
| 支撑位 | `levels_4h.lows` 或 `support` | |
| 阻力位 | `levels_4h.highs` 或 `resistance` | |
| 威科夫阶段 | `wyckoff_data.phase` | "Markup (Phase D→E)" |
| 威科夫置信度 | `wyckoff_data.confidence` | 60/77 等百分比 |
| Spring/SOS/LPS/SC | `kline_pattern_times` + `wyckoff_data.events` | **以 JSON 为准，不信 stdout** |
| Volume Profile | `vp_data.POC`/`VAH`/`VAL`/`session` | |
| 订单流费率 | `order_flow.funding_rate_pct` | ETH/BTC 共用 regime_cache，值可以相同 |
| 订单流Taker | `order_flow.taker_buy_ratio` | |
| FG | `macro_external.fg_actual` | |
| FG 标签 | `macro_external.fg_label` | "Extreme Fear" |
| DXY | `macro_external.dxy` | |
| VIX | `macro_external.vix` | |
| 10Y | `macro_external.yield10` | |
| BTC.D | `macro_external.btc_dominance` | |
| 仓位方向 | `position` | "观望（等确认）"/做多/做空 |
| 财经日历 | `calendar_events` | 仅列前3条 |
| 涨跌幅 | `change_pct` | `(last-open24h)/open24h` |

### 3.3 输出模板

**大币种（BTC/ETH/SOL）** → 使用 `分析报告模板v5.0.md` Part 1 + Part 2：

```
⏰ BJ {时间} | FG:{值}({标签})

## BTC ${价格} ({涨跌幅}) | FR:{费率}

📐 K线收盘状态
[TF表格 + 道氏 + 共振]

🔬 技术面·威科夫·K线形态
[威科夫 + VP + K线形态]

## ETH ${价格} ({涨跌幅}) | FR:{费率}
[同上结构]

🌍 宏观环境
[FG/DXY/VIX/10Y/BTC.D 表格 + 关键事件]

--- Part 2 ---

⚠️ 风险提示
[风险项 + 等级]

💰 仓位建议
[方向 + 入场 + 止损 + 止盈 + RR]
```

**山寨币** → 使用庄控分析模板（详见 `analysis-template` skill）。

### 3.4 禁止事项

| ❌ 禁止 | 原因 |
|---------|------|
| 凭记忆/stout 写数字 | 已多次出现数据不一致 |
| 为不同币种写不同的 FR 值 | regime_cache 共享，FR 对 BTC/ETH 相同 |
| 分析报告里输出模拟盘数据 | 用户明确要求不要 |
| 用未收盘蜡烛做判断 | 形态/趋势/威科夫都必须基于已收盘蜡烛 |
| 自由发挥模板格式 | 必须严格按模板 |
| 追加教育叙事/历史类比 | 不要"庄家在想什么"类主观发挥 |
| 输出完整财经日历 | 仅列前3条 |

### 3.5 核验纪律

- 所有数值必须从 `analyses.json` 逐项核对
- JSON 中不存在的数字不能写入分析结论
- 找不到出处的数字标注 `⚠️ 未核验`
- K线形态以 JSON `kline_pattern_times` 为准（stdout 可能列出多余的 LPS）

---

## 四、三层复盘引擎（自动）

分析结论写入 `analyses.json` 后，cron 自动化复盘：

| 层级 | 触发时间 | 内容 | 脚本 |
|------|----------|------|------|
| 6h 快判 | 入场后 6h | 方向初判 | `process_reviews.py` 自动 |
| 12h 深复盘 | 入场后 12h | 道氏+威科夫+价位三维度 | `process_reviews.py` 自动 |
| 72h 终局 | 入场后 72h | 3日趋势+教训 | `process_reviews.py` 自动 |

**状态说明**：新写入的记录 `review_6h/12h/72h` 初始值为 `"待复盘"`，`completed: false`。不要因字段存在就误判为"已复盘"——`"待复盘"` 是占位哨兵值。

**查看复盘状态**：
```bash
cd /root/.hermes/trade_review
python3 -c "
import json
with open('reviews.json') as f:
    data = json.load(f)
for r in data[-10:]:
    print(f\"{r['coin']} @ {r['timestamp'][:16]}: 6h={r.get('review_6h')} 12h={r.get('review_12h')} 72h={r.get('review_72h')} completed={r.get('completed')}\")
"
```

---

## 五、相关文件

| 文件 | 位置 | 用途 |
|------|------|------|
| `analysis_template.py` | `trade_review/` | 分析主脚本 |
| `analyses.json` | `trade_review/` | 分析结论缓存 |
| `reviews.json` | `trade_review/` | 三层复盘记录 |
| `regime_detector.py` | `trade_review/` | 宏观环境检测 |
| `.regime_cache.json` | `trade_review/` | 宏观缓存（BTC/ETH共享） |
| `monitor_and_sync.py` | `trade_review/` | K线同步 |
| `okx_klines.db` | `trade_review/` | K线SQLite数据库 |
| `process_reviews.py` | `trade_review/` | 三层复盘引擎 |
| 分析报告模板v5.0.md | `obsidian-vault/2-分析框架/` | 分析报告输出模板 |
| 社交动态模板v5.1.md | `obsidian-vault/2-分析框架/` | 发动态文案模板（不要与分析报告搞混） |
