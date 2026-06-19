#!/usr/bin/env python3
"""
OKX Day-Trading Altcoin Scanner
=================================
Scans OKX perpetual swaps for altcoins suitable for intraday trading.
Filters: volume > $100M, spread < 0.1%, funding rate within ±0.03%,
listed > 30 days, Top 50-ish by volume.

Usage:
    python3 scan_daytrade_coins.py                # Terminal output
    python3 scan_daytrade_coins.py --json         # JSON output
    python3 scan_daytrade_coins.py --notify       # Output + save to /tmp/daytrade_coins.json

Cron (daily 08:00 BJ):
    0 0 * * * cd /root/.hermes/trade_review && python3 scripts/scan_daytrade_coins.py --json > /tmp/daytrade_coins.json
"""
import subprocess, json, sys, os
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
NOW_BJ = datetime.now(BJT)
NOW_NAIVE = NOW_BJ.replace(tzinfo=None)

# ============ CONFIG ============
MIN_VOLUME_USD = 100_000_000     # $100M 24h
MAX_SPREAD_PCT = 0.1             # 0.1% max spread
MAX_FUNDING_ABS = 0.0003         # ±0.03% funding rate
MIN_DAYS_LISTED = 30             # 30 days minimum history
TOP_N = 50                       # Show top N

# Excluded: BTC, ETH and their variants
EXCLUDE = {'BTC', 'ETH', 'BTC-USDT-SWAP', 'ETH-USDT-SWAP',
           'BTC-USDC-SWAP', 'ETH-USDC-SWAP'}


def okx_api(path):
    """Call OKX public API with retry."""
    url = f'https://www.okx.com{path}'
    for attempt in range(3):
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '15', url],
                             capture_output=True, text=True, timeout=20)
            if r.stdout:
                d = json.loads(r.stdout)
                if d.get('code') == '0':
                    return d['data']
        except Exception:
            pass
    return []


def fetch_all_tickers():
    """Fetch all perpetual swap tickers."""
    inst_type = 'SWAP'
    data = okx_api(f'/api/v5/market/tickers?instType={inst_type}')
    tickers = []
    for t in data:
        inst_id = t.get('instId', '')
        if not inst_id.endswith('-USDT-SWAP'):
            continue
        base = inst_id.replace('-USDT-SWAP', '')
        if base in EXCLUDE:
            continue
        try:
            tickers.append({
                'instId': inst_id,
                'base': base,
                'last': float(t.get('last', 0)),
                'vol24h': float(t.get('vol24h', 0)),
                'volCcy24h': float(t.get('volCcy24h', 0)),
                'askPx': float(t.get('askPx', 0)),
                'bidPx': float(t.get('bidPx', 0)),
            })
        except (ValueError, TypeError):
            pass
    return tickers


