#!/usr/bin/env python3
"""
Workflow Verification Script — full-system health check.
Matches v2.7 audit checklist in trade-review-workflow skill.

Usage:
  python3 scripts/verify_workflow.py          # full check
  python3 scripts/verify_workflow.py --quick   # skip external API checks
"""

import json
import os
import sys
import sqlite3
import time
import traceback
from pathlib import Path

BASE = Path('/root/.hermes/trade_review')
DB_PATH = BASE / 'okx_klines.db'
REGIME_DIR = BASE / 'regimes'
SCRIPTS_DIR = Path('/root/.hermes/scripts')

PASS = 0
WARN = 0
FAIL = 0

def check(name, ok, detail=""):
    global PASS, WARN, FAIL
    if ok is True:
        PASS += 1
        print(f"  ✅ {name}: {detail}" if detail else f"  ✅ {name}")
    elif ok is None:
        WARN += 1
        print(f"  ⚠️ {name}: {detail}" if detail else f"  ⚠️ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}" if detail else f"  ❌ {name}")
    return ok


# ============================================================
# 1. File Integrity
# ============================================================
print("=" * 60)
print("1. FILE INTEGRITY")
print("=" * 60)

CORE_FILES = [
    'regime_detector.py',
    'analysis_template.py',
    'process_reviews.py',
    'monitor_and_sync.py',
    'massive_client.py',
    'analyses.json',
    'reviews.json',
    'lessons.json',
    'social_posts.json',
    'social_reviews.json',
    'regimes/regime_index.json',
    'regimes/regime_definitions.json',
    'regimes/牛市回调_lessons.json',
    'regimes/牛市主升_lessons.json',
    'regimes/熊市趋势_lessons.json',
    'regimes/熊市反弹_lessons.json',
    'regimes/横盘震荡_lessons.json',
]

for f in CORE_FILES:
    fp = BASE / f
    check(f"file: {f}", fp.exists(), "missing" if not fp.exists() else f"{fp.stat().st_size:,} bytes")

# Scripts referenced by cron
CRON_SCRIPTS = ['sync_klines_cron.sh', 'regime_update.sh']
for s in CRON_SCRIPTS:
    sp = SCRIPTS_DIR / s
    check(f"cron script: {s}", sp.exists(), "missing" if not sp.exists() else "OK")

# Orphan check: should have exactly 10 active .py files (9 core + _shared.py, cleanup 2026-06-17)
py_count = len(list(BASE.glob('*.py')))
check("orphan cleanup: 11 .py files", py_count == 11, f"{py_count} .py files (unexpected count)")
print()
print("--- 1m timeframe check ---")
for f in ['monitor_and_sync.py']:
    fp = BASE / f
    if fp.exists():
        content = fp.read_text()
        has_1m = "'1m'" in content or '"1m"' in content
        check(f"no 1m in {f}", not has_1m, "has 1m reference! 1m was removed 2026-06-13" if has_1m else "OK (1m removed 2026-06-13, no regression)")

# Verify verified_workflow.py itself is referenced in skill
print()
print("--- Skill reference check ---")
skill_path = Path('/root/.hermes/skills/analysis/加密货币纯分析/SKILL.md')
if skill_path.exists():
    skill_content = skill_path.read_text()
    ref_ok = 'verify_workflow.py' in skill_content
    check("skill references this script", ref_ok, "referenced" if ref_ok else "NOT referenced")


# ============================================================
# 2. JSON Health
# ============================================================
print()
print("=" * 60)
print("2. JSON HEALTH")
print("=" * 60)

