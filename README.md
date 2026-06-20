# Skills Host

Hermes Agent 技能库备份仓库。存放加密货币交易分析工作流的标准化操作文档。

## 技能列表

| 技能 | 说明 |
|------|------|
| [`加密货币纯分析`](./加密货币纯分析/SKILL.md) | 加密货币纯分析工作流：同步K线 → 执行分析 → 从 JSON 逐项提取数据 → 按模板输出结论 → 三层自动复盘 |
| [`社交动态发布`](./社交动态发布/SKILL.md) | 社交动态发布7步流水线：同步 → 分析 → 复盘上条 → 按模板写文案 → 核验数据 → 配图 → 保存 |
| [`cron`](./cron/README.md) | 定时任务脚本集：K线同步、三层复盘、宏观检测、金十守护、网络监控（10个脚本） |

## 环境

- **系统**：Hermes Agent（Nous Research）
- **交易平台**：OKX 永续合约
- **数据源**：OKX API / 金十MCP / alt.me / Yahoo Finance / FRED
- **时区**：BJ（UTC+8）
- **工作目录**：`/root/.hermes/trade_review/`

## 关键模板

| 模板 | 用途 |
|------|------|
| 分析报告模板 v5.0 | 技术分析结论输出（BTC/ETH/SOL） |
| 社交动态模板 v5.1 | Telegram 社交动态文案输出 |

> ⚠️ 两个模板独立，不可混用。"分析 BTC"用分析报告模板，"发动态"用社交动态模板。

## 结构

```
skills-host/
├── README.md
├── cron/
│   ├── README.md                          ← 定时任务总说明
│   ├── sync_klines_cron.sh               ← K线同步 (每小时)
│   ├── cron_review_process.sh            ← 三层复盘 (每小时)
│   ├── regime_update.sh                  ← 宏观检测 (每小时)
│   ├── refresh_jin10_cache.sh            ← 金十日历 (每6h)
│   ├── guard_jin10_token.sh              ← 金十守护 (每10m)
│   ├── jin10_mcp_guard.py                ← 金十守护 (Python)
│   ├── watch_three_factor.sh             ← 三因子监控
│   ├── net_watchdog.sh                   ← 网络守护
│   ├── net_recovery.sh                   ← 网络恢复
│   └── scan_daytrade_coins.py            ← 币种扫描
├── 加密货币纯分析/
│   ├── README.md
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── templates/
└── 社交动态发布/
    ├── README.md
    ├── SKILL.md
    ├── scripts/
    ├── references/
    └── templates/
```

## 更新日志

- **2026-06-20**：快讯集成（金十 MCP `list_flash` + `search_flash`）到两个工作流；删除 `analysis_template.py` 死代码；文档审计同步
- **2026-06-20**：目录中文化（`crypto-analysis-workflow` → `加密货币纯分析`，`social-posting-workflow` → `社交动态发布`）
- **2026-06-20**：统一计算层重构（`_social_publish.py` 全量从 `analysis_template` import，消除 11 个重复函数）
