#!/usr/bin/env python3
"""
复盘上一条社交动态
用法: python3 scripts/review_last_post.py [--save]

从 social_posts.json 读最近一条有文案的动态，从 DB 查入场后 1H K线，
计算方向正确性和 SL 触发状态，输出复盘结果。
新增：观察价格路径（先涨后跌/先跌后涨/窄幅震荡/单边），标注最高/最低及时间。
--save: 同时写入 social_reviews.json
"""
import sqlite3, json, os, sys, re
from datetime import datetime, timezone, timedelta
from _shared import BJT

DB = '/root/.hermes/trade_review/okx_klines.db'
POSTS_FILE = '/root/.hermes/trade_review/social_posts.json'
REVIEWS_FILE = '/root/.hermes/trade_review/social_reviews.json'
THRESHOLD = 2.0  # ±2% 判定


def _parse_sl_from_direction(direction):
    """从方向字符串中解析实际止损价（SL 值）。
    解析失败返回 None，调用方回退到 entry*0.99。
    """
    if not direction:
        return None
    match = re.search(r'SL\s*([\d,.]+)', direction)
    if match:
        try:
            return float(match.group(1).replace(',', ''))
        except ValueError:
            pass
    return None


def _calc_entry_price(entry_min, entry_max, sl):
    """计算实际挂单价（与 place_live_orders.py 的 calc_order_price 一致）。
    用于复盘时以正确的 entry 为基准计算价格路径。
    """
    mid = (entry_min + entry_max) / 2
    spread = entry_max - entry_min
    raw = mid - spread * 0.10
    order_px = int(raw / 5) * 5
    sl_rounded = int(sl / 5) * 5
    if sl_rounded <= sl:
        sl_rounded += 5
    if order_px <= sl_rounded:
        order_px = sl_rounded + 5
    return order_px


def analyze_path(rows, entry):
    """分析价格路径：从入场后蜡烛序列，按时间顺序检测先涨/先跌。
    输入 rows 必须已过滤为入场时间之后的蜡烛，按时间升序排列。"""
    if not rows:
        return None

    max_row = max(rows, key=lambda r: r[1])  # (ts, high, low, close)
    min_row = min(rows, key=lambda r: r[2])
    max_ts, min_ts = max_row[0], min_row[0]
    current = rows[-1][3]
    high_val, low_val = max_row[1], min_row[2]
    change_ratio = (current - entry) / entry
    change = change_ratio * 100

    path_type = _path_classify(high_val, low_val, entry, current, max_ts, min_ts, change_ratio)

    high_ts_bj = datetime.fromtimestamp(max_ts / 1000, tz=BJT)
    low_ts_bj = datetime.fromtimestamp(min_ts / 1000, tz=BJT)
    return {
        'high': high_val,
        'low': low_val,
        'high_ts': high_ts_bj.strftime('%m-%d %H:%M'),
        'low_ts': low_ts_bj.strftime('%m-%d %H:%M'),
        'current': current,
        'change': change,
        'path_type': path_type,
    }


def _path_classify(high_val, low_val, entry, current, max_ts, min_ts, change_ratio):
    """按时间顺序判定价格路径类型。"""
    high_first = max_ts < min_ts
    saw_both = high_val > entry * 1.02 and low_val < entry * 0.98

    if saw_both:
        return '先涨后跌（冲高回落）' if high_first else '先跌后涨（探底回升）'
    elif high_val > entry * 1.02 and current < entry * 1.01:
        return '先涨后跌（冲高回落）'
    elif low_val < entry * 0.98 and current > entry * 0.99:
        return '先跌后涨（探底回升）'
    elif abs(change_ratio) < 0.02:
        return '窄幅震荡'
    elif change_ratio > 0.02:
        return '单边上涨'
    elif change_ratio < -0.02:
        return '单边下跌'
    else:
        return '偏多震荡' if change_ratio > 0 else '偏空震荡'


def _build_coin_result(coin, entry, direction, path, actual_sl=None):
    """根据路径分析构建单币种复盘结果。返回 (verdict_str, result_dict)."""
    change = path['change']
    low_val = path['low']

    if abs(change) <= THRESHOLD:
        verdict = '🟡 横盘震荡'
        verdict_short = '横盘'
    elif change > THRESHOLD:
        if '做多' in direction and '做空' not in direction:
            verdict = '✅ 做多正确'
        elif '做空' in direction and '做多' not in direction:
            verdict = '❌ 做空错误'
        else:
            verdict = '⚠️ 方向未知（上涨）'
        verdict_short = '正确' if ('做多' in direction and '做空' not in direction) else ('错误' if ('做空' in direction and '做多' not in direction) else '未知')
    else:
        if '做多' in direction and '做空' not in direction:
            verdict = '❌ 做多错误'
        elif '做空' in direction and '做多' not in direction:
            verdict = '✅ 做空正确'
        else:
            verdict = '⚠️ 方向未知（下跌）'
        verdict_short = '错误' if ('做多' in direction and '做空' not in direction) else ('正确' if ('做空' in direction and '做多' not in direction) else '未知')

    # 优先用 direction 中解析出的实际 SL，回退到 entry*(0.99|1.01)
    # ⚠️ 精度假设: ±1% 固定回退，SL可能在极端波动中误判
    if actual_sl:
        sl = actual_sl
    elif '做空' in direction:
        sl = entry * 1.01
    else:
        sl = entry * 0.99
    # 做空 SL 在上方（跌破触发），做多 SL 在下方（涨破触发）
    if '做空' in direction:
        sl_hit = path['high'] > sl
    else:
        sl_hit = low_val < sl

    return verdict, {
        'entry': entry,
        'current': path['current'],
        'high': path['high'],
        'low': low_val,
        'change_pct': change,
        'verdict': verdict,
        'verdict_short': verdict_short,
        'sl': sl,
        'sl_hit': sl_hit,
        'path_type': path['path_type'],
        'high_ts': path['high_ts'],
        'low_ts': path['low_ts'],
    }


