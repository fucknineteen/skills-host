#!/bin/bash
# Auto-push local script changes to GitHub fucknineteen/skills-host
# Maps local files to their corresponding paths in the repo

set -e

MIRROR="/root/.hermes/skills-host-mirror"
TRADE_REVIEW="/root/.hermes/trade_review"
SCRIPTS_DIR="/root/.hermes/scripts"

# ── Pull latest first ──
cd "$MIRROR"
git pull --ff-only origin master 2>/dev/null || true

# ── Map & copy local files to mirror ──
# cron scripts
for f in sync_klines_cron.sh cron_review_process.sh regime_update.sh guard_jin10_token.sh refresh_jin10_cache.sh; do
    if [ -f "$SCRIPTS_DIR/$f" ]; then
        cp "$SCRIPTS_DIR/$f" "$MIRROR/cron/$f"
    fi
done

# 加密货币纯分析 scripts
for f in monitor_and_sync.py process_reviews.py analysis_template.py regime_detector.py _shared.py jin10_fallback.py; do
    if [ -f "$TRADE_REVIEW/$f" ]; then
        cp "$TRADE_REVIEW/$f" "$MIRROR/analysis/加密货币纯分析/scripts/$f"
    fi
done

# 社交动态发布 scripts (in trade_review/ root)
for f in publish_social.py verify_social_post.py _social_publish.py; do
    if [ -f "$TRADE_REVIEW/$f" ]; then
        cp "$TRADE_REVIEW/$f" "$MIRROR/social/社交动态发布/scripts/$f"
    fi
done

# 社交动态发布 scripts (in trade_review/scripts/)
for f in gen_charts.py save_social_post.py review_last_post.py; do
    if [ -f "$TRADE_REVIEW/scripts/$f" ]; then
        cp "$TRADE_REVIEW/scripts/$f" "$MIRROR/social/社交动态发布/scripts/$f"
    fi
done

# ── Check for changes ──
cd "$MIRROR"
if git diff --quiet && git diff --cached --quiet; then
    exit 0  # No changes, silent
fi

# ── Commit & push ──
git add -A
git commit -m "auto sync: $(date '+%Y-%m-%d %H:%M BJ')"
git push origin master
