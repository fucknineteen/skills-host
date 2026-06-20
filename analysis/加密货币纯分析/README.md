# 加密货币纯分析

技术分析工作流：同步K线 → 执行 analysis_template.py → 从 analyses.json 提取数据 → 按模板输出报告。

## 文件结构
- `SKILL.md` — 工作流操作手册
- `scripts/` — 分析脚本（analysis_template.py, monitor_and_sync.py, jin10_fallback.py 等）
- `references/` — 架构文档和设计决策
- `templates/` — 输出模板
