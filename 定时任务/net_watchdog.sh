#!/bin/bash
# 网络恢复检测 — 每分钟执行，网络通了就发消息
LOG="/tmp/net_recovery.log"

# 测试连通性
if curl -s --connect-timeout 5 --max-time 10 "https://api.telegram.org" -o /dev/null 2>/dev/null; then
    # 检查是否之前断过（有日志文件说明断过）
    if [ -f /tmp/net_was_down ]; then
        MSG="✅ 网络已恢复 $(date '+%m-%d %H:%M') BJ"
        # 用 curl 发 Telegram（直接走 Bot API，不依赖 Hermes 推送）
        curl -s --connect-timeout 10 --max-time 15 \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=1332506590" -d "text=${MSG}" -o /dev/null 2>/dev/null
        rm /tmp/net_was_down
        echo "$(date): 网络已恢复，已通知" >> "$LOG"
    fi
else
    # 网络不通，标记
    touch /tmp/net_was_down
fi
