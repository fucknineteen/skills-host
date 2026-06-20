# 数据文件架构变更（2026-06-20）

## 变更前

两个脚本写同一个文件：

```
analysis_template.py ──→ analyses.json (flat_old)
publish_social.py   ──→ analyses.json (full_obj, 覆盖)
```

**后果**：
- `publish_social.py` 的 full_obj 写入会覆盖 `analysis_template.py` 的 flat_old 记录
- `process_reviews.py` 复盘时的 `find_analysis()` 找不到对应的分析记录 → `analysis is None`
- `extract_lessons()` 直接返回空数组，教训静默丢失
- 例：BTC 06-17 08:17 分析在 12h 复盘时记录已无 → 3 条教训全部丢失

## 变更后

两个脚本写不同文件：

```
analysis_template.py ──→ analyses.json (flat_old, 纯分析)
publish_social.py   ──→ social_analyses.json (full_obj, 社交发布)
```

**读取方**：
- `process_reviews.py` 同时读取两个文件，按 (coin, date) 去重，social 优先覆盖
- `verify_social_post.py` 只读 social_analyses.json
- `gen_charts.py` 优先 social_analyses.json，fallback analyses.json
- `regime_detector.py` 只读 analyses.json 的 FG 值

## publish_social.py 冗余消除

**变更前**：
1. 调用 `analysis_template.py` 子进程 → 写入 analyses.json
2. 读 social_analyses.json → 空 → fallback 触发重新分析 → 写入 social_analyses.json
→ 同一批币种被分析了两次

**变更后**：
1. 直接执行内置 `analyze_single_coin()` → 写入 social_analyses.json
→ 只分析一次，删除 `step_analyze()`、`ANALYSIS_SCRIPT`、`analyses_raw` 等死代码

## 已知遗留：代码重复（2026-06-20 诊断）

`_social_publish.py` 只 import 了 `analysis_template` 中的 4 个函数：
- `session_vp`
- `wyckoff_detect`
- `detect_kline_patterns`
- `get_jin10_key_events`

但以下函数在 `_social_publish.py` 中有**独立副本且实现不同**：

| 函数 | analysis_template.py | _social_publish.py | 差异 |
|------|:-:|:-:|------|
| `analyze_single_coin` | L704 (~200行) | L308 (~200行) | social 版缺 `last_o/h/l/v/ts` 字段，fallback 链更薄 |
| `get_rows` | L617 (含币安缓存回退) | L272 (无缓存) | social 版把 DB 结果全当作 closed |
| `build_data_freshness` | L646 | L292 | 重复 |
| `get_db_coins` | L185 | L283 | 重复 |
| `MACD_PARAMS` | L94 | L25 | 重复 |

**影响**：
- `get_jin10_key_events()` 在 social 侧若 MCP 超时直接返回空 → `calendar_events: []`
- `macro_external` 在 social 侧直接读 regime_cache 文件 → 主流程则是 `get_regime_result()` 统一获取
- `vp_data` 若 DB 查询异常，social 侧无额外回退
- 两个 `analyze_single_coin` 需独立维护，已出现字段不一致

**方向**：考虑 `_social_publish.py` 直接 import `analyze_single_coin`，删除重复的 ~200 行 + 5 个工具函数。

## 相关文件

| 文件 | 变更 |
|------|------|
| `_shared.py` | 新增 `SOCIAL_ANALYSES_PATH` |
| `publish_social.py` | 写入目标改为 social_analyses.json；删除 analysis_template 子进程 |
| `verify_social_post.py` | 读取源改为 social_analyses.json |
| `gen_charts.py` | 优先 social_analyses.json，fallback analyses.json |
| `process_reviews.py` | 双文件合并读取 |
