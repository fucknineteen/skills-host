#!/bin/bash
# 每2分钟检测网络，恢复时输出通知
STAMP_FILE="/tmp/net_was_down"

# 测试外网
if curl -s --connect-timeout 5 --max-time 10 "https://www.okx.com/api/v5/public/time" -o /dev/null 2>/dev/null; then
    if [ -f "$STAMP_FILE" ]; then
        DOWN_SINCE=$(cat "$STAMP_FILE")
        echo "✅ 网络已恢复 | 断网时段: ${DOWN_SINCE} → $(date '+%m-%d %H:%M') BJ"
        echo ""
        echo "正在补同步K线数据..."
        cd /root/.hermes/trade_review
        python3 -u monitor_and_sync.py BTC ETH SOL DOGE 2>&1
        rm "$STAMP_FILE"
    fi
    # 网络正常且之前没断 → 静默
else
    if [ ! -f "$STAMP_FILE" ]; then
        date '+%m-%d %H:%M' > "$STAMP_FILE"
    fi
    # 断网中 → 静默（推送会失败）
fi