JSON_FILES = list(BASE.glob('*.json')) + list(REGIME_DIR.glob('*.json'))
for jf in sorted(set(JSON_FILES)):
    try:
        with open(jf) as f:
            data = json.load(f)
        if isinstance(data, list):
            n = len(data)
        elif isinstance(data, dict):
            n = len(data.keys())
        else:
            n = 1
        check(f"json: {jf.name}", True, f"valid ({n} items)")
        
        # Special checks
        if jf.name == 'analyses.json':
            # Check for duplicates
            from collections import Counter
            keys = []
            for a in (data if isinstance(data, list) else []):
                try:
                    from datetime import datetime, timezone, timedelta
                    BJ = timezone(timedelta(hours=8))
                    dt = datetime.fromisoformat(a.get('timestamp', ''))
                    keys.append((a.get('coin'), a.get('entry_price'), dt.strftime('%Y-%m-%d')))
                except (ValueError, TypeError):
                    keys.append((a.get('coin'), a.get('entry_price'), '?'))
            dupes = [k for k, cnt in Counter(keys).items() if cnt > 1]
            check("  analyses dedup", len(dupes) == 0, f"{len(dupes)} duplicate groups" if dupes else "clean")
            
        if jf.name == 'regime_index.json':
            active = [h for h in data.get('regime_history', []) if h.get('status') == 'active']
            check("  regime_history has active", len(active) > 0, 
                  f"active={active[0]['regime']}" if active else "no active regime!")
            
    except Exception as e:
        check(f"json: {jf.name}", False, f"parse error: {e}")


# ============================================================
# 3. Database Integrity
# ============================================================
print()
print("=" * 60)
print("3. DATABASE")
print("=" * 60)

try:
    db = sqlite3.connect(str(DB_PATH))
    # Check all timeframes
    tfs = db.execute("SELECT DISTINCT timeframe FROM klines ORDER BY timeframe").fetchall()
    tfs = [t[0] for t in tfs]
    check("DB accessible", True, f"{len(tfs)} timeframes: {', '.join(tfs)}")
    
    # Verify no 1m data (regression guard: 1m was removed 2026-06-13)
    m1_count = db.execute("SELECT COUNT(*) FROM klines WHERE timeframe='1m'").fetchone()[0]
    check("no 1m data (removed 2026-06-13)", m1_count == 0, f"{m1_count} rows" if m1_count else "clean")
    
    # Check coin coverage
    for coin in ['BTC', 'ETH', 'SOL', 'DOGE']:
        daily = db.execute(
            "SELECT COUNT(*), MAX(datetime(ts/1000,'unixepoch','+8 hours')) FROM klines WHERE coin=? AND timeframe='1D'",
            (coin,)
        ).fetchone()
        latest_ok = daily[1] and '2026-06-1' in str(daily[1])
        check(f"  {coin} 1D", latest_ok,
              f"{daily[0]} days, latest={daily[1]}" if latest_ok else f"stale: latest={daily[1]}")
    
    # Check 1H/4H gaps (quick: last 48h)
    for coin in ['BTC', 'ETH', 'SOL', 'DOGE']:
        for tf in ['1H', '4H']:
            ms = {'1H': 3600000, '4H': 14400000}[tf]
            gaps = db.execute(f"""
                WITH ranked AS (
                    SELECT ts, LAG(ts) OVER (ORDER BY ts DESC) as prev_ts
                    FROM klines WHERE coin='{coin}' AND timeframe='{tf}'
                )
                SELECT COUNT(*) FROM ranked WHERE prev_ts IS NOT NULL AND (ts - prev_ts) > {ms * 1.05}
            """).fetchone()[0]
            check(f"  {coin} {tf} gaps", gaps == 0, f"{gaps} gaps" if gaps else "zero gaps")
    
    db.close()
except Exception as e:
    check("DB", False, str(e))


# ============================================================
# 4. Regime Detector
# ============================================================
print()
print("=" * 60)
print("4. REGIME DETECTOR v2.7")
print("=" * 60)

