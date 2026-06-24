---
name: 代码修改规范
description: 代码修改强制纪律。修改任何代码前加载。严格补丁模式：默认禁止修改，完成全部审计要求后才允许。四条铁律：根因门禁、最小补丁、回归测试、依赖影响分析。
category: devops
---

# 代码修改规范（高约束执行版）

## EXECUTION CONTRACT（最高优先级）

本 Skill 优先级高于其他代码修改类 Skill。

任何代码修改行为开始前：

**必须加载本 Skill。**

加载后自动进入：

**STRICT PATCH MODE（严格补丁模式）**

在该模式下：

**默认禁止修改代码。**

只有完成全部审计要求后：

才允许修改。

---

## CORE PRINCIPLE（核心原则）

修复 Bug 的目标：

**恢复正确行为。**

不是：

- 优化代码
- 重构代码
- 美化代码
- 统一风格
- 提升可读性

除非用户明确要求。

否则：

**禁止执行上述行为。**

---

## GATE-1 ROOT CAUSE CONTRACT（根因门禁）

修改前必须回答：

### ① Bug 根因是什么？

必须描述根因。

禁止描述表象。

例如：
- **错误：** 程序崩溃
- **正确：** fg_label 为 None，执行 .upper() 触发异常

### ② 修改点被谁调用？

必须完成调用链分析：

调用方 → 参数来源 → 返回值消费方

### ③ 修改后哪些路径会走到这里？

必须完成正向路径分析：

入口 → 中间层 → 修改点

**若任意问题无法回答：**

立即停止。继续阅读代码。**禁止修改。**

---

## CHANGE SCOPE CONTRACT（修改范围合同）

**允许修改：**

修复当前 Bug 所需最小代码范围。

**禁止修改：**

- 格式化
- 空格
- 缩进
- 换行
- import 排序
- 注释修正
- 命名优化
- 风格统一

即使顺手即可完成：**仍然禁止。**

---

## MINIMAL PATCH CONTRACT（最小补丁合同）

必须满足：

```
修复代码量 = 最小必要改动
```

出现多个方案时：**选择改动范围最小方案。**

**禁止防御性重写。**

例如：一个 if 判断错误 → 重构整个函数 → **属于违规。**

必须自检：

- [ ] 删除任何一行后修复是否失效
- [ ] 是否存在更小补丁
- [ ] 是否存在更短路径

若答案为存在更小方案：**必须回退。**

---

## DEAD CODE CONTRACT（死代码合同）

删除代码前必须证明：

1. `grep` 全项目无引用
2. 不属于动态调用（包括 importlib / eval / exec / 反射机制）
3. 不属于文档示例
4. 删除后 `py_compile` 通过

无法证明：**禁止删除。** 改为 `# DEAD:` 标记。

### 陷阱 E：replace_all 只匹配完全一致的字符串

`replace_all=True` 要求 `old_string` 在文件中**逐字符完全一致**。即使逻辑完全相同的代码块，如果局部变量名不同（如 `ev_str` vs `events_str`），`replace_all` 也只会匹配其中一个。

**诊断**：`grep -c "old_string_excerpt" file.py` — 若返回 N 但 patch 只替换了 M < N 处，说明有空格/变量名/引号差异。

**修复**：用变量名差异的 `old_string` 再次执行 patch，或逐站点替换。

**真实案例**：`process_reviews.py` 中 `any(kw in ev_str ...)` 和 `any(kw in events_str ...)` 逻辑完全相同但变量名不同 → `replace_all` 只替换了前者。

### 陷阱 F：`or`-短路吞零（重构 helper 函数时的回归 bug）

**场景**：从原始代码中提取共享 helper 函数时，原始使用 `is not None` 保护零值，但新 helper 简化为 `x or y`。

**经典案例**：
```python
# 原始（正确）
rsi = rsi_val if rsi_val is not None else analysis.get('rsi_4h', 50)
# 重构后（错误） — or 吞掉 RSI=0
return analysis.get('rsi_14') or analysis.get('rsi_4h', 50)
```

当 `rsi_14=0` 或 `fg_val=0` 时，`or` 视其为 falsy → 回退到默认值，静默产生错误。

**规则**：提取值为数值的 helper 时，**一律用 `is not None` 显式检查**，禁止 `or` 回退。

**检测**：测试边界 0 → 必须返回 0。

---