def main():
    save_flag = '--save' in sys.argv

    # 1. Read last post with content
    if not os.path.exists(POSTS_FILE):
        print('❌ social_posts.json not found')
        sys.exit(1)
    with open(POSTS_FILE) as f:
        posts = json.load(f)

    valid = [p for p in posts if p.get('text', '').strip()]
    if not valid:
        print('⚠️ No posts with content found')
        return

    prev = valid[-1]
    post_id = prev.get('id', prev.get('post_id', '?'))
    print(f'📋 复盘上条: Post #{post_id} | {prev.get("time")}')

    btc_p = prev.get('btc_price')
    eth_p = prev.get('eth_price')
    btc_dir = prev.get('btc_direction', '')
    eth_dir = prev.get('eth_direction', '')

    # Parse post time to filter candles after posting only
    post_time_str = prev.get('time', '')
    post_time_ms = None
    if post_time_str:
        try:
            dt_str = post_time_str.replace(' BJ', '')
            post_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
            post_dt = post_dt.replace(tzinfo=BJT)
            post_time_ms = int(post_dt.timestamp() * 1000)
        except Exception:
            pass
    if not post_time_ms:
        print('⚠️ Post time missing/unparseable — using all available 48H candles (may not be accurate)')

    # 2. Query DB for 1H klines since post time
    db = sqlite3.connect(DB)
    results = {}

    for coin, entry_price, direction in [('BTC', btc_p, btc_dir), ('ETH', eth_p, eth_dir)]:
        if not entry_price:
            print(f'  {coin}: ⏭️ no entry price')
            continue

        # Parse entry range and SL from direction to compute actual order price
        entry_min = entry_max = None
        actual_sl = _parse_sl_from_direction(direction)
        entry_match = re.search(r'([\d,]+)-([\d,]+)', direction)
        if entry_match:
            entry_min = float(entry_match.group(1).replace(',', ''))
            entry_max = float(entry_match.group(2).replace(',', ''))

        # Use calc_order_price equivalent for consistent entry
        if entry_min is not None and entry_max is not None and actual_sl is not None:
            review_entry = _calc_entry_price(entry_min, entry_max, actual_sl)
        else:
            review_entry = entry_price  # fallback to post price

        # Get last 48 1H candles (covers up to 2 days)
        rows = db.execute("""
            SELECT ts, high, low, close FROM klines
            WHERE coin=? AND timeframe='1H'
            ORDER BY ts DESC LIMIT 96
        """, (coin,)).fetchall()
        if not rows:
            print(f'  {coin}: ❌ no 1H data')
            continue

        rows.reverse()

        # Filter to only candles after post time
        if post_time_ms:
            rows = [r for r in rows if r[0] >= post_time_ms]
        if not rows:
            print(f'  {coin}: ❌ no candles after post time')
            continue

        # Path analysis using actual order price as entry
        path = analyze_path(rows, review_entry)
        if not path:
            print(f'  {coin}: ❌ no path data')
            continue

        verdict, result = _build_coin_result(coin, review_entry, direction, path, actual_sl)
        results[coin] = result

        print(f'  {coin}: {result["entry"]:.0f}→{result["current"]:.0f} ({result["change_pct"]:+.1f}%) {verdict}'
              + (' ⚠️SL触发' if result['sl_hit'] else '')
              + f' | {result["path_type"]}')

    db.close()

    # 3. Optionally save to reviews
    if save_flag and results:
        reviews = []
        if os.path.exists(REVIEWS_FILE):
            with open(REVIEWS_FILE) as f:
                reviews = json.load(f)

        # 新格式（统一）：flat btc_verdict / eth_verdict
        btc_r = results.get('BTC', {})
        eth_r = results.get('ETH', {})
        record = {
            'time': datetime.now(BJT).strftime('%Y-%m-%d %H:%M BJ'),
            'post_id': post_id,
            'post_time': prev.get('time'),
            'post': {
                'time': prev.get('time'),
                'btc_price': prev.get('btc_price'),
                'eth_price': prev.get('eth_price'),
                'btc_direction': prev.get('btc_direction', ''),
                'eth_direction': prev.get('eth_direction', ''),
            },
            'btc_verdict': btc_r.get('verdict_short', '?'),
            'btc_change_pct': round(btc_r.get('change_pct', 0), 2),
            'btc_sl_hit': btc_r.get('sl_hit', False),
            'eth_verdict': eth_r.get('verdict_short', '?'),
            'eth_change_pct': round(eth_r.get('change_pct', 0), 2),
            'eth_sl_hit': eth_r.get('sl_hit', False),
            'path_type': btc_r.get('path_type', ''),
            'eth_path_type': eth_r.get('path_type', ''),
            'btc_high': btc_r.get('high'),
            'btc_low': btc_r.get('low'),
            'btc_high_ts': btc_r.get('high_ts'),
            'btc_low_ts': btc_r.get('low_ts'),
            'eth_high': eth_r.get('high'),
            'eth_low': eth_r.get('low'),
            'eth_high_ts': eth_r.get('high_ts'),
            'eth_low_ts': eth_r.get('low_ts'),
        }
        reviews.append(record)
        tmp = REVIEWS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(reviews, f, indent=2, ensure_ascii=False)
        os.replace(tmp, REVIEWS_FILE)
        print(f'✅ Saved to social_reviews.json ({len(reviews)} total)')

    return results


if __name__ == '__main__':
    main()
