#!/usr/bin/env python3
"""
山寨币订单流分析模板 v2.2
=========================
数据源：OKX (费率/价差/量/OI变化) + Binance (大户多空比/Taker量比) + DexScreener (DEX价格/LP/买卖比)
庄家评分 9 维度，总分 100。

用法:
    python3 analysis_altcoin.py DOGE
    python3 analysis_altcoin.py --top 3
    python3 analysis_altcoin.py --batch
"""
import subprocess, json, sys, os, time
from datetime import datetime, timezone, timedelta
from _shared import BJT

def _now_bj():
    """Return current BJ time (call-time, not import-time)."""
    return datetime.now(BJT)
SCAN_OUTPUT = '/tmp/daytrade_coins.json'
BINANCE_KEY = os.environ.get('BINANCE_API_KEY', '')


def api_get(url, headers=None, timeout=8):
    """Generic HTTP GET with retry."""
    cmd = ['curl', '-s', '--max-time', str(timeout)]
    if headers:
        for k, v in headers.items():
            cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    for attempt in range(2):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
            if r.stdout:
                return json.loads(r.stdout)
        except Exception:
            if attempt == 0: time.sleep(1)
    return None


def binance_get(path, timeout=8):
    return api_get(f'https://fapi.binance.com{path}',
                   {'X-MBX-APIKEY': BINANCE_KEY}, timeout)


def okx_get(path, timeout=8):
    data = api_get(f'https://www.okx.com{path}', timeout=timeout)
    if data and data.get('code') == '0':
        return data['data']
    return None


def dexscreener_search(query, timeout=8):
    """Search DexScreener for DEX pair data."""
    data = api_get(f'https://api.dexscreener.com/latest/dex/search?q={query}', timeout=timeout)
    if data and data.get('pairs'):
        return data['pairs']
    return []


