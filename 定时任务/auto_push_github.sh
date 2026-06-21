#!/bin/bash
# Auto-push local script changes to GitHub fucknineteen/skills-host
# Only updates files that already exist in the remote repo — never adds new files

set -e

MIRROR="/root/.hermes/skills-host-mirror"
TRADE_REVIEW="/root/.hermes/trade_review"
SCRIPTS_DIR="/root/.hermes/scripts"

# ── Pull latest first ──
cd "$MIRROR"
git pull --ff-only origin master 2>/dev/null || true

# ── Build whitelist: only files already tracked in the repo ──
WHITELIST=$(mktemp)
git ls-files > "$WHITELIST"

# Helper: copy only if destination is tracked in the repo
safe_copy() {
    local src="$1"
    local dest="$2"
    # compute relative path within repo
    local rel="${dest#$MIRROR/}"
    if grep -qFx "$rel" "$WHITELIST"; then
        cp "$src" "$dest"
    fi
}

# ── Map & copy local files to mirror (only if tracked in repo) ──
# cron scripts
for f in sync_klines_cron.sh cron_review_process.sh regime_update.sh guard_jin10_token.sh refresh_jin10_cache.sh; do
    if [ -f "$SCRIPTS_DIR/$f" ]; then
        safe_copy "$SCRIPTS_DIR/$f" "$MIRROR/定时任务/$f"
    fi
done

# 加密货币纯分析 scripts
for f in monitor_and_sync.py process_reviews.py analysis_template.py regime_detector.py _shared.py jin10_fallback.py massive_client.py; do
    if [ -f "$TRADE_REVIEW/$f" ]; then
        safe_copy "$TRADE_REVIEW/$f" "$MIRROR/分析/加密货币纯分析/scripts/$f"
    fi
done

# 社交动态发布 scripts (in trade_review/ root)
for f in publish_social.py verify_social_post.py _social_publish.py; do
    if [ -f "$TRADE_REVIEW/$f" ]; then
        safe_copy "$TRADE_REVIEW/$f" "$MIRROR/社交/社交动态发布/scripts/$f"
    fi
done

# 社交动态发布 scripts (in trade_review/scripts/)
for f in gen_charts.py save_social_post.py review_last_post.py; do
    if [ -f "$TRADE_REVIEW/scripts/$f" ]; then
        safe_copy "$TRADE_REVIEW/scripts/$f" "$MIRROR/社交/社交动态发布/scripts/$f"
    fi
done

# 山寨币分析 scripts
for f in analysis_altcoin.py review_altcoin.py scan_daytrade_coins.py; do
    if [ -f "$TRADE_REVIEW/scripts/$f" ]; then
        safe_copy "$TRADE_REVIEW/scripts/$f" "$MIRROR/分析/山寨币庄控分析/scripts/$f"
    fi
done

# 辅助脚本（分析/复盘/验证）
for f in inject_lessons.py price_path_report.py verify_workflow.py; do
    case "$f" in
        inject_lessons.py) dest="$MIRROR/分析/加密货币纯分析/scripts/$f" ;;
        price_path_report.py) dest="$MIRROR/社交/社交动态发布/scripts/$f" ;;
        verify_workflow.py) dest="$MIRROR/社交/社交动态发布/scripts/$f" ;;
    esac
    if [ -f "$TRADE_REVIEW/$f" ] || [ -f "$TRADE_REVIEW/scripts/$f" ]; then
        [ -f "$TRADE_REVIEW/$f" ] && SRC="$TRADE_REVIEW/$f" || SRC="$TRADE_REVIEW/scripts/$f"
        safe_copy "$SRC" "$dest"
    fi
done

rm -f "$WHITELIST"

# 实盘下单（手动调用，不自动同步）
# place_live_orders.py — 含 API 密钥，不推送到仓库

# ── Check for changes ──
cd "$MIRROR"
if git diff --quiet && git diff --cached --quiet; then
    exit 0  # No changes, silent
fi

# ── Commit & push ──
git add -A
git commit -m "auto sync: $(date '+%Y-%m-%d %H:%M BJ')"
git push origin master
