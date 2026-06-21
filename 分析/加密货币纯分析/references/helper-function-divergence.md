# 底层函数差异分析（历史记录 — 2026-06-20 已修复）

**状态**：✅ 已修复。`_social_publish.py` 删除 326 行重复代码，全部 11 个底层函数 + 3 个数据库函数改为从 `analysis_template` import。`analyze_single_coin` 改为 50 行 wrapper。两个工作流 18/18 核心字段 100% 一致。

## 背景

`_social_publish.py` 曾有 11 个计算函数的独立副本，与 `analysis_template.py` 同名函数**实现不同**，导致两个工作流产出的分析数据不一致。

## 差异清单

| 函数 | analysis_template | _social_publish (旧版) | 影响 |
|------|:-:|:-:|------|
| `trend_direction` | HH/HL 道氏判定，返回「上升/下降/盘整」 | 涨跌计数多数决，返回「偏多/偏空/盘整」 | 道氏标签完全不同 |
| `calc_adx` | Wilder DMI（只计主导方向）+DM/-DM | 简化版（所有涨跌计DM+/-） | ADX 值差 20+ 点 |
| `calc_bollinger` | 返回 `mid` key，threshold `period+1` | 返回 `middle` key，threshold `period`，取整 | %b 值不同 |
| `candle_body_label` | 含锤子线/射击之星/普通±/大阳+ 等分类 | 简化为大阳+/中阳/小阳 | K线标签完全不同 |
| `calc_rsi` | 不足数据返回 `None` | 不足数据返回 `50.0` | 边界行为不同 |
| `calc_macd` | 不足数据返回 `(None,None,None)` | 不足数据返回 `(0.0,0.0,0.0)` | 边界行为不同 |
| `check_data_event_window` | 含 CPI+FOMC+PPI 三个事件 | 仅 CPI+FOMC 两个事件 | 缺少 PPI 检测 |

## 实测后果

同一数据源（BTC $63,548, 2026-06-20 10:08 BJ），两版本对比：

| 字段 | analysis_template | _social_publish 旧版 | 一致? |
|------|:-:|:-:|:-:|
| 4H trend | 上升 | 盘整 | ❌ |
| 1D label | 大阳+ | 中阳 | ❌ |
| RSI 1D | 40.83 | 42.11 | ❌ |
| ADX 4H | 48.36 | 78.00 | ❌ |
| %b 1D | 44.25 | 51.40 | ❌ |
| accel | decelerating | steady | ❌ |

13/22 核心字段不一致。

## 修复方案（2026-06-20 已实施）

`_social_publish.py` 重构：
- 删除 11 个重复函数 + get_rows/build_data_freshness/get_db_coins/MACD_PARAMS（共 326 行）
- 导入 `analysis_template.analyze_single_coin` 作为 `_base_analyze`
- `analyze_single_coin` 改为 50 行 wrapper：调 `_base_analyze` + 补 position/vp/wyckoff/calendar/macro
- 所有底层函数统一从 `analysis_template` import

修复后：18/18 核心字段 100% 一致。