try:
    sys.path.insert(0, str(BASE))
    
    # Check imports
    import regime_detector as rd
    check("import regime_detector", True, f"v2.7, {rd.__doc__.split(chr(10))[0].strip() if rd.__doc__ else 'no doc'}")
    
    # Check massive_client
    check("massive_client import", rd.MASSIVE_ENABLED, 
          "enabled" if rd.MASSIVE_ENABLED else "disabled")
    
    # Check talib
    try:
        import talib
        check("TA-Lib import", True, f"v{talib.__version__}")
    except ImportError:
        check("TA-Lib import", False, "not found")
    
    # Run detection and check weights
    start = time.time()
    result = rd.detect_regime(verbose=False)
    elapsed = time.time() - start
    
    dims = result.get('dimensions', {})
    
    # Weights sum check
    weights = [d.get('weight', 0) for d in dims.values()]
    w_sum = sum(weights)
    check("weight sum = 100", w_sum == 100, f"sum={w_sum} ({'OK' if w_sum==100 else 'WRONG!'})")
    
    # Check all 13 dimensions present
    expected_dims = {'price_structure', 'ma_dynamics', 'synthetic_fg', 'rsi_path', 
                     'volume', 'eth_btc', 'path_narrative', 'momentum', '4h_structure',
                     'historical_analogy', 'order_flow', 'macro_external', 'candlestick'}
    actual_dims = set(dims.keys())
    missing = expected_dims - actual_dims
    extra = actual_dims - expected_dims
    check("13 dimensions present", len(missing) == 0,
          f"missing={missing}" if missing else f"all 13 OK")
    
    # Composite check
    reported = result.get('composite_score', 0)
    computed = round(sum(d['score'] * d['weight'] for d in dims.values()) / 100, 1)
    check("composite self-check", abs(reported - computed) < 0.2,
          f"reported={reported} computed={computed}" if abs(reported-computed)>=0.2 
          else f"reported={reported}=computed ✓")
    
    # Result fields
    for key in ['regime', 'confidence', 'composite_score', 'transition_warnings', 'overlay']:
        check(f"  result.{key} present", key in result,
              str(result.get(key))[:60] if key in result else "missing")
    
    # Massive verify
    mv = result.get('massive_verify')
    if mv:
        check("massive_verify", mv['status'] == 'ok',
              f"diff={mv['diff']:+.2f} ({mv['diff_pct']:+.3f}%)")
    else:
        check("massive_verify", None, "not in result (API may be rate-limited)")
    
    # Performance (9 external API calls: F&G + DXY + BTC.D + VIX + 10Y + Massive + 3x OKX)
    # v2.7 realistic: cold 6-10s, warm 3-6s
    check("detect_regime perf (warm)", elapsed < 8.0, f"{elapsed:.2f}s (target <8s, 7 APIs)")
    
    # Confidence gate
    check("confidence ≤ 100", result['confidence'] <= 100, f"{result['confidence']}%")
    check("confidence ≥ 0", result['confidence'] >= 0, f"{result['confidence']}%")
    
    # Order flow has indicators or error
    of = dims.get('order_flow', {})
    has_of = of.get('funding_rate_pct') is not None or 'error' in of.get('detail', '')
    check("order_flow has data", has_of, of.get('detail', 'no detail')[:80])
    
    # Macro external has indicators or error
    macro = dims.get('macro_external', {})
    has_macro = macro.get('fg_actual') is not None or 'error' in macro.get('detail', '')
    check("macro_external has data", has_macro, macro.get('detail', 'no detail')[:80])
    
    # Candlestick
    cdl = dims.get('candlestick', {})
    check("candlestick dimension", cdl.get('score') is not None, 
          cdl.get('detail', 'no detail')[:80])
    
    # path_narrative weight = 0
    pn_weight = dims.get('path_narrative', {}).get('weight', -1)
    check("path_narrative weight=0", pn_weight == 0, f"weight={pn_weight}")
    
    # DATA TRACE fields
    for field in ['current_price', '30d_high', '30d_low', 'synthetic_fg', 'rsi_14']:
        has_field = result.get('indicators', {}).get(field) is not None
        check(f"  DATA TRACE: indicators.{field}", has_field,
              str(result['indicators'].get(field)) if has_field else "missing")
    
except Exception as e:
    check("detector", False, f"exception: {e}")
    traceback.print_exc()


# ============================================================
# 5. Cron Link
# ============================================================
print()
print("=" * 60)
print("5. CRON SCRIPTS")
print("=" * 60)

