---
name: 加密货币纯分析
description: 加密货币纯分析工作流。步骤：同步K线→执行analysis_template.py→从analyses.json逐项提取数据→按分析报告模板v5.0输出→标注来源。触发词：纯分析、分析行情、分析BTC、分析ETH。⚠️ 不是发动态——发动态用「社交动态发布」skill。与大币种(标准分析模板)和山寨币(庄控分析模板)两套输出模板独立。
---

# 加密货币分析步骤

> 最后更新：2026-06-21
> 适用范围：BTC / ETH / SOL / DOGE（ETH 全周期数据已于 2026-06-21 补齐）
> 核心脚本：`analysis_template.py` → 写入 `analyses.json`
> 模板来源：`Obsidian/2-分析框架/分析报告模板v5.0.md`
> 更新：SL/TP 多层级优化（VAL/VAH→ATR→原逻辑，三文件联动）、ETH 数据缺口关闭、VP 键名 vp_data → session_vp、gen_charts.py 五 bug 修复、强制分析规范字段映射修正、发动态最终交付改为发给你确认（不自动发频道）、TP 回溯窗放宽 `[-8:]`→`[-32:]` + `get_levels(n=20)`→`(n=32)` 统一纯分析/社交管线（1H 排除、3天排除、5.3天确认最优）、实盘开仓全流程（市价单+SL/TP+交易日志）

---

---

### ⛔ 执行前必查清单（每次分析前逐项过，漏一项 = 结论不可靠）

| # | 检查项 | 错了会怎样 |
|---|--------|-----------|
| 1 | 已加载本 skill，下一步直接运行 `python3 analysis_template.py BTC ETH` | 停在这里不执行 |
| 2 | 同步K线：`python3 monitor_and_sync.py BTC ETH` 确认无缺口 | 用过期数据分析 |
| 3 | 运行分析：`python3 analysis_template.py BTC ETH` | 数据未更新 |
| 4 | **所有数字从 `analyses.json` 逐字段提取**，禁止 stdout/记忆 | 编造数据（如上次"63188"） |
| 5 | 大币种用标准分析模板 v5.0，山寨币用庄控模板——不可混用 | 格式错乱 |
| 6 | 字段映射：`kline_table.{TF}.close`（非 `last_close`），`kline_table.{TF}.pct_b`（非 `bb.pct_b`） | 字段名取错→读空值 |
| 7 | 每个数字写入结论前，回头看 JSON 确认 | 核验盲区藏假数据 |
| 8 | `analyses.json` 和 `social_analyses.json` 是**两个独立文件**——读纯分析数据用前者 | 字段不兼容 |
| 9 | 不输出模拟盘/仓位建议（除非用户明确要求） | 用户多次纠正 |
| 10 | 写结论前加载教训：`cat .lesson_context.txt` | 忽略已验证的规律（如RSI<20不追空） |
| 11 | 每个数字标注来源：JSON字段路径可追溯 | 找不到出处时混入编造数字 |

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

**什么是"纯分析" vs "发动态"**：本条命令只做纯技术分析，不生成社交文案。发动态需走 `publish_social.py`（详见 `社交动态发布` skill）。

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
| ⑩ | 金十财经日历 | 金十MCP `list_calendar` |
| ⑪ | **金十快讯（🆕 2026-06-20）** | 金十MCP `list_flash` + `search_flash`（加密相关过滤，7天内，关联度排序） |
| ⑫ | 写入 `analyses.json` | 本地文件 |

### 2.3 输出产物

| 产物 | 位置 | 格式 | 说明 |
|------|------|------|------|
| K线数据库 | `okx_klines.db` | SQLite | 5m/15m/30m/1H/4H/1D/1W |
| 分析缓存 | `analyses.json` | JSON数组 | flat_old（纯分析，analysis_template.py写入） |
| 社交分析缓存 | `social_analyses.json` | JSON数组 | full_obj（社交发布，publish_social.py写入）。详见 社交动态发布 skill 的 `references/two-file-architecture.md` |
| 宏观缓存 | `.regime_cache.json` | JSON | 复用于BTC/ETH，3小时有效期 |

