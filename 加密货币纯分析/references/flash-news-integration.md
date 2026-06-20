# Flash News 集成架构

> 创建：2026-06-20 | 最后更新：2026-06-20 23:45 BJ
> 状态：✅ 已上线（P0/P2 已修复）

## 数据流

```
jin10 MCP
  ├─ list_flash (2页, 最新快讯列表)
  └─ search_flash (5关键词: 美联储, 比特币, 以太坊, 加密货币, BTC)
        ↓
  _flash_relevance_score()  关联度评分
        ├─ 高权重 (+3): 美联储/加息/通胀/CPI/BTC/ETH/加密货币/SEC/DXY/中东/油价
        ├─ 中权重 (+1): 黄金/美股/纳指/特朗普/监管/关税/ETF/贝莱德/清算/爆仓
        └─ 黑名单 (-10): A股/IPO/票房/芯片/宁德时代/BTC原油管道
        ↓
  7天时间窗口过滤
        ↓
  去重 + 排序 (取前15)
        ↓
  缓存 (5min TTL, ~/.hermes/trade_review/data/jin10_flash_cache.json)
        ↓
  ├─ analyses.json (analysis_template.py main() → record building)
  └─ social_analyses.json (_social_publish.py wrapper → publish_social.py record dict)
```

## 关联度评分逻辑

| 权重 | 关键词 | 目的 |
|------|--------|------|
| +3 | 美联储/加息/降息/通胀/CPI/PPI/PCE/非农/BTC/ETH/比特币/以太坊/加密货币/SEC/DXY/美元指数/中东/霍尔木兹/油价/原油 | 直接关联加密或宏观核心变量 |
| +1 | 黄金/美股/纳指/标普/地缘/特朗普/监管/关税/贸易战/日本央行/欧洲央行/ETF/贝莱德/微策略/清算/爆仓/交易所/币安/Coinbase/灰度 | 间接关联或情绪指标 |
| -10 | A股/上证/深证/创业板/科创板/港股午评/IPO/新股/票房/端午/芯片/宁德时代/BTC原油/BTC管道/杰伊汉/阿塞拜疆 | 不关联加密的噪声 |

搜索命中额外 +2 分。

## 集成点

| 脚本 | 获取方式 | 写入目标 | 状态 |
|------|----------|----------|------|
| `jin10_fallback.py` | `fetch_flash_news()` — MCP实时 → 缓存 → 空列表 | 缓存文件 | ✅ |
| `analysis_template.py` | `main()` 记录构建阶段调用 `fetch_flash_news()` | `analyses.json` `flash_news` 字段 | ✅ |
| `_social_publish.py` | `analyze_single_coin()` wrapper 调用 `fetch_flash_news()` | 返回 dict 的 `flash_news` 键 | ✅ |
| `publish_social.py` | 从 `_social_publish` 返回的 dict 读取 | `social_analyses.json` `flash_news` 字段 | ✅ |
| `generate_social_draft()` | 从 analyses dict 读取 `btc.get('flash_news')` | 文案 📰 行 | ✅ |

## JSON 格式

```json
{
  "flash_news": [
    {
      "time": "2026-06-20T10:26:10+08:00",
      "content": "美联储加息押注上升...",
      "score": 8,
      "url": "https://flash.jin10.com/detail/..."
    }
  ]
}
```

## 历史修复记录

| 问题 | 严重度 | 状态 | 日期 |
|------|--------|------|------|
| `publish_social.py` 记录字典缺 `flash_news` | P0 | ✅ 已修复 | 2026-06-20 |
| `analysis_template.py` `generate_social_draft()` 死代码 | P2 | ✅ 已删除 | 2026-06-20 |
| stdout 报告不含快讯 | P2 | 🟡 设计如此（format_report 在记录构建前执行） | — |
