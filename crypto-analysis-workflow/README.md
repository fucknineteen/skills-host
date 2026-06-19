# Crypto Analysis Workflow

加密货币纯技术分析工作流。从 K线同步到分析结论输出的完整标准化流程。

## 文件说明

| 文件 | 用途 |
|------|------|
| `SKILL.md` | 工作流完整说明：前置条件 → 执行分析 → 生成结论 → 三层复盘 |
| `scripts/analysis_template.py` | 分析主脚本。同步K线、计算指标、威科夫/VP/K线形态检测、宏观环境、写入 analyses.json |
| `scripts/monitor_and_sync.py` | K线增量同步。从 OKX API 拉取 5m/15m/30m/1H/4H/1D/1W 全周期数据存入 okx_klines.db |
| `scripts/regime_detector.py` | 宏观环境检测。计算 FG/DXY/VIX/10Y/BTC.D、道氏方向、加速判定、威科夫阶段、共振评级 |
| `scripts/process_reviews.py` | 三层复盘引擎。每小时 cron 自动运行，对 analyses.json 中的分析结论按 6h/12h/72h 阈值逐层复盘 |
| `scripts/_shared.py` | 共享工具。BJT 时区、DB 路径、classify_price_path() 路径分类、get_klines() K线获取 |
| `templates/分析报告模板v5.0.md` | 分析报告输出模板（Part 1 行情+技术+宏观，Part 2 风险+仓位） |
| `cron/sync_klines_cron.sh` | K线同步定时任务脚本 |

## 工作流概览

```
同步K线 → 执行分析 → 从 JSON 逐项提取 → 按模板输出 → 三层自动复盘
  ↓           ↓              ↓              ↓              ↓
monitor    analysis      analyses.json   分析报告模板    process_reviews
_and_sync  _template                      v5.0            (6h/12h/72h)
```

## 与其他工作流的关系

- 本工作流只做**纯技术分析**，不生成社交文案
- 发动态需走 `social-posting-workflow`
- 三层复盘引擎自动化运行，无需手动触发

## 环境依赖

- Python 3.11+
- jq CLI (`apt install jq`)
- SQLite3（okx_klines.db）
- OKX API 访问权限
- 金十 MCP 数据源
