# 金十快讯集成架构

> 2026-06-20 新增。快讯（Flash）通过 `jin10_fallback.py` 接入两个分析工作流。

## 数据流

```
金十MCP
├── list_flash (2页, 游标分页, 每页20条)
├── search_flash × 5关键词 (美联储/比特币/以太坊/加密货币/BTC)
│
▼
jin10_fallback.py::fetch_flash_news()
├── 关联度评分 (_flash_relevance_score)
├── 7天时间窗口
├── 黑名单排除 (A股/IPO/票房等)
├── URL去重
├── 排序 → 前15条
│
▼
本地缓存 (5min TTL)
│  ~/.hermes/trade_review/data/jin10_flash_cache.json
│
├─→ analysis_template.py main() 记录构建 → analyses.json.flash_news
└─→ _social_publish.py wrapper → publish_social.py → social_analyses.json.flash_news
```

## 关联度评分

### 高权重 (+3/词)
美联储 加息 降息 通胀 CPI PPI PCE 非农 BTC ETH 比特币 以太坊 加密货币 SEC DXY 美元指数 中东 霍尔木兹 油价 原油

### 中权重 (+1/词)
黄金 美股 纳指 标普 地缘 特朗普 监管 关税 贸易战 日本央行 欧洲央行 ETF 贝莱德 微策略 清算 爆仓 交易所 币安 Coinbase 灰度

### 黑名单 (-10/词)
A股 上证 深证 创业板 科创板 港股午评 港股收评 IPO 新股 打新 上市辅导 票房 端午 出游 外卖 芯片 存储芯片 英韧科技 固态硬盘 宁德时代 联想集团 熊猫债 BTC原油 BTC管道 杰伊汉 阿塞拜疆

## 缓存策略

- TTL: 5分钟 (`FLASH_CACHE_TTL = 300`)
- 三层回退: MCP实时 → 本地缓存 → 空列表
- 缓存路径: `~/.hermes/trade_review/data/jin10_flash_cache.json`
- 日历缓存独立 (`jin10_calendar_cache.json`, 6h TTL)

## 两个工作流的使用

### 纯分析流程
1. `analysis_template.py main()` → 记录构建循环逐币种调用 `fetch_flash_news()`
2. 写入 `analyses.json` 的 `flash_news` 字段: `[{time, content, score, url}]`
3. LLM 分析时从 JSON 读取 📰 消息面段落

### 社交发动态流程
1. `_social_publish.py analyze_single_coin()` wrapper 调用 `fetch_flash_news()`
2. 写入 result dict 的 `flash_news` 字段
3. `publish_social.py` 记录字典包含 `flash_news` → 写入 `social_analyses.json`
4. `generate_social_draft()` 从分析结果读取前3条高关联度快讯

## 核验脚本影响

`verify_social_post.py` 当前不校验 `flash_news` 字段。📰 行内容来自动态快讯文本，不是 JSON 中的结构化数值，无法做精确比对。核验脚本仅跳过此字段，不报假阳性。

## 关键函数

| 函数 | 文件 | 用途 |
|------|------|------|
| `_jin10_mcp_session()` | jin10_fallback.py | 建立金十MCP会话（可复用） |
| `fetch_flash_news()` | jin10_fallback.py | 主入口：拉取+过滤+缓存 |
| `get_flash_summary()` | jin10_fallback.py | 人类可读摘要 |
| `_flash_relevance_score()` | jin10_fallback.py | 关联度评分引擎 |
| `try_jin10_flash_paginate()` | jin10_fallback.py | 分页拉取 |
| `try_jin10_search_flash()` | jin10_fallback.py | 关键词搜索 |

## CLI 测试

```bash
cd /root/.hermes/trade_review
python3 jin10_fallback.py --flash           # 查看快讯列表
python3 jin10_fallback.py --flash-summary   # 人类可读摘要
```
