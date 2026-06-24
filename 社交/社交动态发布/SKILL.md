---
name: 社交动态发布
description: 社交动态发布前的数据核验、叙事核验、模板核验、发布核验。强制合同模式：所有数字必须从social_analyses.json逐项核验，禁止凭stdout/记忆/推测。
---

# 社交动态发布核验规范（高约束执行版）

## SKILL ROLE CONTRACT（技能职责合同）

本 Skill 负责：

**社交动态发布前的数据核验、叙事核验、模板核验、发布核验。**

本 Skill 不负责：

- 指标计算
- 行情分析算法
- K线同步算法
- 订单流计算
- 威科夫计算

上述逻辑由：

- `publish_social.py`
- `analysis_template.py`

负责。

本 Skill 负责确保：

**最终动态中的每一个数字、每一个结论、每一个方向判断均可追溯并通过核验。**

---

## EXECUTION CONTRACT（最高优先级）

加载 Skill ≠ 执行 Skill。

读取 Skill ≠ 遵守 Skill。

每次发布动态前：

**必须完整执行本 Skill 定义的全部核验流程。**

**禁止：**

- 跳过核验
- 部分核验
- 人工推测补全
- 凭 stdout 写文案
- 凭历史动态写文案
- 凭上一轮对话写文案

若核验未完成：

**禁止发布。**

---

## GATE-1 数据门禁

开始写动态前必须确认：

- [ ] social_analyses.json 存在
- [ ] .regime_cache.json 存在
- [ ] social_analyses.json 时间 ≤ 60分钟
- [ ] indicators 字段存在
- [ ] levels_4h 字段存在

任意失败：

**立即停止。**

---

## DATA SOURCE CONTRACT（数据来源合同）

动态中的所有数字必须来自：

- social_analyses.json
- .regime_cache.json

**禁止来源：**

- stdout
- 历史动态
- Agent记忆
- 人工推测
- 用户历史对话

---

## NUMERIC TRACEABILITY CONTRACT（数字可追溯合同）

动态中的每一个数字：

必须满足：

```
数字 → JSON字段 → JSON路径 → 真值
```

四项同时成立。

否则：

**禁止输出。**

### 适用范围

包括但不限于：

BTC价格、ETH价格、FG、FR、Taker Ratio、支撑位、阻力位、止损、止盈、RR、RSI、ADX、MACD、VIX、DXY、BTC.D、10Y、所有百分比、所有价格、所有评级

---

## NARRATIVE CONTRACT（叙事一致性合同）

文案中的结论必须能够被数据证明。

**禁止：**

- 数据观望 → 文案看多
- 数据观望 → 文案看空
- 数据震荡 → 文案写单边趋势
- 数据弱势 → 文案写主升浪启动
- 数据空头 → 文案写牛市确认

### 方向一致性规则

**若 position = 观望：**

禁止写：立即做多、立即做空、重仓做多、重仓做空

**若 position = 做多：**

禁止写：看空结论

**若 position = 做空：**

禁止写：看多结论

---

## JSON > STDOUT CONTRACT

若 stdout 与 social_analyses.json 冲突：

**必须以 JSON 为准。**

禁止引用 stdout 数值。

---

## REGIME CACHE CONTRACT

BTC 与 ETH 共享 `.regime_cache.json`。

因此 FR、Taker、FG、DXY、VIX、BTC.D、10Y 等宏观与订单流字段可能相同。

**禁止为两个币种编造不同值。**

---

## TEMPLATE CONTRACT（模板合同）

必须使用：

**社交动态模板 v5.1**

**禁止：**

- 删除字段
- 增加字段
- 修改字段顺序
- 替换结构

### 必须保留：

🔥标题、📋上条复盘、💡大资金在干嘛、📐盘面看了什么、📊钱堆在哪、🌍宏观给不给面子、📰快讯风往哪吹、一锤定音结论、支撑阻力、方向策略、收尾金句

---

## NEWS CONTRACT（快讯合同）

快讯必须按关联度排序。

仅保留前 3～5 条最相关内容。

**禁止：**

- 堆砌全部快讯
- 复制全部新闻

---

## REVIEW CONTRACT（复盘合同）

发布前必须完成：

```bash
review_last_post.py --save
```

并成功写入 `social_reviews.json`。

否则：**立即停止。**

