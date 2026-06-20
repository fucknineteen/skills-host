# Skills Host

Hermes Agent 技能库备份仓库。存放加密货币交易分析工作流的标准化操作文档。

## 技能列表

| 技能 | 说明 |
|------|------|
| [`加密货币纯分析`](./加密货币纯分析/SKILL.md) | 同步K线 → 执行分析 → 从 JSON 提取数据 → 按模板输出结论 → 三层自动复盘 |
| [`社交动态发布`](./社交动态发布/SKILL.md) | 7步流水线：同步 → 分析 → 复盘上条 → 写文案 → 核验数据 → 配图 → 保存 |
| [`cron`](./cron/README.md) | 定时任务脚本集：K线同步、三层复盘、宏观检测、金十守护、网络监控（10个脚本） |

## 交易教程（25篇）

| 教程 | 分类 |
|------|------|
| [道氏理论完全教程](./trading-tutorials/道氏理论完全教程.md) | 理论基础 |
| [威科夫方法完全教程](./trading-tutorials/威科夫方法完全教程.md) | 理论基础 |
| [缠论完全教程](./trading-tutorials/缠论完全教程.md) | 理论基础 |
| [价格行为完全教程](./trading-tutorials/价格行为Price_Action完全教程.md) | 技术分析 |
| [PA 深度提取](./trading-tutorials/PA深度提取_市场结构_趋势_支撑阻力_推动修正波.md) | 技术分析 |
| [K线形态完全指南](./trading-tutorials/K线形态完全指南.md) | 技术分析 |
| [K线形态实战精华](./trading-tutorials/K线形态实战精华_BTC_ETH.md) | 技术分析 |
| [FVG 公允价值缺口](./trading-tutorials/FVG公允价值缺口完全教程.md) | 技术分析 |
| [技术指标手册·趋势类](./trading-tutorials/技术指标手册_第1卷_趋势类.md) | 技术指标 |
| [技术指标手册·震荡类](./trading-tutorials/技术指标手册_第2卷_震荡类.md) | 技术指标 |
| [技术指标手册·波动率与成交量](./trading-tutorials/技术指标手册_第3卷_波动率与成交量类.md) | 技术指标 |
| [VSA量价差分析](./trading-tutorials/VSA量价差分析完全教程.md) | 量价分析 |
| [VSA 21个核心信号](./trading-tutorials/VSA_21_Signals_Extraction.md) | 量价分析 |
| [成交量分布完全教程](./trading-tutorials/成交量分布完全教程.md) | 量价分析 |
| [市场轮廓与订单流](./trading-tutorials/市场轮廓与订单流完全教程.md) | 订单流 |
| [订单流实战进阶](./trading-tutorials/订单流实战进阶教程.md) | 订单流 |
| [VWAP 完全教程](./trading-tutorials/VWAP完全教程.md) | 订单流 |
| [合约市场深度·第1卷](./trading-tutorials/合约市场深度分析手册_第1卷.md) | 合约交易 |
| [合约市场深度·第2卷](./trading-tutorials/合约市场深度分析手册_第2卷.md) | 合约交易 |
| [洗盘与出货·庄家思维](./trading-tutorials/洗盘与出货_庄家思维完全教程.md) | 合约交易 |
| [做T完全教程](./trading-tutorials/做T完全教程.md) | 短线策略 |
| [剥头皮策略完全教程](./trading-tutorials/剥头皮策略完全教程.md) | 短线策略 |
| [筹码峰完全教程](./trading-tutorials/筹码峰完全教程.md) | 辅助工具 |
| [正态分布与加密交易](./trading-tutorials/正态分布完全教程.md) | 辅助工具 |
| [DeFi 完全教程](./trading-tutorials/DeFi完全教程.md) | 生态 |

## 其他

| 文件 | 说明 |
|------|------|
| [`玄学/xuanxue-advanced-research.md`](./玄学/xuanxue-advanced-research.md) | 八字/玄学研究文档 |

## 环境

- **系统**：Hermes Agent（Nous Research）
- **交易平台**：OKX 永续合约
- **数据源**：OKX API / 金十MCP / alt.me / Yahoo Finance / FRED
- **时区**：BJ（UTC+8）
- **工作目录**：`/root/.hermes/trade_review/`

## 结构

```
skills-host/
├── README.md
├── cron/                          ← 定时任务脚本（10个）
├── 加密货币纯分析/                 ← 分析工作流
│   ├── SKILL.md
│   ├── scripts/                  ← analysis_template.py, jin10_fallback.py 等
│   ├── references/               ← 架构文档、设计决策
│   └── templates/                ← 输出模板
├── 社交动态发布/                   ← 发布工作流
│   ├── SKILL.md
│   ├── scripts/                  ← publish_social.py, verify_social_post.py 等
│   ├── references/
│   └── templates/
├── trading-tutorials/             ← 25篇交易教程
└── 玄学/                          ← 八字/玄学研究
```

## 更新日志

- **2026-06-20**：快讯集成（金十 MCP）到两个工作流；删除 `analysis_template.py` 死代码；文档审计同步
- **2026-06-20**：目录中文化（`crypto-analysis-workflow` → `加密货币纯分析`，`social-posting-workflow` → `社交动态发布`）
- **2026-06-20**：统一计算层重构（`_social_publish.py` 全量从 `analysis_template` import，消除 11 个重复函数）
