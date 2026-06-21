#!/bin/bash
# 三因子共振 Bot 守护 — 每小时检查进程 + 输出最近日志
BOT_PID=$(pgrep -f "three_factor_bot.py" | head -1)
LOG_FILE="/root/.hermes/trade_review/three_factor_bot.log"

if [ -z "$BOT_PID" ]; then
    echo "❌ 三因子 Bot 未运行！尝试重启..."
    cd /root/.hermes/trade_review
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
    nohup python3 -u three_factor_bot.py >> three_factor_bot.log 2>&1 &
    sleep 5
    NEW_PID=$(pgrep -f "three_factor_bot.py" | head -1)
    if [ -n "$NEW_PID" ]; then
        echo "✅ Bot 已重启 PID=$NEW_PID"
    else
        echo "❌ 重启失败！检查 three_factor_bot.log"
    fi
else
    echo "✅ Bot 运行中 PID=$BOT_PID"
fi

echo "--- 最近日志 (最后8行) ---"
tail -8 "$LOG_FILE" 2>/dev/null || echo "(日志为空)"
