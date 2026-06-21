---
name: 三层复盘引擎
description: 三层复盘引擎。每小时 cron 自动运行 process_reviews.py，对 analyses.json 中的分析结论按时间阈值执行6h快判→12h深复盘→72h终局，提取教训写入 lessons.json。与社交动态复盘（review_last_post.py，发动态前一次性）是独立的两套系统。
---

# 三层复盘引擎

> 来源：`process_reviews.py`(927行) 脚本逻辑
> 与社交动态复盘（social-dynamic Step ③，`review_last_post.py`）是**两套独立系统**。
> 三层复盘复盘的是**分析结论**的准确性；社交动态复盘复盘的是**发出去的帖子**的方向对错。

## 触发方式

```
cron :03 每小时自动运行
```

```bash
cd /root/.hermes/trade_review && python3 process_reviews.py
```

**每小时运行一次**，读取 `reviews.json` 中所有待复盘记录，判定每条记录的 elapsed 时间是否满足 6h/12h/72h 阈值，满足则执行对应层级复盘。

---

## 数据流

```\nanalyses.json (纯分析结论 flat_old) + social_analyses.json (社交分析 full_obj)\n      ↓ 合并去重（social 优先）\nreviews.json (复盘记录列表，含 待复盘/已完成 状态)\n      ↓ 每小时 cron :03\nprocess_reviews.py\n      ↓  判定 elapsed ≥ 6h/12h/72h\n      ↓\nreviews.json (更新) + lessons.json (新教训) + regimes/{type}_lessons.json\n```

---

## Step 0: 同步补齐 + 去重

每次运行先做：
- **去重**：同 (coin, date) 有多条记录 → 保留 most complete 的一条
- **同步**：`analyses.json` 有新分析但 `reviews.json` 无记录 → 创建 `{"review_6h":"待复盘","review_12h":"待复盘","review_72h":"待复盘","completed":false}` 占位
- 每个币种每天只记第一条

---

## 三层复盘

| 层级 | 触发阈值 | 复盘内容 | 数据窗口 |
|:---:|------|------|------|
| **6h 快判** | elapsed ≥ 6h | 方向初判 | 入场后最近 12 根 1H K线 |
| **12h 深复盘** | elapsed ≥ 12h | 道氏 + 威科夫 + 价位三维度打分 | 1H/4H/1D K线 + 威科夫形态 |
| **72h 终局** | elapsed ≥ 72h | 3日趋势 + 连续误判 + near_bottom 验证 + 教训 | 3根日线 + 高低点 |

### 各层级数据要求（来自 process_reviews.py 源码）

**6h：**
```
数据源: okx_klines.db
周期: 1H
窗口: [分析时间-1h, 分析时间+7h]（共8h）
数量: limit=10 根
字段: ts, open, high, low, close, volume
条件: 窗口内无蜡烛 → 跳过本次
判定: pct_change > ±2% → 正确/错误，否则横盘震荡
路径: classify_price_path() 输出 path_type + detail（存入 review_6h_path / review_6h_path_detail）
```

**12h：**
```
数据源: okx_klines.db
周期: 1H
窗口: [分析时间-2h, 分析时间+13h]（共15h）
数量: limit=20 根
字段: ts, open, high, low, close, volume
条件: 窗口内无蜡烛 → 跳过本次
打分: 道氏(±2%判定) + 威科夫(spring_confirmed/near_bottom/SOS) + 价位(支撑阻力0.5%误差)
路径: classify_price_path() 输出 path_type + detail（存入 review_12h_result.price_path）
```

**72h：**
```
数据源: okx_klines.db — 仅1H（单一数据源，不再使用4H）
窗口: [分析时间-4h, 分析时间+73h]（共77h）
数量: 1H limit=80 根
字段: ts, open, high, low, close, volume
条件: 1H 数据为空 → 跳过本次
判定: 77h窗口内1H K线客观方向(±2%) + near_bottom(跌>8%失败) + 连续误判检测 + 价格路径分类
```

