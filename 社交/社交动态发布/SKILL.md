---
name: 社交动态发布
description: 发动态文案8步流水线。步骤：同步K线→分析行情→复盘上条→按模板写文案→核验数据→用户审核→生成配图→发完整文案+配图给你（不自动发频道）。触发词：发动态、发帖、发文案、我要发。⚠️ 不是纯分析——纯分析用「加密货币纯分析」skill。与纯分析工作流是两条独立流程，模板不可混用。
---

# 发动态文案步骤

> 最后更新：2026-06-22
> 核心入口：`publish_social.py`（8步流水线）
> 更新：新增数据转录铁律+提取脚本、加载技能强制规则、Pitfalls 10.16/10.17
> 模板来源：`Obsidian/2-分析框架/社交动态模板v5.1.md`
> ⚠️ 与纯分析（`加密货币纯分析` skill）是两条独立流程，输出模板不可混用

### 💡 本技能已加载，立即进入执行

**下一步：直接运行 `python3 publish_social.py BTC ETH` 启动发动态流水线，不要停在这里。** 本技能包含：
- 一键入口命令（避免 `analysis_template.py --social BTCUSDT` 等错误尝试）
- 11个已知坑（10.1~10.15）——每次核验失败几乎都能在这里找到答案
- 手动流程每一步的 JSON 数据提取方法

> 真实代价：2026-06-22 一次发动态因未加载技能，20分钟才完成（应 <5min），且文案中混入凭空数字 `63188`。

---

### ⛔ 执行前必查清单（每次发动态前逐项过，漏一项 = 浪费 15min+）

| # | 检查项 | 错了会怎样 |
|---|--------|-----------|
| 1 | `skill_view("社交动态发布")` 已加载？ | 走弯路试错 → 20min 变 3min |
| 2 | 入口用 `python3 publish_social.py BTC ETH`，不自己拼参数 | `analysis_template.py --social BTCUSDT` 报错白跑 |
| 3 | 所有数字从 `social_analyses.json` 逐字段提取，不用 stdout / 记忆 | 编造出 `63188` 这种不存在的数据 |
| 4 | 文案写好每个数字后，回头看 JSON 确认 | 核验 11/11 通过但有假数字 |
| 5 | 核验 `exit 1` → 改文案（不改 JSON），不重试同一条命令超过 1 次 | 死循环超时 |
| 6 | ~~FG 核验偏差 → 先清理旧 FG 缓存再核验~~ （✅ 已修复：FG 按时间戳取最新记录，不再需要手动清理缓存） | ~~FG: 文案=20 JSON=23 假阳性~~ |
| 7 | 价格不加 `$` 和千分位逗号 `,` | 核验正则 `[\\d.]+` 提取失败 ~~（2026-06-22 修复：`generate_social_draft()` 已自动输出 `int()` 格式）~~ |
| 8 | 支撑阻力用 `→` 分隔，不用 `/` | ~~同上。2026-06-22 修复：`generate_social_draft()` 已自动输出 `→` 格式~~ |
| 9 | 观望方向不要写 `🎯` 前缀 | 核验报 ⚠️ 警告 |
| 10 | 先复盘再写文案（`review_last_post.py --save`） | 文案缺复盘段落 |
| 11 | 核验失败 >2 次 → 告诉用户，不继续闷头改 | 无限循环浪费上下文 |
| 12 | 核验通过后跑 `python3 scripts/audit_draft.py --social 文案` 交叉审计 | 核验器只管11个结构化字段，叙述段数字是盲区 |

---

## 一、前置条件

### ⛔ 最高优先级规则：绝不自动发送

**所有动态交付物只发给用户本人，不发到任何Telegram频道。**

流程必须在以下节点停下等待用户输入：
1. Step ⑤ 核验通过后 → 发文案草稿给用户
2. 等待用户说"确认"或"没问题"
3. Step ⑧ → 发完整文案 + 配图给用户
4. **结束。不发到任何频道。**

