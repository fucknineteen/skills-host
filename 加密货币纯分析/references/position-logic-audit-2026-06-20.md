# 仓位推荐系统优化 — 2026-06-20

## 优化轮次

### 第一轮：P0/P1/P2/P3（6 项）

| 编号 | 类别 | 改动 | 文件 |
|------|------|------|------|
| P0 | Bug | SL/TP 区分多空——做空 SL 在高位上方、TP 在低位 | `analysis_template.py` L1304-1310, `_social_publish.py` L557-578, `publish_social.py` L302-312 |
| P1a | 优化 | entry 改用 `ticker.last`（现价）替代 `close_1d` | `analysis_template.py` L1303 |
| P1b | 优化 | 社交 SL 改用 ATR 缓冲替代硬编码 0.99/0.985 | `_social_publish.py` L563-564, `publish_social.py` L302-312 |
| P2 | 优化 | `calc_position` 常量提取(LEVERAGE/ACCOUNT_USD/MARGIN_MAINTENANCE) + contracts floor() + 补全 SOL/DOGE mmr | `analysis_template.py` L127-166 |
| P3a | 修复 | JSON position 字段统一：near_bottom 触发偏多（不再抑制） | `analysis_template.py` L2143-2160 |
| P3b | 新增 | 爆仓价输出 + 仓位公式行重构 | `analysis_template.py` L1342, L1465 |

### 第二轮：f1-f5（5 项清理）

| 编号 | 类别 | 改动 | 文件 |
|------|------|------|------|
| f1 | Bug | 社交结语根据方向动态切换（偏空不再说"做空的都成了燃料"） | `_social_publish.py` L595-613 |
| f2 | Bug | 观望时不输出 🎯 行（避免假 SL/TP） | `_social_publish.py` L591-595 |
| f3 | 统一 | RR 警告阈值 1.0→1.5 | `_social_publish.py` L593 |
| f4 | 清理 | 删除 `close_1d` 死变量 | `analysis_template.py` L1301 |
| f5 | 清理 | 删除 `_format_summary_section` 死分支 | `analysis_template.py` L1461 |

### 第三轮：L1/L3（做空入口 + V 反保护）

| 编号 | 类别 | 改动 | 文件 |
|------|------|------|------|
| L1 | 新增 | **near_top**：RSI_1d>67 + 1D方向='下降' + MACD_4h<0 → 跳过共振直接触发做空 | `analysis_template.py` L1306-1307, `_social_publish.py` L453-455, L2148-2150 |
| L3a | 新增 | V 反保护(bottom)：near_bottom=True → 做空降级为 `观望（near_bottom保护）` | `analysis_template.py` L1311-1312, `_social_publish.py` L458 |
| L3b | 新增 | V 反保护(bounce)：8 根 4H 内从最低点反弹 > 3% → 做空降级为 `观望（反弹X%，V反保护）` | `analysis_template.py` L1313-1320 |
| — | 配套 | 所有 `pos_dir` 判断从 `== '观望'` 改为 `.startswith('试')` / `.startswith('观望')` | `analysis_template.py` 6 处 |

## 验证结果

```
✅ near_top 触发 (RSI>67+下降+MACD<0)    → 试空
✅ near_top 不触发 (RSI>67+上升+MACD<0)  → 观望
✅ near_top 不触发 (RSI≤67+下降+MACD<0)  → 观望
✅ 标准做空 (共振弱)                       → 试空
✅ V 反保护 (near_bottom)                → 观望（near_bottom保护）
✅ V 反保护 (反弹>3%)                    → 观望（反弹3.9%，V反保护）
```

## 现有做空入口（3 条路径）

1. **共振弱**：score ≤ -2 → 🔴偏弱 → 试空
2. **RSI+MACD 组合**：RSI_1d>65 + MACD_4h<-50 → 试空
3. **near_top**：RSI_1d>67 + 1D下降 + MACD_4h<0 → 试空

三条路径共享 V 反保护：near_bottom=True 或 8根4H内反弹>3% → 降级观望。