> ⚠️ `analyses.json` 和 `social_analyses.json` 已分离（2026-06-20）。`process_reviews.py` 复盘引擎同时读取两个文件合并，social 优先覆盖同日同币记录。

**统一计算层（2026-06-20 重构）**：`_social_publish.py` 的 `analyze_single_coin()` 改为 wrapper，底层统一调用 `analysis_template.analyze_single_coin()`。所有底层计算函数（RSI/MACD/ADX/BB/trend/label/accel/resonance 等 18 个核心字段）由 `analysis_template` 一站式提供，`_social_publish` 在此基础上补充 position/vp/wyckoff/calendar/macro/flash_news 六个社交字段。两个工作流产出的分析数据从根源一致。

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
| VP POC/VAH/VAL | `session_vp.POC`/`VAH`/`VAL`（**24h 全天**，已改为全时段） | |
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
| 快讯 | `flash_news` | `[{time, content, score, url}]` 关联度排序，前3-5条 |
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

📰 消息面（🆕 2026-06-20）
[金十快讯前3-5条，标注时间+关联度]

--- Part 2 ---

⚠️ 风险提示
[风险项 + 等级]

💰 仓位建议
[方向 + 入场 + 止损 + 止盈 + RR]
```

**山寨币** → 使用庄控分析模板（详见 `分析结论模板` skill）。

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

### 3.6 教训闭环（轻量）🔄 2026-06-20 新增

**分析前必须加载教训上下文**：

```bash
cat /root/.hermes/trade_review/.lesson_context.txt
```

读取后，分析时注意：

1. **同币种近期教训**：如 `.lesson_context.txt` 中有当前币种的教训，先复习
2. **同行情类型教训**：如当前行情是「牛市回调」，关注 `[牛市回调]` 标签的教训
3. **已知规律**：如 `RSI<20+FG<15 → 空头降级` 等已被多次验证的规则，直接应用
4. **数据事件避让**：如临近 CPI/FOMC/非农，参考历史事件教训做方向降级

该文件由 `inject_lessons.py`（cron `:05` 每小时）自动生成，源数据来自 `lessons.json` + `regimes/*_lessons.json`。

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
| `jin10_fallback.py` | `trade_review/` | 金十数据源（日历+快讯）🆕 2026-06-20 |
| 快讯架构文档 | `references/flash-news-architecture.md` | 关联度评分、黑名单、缓存、数据流 |
| 分析报告模板v5.0.md | `obsidian-vault/2-分析框架/` | 分析报告输出模板 |
| 社交动态模板v5.1.md | `obsidian-vault/2-分析框架/` | 发动态文案模板（不要与分析报告搞混） |

## 六、已知数据问题

- **ETH 数据缺口（✅ 2026-06-21 已修复）**：此前 ETH 缺少 1h/4h/1d 数据（仅 5m/15m/30m 可用），现已由 hourly cron `monitor_and_sync.py BTC ETH` 自动补齐。目前 ETH 全周期 5m/15m/30m/1H/4H/1D 与 BTC 对齐。
- **快讯集成架构**：详见 [`references/flash-news-integration.md`](references/flash-news-integration.md)。
- **技能维护陷阱**：详见 [`references/skill-maintenance-pitfalls.md`](references/skill-maintenance-pitfalls.md)。
- **代码审计已知问题**：详见 [`references/code-audit-2026-06-20.md`](references/code-audit-2026-06-20.md)（三轮共 17 项，前两轮 14 项全部已修复 ✅ 2026-06-20）。
- **跨文件字段映射**：详见 [`references/cross-file-field-mapping.md`](references/cross-file-field-mapping.md)。analyses.json（flat_old）与 social_analyses.json（full_obj）使用不同字段命名约定——开发跨文件消费代码前必读。
- **实盘开仓流程**：详见 [`references/live-trading-via-social-posts.md`](references/live-trading-via-social-posts.md)。从纯分析结论直接用 `place_live_orders.py` 挂 OKX 实盘限价单的标准操作流程（social_posts.json 格式要求 + 步骤）。
- **实盘开仓全流程**（市价单 + SL/TP + 交易日志）：详见 [`references/live-trading-workflow.md`](references/live-trading-workflow.md)。🆕 2026-06-21 — 市价单优先、分步挂 SL/TP、`trade_journal.json` 追踪每笔交易的盈亏比。
- **OKX 实盘账户查询**：详见 [`references/okx-live-account-query.md`](references/okx-live-account-query.md)。🆕 2026-06-22 — 三种查询方法（内置命令/api_call/curl直查），含时间戳格式陷阱和 base64 签名要点。
- **1H vs 4H TP 对比**：详见 [`references/1h-vs-4h-tp-analysis.md`](references/1h-vs-4h-tp-analysis.md)。2026-06-21 结论：4H 优于 1H（噪声少 6 倍，结构分量重）。

## 七、仓位方向逻辑（`position` 字段）

> 最后更新：2026-06-20 — P0/P1/P2/P3 仓位系统全量修复

### 7.1 `position` 字段判定（写入 analyses.json）

| 条件 | position 值 |
|------|------------|
| `resonance` 含 `强` 或 `near_bottom=True` | `偏多` |
| `resonance` 含 `弱` | `偏空` |
| `resonance` 含 `观望`/其他 | `观望（等确认）` |

- 来源：`analysis_template.py` `analyze_single_coin()` + `_social_publish.py` wrapper（`analyze_single_coin` 返回值）
- **2026-06-20 修复**：原逻辑 `'强' and not near_bottom → 偏多` 与 stdout 的 `near_bottom → 试多` 矛盾，已统一为 `near_bottom` 也触发偏多。
- **2026-06-22 修复（#5）**：stdout 做多条件 `near_bottom AND macd_4h>-50 AND rsi_1h<45` 与 JSON `near_bottom` 不一致 → 统一为 `'强' or near_bottom`，两处同一条件。
- **2026-06-22 修复（#4/#6）**：wrapper 补上 `near_bottom→偏多` 路径和 `rsi_1d>65+MACD_4h<-50→偏空` 路径，三地方向判定对齐。

### 7.2 stdout 仓位行判定（`_format_coin_section`）

**判定优先级**（L1297-1320，2026-06-22 全审计对齐）：

| 优先级 | 条件 | pos_dir |
|--------|------|---------|
| 1 | resonance 含 `强` 或 `a.get('near_bottom')` | `试多` |
| 2 | resonance 含 `弱` 或 (RSI_1d>65 + MACD_4h<-50) | `试空` |
| 3 | **near_top**：RSI_1d>67 + 1D方向='下降' + MACD_4h<0 | `试空` |
| 4 | 其他 | `观望` |

> **2026-06-22 修复（#5 回归）**：原条件 `near_bottom`（裸变量，NameError）→ `a.get('near_bottom')`。三地方向判定阈值已完全对齐（见 7.11）。

**V 反保护**（L3，L1309-1320）：做空信号确定后，如满足以下任一条件则降级为观望：
- `near_bottom == True` → `观望（near_bottom保护）`
- 过去 8 根 4H 蜡烛内，当前价从最低点反弹 > 3% → `观望（反弹X%，V反保护）`

> ⚠️ **第二轮审计发现**（2026-06-20）：V 反保护的 8 根 4H 反弹检测**最初仅在 `_format_coin_section`（stdout）实现**，`_social_publish.py` wrapper 和 `main()` record builder 只实现了 near_bottom 检测。**已修复**：三处全部补上反弹>3% 检测，与 `_format_coin_section` 对齐。

**多空 SL/TP — 多层级优化**（🆕 2026-06-21）：

原公式 `SL = 最近低点 - ATR×0.5` 在价格已从 Spring 低点大幅反弹后（如反弹 3.5%），SL 会被拖到极远处（RR<0.8）。改三层候选择优：

| 优先级 | 做多候选 | 做空候选 | 说明 |
|--------|----------|----------|------|
| ① VAL/VAH 锚点 | `sl = VAL - atr_4h × 0.3` | `sl = VAH + atr_4h × 0.3` | 价值区边界 + 微缓冲，结构失效即离场 |
| ② ATR 入口锚点 | `sl = entry - atr_4h × 1.5` | `sl = entry + atr_4h × 1.5` | 纯波动率止损，不依赖结构 |
| ③ 原逻辑（保底） | `sl = lows_near[0] - atr_4h × 0.5` | `sl = highs_near[0] + atr_4h × 0.5` | VAL/VAH 缺失或异常时回退 |

**选则**：距 entry 最近且 RR ≥ 1.2 的候选 → 全不满足则取最近。
**改动文件**：`analysis_template.py` L1313-1362、`_social_publish.py` L333-397、`publish_social.py` L277-348（三处均同步）。

**TP 优化：ATR 倍数 vs 绝对高点**（🆕 2026-06-21）：

`highs_near[0]` 在 Spring 后反弹环境可能给出过远的目标（如 66790 需 3.9% 涨幅），FG=23 + 1D RSI<45 时难以达到。改用 `entry + ATR_4H × 1.5` 锚定波动率：

| 环境 | TP 算法 | 效果 |
|------|---------|------|
| 正常/强势 | `highs_near[0]`（放宽回溯后的最近阻力） | ✅ 有结构意义 |
| 极端恐惧/低 RSI | `entry + ATR_4H × 1.5` | ✅ 更易触及，RR 仍 ≥ 1.5 |

示例：BTC entry=64268，ATR_4H=1318，ATR×1.5=1977 → TP=66244（距入场 3.1%，RR=1.89）。相比绝对高点 66790（3.9%），66244 在 1~2 天内更大概率触及。

**注意**：选 ATR 倍数时从 1.0/1.2/1.5 候选中取 RR ≥ 1.5 的最小倍率，避免 TP 过近。

- entry 用 `ticker.last`（现价）
- 做多 SL 必须 < entry，做空 SL 必须 > entry（候选剔除）

**pos_dir guard 规则**：所有后续判断使用 `pos_dir.startswith('试')` 和 `pos_dir.startswith('观望')` 而非等值比较，以兼容带后缀的变体（如 `观望（near_bottom保护）`）。

### 7.3 仓位公式（`calc_position`）

模块级常量（`analysis_template.py` L127-166）：

| 常量 | 值 | 说明 |
|------|-----|------|
| `LEVERAGE` | 20 | 默认杠杆倍数 |
| `ACCOUNT_USD` | 100 | 小账户本金 |
| `MAX_RISK_PCT` | 2.0 | 单笔最大风险% |
| `MARGIN_MAINTENANCE` | {BTC:0.5, ETH:1.0, SOL:2.0, DOGE:2.5} | OKX 维护保证金率% |
| `CONTRACT_SIZE` | {BTC:0.01, ETH:0.1, SOL:1.0, DOGE:10.0} | 合约面值 |

- **P2 修复**：`contracts` 已 `math.floor()` 取整（OKX 要求整张）
- **P2 修复**：mm_map 补全 SOL(2.0%) / DOGE(2.5%)
- **P3 新增**：输出行含 `爆仓价{liq_price:.1f}(距{liq_pct}%)`

### 7.4 社交动态文案的 SL/TP

`_social_publish.py` `generate_social_draft()` + `publish_social.py` fallback：

- **P0 修复**：`_calc_sl_tp_pair()` 方向感知——做空 SL 在高位上方、TP 在低位
- **P1b 修复**：SL 改用 ATR 缓冲（`indicators[4H].atr`），不再用硬编码 `0.99/0.985`
- **P3 修复**：方向标签读 `position` 字段动态切换，不再硬编码 `偏多`
- **f1 修复**：结语根据 `btc_dir_label` 动态切换——偏空→"顺势而为不猜底"；偏多+极恐→保留原句；观望→"等确认信号"
- **f2 修复**：`btc_dir_label='观望'` 时不输出 🎯 行（避免无意义 SL/TP）
- **f3 修复**：RR 警告阈值统一为 1.5（与分析报告一致）
- `analyze_single_coin` 返回值新增 `position` 字段（L464）
- `publish_social.py` fallback record 新增 `position` 字段

### 7.5 B3/B4/X1 压制规则（⚠️ 仅文档，代码未执行）

`BTC_RULES` 配置字典（L66-71）中记录了 B3/B4 规则，但**没有任何代码引用它们**：
- `B3`: `bias≤-2+FG<15 → 不给出做空建议` — **未执行**
- `B4`: `RSI<20+FG<15 → 空头信号强制降级` — **未执行**
- `X1`: `check_extreme_oversold()`（L100-104）存在但**仅写 lessons_warnings，不干预 direction**

⚠️ **关键认识**：做空稀少不是因为 B3 封杀，而是因为共振评分门槛（见 7.7）。当 resonance='🔴偏弱' 时，做空信号**确实会被输出**（ETH 6/19-20 已验证）。

### 7.6 踩过的坑

| 坑 | 表现 | 根因 | 修复 |
|----|------|------|------|
| SL/TP 不区分多空 | 做空时 SL 仍在低位下方 | L1302-1303 始终 `sl=lows[0]-ATR×0.5` | P0：按 pos_dir 分支 |
| entry 用日线收盘价 | 入场价可能已过时数小时 | L1301 `entry=close_1d` | P1a：改用 `ticker.last` |
| 社交 SL 硬编码系数 | 波动大时止损太紧、波动小时太宽 | `low×0.99` | P1b：改用 ATR 缓冲 |
| contracts 不取整 | 输出小数张数 | calc_position 无 floor | P2：`math.floor()` |
| SOL/DOGE 爆仓距偏低 | mmr 默认 1.5% 与 OKX 实际不符 | mm_map 缺 SOL/DOGE | P2：补全 MARGIN_MAINTENANCE |
| JSON position 与 stdout 矛盾 | near_bottom 在 JSON 抑制偏多、在 stdout 触发试多 | 两套独立判定逻辑 | P3：统一为 near_bottom 触发偏多/试多 |
| 社交结语与做空矛盾 | 输出做空建议时结语说"做空的都成了燃料" | L597 结语硬编码 | f1：根据方向动态切换 |
| 观望时显示假 SL/TP | 观望无交易计划但 🎯 行仍有数字 | `_calc_sl_tp_pair` 走 else 默认算做多 | f2：观望时跳过输出 |
| close_1d 死变量 | 已改用 ticker.last 但 close_1d 仍被计算 | L1301 未删除 | f4：删除变量 |
| 做空入口稀缺 | 震荡市几乎不出做空信号 | 共振门槛 2/3 + 做空无捷径 | L1：加 near_top 检测 |
| FG<15 一刀切假设 | V 反恐惧与阴跌恐惧不分 | 无趋势/反弹上下文 | L3：near_bottom+反弹检测做 V 反保护 |
| 教训静默丢失 | `extract_lessons()` 应生成教训但返回空 | `find_analysis()` 因 analyses.json 被 publish_social 覆盖返回 None → analysis is None → 提前 return [] | 分离 analyses.json/social_analyses.json（2026-06-20）|
| VP 开盘为空 | `session_vp()` 新交易时段前2h无数据 | 仅查当前8h时段15m蜡烛（≥8根才计算） | 改为24h全天（2026-06-20）|
| 日历缓存过期 | 日常为空（cron 6h刷新间隔太长） | 仅读本地缓存文件 | 改为 MCP 实时+缓存+硬编码回退（2026-06-20）|
| 底层函数重复实现 | 13/22 核心字段不一致，复盘取到不同源数据 | `_social_publish.py` 11 个函数有独立副本，与 `analysis_template` 实现不同 | 全量从 `analysis_template` import，`analyze_single_coin` 改为 wrapper（2026-06-20） |
| V反保护不完整 | `social_analyses.json` 和 `analyses.json` 的 `position` 在反弹>3%时仍输出偏空 | wrapper 和 main() record builder 的 V反保护仅 near_bottom，缺 8 根 4H 反弹检测 | P0（二次审计）：三地全部补上反弹>3%检测（2026-06-20） |
| position 三地重复计算 | 三个位置的 position 判定逻辑各不同，值可能不一致 | `_format_coin_section` / wrapper / main() 各自实现，分歧时 wrapper=`观望`、main()=`观望（等确认）` | P0 修复同步消解功能差异；position 值统一为 `观望（等确认）`（2026-06-20） |
| `'观望' in resonance` 死代码 | 永假分支，resonance 不含 '观望' | main() L1994 检查 `'观望' in str(resonance)` | P2：删除分支，兜底由 else 处理（2026-06-20） |
| 快讯 per-coin 重复（社交流） | `publish_social.py` 每个币调一次 `fetch_flash_news()` | wrapper `analyze_single_coin` 自行拉取快讯 | P2：移到 `publish_social.py` 循环外，通过参数传入 wrapper（2026-06-20） |
| RR 不区分多空（main 记录层） | `analyses.json` 做空时 `sl_val/tp_val/rr_str` 始终按做多算，RR 永远是 `?` | main() 的 SL 固定取 lows[0]、TP 固定取 highs[0]，无方向分支 | P0（2026-06-20 审计）：读 `position` 区分——做空 SL=highs[0]×1.01, TP=lows[0], RR=(entry-tp)/(sl-entry) |
| 快讯 per-coin 重复拉取 | 多币种分析时每个币都调一次 MCP 分页+搜索 | `fetch_flash_news()` 在循环内 | P2（2026-06-20 审计）：移到循环外，多币种共享 `_all_flash_news` |
| 快讯分页/搜索同 try 块 | 搜索关键词异常会丢弃已获取的分页结果 | 单一大 try 包裹所有逻辑 | P2（2026-06-20 审计）：拆成独立 try 块 + `if all_items` 门控 |
| VP 键名不一致 | `_social_publish` wrapper 存 `vp_data`，`analysis_template` main() 存 `session_vp` | 两套命名各自为政 | P2（2026-06-20 审计）：统一为 `session_vp`（3 文件联动） |
| 文件锁 None 保护缺失 | 锁超时时 `os.close(None)` 静默 TypeError | finally 未判空 | P3（2026-06-20 审计）：加 `if lock_fd is not None` |
| `has_saved_analyses` 硬编码 | 无论分析是否成功都写 social_analyses.json | 变量始终 True | P3（2026-06-20 审计）：改为 `len(analyses) > 0` |
| verify_social_post 字段不兼容 | `verify_social_post.py` 读取 social_analyses.json 但期望 `kline_table`/`support`/`kline_pattern_times`（flat_old 格式），这些字段在 social_analyses.json 中不存在 → 约 40% 核验项静默跳过 | 双文件分离后未更新字段映射 | P1（第三轮审计 2026-06-21 已修复）：更新 verify_social_post.py 适配 social_analyses.json 字段名（indicators/levels_4h/kline_patterns/last_close/bb.pct_b） |
| verify_social_post 回退路径死循环 | 数据缺失时运行 `analysis_template.py`（→ 写入 analyses.json），但后续仍读 social_analyses.json（→ 仍为空） | 回退脚本跑错目标文件 | P2（第三轮审计 2026-06-21 已修复）：publish_social.py 新增 `--verify-only` 模式，run_analysis() 改为调用它 |
| analyses.json VP 键名漂移 | analyses.json 用 `vp_data`，social_analyses.json 用 `session_vp` | 两文件各自命名 | P3（第三轮审计 2026-06-21 已修复）：统一为 `session_vp`，verify_social_post.py 加向后兼容 |
| SL 锚点用绝对最低点致距入场过远 | BTC 从 Spring 低点 62222 反弹 3.5% 到 64384 后，SL=62222-ATR×0.5=61559，距入场 2825 点 (4.4%)，RR=0.72 | `lows_near[0]` 是绝对最低点而非最近支撑 | 🆕 2026-06-21：三段文件改为三层候选择优（VAL/VAH→ATR→原逻辑），选距 entry 最近且 RR≥1.2 者。BTC SL 缩至 62920（距入场 1571 点） |
| **TP 数据源分歧** | `_format_coin_section`(stdout) TP=64447（`highs_near[0]`，仅已收盘 4H 蜡烛，且限 `[-8:]`=32h）；`publish_social.py` 和 `analyses.json` flat_old TP=64775（`levels_4h.highs[0]`，含当前未收盘蜡烛）。差距 328 点，纯分析终端输出与 analyses.json 不一致 | 三处 TP 来源不同——`highs_near` 用 closed 4H candle highs 且受 ATR band 过滤 + `[-8:]` 限制，`levels_4h` 是全量高低点含当前蜡烛 | ✅ 2026-06-21 三步修复：(1) `closed_4h[-8:]` → `closed_4h[-32:]`（≈5.3天），BTC TP 从 64447 升至 66790（RR 0.01→2.5）；(2) `get_levels('4H')` → `get_levels('4H', n=32)`，使 `levels_4h` 与 `highs_near` 对齐同一窗口；(3) flat_old 导出不再独立重算 SL/TP/RR，改为直接读 `a['_sl']`/`a['_tp']`/`a['_rr']`（`_format_coin_section` 已算好的多层优化结果），analyses.json 与终端输出完全一致。3 天窗口已排除（RR 跌到 0.4），1H 已排除（噪声 6 倍）。最终：4H 收盘蜡烛 + [-32:] 窗口 |

### 7.7 共振评分公式（`resonance` 如何决定做空/做多）

`analysis_template.py` `analyze_single_coin()`：

```
score = 0
RSI_4H > 55  → +1    |  RSI_4H < 45  → -1
MACD_h_4H > 0 → +1   |  MACD_h_4H < 0 → -1
%b_1H < 30   → -1    |  %b_1H > 70   → +1

score ≥  2 → 🟢偏强 → position='偏多'
score ≤ -2 → 🔴偏弱 → position='偏空'
其他       → 🟡分歧 → position='观望（等确认）'
```

**2026-06-20 L1 优化**：做空增加 **near_top 捷径**（跳过共振门槛，直接触发偏空）：
```
RSI_1d > 67 + 1D方向='下降' + MACD_h_4H < 0 → position='偏空'/'试空'
```
该逻辑在 `_format_coin_section`（stdout）和 `analyze_single_coin`（JSON）两处均有实现，同时 V 反保护（near_bottom/反弹>3%）会在做空触发后降级为观望。

> 📊 数据验证（2026-06-19/20）：4 条有 resonance 的记录中，ETH 2 次🔴偏弱、BTC 2 次🟡分歧。

### 7.8 已知局限 & 待改进

| 局限 | 影响 | 状态 |
|------|------|------|
| 共振门槛 2/3 | 做空信号仍偏少（需至少 2 指标偏空） | 🟡 已部分缓解：near_top 提供额外入口 |
| FG<15 上下文区分 | V 反恐惧 vs 阴跌恐惧目前通过 near_bottom/反弹检测区分 | ✅ 已实现：L3 V 反保护 |
| 做空入口 | 此前无 near_bottom 等价捷径 | ✅ 已实现：L1 near_top |
| 社交动态方向标签硬编码 | 此前始终输出 偏多 | ✅ 已修复：读 position 字段动态切换 |

### 7.9 MACD_4H vs MACD_1H 设计决策

**为什么用 4H 而非 1H**（2026-06-20 数据验证）：

近 4 条记录中，4H MACD 和 1H MACD **100% 方向冲突**：
```
BTC: 4H=空(-535)  1H=多(+154)  ⚡
ETH: 4H=空(-16)   1H=多(+2)    ⚡
```

1H MACD 在下跌通道中频繁翻红（日内反弹），用 1H 判断方向会导致：
- `near_top` 条件 `MACD<0` 永不触发
- 共振评分 MACD 项从 -1 变 +1
- **做空信号更少**（与优化目标相反）

**结论**：4H MACD 保持不变。对应持有周期（6h-72h 复盘窗口），滤掉日内噪声。

### 7.10 教训系统现状（2026-06-20 更新）

- `process_reviews.py` 有 `extract_lessons()` 函数和 `save_lessons_regime_aware()` 写入
- `lessons.json` + `regimes/{regime}_lessons.json` 双轨存储
- **2026-06-22 修复（#13）**：重建 `lessons.json` 时过滤空条目（`len(lesson.strip()) ≤ 10`）
- **2026-06-22 修复（#9）**：`levels_score` 新增 -1 分支（支撑全部失效），"价位误判" 教训可生成
- 轻量闭环：`inject_lessons.py`（cron :05）刷新 `.lesson_context.txt`

### 7.11 方向判定 5 处同步 + SL/TP 统一（🆕 2026-06-22 全审计）

**方向判定必须在 5 个位置保持阈值一致**，修改任一处即需同步其余 4 处：

| # | 文件 | 函数/位置 | 变量名 |
|---|------|-----------|--------|
| 1 | `analysis_template.py` | `_format_coin_section` | `pos_dir` |
| 2 | `_social_publish.py` | `analyze_single_coin` wrapper | `position_raw` |
| 3 | `analysis_template.py` | `main()` JSON builder | `position` |
| 4 | `analysis_template.py` | SL/TP fallback | `_pos_quick` |
| 5 | `_social_publish.py` | `extract_direction()` | 返回值 |

**SL/TP 已统一为单一实现**：模块级函数 `calc_sl_tp()`（`_social_publish.py`），被 `generate_social_draft()` 和 `publish_social.py` 共同调用（三方同源，消除 67 行重复代码），草案与 `social_analyses.json` 的 SL/TP 值完全一致。

**patch 工具陷阱**：修改含 raw string 的正则时（如 `rf'...\d...'`），patch 可能双重转义 `\\d` → `\\\\d`，导致正则匹配字面 `\d` 而非数字。修改后必须用 `import re; re.search(...)` 验证。

- `process_reviews.py` 有 `extract_lessons()` 函数和 `save_lessons_regime_aware()` 写入
- `lessons.json` + `regimes/{regime}_lessons.json` 双轨存储
- **2026-06-20 门槛调整**：之前仅在评分=-1 时触发 → 现在 `total ≤ 1`（即 +2/+3 才跳过）。新增四类中性教训：「方向未确认」「信号未确认」「价位误判」「价位未触及」
- **轻量闭环已上线**：`inject_lessons.py`（cron :05）刷新 `.lesson_context.txt`，`§3.6` 强制分析前加载
- **无权重反馈**：教训系统是存档+上下文注入，不反馈到 `resonance` 评分权重（重量闭环待 2026-07-20 讨论）
- **重型闭环设计**：详见 [references/weight-adjustment-framework.md](references/weight-adjustment-framework.md)，计划 2026-07-20 根据累计数据讨论

## 八、相关技能

| 技能 | 关系 |
|------|------|
| `社交动态发布` | 📖 发动态操作手册（分析结论消费方） |
| `三层复盘引擎` | 三层复盘引擎（消费 analyses.json） |
| `强制分析规范` | 分析输出核验纪律 |
| `分析结论模板` | 分析模板规范 |
