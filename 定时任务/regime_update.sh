#!/bin/bash
# Regime detection + index update — runs before review cron  
# Silent unless regime actually switched
# Also saves regime cache for analysis_template.py fast access
cd /root/.hermes/trade_review
OUTPUT=$(python3 regime_detector.py --update 2>/dev/null)
SWITCHED=$(echo "$OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('regime_switched', False))" 2>/dev/null)

# Save cache for get_regime_result() in analysis_template.py
echo "$OUTPUT" > .regime_cache.json 2>/dev/null

if [ "$SWITCHED" = "True" ]; then
    echo "$OUTPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
idx_path = '/root/.hermes/trade_review/regimes/regime_index.json'
with open(idx_path) as f:
    idx = json.load(f)
transitions = idx.get('regime_transitions', [])
if transitions:
    last = transitions[-1]
    print(f'⚠️ REGIME SWITCHED: {last[\"from_regime\"]} → {last[\"to_regime\"]}')
    print(f'   Date: {last[\"date\"]} | Confidence: {last[\"confidence\"]}%')
    print(f'   Trigger: {last[\"trigger\"]}')
" 2>/dev/null
fi
exit 0
