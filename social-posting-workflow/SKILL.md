---
name: social-posting-workflow
description: 发动态文案7步流水线。步骤：同步K线→分析行情→复盘上条→按模板写文案→核验数据→用户审核→配图+保存。与纯分析工作流(crypto-analysis-workflow)是两条独立流程，模板不可混用。
---

# 发动态文案步骤

> 最后更新：2026-06-20
> 核心入口：`publish_social.py`（7步流水线）
> 模板来源：`Obsidian/2-分析框架/社交动态模板v5.1.md`
> ⚠️ 与纯分析（`crypto-analysis-workflow` skill）是两条独立流程，输出模板不可混用

---

## 一、前置条件

### 1.1 必须满足才能发动态

| 条件 | 检查方式 |
|------|----------|
| K线数据最新 | `python3 monitor_and_sync.py BTC ETH` |
| social_analyses.json 有当天完整对象格式记录 | 必须有 `indicators` + `levels_4h` 字段 |
| 上条动态已复盘 | `review_last_post.py --save` 成功 |
| 核验通过 | `verify_social_post.py "文案"` exit 0 |

### 1.2 不能发动态的时机

| 禁止窗口 | 原因 |
|----------|------|
| FOMC 公布前 48h | 方向置信度最低 |
| CPI/非农公布前 12h | 数据事件前不确定 |
| 金十财经日历 4★+ 事件前 6h | 重大事件前夕 |
| social_analyses.json 数据超过 2 小时 | 数据过期 |

---

## 二、7步发布流程

### Step ① 同步K线

```bash
cd /root/.hermes/trade_review
python3 monitor_and_sync.py BTC ETH
```

**要求**：
- 确认 5m/15m/30m/1H/4H/1D 全周期无缺口
- 输出必须显示 `数据验证通过：本地DB与OKX数据源一致`
- 如有缺口，标注可修复/不可修复

### Step ② 拉取数据 + 分析行情

`publish_social.py` 直接执行内置分析（不再调用 `analysis_template.py` 子进程）：

1. 拉取 FG + regime（宏观）
2. 并行拉取 ticker + funding rate → `analyze_single_coin()` 生成完整对象格式
3. `_social_publish.py` 从 `analysis_template` 导入全部底层函数（`analyze_single_coin` 作为 `_base_analyze` + 11 个指标计算函数），在 `_base_analyze` 基础上补充 position/vp/wyckoff/calendar/macro 五个社交字段。两个工作流共享同一套计算引擎。

**产物**：
- `social_analyses.json` — 完整对象格式（含 `indicators`/`levels_4h`/`sl_val`/`tp_val`/`rr_str`）
- FG 记录（`coin: "FG"`）
- 复盘文本（`coin: "REVIEW"`）

> `analyses.json`（flat_old 格式）由独立的 `crypto-analysis-workflow` 工作流维护，与此流程**完全分离**。

### Step ③ 复盘上条动态

```bash
PYTHONPATH=/root/.hermes/trade_review python3 scripts/review_last_post.py --save
```

**此步骤只运行一次**。复盘结论直接嵌入 Step ④ 文案。

**复盘内容**：
1. 读 `social_posts.json` 最新一条 → 提取入场区间 + SL
2. 用 `calc_order_price` 计算实际挂单价作为 entry 基准
3. 查入场后 1H K线（最多48根）→ 路径分类
4. 判定方向（±2% 阈值）+ SL 触发检测
5. 写入 `social_reviews.json`

**产出的复盘格式**：
```
📋 上条复盘
Post #{id} {时间} | BTC {方向} {入场}→{现价} ({+/-变化%}) {✅/❌/🟡} {路径类型}
                  | ETH {方向} {入场}→{现价} ({+/-变化%}) {✅/❌/🟡} {路径类型}
```

> ⚠️ 社交动态复盘是一次性的，与三层复盘引擎（`process_reviews.py`，每小时 cron）是**两套独立系统**。区别见第八节。

### Step ④ 按模板写文案