### 6h：方向初判关键逻辑
- ⚠️ **必须使用 `detect_direction_from_klines(candles, entry_price)` 从K线数据客观判定方向**（6h/12h/72h 三层全部统一使用此函数，不再区分4H专用版本）
- 判定逻辑：窗口内最高价 > entry×1.02 → bullish；最低价 < entry×0.98 → bearish
- 双向波动时看收盘价相对入场价位置
- 价格 ±2% 判定正确/错误，否则横盘震荡
- ~~旧版 `detect_direction()` 文本关键词匹配已废弃~~

### 12h：三维度打分
- **道氏方向打分 (direction_score)**：用 `detect_direction_from_klines(candles_1h, entry_price)` 客观判定
- **威科夫阶段打分 (wyckoff_score)**：
  - Spring：读 `sentiment.spring_confirmed` 结构化字段
  - SOS：从K线客观检测放量阳线突破前高（volume > 前4根均量×1.5 且 close > open 且 close > prev_high）
  - ~~旧版搜索 `'sos' in trend_lower` 文本关键词已废弃~~
- **价位打分 (levels_score)**：支撑阻力突破/守住
- 产出 `direction_detail` + `wyckoff_detail` + `levels_detail`
- 提取教训写入 `lessons.json`

### 72h：终局判定
- **3D方向**：用 `detect_direction_from_klines(candles_1h, entry_price)` 从77h窗口内全部1H K线客观判定
- 77h窗口按1H K线切分为3段（每段~25h），计算每段收盘价作为day1/day2/day3
- 窗口内最高/最低价覆盖全部1H K线（非仅最后3根）
- **连续误判检测**：6h错误 + 12h错误 → [WARN] 检查分析框架
- **near_bottom 验证**：读 `analysis.get('near_bottom', False)` 结构化字段，非文本搜索
- **数据事件豁免**：ADP/非农/CPI/FOMC 期间标注「TA 参考有限」
- 标记 `completed: true`

---

## 教训系统

### 生成条件（2026-06-20 更新：降低门槛）

**之前**：仅在评分=-1（明确错误）时触发 → 最近 32 条 +1~+2 复盘未产生任何教训。

**现在**：`total = direction_score + wyckoff_score + levels_score`（每项 -1/0/+1，合计 -3~+3）

| total | 行为 |
|:-----:|------|
| +2 / +3 | 分析基本正确，**不产生教训** |
| ≤ +1 | 逐维度检查，每个 `≤ 0` 的维度产生对应教训 |

| 维度评分 | 类型 | 说明 |
|----------|------|------|
| `dir_score == -1` | 方向误判 | 原判与实际走势相反 |
| `dir_score == 0` | 方向未确认 | 12h 内方向信号未验证 |
| `wyckoff_score == -1` | 信号误判 | 技术信号未被确认 |
| `wyckoff_score == 0` | 信号未确认 | 威科夫/形态信号未得到价格确认 |
| `levels_score == -1` | 价位误判 | 支撑/阻力全部失效 |
| `levels_score == 0` | 价位未触及 | 12h 内未触及任何设定价位 |
| near_bottom + 跌>3% | 加速误判 | 底部喊太早 |
| 数据事件 + 明确方向 | 事件遗漏 | 大事件前给出方向建议 |

---

## 轻量闭环：教训上下文注入 🆕 2026-06-20

复盘产生的教训通过 `inject_lessons.py` 反馈到下次分析：

```
复盘 :03 → regimes/{regime}_lessons.json
注入 :05 → inject_lessons.py
         → 汇总 lessons.json + regimes/*_lessons.json
         → 写入 .lesson_context.txt（按 14 天内/历史分组，带 [行情标签]）
分析时   → crypto-analysis-workflow §3.6 强制加载 .lesson_context.txt
```

| 组件 | 说明 |
|------|------|
| `inject_lessons.py` | 读教训文件，输出上下文摘要到 stdout + `.lesson_context.txt` |
| cron `:05` 每小时 | no_agent 脚本，自动刷新上下文文件 |
| `crypto-analysis-workflow` §3.6 | 分析前强制 `cat .lesson_context.txt`，按币种/行情类型/已知规律/事件避让注入 |

**教训按行情类型分文件存档**（`regimes/{regime}_lessons.json`），不混在一起。因为不同行情的规律互斥（牛市回调的「RSI<20 不做空」在熊市主跌中不成立）。`inject_lessons.py` 读取时合并并带上 `[行情标签]`。