## SIDE EFFECT CONTRACT（副作用合同）

修改前必须列出所有影响对象：

- 函数调用方
- import 方
- JSON 字段
- Cron 任务
- CLI 入口
- API 接口
- 序列化结构
- 数据库模型

每个对象必须回答：**为何不会被破坏？**

无法证明：**禁止修改。**

---

## STOP CONTRACT（停止条件合同）

出现以下情况：**立即停止。提交分析报告。禁止修改。**

| 条件 | 触发 |
|------|------|
| 条件 1 | 下游调用方 ≥ 3 |
| 条件 2 | 存在 Cron 任务依赖 |
| 条件 3 | 跨文件字段名变更 ≥ 2 |
| 条件 4 | 涉及公共 API / 数据库模型 / 序列化结构 / 通信协议 |

满足任意一项：**必须升级为设计变更。禁止按 Bug 修复处理。**

---

## PATCH TOOL TRAP CONTRACT（Patch工具陷阱合同）

### 陷阱 A：`\n` 字面量转义

patch 工具的 `new_string` 参数中 **禁止使用 `\n` 表示换行**。

当在 `new_string` 中写入 `\n` 时，patch 将字面量反斜杠-n 写入文件而非换行符，导致：
- 单行代码分裂为多行
- 变量声明 + 注释被连成一行  
- SyntaxError：`unexpected character after line continuation character`

**正确做法**：从 `read_file` 输出中**直接复制**目标区域的精确换行和缩进，保持原样写入。

**错误示例**：
```
new_string="line1\nline2"  → 文件变成 "line1\\nline2"（一个字面量 \n）
```

**正确示例**：在同一 patch 中分多行写 `new_string`，用真实换行。

### 陷阱 B：双文件同步

当同一脚本存在于两个位置（如 `/root/.hermes/scripts/` 和 `/root/.hermes/trade_review/`），cron 作业指向其中一个，修复必须同时应用到两处。

**检查方法**：`find /root/.hermes -name "filename.py"` → 确认 cron job 引用的实际路径 → 两个文件均需修补后语法验证。

### 陷阱 C：误判已修复 Bug

审计报告标注"已修复"的 Bug 必须通过实际代码校验确认。不可盲目采信任何文档/报告的修复状态——代码才是唯一事实来源。

### 陷阱 D：删除变量声明后消费方 NameError

当标记某变量为 `# DEAD:` 时，必须全文搜索该变量在**同一作用域**内的所有引用。若消费方代码（如 `if not var:`）在变量删除后仍存在，将抛 `NameError`。

**修复**：删除赋值后必须同时处理所有引用，或保留最小初始化（如 `var = ''`）。

### 陷阱 E：replace_all 只匹配完全一致的字符串

`replace_all=True` 要求 `old_string` 在文件中**逐字符完全一致**。即使逻辑完全相同的代码块，如果局部变量名不同（如 `ev_str` vs `events_str`），`replace_all` 也只会匹配其中一个。

**诊断**：`grep -c "old_string_excerpt" file.py` — 若返回 N 但 patch 只替换了 M < N 处，说明有空格/变量名/引号差异。

**修复**：用变量名差异的 `old_string` 再次执行 patch，或逐站点替换。

**真实案例（2026-06-23）**：`process_reviews.py` 中 `any(kw in ev_str ...)` 和 `any(kw in events_str ...)` 逻辑完全相同但变量名不同 → `replace_all` 只替换了前者，需要额外 patch 处理后者。

### 陷阱 F：`or`-短路吞零（重构 helper 函数时的回归 bug）

**场景**：从原始代码中提取共享 helper 函数时，原始使用 `is not None` 保护零值，但新 helper 简化为 `x or y`。

**经典案例**：
```python
# 原始（正确）
rsi = rsi_val if rsi_val is not None else analysis.get('rsi_4h', 50)
# 重构后（错误） — or 吞掉 RSI=0
return analysis.get('rsi_14') or analysis.get('rsi_4h', 50)
```

当 `rsi_14=0` 或 `fg_val=0` 时，`or` 视其为 falsy → 回退到默认值，静默产生错误。

**规则**：提取值为数值的 helper 时，**一律用 `is not None` 显式检查**，禁止 `or` 回退。

**检测**：测试边界 0 → 必须返回 0。

**真实案例（2026-06-23）**：第三次审计发现 `_extract_rsi()`/`_extract_fg()` 的 `or` 吞掉 RSI=0 和 FG=0。

