#!/usr/bin/env python3
"""
Massive (Polygon.io) Crypto Data Client

Provides cross-verification against OKX K-line DB.
Free tier: 5 calls/min, 15-min delayed data.
Ticker format: X:BTCUSD, X:ETHUSD, X:SOLUSD, X:DOGEUSD
"""

import json
import os
import sys
import urllib.request
import time
from datetime import datetime, timezone, timedelta
from _shared import BJT as BJ

API_KEY = os.environ.get('POLYGON_API_KEY', '')
if not API_KEY:
    import warnings
    warnings.warn('POLYGON_API_KEY not set — Polygon.io cross-verification disabled')
BASE_URL = "https://api.polygon.io"

# Coin -> Polygon ticker mapping
COIN_TICKERS = {
    'BTC': 'X:BTCUSD',
    'ETH': 'X:ETHUSD',
    'SOL': 'X:SOLUSD',
    'DOGE': 'X:DOGEUSD',
}

_CALL_WINDOW = []


def _rate_limit():
    """Free tier: max 5 calls per minute. Sleeps if needed."""
    global _CALL_WINDOW
    now = time.time()
    # Remove calls older than 60 seconds
    _CALL_WINDOW = [t for t in _CALL_WINDOW if now - t < 60]
    if len(_CALL_WINDOW) >= 5:
        wait = 60 - (now - _CALL_WINDOW[0]) + 1
        if wait > 0:
            time.sleep(wait)
        _CALL_WINDOW = [t for t in _CALL_WINDOW if now + wait - t < 60]
    _CALL_WINDOW.append(now)


def _get(url, timeout=10):
    """GET request with rate limit + error handling."""
    _rate_limit()
    req = urllib.request.Request(url, headers={'User-Agent': 'massive-client/1.0'})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        if data.get('status') == 'ERROR':
            return {'error': data.get('error', 'Unknown API error')}
        return data
    except Exception as e:
        return {'error': str(e)}


# ============================================================
# Core Data Functions
# ============================================================

def get_prev_close(coin='BTC'):
    """Get previous trading day OHLCV for a coin.
    Returns: {o, h, l, c, v, vw, ts_bj} or None
    """
    ticker = COIN_TICKERS.get(coin.upper())
    if not ticker:
        return None

    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={API_KEY}"
    data = _get(url)

    if data.get('error') or not data.get('results'):
        return None

    r = data['results'][0]
    ts = datetime.fromtimestamp(r['t'] / 1000, tz=BJ)
    return {
        'coin': coin.upper(),
        'o': r['o'], 'h': r['h'], 'l': r['l'], 'c': r['c'],
        'v': r['v'], 'vw': r.get('vw'), 'n': r.get('n'),
        'ts_bj': ts.strftime('%Y-%m-%d %H:%M BJ'),
        'ts_unix': r['t'] // 1000,
    }


def get_daily_bars(coin='BTC', days=5):
    """Get last N daily bars.
    Returns: [{o, h, l, c, v, date}, ...] newest first, or None
    """
    ticker = COIN_TICKERS.get(coin.upper())
    if not ticker:
        return None

    today = datetime.now(BJ).date()
    end = today + timedelta(days=1)
    start = today - timedelta(days=days + 5)  # extra buffer for weekends

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
           f"{start.isoformat()}/{end.isoformat()}?"
           f"adjusted=true&sort=desc&limit={days}&apiKey={API_KEY}")
    data = _get(url)

    if data.get('error') or not data.get('results'):
        return None

    bars = []
    for r in data['results']:
        bars.append({
            'date': datetime.fromtimestamp(r['t'] / 1000, tz=BJ).strftime('%Y-%m-%d'),
            'o': r['o'], 'h': r['h'], 'l': r['l'], 'c': r['c'],
            'v': r['v'], 'n': r.get('n'),
        })
    return bars


def get_snapshot(coin='BTC'):
    """Get current snapshot (all ticker data).
    Returns: {ticker, price, volume, updated} or None
    """
    ticker = COIN_TICKERS.get(coin.upper())
    if not ticker:
        return None

    url = f"{BASE_URL}/v2/snapshot/locale/global/markets/crypto/tickers/{ticker}?apiKey={API_KEY}"
    data = _get(url)

    if data.get('error') or not data.get('ticker'):
        return None

    t = data['ticker']
    day = t.get('day', {})
    return {
        'coin': coin.upper(),
        'ticker': t['ticker'],
        'price': day.get('c'),
        'open': day.get('o'),
        'high': day.get('h'),
        'low': day.get('l'),
        'volume': day.get('v'),
        'updated': t.get('updated'),
    }