## 重量闭环：权重调整（未实现）🕐 2026-07-20 讨论

教训积累后，按行情类型统计高频误判模式，调整共振评分中的指标权重：

```python
# 示例（未实现）
WEIGHTS_BY_REGIME = {
    '牛市回调': {'rsi_short': 0.3, 'rsi_long': 1.0},
    '熊市趋势': {'rsi_short': 1.0, 'rsi_long': 1.0},
}
```

**cron 提醒**：`c892ce3688c8` 于 2026-07-20 09:00 BJ 触发，只统计提醒，不自动调整权重。用户明确要求共同商讨后再决定。

## 归档

- `completed: true` 且 > 30 天的记录 → `reviews_archive.json`
- 归档后从 `reviews.json` 移除

---

## 静默机制

```python
# 仅当有 output_lines 时才打印标题和内容
if output_lines:
    print("=== 复盘处理 | BJ ... ===")
    print('\n'.join(output_lines))
# else: stdout 为空 → cron no_agent 模式不推送消息
```

---

## 约束

1. ⏰ 所有时间 BJ(UTC+8)，时间判断基于 OKX 服务器时间
2. 📝 每个币种每天只记第一条分析
3. ⏱️ 复盘时间阈值严格按 elapsed 小时判定（不提前、不延迟）
4. 🛡 异常捕获用具体类型
5. 🔇 无待复盘时不输出（cron 静默）

---

## ⚠️ 已知陷阱（2026-06-19/20 审计发现）

### 陷阱 1：FG/REVIEW 记录污染 reviews.json — ✅ 已修复

`publish_social.py` 向 `analyses.json` 写入 `coin: "FG"` 和 `coin: "REVIEW"` 记录供核验使用。
`process_reviews.py` 的 Step 0 同步循环（L743-766）未过滤这些特殊 coin 类型，
将其当作正常币种创建复盘占位到 `reviews.json`，`entry_price` 为 0 或 None。

**位置**: `process_reviews.py` L743-766（`for a in analyses:` 循环）
**修复**: 已加 `SKIP_COINS = {'FG', 'REVIEW'}` 过滤 + entry_price fallback

### 陷阱 2："待复盘" 哨兵值 ≠ "已完成" — ⚠️ 易误判

`reviews.json` 中新创建记录的 `review_6h`/`review_12h`/`review_72h` 字段初始值为字符串 `"待复盘"`，`completed: false`。**字段存在不代表复盘已完成**——`process_reviews.py` 在 L831 检查 `review.get('review_6h') == '待复盘'` 防止提前复盘的逻辑是**正确的**。

**常见误判**：用 `if r.get('review_12h')` 检查状态 → 字符串 `"待复盘"` 为 truthy → 错误地显示为 ✅。正确做法：**检查值是否等于 `"待复盘"`** 或 `completed` 字段。

**位置**: `process_reviews.py` L792-794（初始化）+ L831（6h检查）+ L844（12h检查）
**状态**: 代码逻辑正确，无需修复。仅提醒注意读取复盘的代码不要被哨兵值误导。

### 陷阱 3：analysis_template.py 语法错误 — order_flow 始终为空对象 — ✅ 已修复 (2026-06-19)

**根因**：`analysis_template.py` 有两处 bug 导致 order_flow 写入空对象：
1. **第2183行**：调试 `print()` 语句被错误放在字典字面量内部 → SyntaxError，整个 try 块无法执行
2. **第2041-2047行**：`order_flow = {}` 无条件覆盖了第1989-2002行从 regime_result 提取的正确数据

**修复**：
1. 删除第2183行的调试 print 语句
2. 将 L2041-2047 的 `order_flow = {...}` 改为 `order_flow.update({...})` 合并
3. 同时清理了第1991行的残留调试 print

**验证**：修复后 `import analysis_template` 成功，regime_cache 中的 order_flow 数据（funding_rate_pct, fr_pos_ratio, fr_avg_8d, taker_buy_ratio, detail）正确写入 analyses.json。