**强制使用** `Obsidian/2-分析框架/社交动态模板v5.1.md`。

**文案结构**（v5.1）：
```
{一句话炸裂标题} 🔥

📋 上条复盘
{时间} | BTC {方向} {入场}→{现价} ({+/-变化%}) {✅/❌/🟡}
       | ETH {方向} {入场}→{现价} ({+/-变化%}) {✅/❌/🟡}

🕐 BJ {月-日} {时:分} | BTC ${价} | ETH ${价} | FG:{值}

💡 大资金在干嘛？
{威科夫阶段大白话 + VP POC位置 — 说人话，不念经}

📐 盘面看了什么？
{道氏方向 + 关键指标 + 共振标签 + PA精要}

📊 钱堆在哪？
{费率 + VP POC/VAH/VAL + 解读}

🌍 宏观给不给面子？
{FG + DXY/VIX/10Y + 关键事件}

---
{一锤定音的结论}

📍 BTC 支撑 {S1}→{S2} | 阻力 {R1}→{R2}
📍 ETH 支撑 {S1}→{S2} | 阻力 {R1}→{R2}

🎯 BTC {方向} | 入场{区间} | 止损{sl} | 止盈{tp} | RR 1:{rr}
🎯 ETH {方向} | 入场{区间} | 止损{sl} | 止盈{tp} | RR 1:{rr}

💬 {金句收尾 — 金句/数字/反差异，取其二}

🤖 五系统AI分析，仅供参考
```

---

#### 写作规则（11条）

| # | 规则 | 示例 |
|---|------|------|
| 1 | 标题决定50%阅读量 | 数据震撼/反直觉/结论先行/悬念/挑衅五选一 |
| 2 | 开头2行定生死 | 反差、紧迫感、场景化 |
| 3 | 每段解决一个问题 | 💡大资金/📐盘面/📊钱堆/🌍宏观 |
| 4 | 说人话不念经 | "Spring后Test成功" → "庄家砸到60638再回踩61060没破" |
| 5 | 价位用📍行内标注 | 比表格快扫 |
| 6 | 结论一句话说清方向+条件 | "等放量阳线突破VAH即确认反转" |
| 7 | 收尾要让人记住 | 金句/数字/反差异，取其二 |
| 8 | 仓位一行写完 | 方向+入场+止损+止盈+RR，RR<1.5标⚠️ |
| 9 | 去废词 | 删"建议/可能/或许" |
| 10 | Telegram不发第二遍 | 每条动态独立 |
| 11 | 每段≥2-3个精确数字 | 说"量2.8M是正常3-5倍"，不说"放量" |

#### PA精要写法（穿插在 📐 盘面段）

| PA概念 | 说人话写法 |
|--------|-----------|
| Spring | "庄家砸到 xxxx 但没收下去 → 假突破失败，反转信号" |
| UTAD | "冲到 xxxx 被拍回来 → 假突破，Failed Failure" |
| High 2 / Low 2 | "第二段回调结束，最强入场点" |
| 80-20 规则 | "80% 的突破都会失败，真突破要有跟随确认" |
| Always-In | "HH+HL 结构完好 = 维持做多判断" |
| PA > 指标 | "结构好但 RSI 偏弱 → 信结构不信指标" |

---

#### 数字来源规则

| 数据类型 | 来源 | 禁止 |
|----------|------|------|
| 连跌天数 | `regime_detector` | 凭印象数 |
| 费率负值期数 | `fr_pos_ratio` | 四舍五入 |
| VP POC/VAH/VAL | `social_analyses.json` `vp_data` 字段（**24h 全天**，非单时段） | 信 stdout 行情 |
| 回升百分比 | `analysis_template` | 凑整 |
| K线形态 | `kline_patterns`（JSON） | 信 stdout（可能多了不存在 LPS） |
| 威科夫阶段 | `wyckoff_data.phase`（JSON） | 自由发挥 |
| 现价 | `kline_table.1H.close` 或 `ticker.last`（JSON） | 信 stdout ticker（可能差几十点） |
| 金十事件名 | `calendar_events`（JSON） | 简化措辞 |