def analyze_coin(coin):
    inst_id = f'{coin}-USDT-SWAP'
    symbol = f'{coin}USDT'
    result = {
        'coin': coin, 'inst_id': inst_id,
        'timestamp_bj': _now_bj().strftime('%m-%d %H:%M'),
        'score': 0, 'verdict': '', 'flags': [], 'dex_flags': [],
        'data': {}, 'scoring': {}
    }

    # ═══════════ OKX ═══════════

    tickers = okx_get(f'/api/v5/market/ticker?instId={inst_id}')
    if not tickers:
        result['verdict'] = '❌ API失败'; return result

    t = tickers[0]
    try:
        price = float(t['last']); ask = float(t['askPx']); bid = float(t['bidPx'])
        vol24h = float(t['volCcy24h']); high24h = float(t['high24h']); low24h = float(t['low24h'])
        spread_pct = (ask - bid) / price * 100 if price > 0 else 0
        pos_24h = (price - low24h) / (high24h - low24h) * 100 if high24h > low24h else 50
    except (ValueError, KeyError, TypeError):
        result['verdict'] = '❌ 数据异常'; return result

    result['data'].update({
        'cex_price': price, 'vol24h': vol24h, 'high24h': high24h, 'low24h': low24h,
        'spread_pct': round(spread_pct, 4), 'pos_24h': round(pos_24h, 1),
    })

    # Funding Rate
    fr_data = okx_get(f'/api/v5/public/funding-rate?instId={inst_id}')
    funding_rate = 0
    if fr_data:
        try: funding_rate = float(fr_data[0].get('fundingRate', 0))
        except: pass

    fr_history = okx_get(f'/api/v5/public/funding-rate-history?instId={inst_id}&limit=8')
    fr_list = []
    if fr_history:
        for r in fr_history:
            try: fr_list.append(float(r.get('fundingRate', 0)))
            except: pass

    # fr_trend: compare recent 4 rates to older rates.
    # For positive rates: ra > oa*1.5 means funding is increasing (more longs paying).
    # For negative rates: ra < oa (more negative) → NOT detected by ra > oa*1.5
    #   since a more-negative ra is numerically smaller.
    # Example: ra=-0.02, oa=-0.01 → ra is NOT > oa*1.5 (-0.02 > -0.015 = false)
    #   but ra is more extreme. Use abs() for direction-agnostic comparison.
    fr_trend = 'neutral'
    if len(fr_list) >= 4:
        ra = sum(fr_list[:4]) / 4
        oa = sum(fr_list[4:]) / max(1, len(fr_list[4:]))
        if ra > oa * 1.5 and ra > 0.0001: fr_trend = '多头拥挤加剧'
        elif ra < oa * 1.5 and ra < -0.0001: fr_trend = '空头拥挤加剧'
        elif ra > 0 and ra < oa: fr_trend = '多头退潮'
        elif ra < 0 and ra > oa: fr_trend = '空头退潮'

    result['data'].update({
        'funding_rate': round(funding_rate * 100, 4),
        'fr_trend': fr_trend,
        'fr_history': [round(f * 100, 4) for f in fr_list[:8]],
    })

    # OI Change
    oi_data = okx_get(f'/api/v5/rubik/stat/contracts/open-interest-volume?ccy={coin}&limit=288')
    oi_change_pct = 0; oi_trend = 'stable'
    if oi_data and len(oi_data) >= 2:
        try:
            latest_oi = float(oi_data[0][1])
            old_oi = float(oi_data[-1][1])
            oi_change_pct = (latest_oi - old_oi) / old_oi * 100 if old_oi > 0 else 0
            recent = [float(oi_data[i][1]) for i in range(min(12, len(oi_data)))]
            if len(recent) >= 3:
                if recent[0] > recent[-1] * 1.02: oi_trend = 'rising'
                elif recent[0] < recent[-1] * 0.98: oi_trend = 'falling'
        except: pass

    result['data'].update({'oi_change_pct': round(oi_change_pct, 2), 'oi_trend': oi_trend})

    # ═══════════ BINANCE ═══════════

    global_ls = binance_get(f'/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=2')
    g_ls_ratio = 0; g_ls_long = 0
    if global_ls and len(global_ls) >= 1:
        try:
            g_ls_ratio = float(global_ls[0]['longShortRatio'])
            g_ls_long = float(global_ls[0]['longAccount'])
        except: pass

    top_ls = binance_get(f'/futures/data/topLongShortAccountRatio?symbol={symbol}&period=5m&limit=2')
    t_ls_ratio = 0; t_ls_long = 0; divergence = 'none'
    if top_ls and len(top_ls) >= 1:
        try:
            t_ls_ratio = float(top_ls[0]['longShortRatio'])
            t_ls_long = float(top_ls[0]['longAccount'])
            if g_ls_ratio > 0:
                if t_ls_ratio > g_ls_ratio * 1.3: divergence = '🟢大户偏多'
                elif t_ls_ratio < g_ls_ratio * 0.7: divergence = '🔴大户偏空'
                elif t_ls_ratio > g_ls_ratio: divergence = '大户略多'
                elif t_ls_ratio < g_ls_ratio: divergence = '大户略空'
                else: divergence = '一致'
        except: pass

    taker = binance_get(f'/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=2')
    taker_ratio = 0; taker_buy = 0; taker_sell = 0
    if taker and len(taker) >= 1:
        try:
            taker_ratio = float(taker[0]['buySellRatio'])
            taker_buy = float(taker[0]['buyVol'])
            taker_sell = float(taker[0]['sellVol'])
        except: pass

    result['data'].update({
        'g_ls_ratio': round(g_ls_ratio, 2), 'g_ls_long': round(g_ls_long * 100, 1),
        't_ls_ratio': round(t_ls_ratio, 2), 't_ls_long': round(t_ls_long * 100, 1),
        'ls_divergence': divergence, 'taker_ratio': round(taker_ratio, 2),
    })

    # ═══════════ DEXSCREENER ═══════════

    dex_price = None; dex_liq = 0; dex_vol = 0; dex_buys = 0; dex_sells = 0
    dex_spread = None; lp_flag = None; dex_buy_ratio = None; dex_mismatch = False

    pairs = dexscreener_search(coin)
    if pairs:
        # Priority scoring: price match to CEX first, then liquidity
        def pair_score(p):
            try:
                dp = float(p.get('priceUsd', 0))
            except (ValueError, TypeError):
                dp = 0
            liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
            if dp <= 0 or price <= 0:
                return liq
            ratio = max(dp, price) / min(dp, price)
            if ratio < 1.03:
                return liq + 1_000_000_000_000  # <3% spread = same token
            elif ratio < 1.10:
                return liq + 10_000_000
            elif ratio < 1.50:
                return liq
            else:
                return max(0, liq - 10_000_000_000)  # >50% = wrong token

        pairs.sort(key=pair_score, reverse=True)

        # Try best match, verify it's the same token
        best = pairs[0]
        try:
            dp = float(best.get('priceUsd', 0))
            if dp > 0 and price > 0:
                ratio = max(dp, price) / min(dp, price)
                if ratio > 1.05:
                    dex_mismatch = True  # >5% spread = wrapped/different token
                else:
                    dex_price = dp
            else:
                dex_price = dp

            if not dex_mismatch:
                dex_liq = float(best.get('liquidity', {}).get('usd', 0) or 0)
                dex_vol = float(best.get('volume', {}).get('h24', 0) or 0)
                txns = best.get('txns', {}).get('h24', {})
                dex_buys = int(txns.get('buys', 0))
                dex_sells = int(txns.get('sells', 0))
                if dex_buys + dex_sells > 0:
                    dex_buy_ratio = dex_buys / (dex_buys + dex_sells)

                # CEX vs DEX spread
                if dex_price > 0:
                    dex_spread = (price - dex_price) / dex_price * 100

                # LP flag
                if 0 < dex_liq < 100_000:
                    lp_flag = '⚠️LP极低(易被操纵)'

        except (ValueError, TypeError):
            pass

    result['data'].update({
        'dex_price': dex_price,
        'dex_liq': dex_liq,
        'dex_vol': dex_vol,
        'dex_buys': dex_buys,
        'dex_sells': dex_sells,
        'dex_spread': round(dex_spread, 2) if dex_spread is not None else None,
        'dex_buy_ratio': round(dex_buy_ratio * 100, 1) if dex_buy_ratio is not None else None,
        'dex_mismatch': dex_mismatch,
    })

    if lp_flag:
        result['dex_flags'].append(lp_flag)

    # ═══════════ SCORING (9 dimensions, 100 total) ═══════════

    sc = {}

    # 1. Funding Rate (25)
    if funding_rate < -0.0002:      pts, reason = 25, '空头拥挤(逼空燃料)'
    elif -0.0002 <= funding_rate <= 0.0001: pts, reason = 22, '平衡'
    elif 0.0001 < funding_rate <= 0.0003:   pts, reason = 12, '温和偏多'
    else: pts, reason = 5, '多头拥挤⚠️'; result['flags'].append('⚠️多头拥挤')
    sc['费率'] = (pts, reason)

    # 2. OI + Position (18)
    if pos_24h < 30 and oi_change_pct > -5: pts, reason = 18, f'低位吸筹(位置{pos_24h:.0f}%)'
    elif pos_24h < 30: pts, reason = 7, '低位+OI降(资金撤离)'
    elif pos_24h < 50: pts, reason = 13, f'中低位(位置{pos_24h:.0f}%)'
    elif pos_24h > 80: pts, reason = 4, '高位⚠️'; result['flags'].append('⚠️高位风险')
    else: pts, reason = 9, '中性'
    sc['OI位置'] = (pts, reason)

    # 3. Top Trader Divergence (10)
    if t_ls_ratio > 0 and g_ls_ratio > 0:
        if t_ls_ratio > g_ls_ratio * 1.3: pts, reason = 10, f'大户强偏多({t_ls_ratio:.1f} vs {g_ls_ratio:.1f})'
        elif t_ls_ratio < g_ls_ratio * 0.7: pts, reason = 10, f'大户强偏空({t_ls_ratio:.1f} vs {g_ls_ratio:.1f})'
        elif t_ls_ratio > g_ls_ratio: pts, reason = 6, f'大户略偏多'
        elif t_ls_ratio < g_ls_ratio: pts, reason = 4, f'大户略偏空'
        else: pts, reason = 5, '一致'
    else: pts, reason = 5, '无数据'
    sc['大户背离'] = (pts, reason)

    # 4. Taker Buy/Sell (10)
    if taker_ratio > 1.5: pts, reason = 10, f'主动买主导({taker_ratio:.1f}x)'
    elif taker_ratio > 1.2: pts, reason = 8, f'偏买({taker_ratio:.1f}x)'
    elif taker_ratio > 0.8: pts, reason = 5, f'均衡({taker_ratio:.1f}x)'
    elif taker_ratio > 0.5: pts, reason = 3, f'主动卖偏多'
    else: pts, reason = 1, f'主动卖主导'
    sc['Taker量比'] = (pts, reason)

    # 5. OI Change (5)
    if oi_trend == 'rising' and oi_change_pct > 2:
        pts, reason = 5 if pos_24h < 50 else 2, f'OI升+{"低位" if pos_24h<50 else "高位"}({"资金进场" if pos_24h<50 else "追多风险"})'
    elif oi_trend == 'falling' and oi_change_pct < -2:
        pts, reason = 1 if pos_24h < 50 else 4, f'OI降+{"低位" if pos_24h<50 else "高位"}({"资金撤退⚠️" if pos_24h<50 else "获利了结"})'
    else: pts, reason = 3, f'稳定({oi_change_pct:+.1f}%)'
    sc['OI变化'] = (pts, reason)

    # 6. Global L/S (12)
    if 0 < g_ls_ratio <= 1.5: pts, reason = 12, f'散户不拥挤({g_ls_ratio:.1f})'
    elif 1.5 < g_ls_ratio <= 2.5: pts, reason = 8, f'温和拥挤({g_ls_ratio:.1f})'
    elif 2.5 < g_ls_ratio <= 4: pts, reason = 4, f'拥挤({g_ls_ratio:.1f})'
    elif g_ls_ratio > 4: pts, reason = 2, f'严重拥挤⚠️'; result['flags'].append(f'⚠️多空比{g_ls_ratio:.1f}')
    else: pts, reason = 6, '无数据'
    sc['多空比'] = (pts, reason)

    # 7. Spread (8)
    if spread_pct < 0.03: pts, reason = 8, f'极紧({spread_pct:.3f}%)'
    elif spread_pct < 0.06: pts, reason = 6, f'良好'
    elif spread_pct < 0.1: pts, reason = 3, f'可接受'
    else: pts, reason = 1, f'偏大⚠️'; result['flags'].append('⚠️价差偏大')
    sc['价差'] = (pts, reason)

    # 8. Volume (5)
    if vol24h > 1_000_000_000: pts, reason = 5, f'充沛(${vol24h/1e9:.1f}B)'
    elif vol24h > 500_000_000: pts, reason = 4, f'良好'
    elif vol24h > 200_000_000: pts, reason = 3, f'一般'
    elif vol24h > 100_000_000: pts, reason = 2, f'偏低'
    else: pts, reason = 1, f'低⚠️'; result['flags'].append('⚠️流动性偏低')
    sc['24h量'] = (pts, reason)

    # 9. DEX Cross-Market (7)
    if dex_mismatch:
        pts, reason = 4, 'DEX同名不同币(无法比价)'
    elif dex_spread is not None:
        abs_spread = abs(dex_spread)
        if abs_spread < 1:
            pts, reason = 7, f'CEX≈DEX(偏差{dex_spread:+.1f}%)'
        elif abs_spread < 3:
            pts, reason = 5, f'轻微偏离({dex_spread:+.1f}%)'
            if dex_spread > 0: result['dex_flags'].append('⚠️永续溢价>DEX')
            else: result['dex_flags'].append('⚠️永续折价<DEX')
        elif abs_spread < 5:
            pts, reason = 3, f'显著偏离({dex_spread:+.1f}%)'
            if dex_spread > 0: result['dex_flags'].append('🚨永续大幅溢价(逼空风险)')
            else: result['dex_flags'].append('🚨永续大幅折价(逼多风险)')
        else:
            pts, reason = 1, f'严重偏离⚠️({dex_spread:+.1f}%)'
            result['dex_flags'].append('🚨CEX-DEX价差异常')
    elif dex_price is not None:
        pts, reason = 4, '有DEX价(无偏差数据)'
    else:
        pts, reason = 4, '无DEX数据'
    sc['DEX价差'] = (pts, reason)

    # DEX secondary signals (bonus/penalty, only if not mismatched)
    dex_bonus = 0
    if not dex_mismatch:
        if dex_buy_ratio is not None:
            if dex_buy_ratio < 0.35:
                result['dex_flags'].append(f'⚠️DEX恐慌抛售(买{dex_buy_ratio*100:.0f}%)')
                dex_bonus -= 2
            elif dex_buy_ratio > 0.65:
                result['dex_flags'].append(f'⚠️DEX散户FOMO(买{dex_buy_ratio*100:.0f}%)')
                dex_bonus -= 3
            elif 0.40 <= dex_buy_ratio <= 0.60:
                dex_bonus += 2

        if dex_liq > 0 and dex_liq < 100_000:
            dex_bonus -= 3

    total = sum(p for p, _ in sc.values()) + dex_bonus
    total = max(0, min(100, total))
    result['score'] = total
    result['scoring'] = sc
    result['dex_bonus'] = dex_bonus

    # ═══════════ VERDICT ═══════════

    if total >= 75:
        if pos_24h < 35: result['verdict'] = '🟢 庄家可能在收筹码，可关注'
        else: result['verdict'] = '🟡 信号强但位置偏高，警惕诱多'
    elif total >= 60:
        result['verdict'] = '🟡 信号可参考，轻仓试错，严格止损'
    elif total >= 36:
        result['verdict'] = '⚪ 信号不明确，建议观望'
    else:
        if pos_24h < 30: result['verdict'] = '🔴 低位无量，庄家可能已离场——远离'
        else: result['verdict'] = '🔴 庄家行为不利，远离'

    return result


