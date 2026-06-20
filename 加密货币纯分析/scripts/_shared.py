# _shared.py — 全工作流共享常量 + 公共函数
# Import: from _shared import BJT, DB_PATH, TRADE_DIR, classify_price_path, get_klines
from datetime import datetime, timezone, timedelta
import re

BJT = timezone(timedelta(hours=8), 'Asia/Shanghai')
TRADE_DIR = '/root/.hermes/trade_review'
DB_PATH = f'{TRADE_DIR}/okx_klines.db'
SOCIAL_ANALYSES_PATH = f'{TRADE_DIR}/social_analyses.json'  # publish_social.py full_obj 格式


def get_klines(db, coin_raw, timeframe, since_ts_ms, until_ts_ms=None, limit=200):
    """Get klines from DB. coin_raw like 'BTCUSDT' → DB coin='BTC'.
    If until_ts_ms is None: get from since_ts_ms onward.
    If until_ts_ms is set: get within [since, until] range.
    """
    coin = re.sub(r'USDT$', '', coin_raw)
    if until_ts_ms is not None:
        query = ("SELECT ts, open, high, low, close, volume FROM klines "
                 "WHERE coin=? AND timeframe=? AND ts >= ? AND ts <= ? "
                 "ORDER BY ts ASC LIMIT ?")
        return db.execute(query, (coin, timeframe, since_ts_ms, until_ts_ms, limit)).fetchall()
    else:
        query = ("SELECT ts, open, high, low, close, volume FROM klines "
                 "WHERE coin=? AND timeframe=? AND ts >= ? "
                 "ORDER BY ts ASC LIMIT ?")
        return db.execute(query, (coin, timeframe, since_ts_ms, limit)).fetchall()


def classify_price_path(candles, entry_price):
    """
    将入场后的K线序列分类为价格路径类型。
    返回: (path_type_str, detail_dict)
    """
    if not candles or not entry_price or len(candles) < 4:
        return 'unknown', {'error': '数据不足'}

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    opens = [c[1] for c in candles]
    timestamps = [c[0] for c in candles]

    n = len(candles)
    half = n // 2
    entry = entry_price

    first_close = opens[0]
    last_close = closes[-1]

    global_high = max(highs)
    global_low = min(lows)
    global_high_idx = highs.index(global_high)
    global_low_idx = lows.index(global_low)
    net_change = (last_close - entry) / entry * 100
    total_range = (global_high - global_low) / entry * 100

    # 分段统计
    first_half = candles[:half]
    second_half = candles[half:]
    first_close_price = first_half[-1][4]
    second_close_price = second_half[-1][4] if second_half else first_close_price
    first_change = (first_close_price - entry) / entry * 100
    second_change = (second_close_price - first_close_price) / first_close_price * 100
    first_high = max(c[2] for c in first_half)
    first_low = min(c[3] for c in first_half)
    second_high = max(c[2] for c in second_half) if second_half else first_high
    second_low = min(c[3] for c in second_half) if second_half else first_low

    # 最大回撤和最大反弹
    running_max = entry
    max_drawdown = 0
    max_rally = 0
    for c in candles:
        running_max = max(running_max, c[2])
        dd = (running_max - c[3]) / running_max * 100
        max_drawdown = max(max_drawdown, dd)
        rally = (c[2] - running_max) / running_max * 100 if running_max > 0 else 0
        max_rally = max(max_rally, rally)

    def ts_to_bj(ts_ms):
        return datetime.fromtimestamp(ts_ms / 1000, tz=BJT).strftime('%m-%d %H:%M')

    peak_early = global_high_idx < n * 0.3
    trough_early = global_low_idx < n * 0.3
    peak_late = global_high_idx > n * 0.7
    trough_late = global_low_idx > n * 0.7

    if total_range < 2.0:
        path_type = '横盘震荡'
    elif peak_early and trough_late and second_change < -first_change * 0.5:
        path_type = '先涨后跌'
    elif trough_early and peak_late and second_change > 0:
        path_type = '先跌后涨'
    elif net_change > 3 and max_drawdown < 1.0 and first_change > 0 and second_change > 0:
        path_type = '单边上涨'
    elif net_change < -3 and max_rally < 1.0 and first_change < 0 and second_change < 0:
        path_type = '单边下跌'
    elif peak_early and net_change < 0 and second_change < -2:
        path_type = '冲高回落'
    elif trough_early and net_change > 0 and second_change > 2:
        path_type = '探底回升'
    elif total_range > 5.0 and abs(net_change) < 2.0:
        path_type = '宽幅震荡'
    elif net_change > 2:
        path_type = '偏多'
    elif net_change < -2:
        path_type = '偏空'
    else:
        path_type = '横盘震荡'

    detail = {
        'path_type': path_type,
        'entry_price': entry,
        'first_close': first_close,
        'last_close': last_close,
        'net_change_pct': round(net_change, 2),
        'total_range_pct': round(total_range, 2),
        'global_high': round(global_high, 2),
        'global_low': round(global_low, 2),
        'global_high_time': ts_to_bj(timestamps[global_high_idx]),
        'global_low_time': ts_to_bj(timestamps[global_low_idx]),
        'max_drawdown_pct': round(max_drawdown, 2),
        'max_rally_pct': round(max_rally, 2),
        'first_half': {
            'change_pct': round(first_change, 2),
            'high': round(first_high, 2),
            'low': round(first_low, 2),
            'close': round(first_close_price, 2),
        },
        'second_half': {
            'change_pct': round(second_change, 2),
            'high': round(second_high, 2),
            'low': round(second_low, 2),
            'close': round(second_close_price, 2),
        },
        'candle_count': n,
    }
    return path_type, detail