#### ⚠️ 方向标签（2026-06-20 已修复）

~~旧版 `generate_social_draft()` 硬编码了 `偏多`（L1676-1677），未读取 `position` 字段。~~ 

**已修复**：
- 🎯 行方向标签现在读取 `btc.get('position', '观望')` / `eth.get('position', '观望')` 动态切换
- `analyze_single_coin` 返回值新增 `position` 字段（从 resonance 推导）
- P0 修复：SL/TP 区分多空 — 做空 SL 在高位上方、TP 在低位
- P1b 修复：SL 改用 ATR 缓冲，不再用硬编码 0.99/0.985

---

### Step ⑤ 核验文案

```bash
cd /root/.hermes/trade_review
python3 verify_social_post.py "完整文案文本"
```

**核验架构（v4）**：数据源驱动 —— 从 `social_analyses.json` 读取真值，正则定位文案位置，精确比对。

**核验三类**：
| 类别 | 方法 | 容差 | 不通过 |
|------|------|------|--------|
| 结构化字段（📍🎯🕐） | 正则提取→与JSON比对 | 价格±3%, RR±0.1 | ❌ exit 1 |
| 叙述字段（📐📊🌍） | 检查JSON真值是否出现 | 80-120字符内 | ⚠️ 警告 |
| 形态字段（威科夫/K线） | 检查phase/confidence/Spring/SOS | 上下文 | ⚠️ 警告 |

**exit 1 意味着什么**：
- ❌ 核验不通过 → `sys.exit(1)` → **Step ⑦ 保存被跳过**
- `social_posts.json` 中不会出现本次记录
- 修正文案后必须重新走完整流程（或手动调 `save_social_post.py`）

**草稿缓存**：文案会自动写入 `/tmp/social_draft.txt`。如果动态丢失，这是恢复来源。

### Step ⑥ 用户审核

核验通过后，将文案发给用户确认。**不要跳过此步骤直接发送。**

### Step ⑦ 生成配图

```bash
python3 scripts/gen_charts.py --style {1|2|3|4} /tmp/social_chart.png
```

**风格自动选择**：
| Style | 触发条件 | 风格 |
|-------|----------|------|
| 1 | 共振偏强 + MACD_h_4h > 200 | 营销K线 |
| 2 | 多指标共振 + ADX > 40 | 仪表盘 |
| 3 | 威科夫 Spring/SOS/LPS 明确 | 结构标注 |
| 4 | 其他/每日快照 | 方形卡片 |

配图保存在 `/tmp/social_chart.png`，缓存 1 小时内有复用。

### Step ⑧ 保存记录

```bash
PYTHONPATH=/root/.hermes/trade_review python3 scripts/save_social_post.py \
  --btc {价格} --eth {价格} --fg {FG值} \
  --direction-btc "{方向 入场 止损 止盈 RR}" \
  --direction-eth "{方向 入场 止损 止盈 RR}" \
  --regime "{当前阶段}" \
  --text "{完整文案}"
```

**去重**：检测文案与上条是否完全一致，一致则跳过（防止重复保存）。

**保存位置**：`social_posts.json`（ID 自增，5级滚动备份）

---

## 三、快速执行（一键流水线）

```bash
cd /root/.hermes/trade_review
python3 publish_social.py BTC ETH
```

`publish_social.py` 自动串行执行 Step ①→⑧，中间有核验阻断时会 `sys.exit(1)`，此时修正文案后重新运行即可。

---

## 四、常见问题

### Q: 核验显示 exit 1 怎么办？
A: 看核验输出中标记为 ❌ 的具体项，修正文案中的数字后重新走完整流程。

### Q: social_posts.json 丢了某条动态怎么办？
A: 检查 `/tmp/social_draft.txt`，这是自动保存的草稿缓存。用 `save_social_post.py` 手动写入。