**遗留**：publish_social.py 有独立的 analyses.json 写入逻辑（L269-286），写入 full_obj 格式不含 order_flow。两个脚本同时写入同一文件，schema 不同。长期应统一写入路径。

### 陷阱 4：analyses.json 在 12h 复盘前被覆盖 → 丢失教训 — ✅ 已修复 (2026-06-20)

`analyses.json` 被 `analysis_template.py`(flat_old) 和 `publish_social.py`(full_obj) 共享写入。当同币种同一天有多次分析时，后写入覆盖前写入。12h 复盘触发时 `find_analysis()` 可能返回 None，导致 `extract_lessons()` 跳过全部教训生成。

**修复**：`analyses.json` 与 `social_analyses.json` **分离**：
- `analysis_template.py` → `analyses.json`（flat_old，纯技术分析）
- `publish_social.py` → `social_analyses.json`（full_obj，社交发动态数据）
- `process_reviews.py` 读取时合并两个文件，按 (coin, date) 去重，social 优先

---

## 与社交动态复盘的区别

| | 三层复盘引擎 | 社交动态复盘 |
|------|------|------|
| 脚本 | `process_reviews.py` | `review_last_post.py` |
| 触发 | cron :03 每小时 | 发动态前手动一次 |
| 复盘对象 | analyses.json 中的分析结论 | social_posts.json 中的帖子方向 |
| 频率 | 持续：6h/12h/72h 逐层推进 | 一次性：发帖前跑一次 |
| 输出 | reviews.json + lessons.json | social_reviews.json |
| 归属 | trade-review-workflow | social-dynamic Step ③ |
| **写入方式** | **原地更新** — 同一条记录逐层填充 review_6h/12h/72h 字段 | **追加新条目** — 每次复盘一条新记录追加到数组末尾 |
| **去重** | (coin, date) 唯一 — 同日同币只记第一条 | 无去重 — 每条帖子独立一条记录 |
| **原子性** | tmp → os.replace | tmp → os.replace |

---

**架构审计笔记 (2026-06-19 v3)**

详见 `references/code-audit-methodology.md`。

**2026-06-19 全量审计修复（6项）：**
- ✅ `process_reviews.py` 过滤 FG/REVIEW 记录，防止泄漏到 reviews.json
- ✅ `process_reviews.py` 兼容 full_obj 格式：support→levels_4h.lows, rsi_14→rsi_4h, macro→lessons_warnings
- ✅ `process_reviews.py` entry_price fallback：full_obj 无 entry_price 时用 ticker.last
- ✅ `analysis_template.py` 写入 sl_val/tp_val/rr_str 计算字段（核验必需）
- ✅ `analysis_template.py` 新增 resonance/risk_warnings 字段（原字段名错位）
- ✅ `publish_social.py` fallback 使用用户传入 coins 而非硬编码 ['BTC','ETH']

**2026-06-20 架构统一（已完成）**：
- ✅ `analyses.json` / `social_analyses.json` 分离：`analysis_template.py` → flat_old，`publish_social.py` → full_obj
- ✅ `process_reviews.py` 复盘时合并双文件，按 (coin, date) 去重，social 优先
- ✅ `_social_publish.py` 从 `analysis_template` 导入 `session_vp`/`wyckoff_detect`/`detect_kline_patterns`/`get_jin10_key_events`，两工作流分析深度一致
- ✅ `publish_social.py` 移除对 `analysis_template.py` 的子进程调用，消除双重冗余
- ✅ GitHub 自动推送（`auto_push_github.sh`，cron `*/5 * * * *`）已上线

**遗留**：
- ✅ `gen_charts.py` 四 bug 修复（2026-06-21）：import json 缺失 + UnboundLocalError + RSI 键名不兼容 + Style 2 MACD 全 NaN（详见 `社交动态发布` SKILL.md §10.1）

**2026-06-20 统一计算层（已完成）**：
- ✅ `_social_publish.py` 重构：删除 326 行重复代码，`analyze_single_coin()` 改为 50 行 wrapper，底层统一调 `analysis_template.analyze_single_coin()`
- ✅ 所有底层计算函数（RSI/MACD/ADX/BB/trend/label/accel/resonance 等 18 个核心字段）统一从 `analysis_template` import
- ✅ 两个工作流产出的分析数据 100% 一致（详见 `crypto-analysis-workflow/references/helper-function-divergence.md`）

