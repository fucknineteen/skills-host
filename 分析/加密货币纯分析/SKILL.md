---
name: 加密货币纯分析（强约束执行版）
description: 加密货币纯分析工作流。强制执行合同模式：所有数据必须来自analyses.json逐项核验，禁止凭stdout/记忆/经验推断。
---

# 加密货币纯分析 Skill（强约束执行版）

> 最后更新：2026-06-23  
> 适用范围：BTC / ETH / SOL / DOGE  
> 核心脚本：`analysis_template.py` → 写入 `analyses.json`  
> 模板来源：`Obsidian/2-分析框架/分析报告模板v5.0.md`

---

## EXECUTION CONTRACT（最高优先级）

当本 Skill 被加载后，必须进入【严格执行模式】。

本 Skill 中定义的流程、字段映射、核验规则、输出格式属于**强制执行合同**。

**禁止：**
- 跳过步骤
- 调整步骤顺序
- 合并步骤
- 使用替代流程
- 凭经验推断
- 凭记忆补全
- 根据 stdout 直接生成结论
- 使用未核验数据

**若任何规则无法满足：**
- 立即停止执行
- 不得继续分析
- 不得输出结论

---

## 第一部分：执行门禁

### GATE-1 数据同步门禁

**必须先执行：**
```bash
cd /root/.hermes/trade_review
python3 monitor_and_sync.py BTC ETH SOL DOGE
```

**确认同步完成后方可进入下一步。**

**禁止：**
- 使用历史缓存
- 使用旧分析结果
- 跳过同步步骤

**若同步失败：立即停止。**

---

### GATE-2 分析生成门禁

**必须执行：**
```bash
cd /root/.hermes/trade_review
python3 analysis_template.py BTC ETH SOL DOGE
```

**生成：** `analyses.json`

**只有 analyses.json 成功生成后，才允许进入下一步。**

**若 analyses.json 不存在：立即停止。**

---

### GATE-3 数据来源门禁

后续所有分析数据**必须来自 analyses.json**。

**禁止来源：**
- stdout
- 历史分析
- Agent记忆
- 推测结果
- 用户上下文中的旧结论

**若数据无法追溯到 analyses.json：禁止输出。**

---

## 第二部分：数据提取合同

### 唯一数据源原则

所有数字必须满足以下四项同时成立：

1. 数字
2. → JSON字段
3. → JSON路径
4. → 真值核验

**否则：禁止输出。**

---

### 字段映射合同

必须严格按照字段映射表提取。

**禁止：**
- 猜测字段
- 模糊匹配字段
- 自动寻找相似字段
- 使用替代字段

**若字段不存在：立即停止。并报告：**
- ⚠️字段不存在
- ⚠️疑似数据结构变更

**禁止继续分析。**

---

### None值处理合同

**特别规则：**

`.get(key, default)` 对于字段值为 `None` 的情况：
- **不会返回 default**

**因此，若字段值为 None：**

**禁止：**
- 使用默认值
- 使用0替代
- 使用空字符串替代
- 自行推测

**必须输出：**
- ⚠️字段值为None
- ⚠️未通过核验

---

## 第三部分：分析生成合同

### 分析来源合同

所有分析结论必须基于 `analyses.json` 提取的数据生成。

**禁止：**
- 先写结论再找依据
- 用经验修正数据
- 用主观判断覆盖数据

---

### 大币种输出合同

大币种分析必须使用 `标准分析模板 v5.0`。

**禁止：**
- 删除字段
- 增加字段
- 修改字段顺序
- 改写模板结构

---

### 快讯处理合同

快讯必须按关联度排序。

**仅保留前3～5条最相关内容。**

**禁止：**
- 按发布时间排序
- 堆砌全部快讯
- 引用低关联内容

---

## 第四部分：核验纪律（强制执行）

### 输出前核验

输出前必须逐项检查：

- [ ] 数据来自 analyses.json
- [ ] 字段映射正确
- [ ] JSON路径存在
- [ ] 数值与真值一致
- [ ] 模板结构正确

**全部通过：允许输出。否则：停止。**

---

### 输出时核验