### 陷阱 G：大段替换 Escape-drift → 分段 patch

`old_string`>30 行且含引号/f-string 时，patch 可能注入额外转义触发 `Escape-drift`。拆分为每段 10-15 行逐个 patch。案例：SL/TP 40 行替换两次触发 → 拆分解决。

### 陷阱 H：备份时点 — 回滚丢失改动

①改动前 `cp file.py file_vN.py` ②每阶段语法验证 ③回滚前确认备份包含哪些改动。反例：备份在信号层改动之前 → 回滚时丢失四层架构 → 需重新 apply。

### 陷阱 I：wyckoff_detect 重复调用

wrapper 中已调用 `wyckoff_detect()` 后，下游复用 `wk` 变量即可。案例：`_social_publish.py` L175/L241 重复。

### 陷阱 K：`min(a, b)` / `max(a, b)` 吞零（数值过滤陷阱 v062309）

`min(0, 1700) = 0`。当候选值中一个为 0（缺失/无数据）时，min/max 吞掉有效值。

**修复**: `min(v for v in [a, b] if v > 0)` 或 `max(v for v in [a, b] if v > 0)`。

**真实案例**: `tp3_val = min(val, d1_l)` — val=0(无VP数据)时吞掉有效的 d1_l=1700。

### 陷阱 L：信号级别假阳性 — 关键词匹配 ≠ 完整条件 v062309

只检查事件列表关键词存在（`any('Spring' in e for e in events)`）而不验证完整逻辑条件（confidence≥50, Spring+SOS同时出现），导致信号级别被错误提升，远端目标门控被绕过。

**修复**: 完整复现原 L0 条件，包括 confidence 阈值和组合检查。

### 陷阱 M：数据先读后写 v062309

在调用计算函数之前读取其结果字段 → 永远取到空值/默认值，静默跳过该层逻辑。

**诊断**: 搜索 `result.get('field')` 和 `result['field'] = compute()` 的相对行号。

**真实案例**: `_social_publish.py` 中 `result.get('wyckoff_data')` 在 L177，但 `wyckoff_detect()` 在 L241 → L0 威科夫永远不触发。

### 陷阱 N：多模块参数签名不一致 v062309

新增参数后旧调用方因缺少参数走默认值，默认值可能静默降级（如 signal_level=None 被 treat 为 'L2'，远端目标被错误门控）。

**修复**: ① 新增参数提供合理默认值 ② 更新所有调用方 ③ 端到端验证两端一致。

---

## REGRESSION TEST CONTRACT（回归测试合同）

每个 Bug 修复**必须附带回归测试。**

没有测试：**视为未修复。**

必须证明：

- 修复前：失败
- 修复后：成功

必须覆盖：**边界情况。**

---

## TEST TYPE CONTRACT（测试选择合同）

| Bug 类型 | 测试方式 |
|----------|---------|
| 独立函数 | `assert` 测试 |
| 崩溃 Bug | `try/except` 测试 |
| 签名不匹配 | `py_compile` + import 链测试 |
| 空值问题 | 边界输入测试 |
| 数据流问题 | 端到端测试 |

**禁止只运行成功路径。**

---

## VERIFICATION CONTRACT（验证合同）

修改完成后必须依次执行：

```
1. 回归测试
  ↓
2. 语法验证
  ↓
3. Import 链验证
  ↓
4. 影响分析复查
  ↓
5. 端到端验证
```

**禁止跳步。**

---

## SELF CHECK CONTRACT（最终自检）

提交前必须确认：

- [ ] 已完成三问
- [ ] 已确认根因
- [ ] 已完成调用链分析
- [ ] 已完成影响分析
- [ ] 修改范围最小
- [ ] 未修改无关代码
- [ ] 已添加回归测试
- [ ] 回归测试通过
- [ ] py_compile 通过
- [ ] import 链通过
- [ ] E2E 验证通过

全部 YES：允许提交。

否则：**禁止提交。**

---

## FINAL DELIVERY CONTRACT（最终交付合同）

任何代码修改必须同时满足：

1. 已找到真实根因
2. 修改范围最小
3. 无无关改动
4. 已完成影响分析
5. 已完成回归测试
6. 已完成验证链
7. 未触发停止条件

否则：

**禁止提交代码。**

返回：`⚠️修改条件未满足` 并停止执行。