**2026-06-20 仓位系统全量修复（7项）**：
- ✅ P0: SL/TP 区分多空 — 做空 SL 在高位上方、TP 在低位（`_format_coin_section` + `_social_publish` + `publish_social`）
- ✅ P1a: entry 改用 ticker.last（现价），不再用 close_1d（日线收盘价）
- ✅ P1b: `_social_publish` / `publish_social` SL 改用 ATR 缓冲，不再用硬编码 0.99/0.985
- ✅ P2: `calc_position` 常量提取（LEVERAGE/ACCOUNT_USD/MARGIN_MAINTENANCE）+ contracts `math.floor()` + mm_map 补全 SOL/DOGE
- ✅ P3: JSON `position` 字段与 stdout `pos_dir` 统一逻辑 + 输出行含爆仓价
- ✅ P3: `analyze_single_coin` 返回值新增 `position` 字段
- ✅ P3: `_social_publish` 方向标签读 `position` 动态切换，不再硬编码 `偏多`

## 2026-06-19 数据完整性修复记录

**问题**：`analysis_template.py` 写入 analyses.json 的 record 中，以下字段为空或错误：
- `kline_table` — close/pct_b/shape 字段名不匹配（应为 last_close/label/bb.pct_b）
- `vp_data` — 未调用 `session_vp()` 函数
- `wyckoff_data` — 未调用 `wyckoff_detect()` 函数
- `kline_pattern_times` — 未调用 `detect_kline_patterns()` 函数
- `macro_external` — 从错误的源 `a['extra']` 提取（BTC/ETH在db_coins里时extra={}）
- `change_pct` — 从 `ticker.change_pct` 获取（该字段不存在）

**修复**：
1. `kline_table` 字段映射修正：`last_close`(非`close`), `label`(非`shape`), `bb.pct_b`(非直接`pct_b`)
2. 在 record 构建路径中调用 `session_vp()`/`wyckoff_detect()`/`detect_kline_patterns()` 并将结果写入 record
3. `macro_external` 改为从 `regime_result.dimensions.macro_external` 提取
4. `change_pct` 改为从 `ticker(last-open24h)/open24h` 计算

**2026-06-19 代码审计完成（7项修复）**：
- ✅ `orig_dir_label` 未使用变量已删除
- ✅ `from collections import defaultdict` 已移至文件顶部
- ✅ `classify_price_path()` 和 `get_klines()` 已提取到 `_shared.py`，三个脚本（process_reviews.py、price_path_report.py、review_path_enhancer.py）统一引用
- ✅ 重复备份目录 `backup/20260618_222319/` 已删除（MD5与另一目录完全一致）
- ✅ `.bak` 文件已清理（`okx_klines.db.bak` 7.5MB + `strategy_bot.log.old_bak` 3.8MB）
- ✅ `__pycache__/` 已清空
- ✅ `analysis_template.py --social` 旧管道（L1936-2003，68行）已删除
- ✅ `analysis_template.py` 的 `_retry` 升级：max_retries 3→5，新增 `no_data`/`51001` 错误类型
- ✅ `review_last_post.py` 输出格式简化：移除 High/Low 价格和时间字段，仅保留路径类型

**旧管道已废弃**：
- `analysis_template.py --social`（5步旧管道）已被 `publish_social.py`（7步新管道）完全替代
- `publish_social.py` 调用 `analysis_template.py`（不带 `-s`）做分析，用 `_social_publish.generate_social_draft()` 生成文案
- 删除 `analysis_template.py` 中的 `--social` 代码块（L1936-2003）是安全的，不影响当前工作流
- 但 `social-dynamic` skill 文档仍记录了旧管道，需同步更新

## 价格走势路径分析 (2026-06-19 集成)