### 复盘时间显示（2026-06-23修复）

`step_review()` 原先过滤掉含"复盘上条:"的行→丢失发帖时间。修复后单独提取时间行，格式为`📋 上条 Post #N | YYYY-MM-DD HH:MM BJ`，追加到复盘数据行前面。

---

## RR门禁（v062309升级）

`_social_publish.py` wrapper 使用 `decision_engine` 三层解耦引擎计算 SL/TP/RR。

**方向判定**: L0威科夫 + L0.5缠论 + L1道氏 + L2共振（与 analysis_template.py 完全一致）

**SL/TP**: `sltp_engine.decision_engine()` → 结构锚定SL + 三层流动性TP + 时间质量门控

**RR分档**: <1.0→禁止 | 1.0-1.3→轻仓(0.6%) | 1.3-1.8→标准(1.4%) | ≥1.8→强趋势(2%)

**关键**: `publish_social.py` L357 优先使用 wrapper 已计算的 `sl_val/tp_val/rr_str`，回退到旧版 `calc_sl_tp`。

### 已知陷阱 v062309

**陷阱 10**: wyckoff_data 先读后写 → L0永远不触发。修复：wyckoff_detect 必须在位置判定前调用。

**陷阱 11**: `publish_social.py` 覆盖 wrapper 的 SL/TP → RR 被旧值覆盖。修复：优先使用 `a['sl_val']` 若已存在。

---

## VERIFY CONTRACT（核验合同）

必须执行：

```bash
verify_social_post.py
```

并满足：`exit code = 0`

若 `exit code ≠ 0`：

当前动态视为失败。

**禁止：**

- 用户审核
- 配图生成
- 保存记录
- 发布动态

**必须：**

- 修正问题
- 重新生成
- 重新核验

直到 `exit code = 0`。

---

## OUTPUT SELF-CHECK（发布前自检）

发布前必须逐项确认：

- [ ] social_analyses.json 已读取
- [ ] .regime_cache.json 已读取
- [ ] 所有数字已找到字段
- [ ] 所有数字已找到路径
- [ ] 所有数字已核验
- [ ] 所有方向判断与数据一致
- [ ] 所有结论与数据一致
- [ ] verify exit=0
- [ ] 模板v5.1正确

全部为 YES：允许进入发布阶段。

否则：**立即停止。**

---

## FAILURE CONTRACT（失败处理合同）

**若发现任意数字无法追溯到 JSON：**

删除该数字或标记 ⚠️未核验

**若发现任意结论无法由数据证明：**

删除该结论，重新生成

**若发现任意叙事与数据冲突：**

视为核验失败。**禁止发布。**

---

## 已知陷阱（2026-06-23 新增）

### 陷阱 7：SL/TP 方向感知选价

`calc_sl_tp()` 始终取 `near_s[0]`/`near_r[0]`=最远端价位。`near_s` 升序、`near_r` 降序。
做空取 `near_r[0]`=最远阻力→SL过宽，做多取 `near_s[0]`=最远支撑→SL过宽，RR被人为压低。

**修复**：做多用 `near_s[-1]`(最近支撑/max below)、`near_r[-1]`(最近阻力/min above)；做空反之。
`_social_publish.calc_sl_tp` 用 `min(r for r in near_r if r > entry)` 筛选。

### 陷阱 8：step_review 时间行过滤

`publish_social.py` `step_review()` 过滤含 `复盘上条:` 的行→丢失发帖时间。
**修复**：单独提取时间行格式化 `📋 上条 Post #N | YYYY-MM-DD HH:MM BJ`，追加到数据行前。

### 陷阱 9：gen_charts.py 独立运行路径

`gen_charts.py` 在 `trade_review/scripts/` 中，`from _shared import BJT` 因不在 Python path 失败。
**修复**：同 inject_lessons，加 `sys.path.insert(0, '../')`。

最终动态发布前必须同时满足：

- [ ] 数据来自 JSON
- [ ] 所有数字可追溯
- [ ] 所有数字已核验
- [ ] 所有结论与数据一致
- [ ] 所有方向与仓位一致
- [ ] review 已完成
- [ ] verify exit=0
- [ ] 模板 v5.1 正确
- [ ] 用户审核通过
- [ ] 配图生成成功
- [ ] 发布记录保存成功

任意一项失败：

**立即停止。禁止发布。**