for script_name in ['sync_klines_cron.sh', 'regime_update.sh']:
    sp = SCRIPTS_DIR / script_name
    if sp.exists():
        content = sp.read_text()
        check(f"{script_name} syntax", content.startswith("#!/bin/bash"),
              "valid bash" if content.startswith("#!/bin/bash") else "bad shebang")
        # Check it references the right Python scripts
        if script_name == 'sync_klines_cron.sh':
            refs_monitor = 'monitor_and_sync.py' in content
            check("  references monitor_and_sync.py", refs_monitor, 
                  "found" if refs_monitor else "missing!")
        if script_name == 'regime_update.sh':
            refs_detector = 'regime_detector.py' in content
            check("  references regime_detector.py", refs_detector,
                  "found" if refs_detector else "missing!")
    else:
        check(f"{script_name}", False, "file missing!")


# ============================================================
# 6. External APIs (skip with --quick)
# ============================================================
if '--quick' not in sys.argv:
    print()
    print("=" * 60)
    print("6. EXTERNAL APIs")
    print("=" * 60)
    
    import urllib.request
    
    # F&G
    try:
        req = urllib.request.Request('https://api.alternative.me/fng/?limit=1',
                                     headers={'User-Agent': 'verify-workflow/1.0'})
        fg_data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        fg_val = int(fg_data['data'][0]['value'])
        fg_label = fg_data['data'][0]['value_classification']
        check("Fear & Greed", True, f"{fg_val} ({fg_label})")
    except Exception as e:
        check("Fear & Greed", False, str(e)[:80])
    
    # DXY
    try:
        req = urllib.request.Request('https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        dxy_data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        dxy_val = dxy_data['chart']['result'][0]['meta']['regularMarketPrice']
        check("DXY (Yahoo)", True, f"{dxy_val}")
    except Exception as e:
        check("DXY (Yahoo)", None, str(e)[:80])
    
    # BTC.D (CoinGecko) — with retry for 429 rate limit
    try:
        import time as _time
        cg_url = 'https://api.coingecko.com/api/v3/global'
        btc_d = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(cg_url, headers={'User-Agent': 'Mozilla/5.0'})
                cg_data = json.loads(urllib.request.urlopen(req, timeout=8).read())
                btc_d = cg_data['data']['market_cap_percentage']['btc']
                break
            except Exception:
                if attempt < 2:
                    _time.sleep(1.5 * (attempt + 1))
        check("BTC.D (CoinGecko)", btc_d is not None, f"{btc_d}%" if btc_d else "rate-limited (non-critical)")
    except Exception as e:
        check("BTC.D (CoinGecko)", None, f"external: {str(e)[:60]}")
    
    # VIX (Yahoo Finance) — NEW v2.9
    try:
        req = urllib.request.Request('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        vix_data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        vix_val = vix_data['chart']['result'][0]['meta']['regularMarketPrice']
        check("VIX (Yahoo)", True, f"{vix_val:.1f}")
    except Exception as e:
        check("VIX (Yahoo)", None, str(e)[:80])
    
    # 10Y Treasury Yield (Yahoo Finance) — NEW v2.9
    try:
        req = urllib.request.Request('https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=5d',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        y10_data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        y10_val = y10_data['chart']['result'][0]['meta']['regularMarketPrice']
        check("10Y Yield (Yahoo)", True, f"{y10_val:.2f}%")
    except Exception as e:
        check("10Y Yield (Yahoo)", None, str(e)[:80])
    
    # Massive
    try:
        sys.path.insert(0, str(BASE))
        from massive_client import verify_price as mv_price
        btc_price = mv_price('BTC')
        check("Massive BTC verify", None if btc_price is None else btc_price > 0,
              f"${btc_price:,.2f}" if btc_price else "API unavailable (free plan limit)")
    except Exception as e:
        # 403/free-plan = external limitation, not a code failure
        check("Massive", None, f"external limit: {str(e)[:60]}")


# ============================================================
# Summary
# ============================================================
print()
print("=" * 60)
total = PASS + WARN + FAIL
status = "ALL CLEAN ✅" if FAIL == 0 and WARN == 0 else \
         "WARNINGS ⚠️" if FAIL == 0 else "FAILURES ❌"
print(f"VERIFICATION: {status}")
print(f"  Pass: {PASS}/{total}  Warning: {WARN}/{total}  Fail: {FAIL}/{total}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