def verify_price(coin='BTC'):
    """Quick price check from Massive. Returns float or None."""
    ticker = COIN_TICKERS.get(coin.upper())
    if not ticker:
        return None

    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={API_KEY}"
    data = _get(url)
    if data.get('results'):
        return data['results'][0]['c']
    return None


# ============================================================
# Cross-Verification
# ============================================================

def compare_with_okx(coin='BTC', okx_daily=None):
    """Compare Massive daily OHLCV with OKX local DB.
    Args:
        coin: BTC/ETH/SOL/DOGE
        okx_daily: list of {close, ts} from OKX DB (newest first)
    Returns: dict with comparison report
    """
    massive = get_prev_close(coin)
    if not massive:
        return {'status': 'massive_error', 'error': 'Failed to fetch Massive data'}

    report = {
        'status': 'ok',
        'coin': coin.upper(),
        'massive': {
            'close': massive['c'],
            'open': massive['o'],
            'high': massive['h'],
            'low': massive['l'],
            'volume': massive['v'],
            'ts_bj': massive['ts_bj'],
        }
    }

    if okx_daily:
        # Match by date
        try:
            massive_date = datetime.strptime(
                massive['ts_bj'].split(' ')[0], '%Y-%m-%d'
            ).date()
        except (ValueError, IndexError, KeyError):
            massive_date = None

        for row in okx_daily:
            row_date = datetime.fromtimestamp(row['ts'] / 1000, tz=BJ).date()
            if row_date == massive_date:
                diff = round(massive['c'] - row['close'], 2)
                diff_pct = round(diff / row['close'] * 100, 3)
                report['okx'] = {
                    'close': row['close'],
                    'date': row_date.isoformat(),
                }
                report['diff'] = diff
                report['diff_pct'] = diff_pct
                report['status'] = 'ok' if abs(diff_pct) < 0.5 else 'divergence'
                break

    return report


# ============================================================
# Multi-Coin Batch
# ============================================================

def batch_prev_close(coins=None):
    """Get previous close for all tracked coins.
    Returns: {BTC: {...}, ETH: {...}, ...}
    """
    if coins is None:
        coins = ['BTC', 'ETH', 'SOL', 'DOGE']

    result = {}
    for c in coins:
        data = get_prev_close(c)
        result[c] = data
    return result


def market_summary():
    """Quick market summary from Massive.
    Returns: formatted string
    """
    batch = batch_prev_close()
    lines = []
    now = datetime.now(BJ).strftime('%Y-%m-%d %H:%M BJ')
    lines.append(f"【Massive 市场快照】 {now}")

    for coin in ['BTC', 'ETH', 'SOL', 'DOGE']:
        d = batch.get(coin)
        if d:
            lines.append(
                f"  {coin}: ${d['c']:,.2f}  "
                f"H={d['h']:,.2f} L={d['l']:,.2f}  "
                f"V={d['v']:,.0f}"
            )
        else:
            lines.append(f"  {coin}: 获取失败")

    return '\n'.join(lines)


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        coin = sys.argv[2] if len(sys.argv) > 2 else 'BTC'

        if cmd == 'prev':
            print(json.dumps(get_prev_close(coin), indent=2, ensure_ascii=False))
        elif cmd == 'bars':
            days = int(sys.argv[3]) if len(sys.argv) > 3 else 5
            print(json.dumps(get_daily_bars(coin, days), indent=2, ensure_ascii=False))
        elif cmd == 'snapshot':
            print(json.dumps(get_snapshot(coin), indent=2, ensure_ascii=False))
        elif cmd == 'compare':
            import sqlite3
            DB = '/root/.hermes/trade_review/okx_klines.db'
            db = sqlite3.connect(DB)
            rows = db.execute(
                "SELECT close, ts FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 5",
                (coin.upper(),)
            ).fetchall()
            db.close()
            okx = [{'close': r[0], 'ts': r[1]} for r in rows]
            print(json.dumps(compare_with_okx(coin, okx), indent=2, ensure_ascii=False))
        elif cmd == 'summary':
            print(market_summary())
    else:
        print(market_summary())
