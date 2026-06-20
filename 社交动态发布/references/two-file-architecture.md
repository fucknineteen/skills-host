# 双文件架构：analyses.json vs social_analyses.json

> 2026-06-20 架构变动

## 分离原因

此前两个工作流共写 `analyses.json`，导致：
1. `analysis_template.py` 写入 flat_old 格式 → 被 `publish_social.py` fallback 的 full_obj 格式覆盖
2. 复盘引擎 `process_reviews.py` 的 `find_analysis()` 找不到原始分析记录 → `extract_lessons()` 因 `analysis=None` 返回空

## 当前架构

```
analysis_template.py  →  analyses.json         (flat_old，纯技术分析)
publish_social.py      →  social_analyses.json  (full_obj，社交发动态)

process_reviews.py  ←  同时读取两个文件，按 (coin, date) 去重，social 优先
```

## 文件格式对比

| 字段 | analyses.json (flat_old) | social_analyses.json (full_obj) |
|------|--------------------------|-------------------------------|
| 指标 | `rsi_14`, `macd_trend` | `indicators[TF].rsi/macd_h/adx/atr` |
| 支撑/阻力 | `support[]`, `resistance[]` | `levels_4h.highs[]`, `levels_4h.lows[]` |
| 底部研判 | `sentiment.spring_confirmed` | `near_bottom` (bool) |
| 共振 | `recommendation` | `resonance` (🟢/🟡/🔴) |
| 威科夫 | ❌ 不在标准字段 | `wyckoff_data.phase` |
| Volume Profile | `vp_data.POC` | `vp_data` (24h 全天，≥8根15m蜡烛) |
| K线形态 | `kline_pattern_times[]` | `kline_patterns[]` |
| 日历 | `calendar_events[]` | `calendar_events[]` (MCP 实时) |
| 费率 | `order_flow.funding_rate_pct` | `funding_rate_pct` |

## 复盘引擎兼容

`process_reviews.py` 合并逻辑：
```python
# 1. 读 analyses.json (flat_old)
# 2. 读 social_analyses.json (full_obj)
# 3. 按 (coin, date) 去重，social 覆盖同日 analysis
# 4. extract_lessons() 兼容双格式字段映射
```

## publish_social.py 分析同步

`_social_publish.py` 的 `analyze_single_coin()` 是 **wrapper**，底层统一调用 `analysis_template.analyze_single_coin()`，保证两个工作流所有指标计算（RSI/MACD/ADX/BB/trend/label/accel/resonance）完全一致。在此基础上补充 5 个社交专用字段：

| 字段 | 来源 |
|------|------|
| `position` | 共振 + near_top 捷径 + V反保护，从 `_base_analyze` 计算的 resonance/rsi_1d/trend_1d 推导 |
| `vp_data` | `session_vp(coin, conn)` — 已从 analysis_template import |
| `wyckoff_data` | `wyckoff_detect(result_dict)` — 同上 |
| `calendar_events` | `get_jin10_key_events()` — 同上 |
| `macro_external` | 读 `regime_cache.json` |

**统一导入（2026-06-20 重构）**：`_social_publish.py` 从 `analysis_template` 导入了全部底层函数：
```python
from analysis_template import (
    session_vp, wyckoff_detect, detect_kline_patterns, get_jin10_key_events,
    is_closed, calc_rsi, calc_macd, calc_adx, calc_bollinger, calc_obv,
    candle_body_label, trend_direction, check_acceleration,
    check_extreme_oversold, check_data_event_window,
    get_rows, build_data_freshness,
    analyze_single_coin as _base_analyze,
    MACD_PARAMS, TIMEFRAMES,
)
```

此前两个文件各自定义了 11 个不同实现的底层函数，导致 `analyses.json` 和 `social_analyses.json` 中 13/22 关键字段不一致（ADX 48 vs 78、trend 上升 vs 盘整 等）。重构后删除 326 行重复代码，所有计算统一。