def fetch_funding_rates(inst_ids):
    """Fetch current funding rates for specific instruments in parallel batches.
    OKX requires instId parameter per request — batch with ThreadPoolExecutor.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def fetch_one(inst_id):
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '5',
                f'https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}'],
                capture_output=True, text=True, timeout=8)
            d = json.loads(r.stdout) if r.stdout else {}
            if d.get('code') == '0' and d.get('data'):
                return inst_id, float(d['data'][0].get('fundingRate', 0))
        except Exception:
            pass
        return inst_id, 0

    rates = {}
    # 10 concurrent workers — OKX rate limit is ~20 req/s
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, iid): iid for iid in inst_ids}
        for future in as_completed(futures):
            iid, rate = future.result()
            rates[iid] = rate
    return rates


def fetch_listing_dates(inst_ids):
    """Fetch listing dates from OKX instruments API.
    Uses listTime field which gives exact listing timestamp.
    """
    dates = {}
    # Batch: 10 instruments per request to avoid URL length issues
    batch_size = 10
    for i in range(0, len(inst_ids), batch_size):
        batch = inst_ids[i:i+batch_size]
        for inst_id in batch:
            try:
                r = subprocess.run(['curl', '-s', '--max-time', '8',
                    f'https://www.okx.com/api/v5/public/instruments'
                    f'?instType=SWAP&instId={inst_id}'],
                    capture_output=True, text=True, timeout=12)
                d = json.loads(r.stdout) if r.stdout else {}
                if d.get('code') == '0' and d.get('data'):
                    ts = int(d['data'][0].get('listTime', 0))
                    if ts > 0:
                        dates[inst_id] = datetime.fromtimestamp(ts/1000)
            except Exception:
                pass
    return dates


def score_coin(ticker, funding_rate, listing_date):
    """Score a coin for day-trading suitability (0-100)."""
    score = 0

    # 1. Volume (40 points) — the most important factor
    vol = ticker['volCcy24h']
    if vol > 1_000_000_000:     # > $1B
        score += 40
    elif vol > 500_000_000:     # > $500M
        score += 30
    elif vol > 250_000_000:     # > $250M
        score += 20
    elif vol > 100_000_000:     # > $100M (minimum)
        score += 10

    # 2. Spread (25 points) — tight spread = good liquidity
    spread_pct = (ticker['askPx'] - ticker['bidPx']) / ticker['last'] * 100 if ticker['last'] > 0 else 999
    if spread_pct < 0.02:
        score += 25
    elif spread_pct < 0.05:
        score += 18
    elif spread_pct < 0.08:
        score += 10
    elif spread_pct < 0.1:
        score += 5

    # 3. Funding rate (20 points) — near zero is best
    fr_abs = abs(funding_rate)
    if fr_abs < 0.005:
        score += 20
    elif fr_abs < 0.01:
        score += 15
    elif fr_abs < 0.02:
        score += 10
    elif fr_abs < 0.03:
        score += 5

    # 4. Listing age (10 points) — older is more predictable
    if listing_date:
        days_listed = (NOW_NAIVE - listing_date.replace(tzinfo=None)).days
        if days_listed > 365:
            score += 10
        elif days_listed > 180:
            score += 7
        elif days_listed > 90:
            score += 5
        elif days_listed > 30:
            score += 2

    # 5. Price stability bonus (5 points) — mid-range prices less manipulated
    price = ticker['last']
    if 1 < price < 500:
        score += 5
    elif 0.1 < price <= 1:
        score += 2

    return score


def run():
    print(f'🔍 OKX 日内山寨扫描 | BJ {NOW_BJ.strftime("%m-%d %H:%M")}', file=sys.stderr)

    # Step 1: Fetch tickers
    tickers = fetch_all_tickers()
    if not tickers:
        print('❌ Failed to fetch OKX tickers', file=sys.stderr)
        sys.exit(1)
    print(f'  拉取 {len(tickers)} 个 USDT 永续合约', file=sys.stderr)

    # Step 2: Pre-filter by volume and spread (no funding rate needed yet)
    pre_candidates = []
    for t in tickers:
        # Volume check
        if t['volCcy24h'] < MIN_VOLUME_USD:
            continue
        # Spread check
        if t['last'] > 0:
            spread_pct = (t['askPx'] - t['bidPx']) / t['last'] * 100
            if spread_pct > MAX_SPREAD_PCT:
                continue
        else:
            continue
        pre_candidates.append(t)

    print(f'  量+价差通过: {len(pre_candidates)} 个', file=sys.stderr)

    # Step 3: Fetch funding rates for candidates only (batch individual requests)
    inst_ids = [t['instId'] for t in pre_candidates]
    rates = fetch_funding_rates(inst_ids)
    print(f'  拉取 {len(rates)} 个资金费率', file=sys.stderr)

    # Step 4: Apply funding rate filter
    candidates = []
    for t in pre_candidates:
        fr = rates.get(t['instId'], 0)
        if abs(fr) > MAX_FUNDING_ABS:
            continue
        candidates.append((t, fr))

    print(f'  通过硬筛选: {len(candidates)} 个', file=sys.stderr)

    if not candidates:
        print('⚠️ 无合格山寨币（所有币种未通过量/价差/费率门槛）', file=sys.stderr)
        return []

    # Step 4: Fetch listing dates for candidates
    inst_ids = [c[0]['instId'] for c in candidates]
    listing_dates = fetch_listing_dates(inst_ids)

    # Step 5: Score and rank
    results = []
    for t, fr in candidates:
        ld = listing_dates.get(t['instId'])
        # Age filter: reject only if KNOWN to be < 30 days
        if ld:
            days = (NOW_NAIVE - ld.replace(tzinfo=None)).days
            if days < MIN_DAYS_LISTED:
                continue

        score = score_coin(t, fr, ld)
        spread_pct = (t['askPx'] - t['bidPx']) / t['last'] * 100 if t['last'] > 0 else 0

        results.append({
            'base': t['base'],
            'instId': t['instId'],
            'price': t['last'],
            'vol24h_usd': t['volCcy24h'],
            'spread_pct': round(spread_pct, 4),
            'funding_rate': round(fr * 100, 4),  # as percentage
            'days_listed': (NOW_NAIVE - ld.replace(tzinfo=None)).days if ld else None,
            'score': score,
            'flags': []
        })

    # Sort by score descending
    results.sort(key=lambda x: -x['score'])

    # Mark flags
    for r in results:
        if r['funding_rate'] > 0.02:
            r['flags'].append('⚠️多头拥挤')
        elif r['funding_rate'] < -0.02:
            r['flags'].append('⚠️空头拥挤')
        if r['spread_pct'] > 0.05:
            r['flags'].append('⚠️价差偏大')
        if r['vol24h_usd'] < 200_000_000:
            r['flags'].append('⚠️流动性偏低')

    # 费率极端预警（所有扫描到的币，不限合格）
    extreme_alerts = []
    for t, fr in [(c[0], c[1]) for c in candidates] + [(t, rates.get(t['instId'], 0)) for t in tickers if t['volCcy24h'] > 50_000_000 and abs(rates.get(t['instId'], 0)) > 0.0005]:
        if abs(fr) > 0.0005:  # > 0.05%
            extreme_alerts.append({
                'base': t['base'],
                'funding_rate': round(fr * 100, 4),
                'direction': '多头拥挤 🔴' if fr > 0 else '空头拥挤 🟢'
            })
    if extreme_alerts:
        extreme_alerts.sort(key=lambda x: -abs(x['funding_rate']))
        # Print extreme alerts to stderr for visibility
        print(f'\\n  🚨 费率极端预警:', file=sys.stderr)
        for a in extreme_alerts[:5]:
            print(f'     {a["base"]}: {a["funding_rate"]:+.3f}% {a["direction"]}', file=sys.stderr)

    return results[:TOP_N]


def format_output(results, as_json=False):
    if as_json:
        output = {
            'timestamp': NOW_BJ.isoformat(),
            'criteria': {
                'min_volume_usd': MIN_VOLUME_USD,
                'max_spread_pct': MAX_SPREAD_PCT,
                'max_funding_abs_pct': MAX_FUNDING_ABS * 100,
                'min_days_listed': MIN_DAYS_LISTED,
            },
            'qualified': len(results),
            'coins': results
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # Human-readable output
    print(f'\n{"="*80}')
    print(f'  日内可交易山寨币 | BJ {NOW_BJ.strftime("%Y-%m-%d %H:%M")} | 合格: {len(results)} 个')
    print(f'{"="*80}')
    print(f'  {"币种":<8s} {"价格":>8s} {"24h量":>10s} {"价差":>6s} {"费率":>7s} {"天数":>5s} {"评分":>4s}  ⚠️')
    print(f'  {"─"*8} {"─"*8} {"─"*10} {"─"*6} {"─"*7} {"─"*5} {"─"*4}  {"─"*20}')

    for r in results:
        vol_str = f'${r["vol24h_usd"]/1e6:.0f}M' if r['vol24h_usd'] < 1e9 else f'${r["vol24h_usd"]/1e9:.1f}B'
        days_str = str(r['days_listed']) if r['days_listed'] else '?'
        flags_str = ' '.join(r['flags']) if r['flags'] else ''
        print(f'  {r["base"]:<8s} ${r["price"]:<7.4f} {vol_str:>9s}  {r["spread_pct"]:>5.3f}% {r["funding_rate"]:>+6.3f}% {days_str:>4s}d {r["score"]:>3d}  {flags_str}')

    print(f'\n  📋 筛选标准: 量>$100M | 价差<0.1% | 费率±0.03% | 上线>30天')
    print(f'  ⚠️ 仅订单流可做山寨日内，不看K线形态/趋势')

    # Top 3 recommendations
    if results:
        top = results[:3]
        print(f'\n  🏆 今日推荐:')
        for i, r in enumerate(top, 1):
            print(f'     {i}. {r["base"]} — 评分{r["score"]}/100 | ${r["price"]:.4f} | 量${r["vol24h_usd"]/1e6:.0f}M | 费率{r["funding_rate"]:+.3f}%')


if __name__ == '__main__':
    as_json = '--json' in sys.argv
    results = run()

    if results:
        format_output(results, as_json)

        # Save for reference
        out_path = '/tmp/daytrade_coins.json'
        with open(out_path, 'w') as f:
            json.dump({
                'timestamp': NOW_BJ.isoformat(),
                'qualified': len(results),
                'coins': results
            }, f, ensure_ascii=False, indent=2)

        if '--notify' in sys.argv:
            print(f'\n  ✅ 结果已保存: {out_path}', file=sys.stderr)