**每个数字必须标注JSON字段路径，保证可追溯。**

**若无法标注：不得输出该数字。**

---

### 输出后核验

完成输出后，必须再次逐项核对：输出值与JSON真值是否一致。

---

### 错误处理合同

**若发现任意1项错误（包括数值错误/字段错误/路径错误/映射错误/模板错误）：**

- 当前分析视为失败
- 必须重新提取数据、重新生成分析、重新核验
- **禁止带错交付**

---

### 未核验数据规则

无法确认真实性的数据，统一标记为 `⚠️未核验`。

**禁止伪造结果。禁止猜测结果。**

---

## 第五部分：已知数据问题

### 周线缺口规则

1W周线在周中出现缺口属于正常现象。

**禁止判定为：**
- 数据损坏
- 数据缺失
- 同步失败

---

### 复盘哨兵规则

复盘记录中的 `待复盘` 属于占位哨兵值。

**不代表：**
- 已完成复盘
- 已验证
- 已确认

**禁止误判。**

---

### VP键名大小写陷阱（2026-06-23发现）

`session_vp` 的实际 JSON 键名是**全小写**：`poc` / `vah` / `val`。

用 `.get("POC")` / `.get("VAH")` / `.get("VAL")` 提取会返回 `None`。

```python
# ✅ 正确
vp = entry.get("session_vp", {})
poc = vp.get("poc")   # 62377.6
vah = vp.get("vah")   # 64058.24
val = vp.get("val")   # 61925.12

# ❌ 错误（返回None）
poc = vp.get("POC")   # None
```

另含 `hours` (24) 和 `bars` (96) 字段。

---

## 第六部分：三层复盘引擎

执行原 Skill 定义的三层复盘流程。

**禁止：**
- 跳过任意层
- 合并层级
- 省略复盘结果

---

## 第七部分：仓位方向逻辑

### v062309 四层架构

方向判定采用 L0→L3 递进，SL/TP 使用 `sltp_engine.py` 三层解耦：

```
L0  威科夫 → Spring+SOS/L0  /  UTAD+Vah→试空
L0.5 缠论 → 一买/二买→试多  /  一卖/二卖→试空
L1  道氏  → 1D上升+4H偏多→试多 / 1D下降+4H偏空→试空
L2  共振  → score≥2偏强 / ≤-2偏弱 / 其他分歧
L3  兜底  → near_bottom→试多 / 否则观望

Quality Layer (sltp_engine):
  SL: max(near_support,swing_low,chanlun_ZD) - ATR×0.3
  TP: 三层流动性目标 + 时间门控(≤120d/≤180d) + 信号级别门控(>4ATR需L0/L1)
  Trade Space: TP-SL ≥ 1.5×ATR

Execution Layer:
  RR <1.0→0  |  1.0-1.3→0.6%  |  1.3-1.8→1.4%  |  ≥1.8→2%
```

详见 `sltp_engine.py` 和 `chanlun.py`。

### 已知陷阱 v062309

**陷阱 v1**: `min(val, d1_l)` 吞零 — val=0时tp3=0。修复：`min(v for v in [val,d1_l] if v>0)`

**陷阱 v2**: signal_level 假阳性 L0 — 只检查 Spring/UTAD 关键词不验证完整条件。必须验证 `Spring+SOS+confidence≥50`。

**陷阱 v3**: wyckoff_data 先读后写 — `_social_publish.py` 中 L0 永远不触发。wyckoff_detect 必须在位置判定前调用。

**陷阱 v4**: wyckoff_detect 零除 — avg_vol=0时崩溃。修复：`if avg_vol>0 else 0`。

**陷阱 v5**: 大段替换 Escape-drift → 拆分为每段10-15行逐个 patch。

**陷阱 v6**: 备份时点错误 → 回滚丢失改动。备份必须在改动前执行。

**陷阱 v7**: 多文件未同步 → 信号层改动跨3个文件，回滚其一导致不一致。

严格执行全部规则。禁止使用旧版本逻辑。若规则冲突：以最新修复记录为准。**

---

## FINAL DELIVERY CONTRACT

