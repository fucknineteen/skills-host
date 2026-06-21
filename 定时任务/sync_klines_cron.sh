#!/bin/bash
# K线同步 + 完整性校验 (cron) — 仅在异常时输出
cd /root/.hermes/trade_review

ISSUES=""

# 0. 备份数据库 (每天 00:00 一次)
DB="okx_klines.db"
DB_BAK="${DB}.bak"
if [ $(date +%H) = "00" ]; then
    cp "$DB" "$DB_BAK" 2>/dev/null || true
fi

# 1. 同步所有币种
SYNC_OUT=$(python3 monitor_and_sync.py BTC ETH SOL DOGE 2>&1) || true
if echo "$SYNC_OUT" | grep -qE '错误|失败|Error|❌'; then
    ISSUES="${ISSUES}[同步异常] ${SYNC_OUT}\\n"
fi

# 2. 完整性校验 (使用相对路径)
INTEGRITY_OUT=$(python3 scripts/check_db_integrity.py 2>&1) || true
if [ $? -ne 0 ] || echo "$INTEGRITY_OUT" | grep -q '⚠️'; then
    ISSUES="${ISSUES}[数据异常] ${INTEGRITY_OUT}\\n"
fi

# 输出 — 仅异常时打印
if [ -n "$ISSUES" ]; then
    echo -e "$ISSUES"
fi

# 3. 清理旧 cron 日志 (>7天) + jin10 日历缓存 (>24h)
find /root/.hermes/cron/output/ -type f -mtime +7 -delete 2>/dev/null || true
find /root/.hermes/trade_review/data/ -name 'jin10_calendar_cache.json' -mtime +1 -delete 2>/dev/null || true