def format_output(r):
    d = r['data']; sc = r.get('scoring', {})

    # Build DEX price display
    if d.get('dex_price') and not d.get('dex_mismatch'):
        dex_price_str = '${:.8f}'.format(d['dex_price'])
        dex_spread_str = '{:+.1f}%'.format(d['dex_spread']) if d.get('dex_spread') is not None else '?'
    elif d.get('dex_mismatch'):
        dex_price_str = '同名不同币'
        dex_spread_str = 'N/A'
    else:
        dex_price_str = '无数据'
        dex_spread_str = 'N/A'

    print(f"""
┌──────────────────────────────────────────────────────────────┐
│  {r['coin']}  庄家行为分析 v2.2 │ BJ {r['timestamp_bj']}
├──────────────────────────────────────────────────────────────┤
│  💰 CEX: ${d['cex_price']:.6f}  │  DEX: {dex_price_str}  │  价差: {dex_spread_str}
│  📍 区间: ${d['low24h']:.6f} ~ ${d['high24h']:.6f}  (位置 {d['pos_24h']:.0f}%)
│  📏 CEX价差: {d['spread_pct']:.3f}%  │  DEX LP: ${d.get('dex_liq',0)/1e6:.1f}M
├──────────────────────────────────────────────────────────────┤
│  📊 OKX 订单流
│  费率: {d['funding_rate']:+.4f}%  → {d['fr_trend']}
│  OI变化: {d['oi_change_pct']:+.2f}% ({d['oi_trend']})
├──────────────────────────────────────────────────────────────┤
│  📊 Binance 多空结构
│  全市场: {d['g_ls_long']:.0f}%做多  ratio={d['g_ls_ratio']:.2f}
│  大户:   {d['t_ls_long']:.0f}%做多  ratio={d['t_ls_ratio']:.2f}  → {d['ls_divergence']}
│  Taker:  buy/sell={d['taker_ratio']:.2f}""")

    # DEX section
    print(f"├──────────────────────────────────────────────────────────────┤")
    print(f"│  📊 DEX 链上 (DexScreener)")
    if d.get('dex_price') and not d.get('dex_mismatch'):
        print(f"│  DEX价: ${d['dex_price']:.8f}  │  LP: ${d.get('dex_liq',0)/1e6:.1f}M  │  24h量: ${d.get('dex_vol',0)/1e3:.0f}K")
    if d.get('dex_buy_ratio') is not None and not d.get('dex_mismatch'):
        print(f"│  买卖比: {d['dex_buys']}买/{d['dex_sells']}卖 ({d['dex_buy_ratio']:.0f}%买)")
    dex_flags = r.get('dex_flags', [])
    if d.get('dex_mismatch'):
        print(f"│  ⚠️ DEX同名不同币，无法比价")
    elif dex_flags:
        print(f"│  {'  |  '.join(dex_flags)}")
    elif not d.get('dex_price'):
        print(f"│  ⚠️ 无DEX数据（该币种DEX交易不活跃）")

    print(f"├──────────────────────────────────────────────────────────────┤")
    print(f"│  🎯 庄家评分: {r['score']}/100")

    for dim, (pts, reason) in sc.items():
        bar = '█' * max(1, pts // 2)
        print(f"│     {dim:<10s} +{pts:<3d} {bar}  {reason}")

    db = r.get('dex_bonus', 0)
    if db != 0:
        sign = '+' if db > 0 else ''
        print(f"│     {'DEX增减':<10s} {sign}{db:<3d}")

    flags = r.get('flags', [])
    if flags:
        print(f"│  {' '.join(flags)}")
    print(f"│  {r['verdict']}")
    print(f"└──────────────────────────────────────────────────────────────┘")


def batch_analyze(top_n=5):
    if not os.path.exists(SCAN_OUTPUT):
        print('❌ 无扫描结果，请先运行 scan_daytrade_coins.py'); return []

    with open(SCAN_OUTPUT) as f:
        scan = json.load(f)
    coins = scan.get('coins', [])[:top_n]
    if not coins:
        print('⚠️ 扫描结果为空'); return []

    print(f'🔍 分析 Top {len(coins)} 山寨 (扫描: {scan.get("timestamp", "?")})')
    results = []
    for i, c in enumerate(coins):
        coin = c['base']
        print(f'\n[{i+1}/{len(coins)}] {coin}...', file=sys.stderr)
        r = analyze_coin(coin)
        results.append(r)
        format_output(r)

    results.sort(key=lambda x: -x['score'])
    print(f'\n{"="*50}')
    print(f'  🏆 庄家评分排名')
    for i, r in enumerate(results):
        print(f'  {i+1}. {r["coin"]:<8s} {r["score"]:>3d}/100  {r["verdict"]}')
    print(f'{"="*50}')
    return results


if __name__ == '__main__':
    if '--batch' in sys.argv:
        batch_analyze()
    elif '--top' in sys.argv:
        try: n = int(sys.argv[sys.argv.index('--top')+1])
        except: n = 5
        batch_analyze(top_n=n)
    elif len(sys.argv) > 1:
        coin = sys.argv[1].upper().replace('-USDT-SWAP', '').replace('USDT', '')
        r = analyze_coin(coin)
        format_output(r)
    else:
        print("用法: python3 analysis_altcoin.py {COIN|--batch|--top N}")
