# Cron Jobs

所有定时任务脚本。由 Hermes Agent cronjob 调度引擎管理，`no_agent: true` 模式运行（脚本 stdout 直接交付，不经过 LLM）。

## 任务清单

| 任务 | 脚本 | 频率 | 说明 |
|------|------|------|------|
| 🔄 K线同步 | `sync_klines_cron.sh` | 每小时 :00 | OKX API 增量同步全周期K线到本地DB |
| 📊 三层复盘 | `cron_review_process.sh` | 每小时 :03 | 对 analyses.json 中的分析结论执行 6h/12h/72h 复盘 |
| 📈 宏观检测 | `regime_update.sh` | 每小时 :02 | 更新 regime_cache（FG/DXY/VIX/10Y/BTC.D） |
| 📅 金十日历 | `refresh_jin10_cache.sh` | 每6小时 :00 | 刷新金十财经日历缓存 |
| 🛡 金十守护 | `guard_jin10_token.sh` | 每10分钟 | 监控金十MCP token 有效性，过期自动刷新 |
| 🛡 金十守护(Py) | `jin10_mcp_guard.py` | 按需 | Python 版金十MCP守护进程 |
| 📡 三因子监控 | `watch_three_factor.sh` | 按需 | 监控 FG/DXY/VIX 三因子异动 |
| 🌐 网络守护 | `net_watchdog.sh` | 按需 | 监控网络连通性 |
| 🔧 网络恢复 | `net_recovery.sh` | 按需 | 网络中断后自动恢复 |
| 🪙 币种扫描 | `scan_daytrade_coins.py` | 按需 | 扫描适合日内交易的币种 |

## 调度时机

```
每小时 :00  → sync_klines_cron.sh        K线同步
每小时 :02  → regime_update.sh           宏观检测
每小时 :03  → cron_review_process.sh     三层复盘
每10分钟   → guard_jin10_token.sh        金十守护
每6小时    → refresh_jin10_cache.sh      金十日历刷新
每日09:00  → (LLM) 教训归因              lessons.json叙事
```

## 运行方式

所有脚本通过 Hermes cronjob 引擎以 `no_agent: true` 模式运行，stdout 不为空时交付给用户：

```
cronjob(action='create', 
         name='...', 
         schedule='...', 
         script='script_name.sh',
         no_agent=True)
```

## 环境

- **工作目录**：`/root/.hermes/`
- **脚本位置**：`/root/.hermes/scripts/`
- **数据目录**：`/root/.hermes/trade_review/`
- **Python**：3.11+