> 即使用户确认了文案，也只发完整文案+配图给用户本人，不在Telegram发动态。
> 用户不在Telegram发布动态，仅用于沟通。

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
3. `_social_publish.py` 从 `analysis_template` 导入全部底层函数（`analyze_single_coin` 作为 `_base_analyze` + 11 个指标计算函数），在 `_base_analyze` 基础上补充 position/vp/wyckoff/calendar/macro/**flash_news** 六个社交字段。两个工作流共享同一套计算引擎。

**产物**：
- `social_analyses.json` — 完整对象格式（含 `indicators`/`levels_4h`/`sl_val`/`tp_val`/`rr_str`）
- FG 记录（`coin: "FG"`）
- 复盘文本（`coin: "REVIEW"`）

> `analyses.json`（flat_old 格式）由独立的 `加密货币纯分析` 工作流维护，与此流程**完全分离**。

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

📰 快讯风往哪吹？（🆕 2026-06-20）
{金十加密相关快讯，取前3条关联度最高的}

---
{一锤定音的结论}

📍 BTC 支撑 {S1}→{S2} | 阻力 {R1}→{R2}
📍 ETH 支撑 {S1}→{S2} | 阻力 {R1}→{R2}

🎯 BTC {方向} | 入场{区间} | 止损{sl} | 止盈{tp} | RR 1:{rr}
🎯 ETH {方向} | 入场{区间} | 止损{sl} | 止盈{tp} | RR 1:{rr}
（方向为「观望」时改为：🎯 BTC 观望 | 空仓等风）

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
| VP POC/VAH/VAL | `social_analyses.json` `session_vp` 字段（**24h 全天**，非单时段） | 信 stdout 行情 |
| 快讯 | `social_analyses.json` `flash_news` 字段（`[{time, content, score}]`）🆕 2026-06-20 | 自由发挥 |
| 回升百分比 | `analysis_template` | 凑整 |
| K线形态 | `kline_patterns`（JSON） | 信 stdout（可能多了不存在 LPS） |
| 威科夫阶段 | `wyckoff_data.phase`（JSON） | 自由发挥 |
| 现价 | `indicators.1H.last_close` 或 `ticker.last`（JSON） | 信 stdout ticker（可能差几十点） |
| 金十事件名 | `calendar_events`（JSON） | 简化措辞 |
| 宏观/订单流 | `.regime_cache.json` 的 `dimensions.macro_external` / `dimensions.order_flow` | `social_analyses.json` 中的 `macro_external` 和 `order_flow` 可能为空对象 `{}`，必须从 regime_cache 提取 |

#### ⚠️ 方向标签（2026-06-20 已修复）

~~旧版 `generate_social_draft()` 硬编码了 `偏多`（L1676-1677），未读取 `position` 字段。~~ 

**已修复**：
- 🎯 行方向标签现在读取 `btc.get('position', '观望')` / `eth.get('position', '观望')` 动态切换
- `analyze_single_coin` 返回值新增 `position` 字段（从 resonance 推导）
- P0 修复：SL/TP 区分多空 — 做空 SL 在高位上方、TP 在低位
- P1b 修复：SL 改用 ATR 缓冲，不再用硬编码 0.99/0.985

#### ⚠️ 观望币种 🎯 行格式（2026-06-22 修正）

**正确格式**：
- 偏多/偏空：`🎯 BTC 偏多 | 入场64384 | 止损61559 | 止盈66426 | RR 1:0.7 ⚠️`
- 观望：`🎯 BTC 观望 | 空仓等风` **可以带 🎯**（2026-06-22 修复：核验器改为检测是否含数字，纯文本 🎯 行不报 ⚠️）

**核验器行为**：观望时 `🎯 ETH 观望 | 空仓等风` 不再报 ⚠️。只有文案误加了入场/止损/止盈数字时才报 ⚠️。

#### ⛔ 数据转录铁律（2026-06-22 强化）

**每写一个数字到文案前，必须从JSON逐项核验。禁止凭 stdout 或记忆写数字。**

**一键提取脚本**（复制粘贴运行，输出所有文案所需数字）：
```bash
cd /root/.hermes/skills/social/社交动态发布
python3 scripts/extract_social_data.py
```

也可以从技能目录外调用：
```bash
python3 /root/.hermes/skills/social/社交动态发布/scripts/extract_social_data.py
```

最容易出错的三类（本次会话实测翻车）：

| 陷阱 | 错误做法 | 正确做法 |
|------|---------|---------|
| 1D 收盘价 | 凭记忆写 `"收在63188"` | 从 `indicators.1D.last_close` 提取 → `64192` |
| VAH 值 | 从 stdout 抄 `64442` | 从 `session_vp.vah` 提取 → `64469` |
| 道氏 1D 方向 | 凭 stdout 印象写 `"上升"` | 从 `indicators.1D.trend` 提取 → `盘整` |

**每次写文案时逐项对照此脚本提取数据**（复制粘贴运行）：
```bash
cd /root/.hermes/trade_review && python3 << 'PYEOF'
import json
with open('social_analyses.json') as f:
    data = json.load(f)
for e in reversed(data):
    coin = e.get('coin','')
    if coin not in ('BTCUSDT','ETHUSDT'): continue
    c = coin.replace('USDT','')
    ind = e.get('indicators',{})
    vp = e.get('session_vp',{})
    wy = e.get('wyckoff_data',{})
    lv = e.get('levels_4h',{})
    print(f'=== {c} ===')
    for tf in ['1D','4H','1H']:
        i = ind.get(tf,{})
        print(f'  {tf}: close={i.get("last_close")} trend={i.get("trend")} RSI={i.get("rsi"):.0f} MACD_h={i.get("macd_h"):.0f} ADX={i.get("adx"):.0f} label={i.get("label")}')
    print(f'  VP: POC={vp.get("poc")} VAH={vp.get("vah")} VAL={vp.get("val")}')
    print(f'  Wyckoff: phase={wy.get("phase")} conf={wy.get("confidence")}')
    print(f'  Levels: lows[:2]={lv.get("lows",[])[:2]} highs[:2]={lv.get("highs",[])[:2]}')
    print(f'  ticker: {e.get("ticker",{}).get("last")}')
    print()
PYEOF
```
只有在此脚本输出确认后，才能将数字写入文案。此脚本的输出就是文案的数字来源。

#### ⚠️ 数字格式要求

- 价格**不加千分位逗号**（用 `64384` 而非 `$64,384`），否则 `verify_social_post.py` 的正则提取失败
- 支撑阻力同理：`62222→62254` 而非 `$62,222→$62,254`

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

核验通过后，**将文案发给你确认**，**不要跳过此步骤直接发送**。

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

### Step ⑧ 发送给你（不自动发布）

**重要**：发动态的最终交付物是**发给你确认**，**绝不**自动发到任何Telegram频道。用户不在Telegram发动态。

严格按这个顺序走：
1. Step ⑤ 核验通过后 → **先把文案草稿发给你确认**
2. 你回复"确定"之后 → **发完整文案给你**
3. **发配图给你**
4. **结束**。不发到任何频道，不调 `save_social_post.py`，不做任何额外操作。

> 即使用户确认了文案，也只发给你本人，不自动发送到频道。如用户需要存档，会自行调用 `save_social_post.py`。

---

## 三、快速执行（一键流水线）

### 完整发布

```bash
cd /root/.hermes/trade_review
python3 publish_social.py BTC ETH
```

`publish_social.py` 自动串行执行 Step ①→⑧，中间有核验阻断时会 `sys.exit(1)`，此时修正文案后重新运行即可。

### 仅分析+保存（供核验回退）

```bash
cd /root/.hermes/trade_review
python3 publish_social.py --verify-only BTC ETH
```

仅执行 Step ①+②（同步K线 + 分析行情 + 写入 social_analyses.json），跳过后续交互步骤。**退出码 0 即表示 social_analyses.json 已更新**，可直接用于 `verify_social_post.py` 核验。

---

## 四、常见问题

### Q: 核验显示 exit 1 怎么办？
A: 看核验输出中标记为 ❌ 的具体项，修正文案中的数字后重新走完整流程。

### Q: social_posts.json 丢了某条动态怎么办？
A: 检查 `/tmp/social_draft.txt`，这是自动保存的草稿缓存。用 `save_social_post.py` 手动写入。

### Q: 为什么文案的 LPS 数量和 stdout 不一样？
A: 以 JSON `kline_patterns` 为准。stdout 可能列出多个 LPS，但 JSON 只存实际检测到的。

### Q: 复盘结论为什么是"横盘"不是"错误"？
A: 复盘用 ±2% 阈值判定方向。价格变化在 ±2% 以内 = 横盘震荡，不是方向错误。

---

## 五、相关文件

> 📖 **完整架构地图**（所有文件 import 关系、数据流向、孤立文件、cron 守护脚本）：详见 `加密货币纯分析` skill 的 [`references/full-architecture-map.md`](references/full-architecture-map.md)。

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
| `price_path_report.py` | `trade_review/` | 走势路径报告（独立脚本） |
| `scripts/audit_draft.py` | `trade_review/scripts/` | 文案数字交叉审计（v2语义匹配，覆盖核验器盲区）🆕 2026-06-22 |
| `scripts/audit_draft.py` | `skills/social/社交动态发布/scripts/` | 同上（技能内副本，随技能同步）🆕 2026-06-22 |
| `references/audit-draft-usage.md` | `skills/social/社交动态发布/references/` | `audit_draft.py` 使用手册（输出分组解读、语义映射表、已知局限）🆕 2026-06-22 |
| `check_db_integrity.py` | `trade_review/scripts/` | ~~DB 完整性检查~~ — 2026-06-21 删除 |
| `verify_workflow.py` | `trade_review/scripts/` | 工作流验证（诊断工具） |
| `social_posts.json` | `trade_review/` | 动态记录 |
| `social_reviews.json` | `trade_review/` | 复盘记录 |
| `social_analyses.json` | `trade_review/` | 社交发动态分析缓存（full_obj 格式，详见 [references/two-file-architecture.md]） |
| `references/manual-draft-workflow.md` | `skills/social/社交动态发布/` | 手动撰写文案并通过核验的完整流程（含数据提取脚本、格式要求、FG清理规避方案） |
| `/_tmp/social_draft.txt` | `/tmp/` | 草稿缓存（恢复用） |
| `jin10_fallback.py` | `trade_review/` | 金十数据源（日历+快讯+新闻）🆕 2026-06-20 |
| `social_analyses.json` | `trade_review/` | 社交发动态分析缓存（full_obj 格式，详见 [references/two-file-architecture.md]） |

| 技能 | 关系 |
|------|------|
| `加密货币纯分析` | 📖 纯分析操作手册（发动态的前置步骤） |
| `三层复盘引擎` | 三层复盘引擎（与社交动态复盘独立） |

## 九、统一计算层（2026-06-20 已修复）

**修复前**：`_social_publish.py` 有 11 个底层计算函数的独立副本，与 `analysis_template.py` 实现不同，导致 `social_analyses.json` 与 `analyses.json` 的指标值不一致（13/22 字段不同）。

**修复后**：`_social_publish.py` 的 `analyze_single_coin()` 改为 50 行 wrapper，底层统一调用 `analysis_template.analyze_single_coin()`。所有底层函数（RSI/MACD/ADX/BB/trend/label 等）从 `analysis_template` import。wrapper 在此基础上补充 position/vp/wyckoff/calendar/macro/flash_news 六个社交字段。两个工作流共享同一套计算引擎，产出的分析数据从根源一致。

**详细对比**：见 `加密货币纯分析` skill 的 `references/helper-function-divergence.md`（历史记录）。

---

## 十、已知坑与变通方案

### 10.1 gen_charts.py 四 bug（import json + UnboundLocalError + RSI键名 + Style2 MACD全NaN）（✅ 2026-06-21 已修复）

`python3 scripts/gen_charts.py --style 4 /tmp/social_chart.png` 曾有四个 bug：

**Bug 1 — 缺少 `import json`**：模块级导入没有 `json`，L360 `json.load(af)` 触发 `NameError`，被 `except Exception: pass` 静默吞掉 → `records` 始终为空，RSI 回退 50 / Footer 徽章全显示 `?`。

**Bug 2 — UnboundLocalError 崩溃**：`records` 在 L356/L383 使用但 L400 才赋值 → 运行时崩溃（skill 文档 10.1 此前记录的已知坑，变通用 Style 2 替代）。

**Bug 3 — RSI/FG 键名不兼容**：L358/L385 用 `kline_table` 键名，但 `social_analyses.json`（优先数据源）用 `indicators` → RSI 始终取默认值 50。费率同理：`social_analyses.json` 顶层为 `funding_rate_pct`，`analyses.json` 嵌套在 `order_flow.funding_rate_pct`。

**Bug 4 — Style 2 MACD 全 NaN**：`style_dashboard()` L175-176 `fetch(…, '4H', 60)` 只取 60 根蜡烛，但 `calc_macd(slow=75)` 慢速 EMA 需 ≥75 根 → 整个 MACD 线 / Signal 线 / 柱状图全为 NaN，仪表盘 MACD 面板空白。

**修复**（5 处改动）：
1. L12 补 `import json`
2. JSON 加载移到 L352（使用前），消除作用域错误
3. 键名双兼容：`r.get('indicators') or r.get('kline_table', {})`
4. 费率双路径：先读顶层 `funding_rate_pct`，若 None 则回退 `order_flow.funding_rate_pct`
5. Style 2 fetch 量 60→100（慢速 EMA 75 + 信号线 9 + 余量，确保 MACD 有值）
6. Footer badges 复用已加载的 records，去掉重复读文件

### 10.2 核验脚本与「观望」冲突（✅ 已修复 6/20，优化 6/22）

**旧行为**：BTC/ETH 的 `position` 均为「观望」时，文案按规则输出「🎯 观望 | 空仓等风」（非入场/止损/止盈/RR），但 `verify_social_post.py` 旧版用正则 `🎯\s*{coin}.*?入场([\d.]+)` 提取数字失败，报 8 个 ❌ 硬失败，`exit 1` 阻断 `publish_social.py` 自动流程。

**修复 v1**（2026-06-20）：若含「观望」→ `continue` 跳过该币种的全部 🎯 结构化校验，但叙事字段也被跳过。

**修复 v2**（2026-06-22 优化）：改为 `else:` 模式——观望时**只跳过入场/止损/止盈/RR 的数字校验**，叙事字段（RSI/MACD/VP/费率/宏观/威科夫/K线）仍正常核验。同时 🎯 行误报检测改为检查是否含数字（`空仓等风` 这类纯文本不触发）。

### 10.3 publish_social.py 写入时 Timestamp 字段遗漏（✅ 已确认不存在）

经 2026-06-21 核验：`save_social_post.py` L46 始终生成 `time` 字段（`datetime.now(BJT)`），`review_last_post.py` L162 读取的也是 `prev.get('time')`。`social_posts.json` 全部 5 条记录均有有效 `time` 值。此前报告的「Post #15 timestamp=None」为一次性写入异常，已由后续发布自动修正。

### 10.4 publish_social.py 自动草案缺少 VP/形态细节（⚠️ 已知）

`generate_social_draft()` 产出的是框架性草稿，缺少 VP POC/VAH/VAL 具体数值、K线形态描述（Spring/LPS/SOS）。最终文案需 LLM 从 `social_analyses.json` 手动提取补全。

### 10.5 快讯集成（✅ 2026-06-20 已修复）

`flash_news` 已通过金十 MCP `list_flash` + `search_flash` 集成到两个工作流。`publish_social.py` 的记录字典包含 `flash_news` 字段，`generate_social_draft()` 在 📰 行输出前 3 条关联度最高的快讯。详见 `jin10_fallback.py`。

### 10.6 verify_social_post.py 字段不兼容 social_analyses.json（✅ 2026-06-21 第三轮审计修复）

`verify_social_post.py` 从 `social_analyses.json` 加载校验数据，但旧版期望的是 flat_old（analyses.json）的字段名，导致约 40% 核验项静默跳过。

**三轮共 3 项修复（P1/P2/P3）**：

| 问题 | 旧值（verify 期望） | 新值（social 实际存储） | 修复 |
|------|---------------------|------------------------|:--:|
| 指标表键名 | `kline_table` | `indicators` | ✅ P1 |
| 指标内价格键 | `close` | `last_close` | ✅ P1 |
| %b 路径 | `pct_b`（顶层） | `bb.pct_b`（嵌套） | ✅ P1 |
| 支撑阻力 | `support`/`resistance`（数组） | `levels_4h.lows`/`highs`（字典内嵌） | ✅ P1 |
| K线形态键名+格式 | `kline_pattern_times`（dict） | `kline_patterns`（dict with `patterns` list） | ✅ P1 |
| VP 键名 | `vp_data`（旧 analyses.json） | `session_vp`（新）+ 向后兼容 `vp_data` | ✅ P3 |
| 回退死循环 | `run_analysis()` 调 `analysis_template.py` → 写 `analyses.json` 而非 `social_analyses.json` | 调 `publish_social.py --verify-only` → 写 `social_analyses.json` | ✅ P2 |

**P2 `--verify-only` 模式**：`publish_social.py --verify-only BTC ETH` 仅执行同步+分析+写入 social_analyses.json，提前 return 跳过后续交互步骤（复盘/文案/核验/配图/保存）。供 `verify_social_post.py` 回退使用。

### 10.7 V反保护补全（✅ 2026-06-20 二次审计修复）

`_social_publish.py` wrapper 的 `analyze_single_coin()` 中 position 判定的 V 反保护原本只有 `near_bottom` 检测，缺少 8 根 4H 蜡烛反弹 >3% 检测（与 `_format_coin_section` 不对齐）。RSI 30-33 区间（不触发 near_bottom）但价格已从近期低点反弹 >3% 时，`social_analyses.json` 的 `position` 会错误输出 `偏空`。

**修复**：wrapper L163-165 增加 `else` 分支，从 `result['close_status']['4H']['closed']` 取最近 8 根蜡烛检测反弹 >3%，触发则降级为 `观望（反弹X%，V反保护）`。三地 V 反保护（`_format_coin_section` / wrapper / main()）现在逻辑等价。

### 10.8 快讯 per-coin 重复拉取（✅ 2026-06-20 优化）

`publish_social.py` 中 `analyze_single_coin()` wrapper 每币种独立调用 `fetch_flash_news()`。虽然金十缓存 TTL=5min 减轻了重复，但多币种时仍浪费。

**修复**：`publish_social.py` 在循环外预拉取 `_shared_flash`，通过新增的 `flash_news` 参数传入 `analyze_single_coin()`。wrapper 收到非 None 值时直接复用，否则 fallback 自行拉取（保持向后兼容）。

### 10.9 auto_push_github.sh 路径遗漏 — gen_charts.py/save_social_post.py 未同步（✅ 2026-06-21 已修复）

`auto_push_github.sh` 原逻辑在 `$TRADE_REVIEW/` 根目录查找 `gen_charts.py` 和 `save_social_post.py`，但这两个文件实际位于 `$TRADE_REVIEW/scripts/` 子目录 → 检查不到 → 静默跳过 → 修改后 GitHub 上仍是旧版本。`review_last_post.py` 有独立的正确路径，但前两者被遗漏。

> 更新：gen_charts.py 五 bug 全修复（import json + UnboundLocalError + RSI键名 + Style2 MACD全NaN + Style1硬编码徽章），auto_push_github.sh 路径修复，timestamp=None 误报澄清，三轮审计 P1/P2/P3 修复，8 个核心脚本同步到 GitHub

### 10.12 宏观/订单流数据在 regime_cache 中（✅ 2026-06-21 确认）

`social_analyses.json` 中的 `macro_external` 和 `order_flow` 字段可能为空对象 `{}`。实际数据存储在 `.regime_cache.json` 的 `dimensions.macro_external` 和 `dimensions.order_flow` 中。

**提取路径**：
```python
import json
with open('.regime_cache.json') as f:
    rc = json.load(f)
dim = rc.get('dimensions', {})
fg = dim['macro_external'].get('fg_actual')  # FG 值
dxy = dim['macro_external'].get('dxy')        # DXY
vix = dim['macro_external'].get('vix')        # VIX
fr = dim['order_flow'].get('funding_rate_pct') # 资金费率
taker = dim['order_flow'].get('taker_buy_ratio') # Taker 买卖比
```

**原因**：`_social_publish.py` wrapper 的 `analyze_single_coin()` 在写入 `social_analyses.json` 时，`macro_external` 和 `order_flow` 的提取路径可能未正确填充（空对象 `{}`），但 `.regime_cache.json` 是 regime_update.sh 定期更新的权威来源。

### 10.11 全量同步清单（✅ 2026-06-21 新增）

`auto_push_github.sh` 现覆盖 **22 个核心脚本 + 8 个 cron 守护 + 10 个 SKILL.md**。未同步：
- `place_live_orders.py` — 含 API 密钥，手动调用

孤立文件：
- ~~`review_path_enhancer.py`~~ — 2026-06-21 删除（功能被 `price_path_report.py` 替代）

### 10.10 gen_charts.py Style 1 徽章硬编码 → 动态数据（✅ 2026-06-21 已修复）

`gen_charts.py` Style 1 的 3 个底部徽章曾是硬编码占位文字（`FG EXTREME FEAR` / `6-DAY UPTREND` / `BREAKOUT CONFIRMED`），不反映实时市场状态。

**修复**：徽章改为从 `social_analyses.json` 读 FG 值 + 从 DB 计算 up/down 日数 + 实时判断突破状态：
- `FG: {val} {LABEL}` — 读 JSON `FG` 记录
- `{n}/10 UP DAYS` — DB 查询最近 10 根日线涨跌计数
- `BREAKOUT` / `RANGE-BOUND` — 比较当前价 vs 7 日最高价

### 10.13 verify_social_post.py 取第一条FG记录而非最新（✅ 2026-06-22 已修复）

`verify_social_post.py._load_analyses()` 加载FG记录时用 `for r in data: if r.get('coin')=='FG': out['FG']=r; break`，取的是**第一条**FG记录而非最新一条。

**修复**（2026-06-22）：改为遍历所有 FG 记录，按 `timestamp` 排序取最新一条。不再需要手动清理旧 FG 缓存。

### 10.14 generate_social_draft 与 _load_analyses 的 coin 命名不一致（⚠️ 已知 2026-06-22）

`_social_publish.generate_social_draft()` 内部用 `next((a for a in analyses if a['coin']=='BTC'), {})` 查找BTC数据，但 `social_analyses.json` 中存储的 coin 键是 `'BTCUSDT'` 而非 `'BTC'`。

**正确用法**：调用 `generate_social_draft()` 前，必须将 JSON 记录的 `'BTCUSDT'` 转换为 `'BTC'`：
```python
btc = dict(btc_raw); btc['coin'] = 'BTC'
eth = dict(eth_raw); eth['coin'] = 'ETH'
analyses = [btc, eth]
```

`publish_social.py` 的 `generate_social_draft()` 调用链中，传入的是 `analyze_single_coin()` 的返回值，其 `coin` 字段已被设为 `'BTC'`/`'ETH'`（见 `_social_publish.py` L131+），所以**通过 publish_social.py 自动生成的文案不受影响**。但**手动调用 generate_social_draft() 时必须注意转换**。

### 10.16 凭空数字混入文案（⚠️ 2026-06-22）

**现象**：核验 11/11 全通过后，回顾发现文案中 `"BTC 1D十字星收在63188"` 的 `63188` 在 JSON 和 stdout 中完全不存在（真实值 `64192`）。核验器只校验了结构化字段（价格/SR/FG），未覆盖叙述性字段中的具体收盘价。

**根因**：写文案时未从 JSON 逐项提取数字，凭记忆或推理编造了不存在的数据点。

**教训**：
1. 核验通过 ≠ 所有数字正确。核验器只覆盖 11 个结构化字段，叙述段落中的 K线收盘价、均线位置、OBV等不在校验范围
2. 文案中**所有数字**必须有 JSON 出处——写完一行数字，回头看一眼 JSON 确认
3. 如果核验通过后还发现数字错误，说明这个数字不在核验规则覆盖范围，需要扩展核验规则或手动核查

### 10.17 第一次发布时复盘数据缺失（⚠️ 2026-06-22）

`publish_social.py` 在本次会话中第一次运行时，内嵌的 `review_last_post()` 输出 `ℹ️ 无可复盘内容`，导致文案缺少「上条复盘」段落。

**根因**：`social_posts.json` 中没有当天或近期的帖子记录，复盘脚本一步返回空。

**修复**：手动运行 `PYTHONPATH=/root/.hermes/trade_review python3 scripts/review_last_post.py --save` 后再生成文案。复盘是发动态的必要前置。

### 10.15 publish_social.py 复盘数据丢失（⚠️ 2026-06-22）

`publish_social.py` 执行 `--verify-only` 模式时不调用 `review_last_post.py`，导致复盘数据缺失。手动流程中必须**先**运行复盘：
```bash
PYTHONPATH=/root/.hermes/trade_review python3 scripts/review_last_post.py --save
```
然后运行 `publish_social.py --verify-only BTC ETH` 或手动写文案。否则文案中 "上条复盘" 段落会为空。

### 10.18 全工作流审计（五轮共 24 项，✅ 全部已修复 2026-06-22）

2026-06-22 全量审计发现 24 个问题，分五轮全部修复。

**第一轮（14 项）**：SR格式、观望核验、position变量、方向逻辑、死代码/死分支、重复实现、未用导入、空条目、千分位匹配。

**第二轮（5 项）**：修复 #5 引入的 `near_bottom` NameError、patch 工具双重转义 `\\\\\\\\d`→regex 失效、JSON builder 缺少 `rsi>65+MACD<-50` 做空路径、`_pos_quick` fallback 忽略 near_bottom、wrapper MACD 阈值不一致。

**第三轮（3 项优化）**：SL/TP 统一到模块级 `calc_sl_tp()`（消除 67 行重复）、核验器叙事字段从关键词正则改为值匹配（消除 12 个 ⚠️ 假阳性噪音）、`_pos_quick` 补全 L1/L1b 做空捷径。

**第四轮（1 项）**：BTC 入场千分位逗号 `{int(x):,}` → `64,200` 导致 regex `[\d.]+` 匹配断裂 → 移除 `:,` 格式。

**第五轮（1 项）**：`calc_sl_tp` 无负 entry 守卫 → 增加 `entry <= 0` 提前返回。

**最终验证**：6 集成场景（含 2 边界）+ 5 calc_sl_tp 极端值 → 全部通过（0 ❌）。5 处方向逻辑完全一致、SL/TP 单一实现、核验器 15/15+ 全通过。

### 10.19 patch 工具修改 regex 原始字符串时会双重转义（⚠️ 2026-06-22）

**现象**：用 `patch` 修改含 Python raw string 的正则模式（如 `rf'...[\\d.]+...'`）时，工具可能把 `\\d` 转成 `\\\\d`（文件中变成两个反斜杠+d），导致正则 `\\\\d` 匹配字面 `\d` 而**不是数字**。

**检测**：`python3 -c "import re; print(repr(line))"` 查看实际字节数。正确应只有 1 个 `\` 在 raw string 中 = regex `\d`。

**修复**：用 `python3 << 'PYEOF' ...` 脚本做字节级替换，避开 patch 的转义逻辑。

**影响行**：`verify_social_post.py` 所有 `re.search(rf'...\d...')` 调用 — L122、L138、L142、L161、L164、L167、L170。

### 10.20 方向判定逻辑 5 处必须同步（⚠️ 2026-06-22）

修改做多/做空/观望的判定条件时，**必须同步修改以下 5 处**，缺一处就产生不一致的输出：

| # | 位置 | 变量名 | 格式 |
|---|------|--------|------|
| 1 | `_format_coin_section` (stdout) | `pos_dir` | `'试多'/'试空'/'观望'` |
| 2 | wrapper `analyze_single_coin` | `position_raw` | `'偏多'/'偏空'/'观望（...）'` |
| 3 | JSON builder `main()` | `position` | `'偏多'/'偏空'/'观望（...）'` |
| 4 | SL/TP fallback `_pos_quick` | `_pos_quick` | `'偏多'/'偏空'/'观望'` |
| 5 | `extract_direction()` | 返回值 | `'做多...'/'做空...'/'观望'` |

**判定阈值**（当前，2026-06-22，五轮审计后锁定）：
- 偏多：`'强' in resonance OR near_bottom`
- 偏空：`'弱' in resonance OR (rsi_1d>65 AND macd_4h<-50)`
- L1 做空捷径：`rsi_1d>67 AND trend_1d='下降' AND macd_4h<0`
- V反保护：`near_bottom` or 8根4H反弹>3% → 降级为观望

### 10.21 千分位逗号格式破坏 regex 匹配（⚠️ 2026-06-22）

`generate_social_draft()` 中 BTC 入场/止损/止盈用了 `{int(x):,}` 格式（千分位逗号），导致 `64,200` 被 regex `[\d.]+` 截断为 `64`，核验报偏差 99.9%。

**修复**：统一移除 `:,` 格式。BTC 价格用 `{int(btc_entry_f)}`，ETH 保持一致用 `{eth_entry_f:.0f}`。价格**不加千分位逗号**。

**影响范围**：`_social_publish.py` L389、核验器正则 L162/L165/L168/L171。

---

## 六、发布清单（发动态前逐项确认）

- [ ] K线数据同步完成，无缺口
- [ ] social_analyses.json 最新记录 ≤ 60 分钟
- [ ] 上条动态已复盘（`social_reviews.json` 有对应记录）
- [ ] 文案按模板 v5.1 格式输出
- [ ] 每个数字从 JSON 逐项提取，未凭记忆
- [ ] 宏观/订单流数据从 `.regime_cache.json` 提取（`social_analyses.json` 中可能为空）
- [ ] 核验通过（exit 0）
- [ ] 文案草稿已发给你确认
- [ ] 你确认后 → 发完整文案 + 配图给你
- [ ] **结束。不发到任何频道。**

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
