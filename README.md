# Skills Host

Hermes Agent 技能库备份仓库。存放加密货币交易分析工作流的标准化操作文档。

## 技能列表

| 技能 | 说明 |
|------|------|
| [`crypto-analysis-workflow`](./crypto-analysis-workflow/SKILL.md) | 加密货币纯分析工作流：同步K线 → 执行分析 → 从 JSON 逐项提取数据 → 按模板输出结论 → 三层自动复盘 |
| [`social-posting-workflow`](./social-posting-workflow/SKILL.md) | 社交动态发布7步流水线：同步 → 分析 → 复盘上条 → 按模板写文案 → 核验数据 → 配图 → 保存 |

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
├── crypto-analysis-workflow/
│   └── SKILL.md
└── social-posting-workflow/
    └── SKILL.md
```