最终输出中的任何数字、评级、方向判断、仓位建议，必须满足：

1. 来源于 analyses.json
2. 存在字段映射
3. 存在JSON路径
4. 已完成真值核验
5. 符合模板要求

**若任意条件不满足：禁止输出。立即停止。并说明原因。**

---

## 附：共享函数

| 函数 | 位置 | 用途 |
|------|------|------|
| `detect_v_reversal(closed_4h, ticker_last)` | `analysis_template.py` | V反检测：过去8根4H从最低点反弹>3%触发。供 `_social_publish.py` 导入复用，消除了 4 处重复代码和 position/extract_direction 之间的语义分歧。 |

## 附：字段映射表（来自原版 Skill）

| 结论中的内容 | 数据源字段 | 备注 |
|-------------|-----------|------|
| 现价 | `kline_table.1H.last_close` 或 `ticker_price` | **不用 stdout ticker 价** |
| TF收盘价 | `kline_table.{TF}.last_close` | 键名是 `last_close`，不是 `close` |
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
| VP POC/VAH/VAL | `session_vp.poc`/`vah`/`val`（24h 全天，⚠️键名全小写） | 用大写键.get("POC")返回None，见第五部分陷阱 |
| 订单流费率 | `order_flow.funding_rate_pct` | ETH/BTC 共用 regime_cache |
| 订单流Taker | `order_flow.taker_buy_ratio` | |
| FG | `macro_external.fg_actual` | |
| FG 标签 | `macro_external.fg_label` | "Extreme Fear" |
| DXY | `macro_external.dxy` | |
| VIX | `macro_external.vix` | |
| 10Y | `macro_external.yield10` | |
| BTC.D | `macro_external.btc_dominance` | |
| 仓位方向 | `position` | "观望（等确认）"/做多/做空 |
| 财经日历 | `calendar_events` | 仅列前3条 |
| 快讯 | `flash_news` | `[{time, content, score, url}]` 关联度排序 |
| 涨跌幅 | `change_pct` | `(last-open24h)/open24h` |

## 附：RR门禁（2026-06-23新增）

方向判定后计算RR。**RR<1.0时自动降级position为观望**，不输出劣质交易。3处同步：`_format_coin_section` + JSON writer + `_social_publish` wrapper。

## 附：SL/TP方向感知选价

`lows_near`升序/`highs_near`降序。做多取`[-1]`(最近支撑)，做空取`[-1]`(最近阻力)，替代原`[0]`(最远价位)。

## 附：回测结论

BTC+ETH 2.5月112信号：B_VP(SL=VAL/VAH)胜率22%最佳。详见`references/rr-gate-and-sltp-backtest.md`。

---

## 附：核验清单

- [ ] 现价 ← ticker.last / ticker_price
- [ ] 涨跌幅 ← change_pct
- [ ] 各周期 RSI/MACD/ADX/%b/trend ← indicators.{TF}.*
- [ ] 威科夫阶段/置信度 ← wyckoff_data.phase/confidence
- [ ] VP POC/VAH/VAL ← session_vp.poc/vah/val（⚠️全小写键名）
- [ ] 支撑/阻力 ← levels_4h.lows/highs
- [ ] 宏观指标 ← macro_external.*
- [ ] 费率/Taker ← order_flow.*
- [ ] 快讯 ← flash_news[*]
- [ ] 风险项 ← risks（如为空则不写）

---

## 相关文件

| 文件 | 位置 | 用途 |
|------|------|------|
| `analysis_template.py` | `trade_review/` | 分析主脚本 |
| `analyses.json` | `trade_review/` | 分析结论缓存 |
| `reviews.json` | `trade_review/` | 三层复盘记录 |
| `monitor_and_sync.py` | `trade_review/` | K线同步 |
| `okx_klines.db` | `trade_review/` | K线SQLite数据库 |
| `process_reviews.py` | `trade_review/` | 三层复盘引擎 |
| `jin10_fallback.py` | `trade_review/` | 金十数据源（日历+快讯） |
| 分析报告模板v5.0.md | `obsidian-vault/2-分析框架/` | 分析报告输出模板 |