### Q: 为什么文案的 LPS 数量和 stdout 不一样？
A: 以 JSON `kline_pattern_times` 为准。stdout 可能列出多个 LPS，但 JSON 只存实际检测到的。

### Q: 复盘结论为什么是"横盘"不是"错误"？
A: 复盘用 ±2% 阈值判定方向。价格变化在 ±2% 以内 = 横盘震荡，不是方向错误。

---

## 五、相关文件

| 文件 | 位置 | 用途 |
|------|------|------|
| `publish_social.py` | `trade_review/` | 7步流水线主入口（内置分析，无子进程） |
| `_social_publish.py` | `trade_review/` | 分析库：`analyze_single_coin()`（wrapper → `analysis_template.analyze_single_coin`）+ 文案生成 |
| `verify_social_post.py` | `trade_review/` | 文案数据核验（v4架构，读 social_analyses.json） |
| `monitor_and_sync.py` | `trade_review/` | K线同步 |
| `scripts/review_last_post.py` | `trade_review/scripts/` | 复盘上条动态 |
| `scripts/save_social_post.py` | `trade_review/scripts/` | 保存动态记录 |
| `scripts/gen_charts.py` | `trade_review/scripts/` | 配图生成 |
| `social_posts.json` | `trade_review/` | 动态记录 |
| `social_reviews.json` | `trade_review/` | 复盘记录 |
| `social_analyses.json` | `trade_review/` | 社交发动态分析缓存（full_obj 格式，详见 [references/two-file-architecture.md]） |
| `/tmp/social_draft.txt` | `/tmp/` | 草稿缓存（恢复用） |
| 社交动态模板v5.1.md | `obsidian-vault/2-分析框架/` | 文案输出模板 |

## 八、相关技能

| 技能 | 关系 |
|------|------|
| `crypto-analysis-workflow` | 📖 纯分析操作手册（发动态的前置步骤） |
| `social-dynamic` | 发动态自动化 skill（本文档是其精简版） |
| `trade-review-workflow` | 三层复盘引擎（与社交动态复盘独立） |

## 九、统一计算层（2026-06-20 已修复）

**修复前**：`_social_publish.py` 有 11 个底层计算函数的独立副本，与 `analysis_template.py` 实现不同，导致 `social_analyses.json` 与 `analyses.json` 的指标值不一致（13/22 字段不同）。

**修复后**：`_social_publish.py` 的 `analyze_single_coin()` 改为 50 行 wrapper，底层统一调用 `analysis_template.analyze_single_coin()`。所有底层函数（RSI/MACD/ADX/BB/trend/label 等）从 `analysis_template` import。wrapper 在此基础上补充 position/vp/wyckoff/calendar/macro 五个社交字段。两个工作流共享同一套计算引擎，产出的分析数据从根源一致。

**详细对比**：见 `crypto-analysis-workflow` skill 的 `references/helper-function-divergence.md`（历史记录）。

---

## 六、发布清单（发动态前逐项确认）

- [ ] K线数据同步完成，无缺口
- [ ] social_analyses.json 最新记录 ≤ 60 分钟
- [ ] 上条动态已复盘（`social_reviews.json` 有对应记录）
- [ ] 文案按模板 v5.1 格式输出
- [ ] 每个数字从 JSON 逐项提取，未凭记忆
- [ ] 核验通过（exit 0）
- [ ] 用户已审核文案
- [ ] 配图已生成（`/tmp/social_chart.png`）
- [ ] 动态已保存（`social_posts.json` 有新记录）

---

## 七、与三层复盘引擎的区别

| | 社交动态复盘 | 三层复盘引擎 |
|------|------|------|
| 脚本 | `review_last_post.py` | `process_reviews.py` |
| 触发 | 发动态前手动一次 | cron :03 每小时 |
| 复盘对象 | `social_posts.json` 帖子方向 | `analyses.json` 分析结论 |
| 频率 | 一次性 | 持续：6h/12h/72h |
| 输出 | `social_reviews.json` | `reviews.json` + `lessons.json` |
| 写入方式 | 追加新条目 | 原地更新同一条记录 |