`classify_price_path(candles, entry_price)` 已内嵌至 `process_reviews.py`，三层复盘全部在输出中携带路径信息：
- **6h**：note 中追加 `| 路径:{type}(前{pct}%后{pct}% 振幅{pct}%)`，存入 `review_6h_path` + `review_6h_path_detail`
- **12h**：`review_12h_result.price_path` + `price_path_detail`，输出行 `路径: {type}(净{pct}% 振幅{pct}%) 前{pct}% 后{pct}%`
- **72h**：`review_72h_result.price_path` + `price_path_detail`，输出行 `72h路径: {type}(净{pct}% 振幅{pct}%) 前{pct}% 后{pct}%`

**8种路径类型判定逻辑**（基于1H K线）：
1. 横盘震荡: 总振幅 < 2%
2. 单边上涨: 净涨>3%, 最大回撤<1%, 前后半段都涨
3. 单边下跌: 净跌<-3%, 最大反弹<1%, 前后半段都跌
4. 先涨后跌: 高点在前30%, 低点在后30%, 后半段跌幅过半
5. 先跌后涨: 低点在前30%, 高点在后30%, 后半段上涨
6. 冲高回落: 高点在前, 净跌<0, 后半段跌>2%
7. 探底回升: 低点在前, 净涨>0, 后半段涨>2%
8. 宽幅震荡: 振幅>5%, 净涨跌<2%

**独立脚本**（不嵌入复盘流程，按需运行）：
- `price_path_report.py` — 批量路径分析报告（`--days N --coin COIN`）
- `review_path_enhancer.py` — 增强器，将路径数据写入已有 reviews.json（`--dry-run` 预览）

**2026-06-19 重构记录**：
- `detect_direction_from_4h()` 已删除，72h复盘统一使用 `detect_direction_from_klines()` 从1H K线判定
- `_build_trend_string()` 已删除（不再调用），`detect_direction()` 保留但标记 deprecated
- 三层复盘全部统一使用 `classify_price_path()` 输出路径信息
- `review_last_post.py` 已自带路径输出（`path_type` 字段），社交动态"上条复盘"自动携带

## 相关文件

| 文件 | 路径 | 用途 |
|------|------|------|
| process_reviews.py | /root/.hermes/trade_review/ | 三层复盘引擎主脚本 |
| reviews.json | /root/.hermes/trade_review/ | 复盘记录列表 |
| lessons.json | /root/.hermes/trade_review/ | 教训库 |
| regimes/{type}_lessons.json | /root/.hermes/trade_review/regimes/ | 按行情分组的教训 |
| inject_lessons.py | /root/.hermes/trade_review/ | 轻量闭环注入器（cron :05） |
| .lesson_context.txt | /root/.hermes/trade_review/ | 教训上下文缓存（分析时读取） |
| `reviews_archive.json` | /root/.hermes/trade_review/ | >30天归档 |
| `analyses.json` | /root/.hermes/trade_review/ | 输入（纯分析结论，flat_old 格式） |
| `social_analyses.json` | /root/.hermes/trade_review/ | 输入（社交发动态分析，full_obj 格式） |
| `_social_publish.py` | /root/.hermes/trade_review/ | 社交分析库：`analyze_single_coin()`（wrapper → `analysis_template.analyze_single_coin`） |
| `_shared.py` | /root/.hermes/trade_review/ | 共享：BJT时区、DB_PATH、TRADE_DIR、SOCIAL_ANALYSES_PATH、classify_price_path()、get_klines() |

## 参考文档

- `references/kline-direction-detection.md` — 方向判定从文本关键词到K线数据的重构记录
- `references/code-audit-methodology.md` — 代码审计方法论与常见冗余模式
- `references/data-format-matrix.md` — analyses.json 两种格式兼容矩阵（full_obj vs flat_old）
- `references/cron-jobs.md` — 所有定时任务清单（K线同步/复盘/宏观/金十）
- `references/analysis-loss-bug.md` — analyses.json 覆盖导致 12h 复盘丢失教训的 Bug（🟡待修复）
- `references/github-auto-push.md` — 脚本变更自动推送到 GitHub（cron 每 5 分钟）
- `references/post-change-github-sync.md` — 代码修改后本地与 GitHub 一致性验证流程

## 相关技能

- **social-posting-workflow** — 📖 发动态权威操作手册（Step ③ 社交动态复盘，一次性）
- **crypto-analysis-workflow** — 📖 纯分析操作手册（产出 analyses.json 供三层引擎消费）
