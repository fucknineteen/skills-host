#!/usr/bin/env python3
"""
社交动态文案生成库 — 从分析数据生成社交动态草稿。
从 analysis_template.py 中提取，保持独立可导入。
"""
import os, sys, json, subprocess, time
from datetime import datetime, timezone, timedelta

# ── 共享常量 ──────────────────────────────────────────────
_TRADE_DIR = os.environ.get('TRADE_DIR', '/root/.hermes/trade_review')
_DB = os.path.join(_TRADE_DIR, 'okx_klines.db')
_REGIME_CACHE = os.path.join(_TRADE_DIR, '.regime_cache.json')
_REGIME_CACHE_TTL = 120  # 2 min

try:
    from _shared import BJT
except ImportError:
    sys.path.insert(0, _TRADE_DIR)
    from _shared import BJT

# ── 从 analysis_template.py 导入威科夫/VP/形态/日历函数 ──────────
from analysis_template import session_vp, wyckoff_detect, detect_kline_patterns, get_jin10_key_events

# ── MACD 参数 ─────────────────────────────────────────────
MACD_PARAMS = {
    'BTC': (12, 75, 9),
    'ETH': (12, 75, 9),
    'SOL': (12, 75, 9),
    'DOGE': (12, 75, 9),
}

# ── 重试 ──────────────────────────────────────────────────
def _retry(fn, max_retries=5, delay=1.5):
    for attempt in range(max_retries):
        try:
            result = fn()
            if isinstance(result, dict) and result.get('_error'):
                err = str(result['_error']).lower()
                if any(k in err for k in ['timed out', 'reset', 'refused', 'no_data', '51001']):
                    if attempt < max_retries - 1:
                        time.sleep(delay * (2 ** attempt))
                        continue
            return result
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            if attempt < max_retries - 1:
                time.sleep(delay * (2 ** attempt))
                continue
            return {'_error': f'failed after {max_retries} retries'}
    return {'_error': f'failed after {max_retries} retries'}

# ── 恐惧贪婪 ──────────────────────────────────────────────
def fetch_fear_greed():
    """拉取恐惧贪婪指数"""
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '15',
            '-H', 'User-Agent: Mozilla/5.0',
            'https://api.alternative.me/fng/'],
            capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('data'):
            return int(d['data'][0]['value']), d['data'][0]['value_classification']
    except Exception:
        pass
    return None, 'no_data'

# ── 行情缓存 ──────────────────────────────────────────────
_CACHE = {}

def _fetch_okx(url, retries=5):
    def _call():
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '10', url],
                capture_output=True, text=True, timeout=15)
            d = json.loads(r.stdout) if r.stdout else {}
            if d.get('code') == '0' and d.get('data'):
                return d['data'][0]
            # If rate limited (code != 0 but no _error), return special marker
            if d.get('code') and d.get('code') != '0':
                return {'_error': f'api_error_{d.get("code")}', '_code': d.get('code')}
        except Exception as e:
            return {'_error': str(e)}
        return {'_error': 'no_data'}
    return _retry(_call, max_retries=retries)

def fetch_okx_ticker(inst_id):
    return _fetch_okx(f'https://www.okx.com/api/v5/market/ticker?instId={inst_id}')

def fetch_okx_funding(inst_id):
    return _fetch_okx(f'https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}')

# ── 行情检测器 ────────────────────────────────────────────
def get_regime_result():
    """获取行情类型。优先读缓存，过期则重新运行 regime_detector"""
    try:
        if os.path.exists(_REGIME_CACHE):
            mtime = os.path.getmtime(_REGIME_CACHE)
            if time.time() - mtime < _REGIME_CACHE_TTL:
                with open(_REGIME_CACHE) as f:
                    return json.load(f)
    except Exception:
        pass
    try:
        r = subprocess.run([sys.executable, os.path.join(_TRADE_DIR, 'regime_detector.py')],
            capture_output=True, text=True, timeout=30, cwd=_TRADE_DIR)
        if r.stdout:
            result = json.loads(r.stdout)
            try:
                tmp = _REGIME_CACHE + '.tmp'
                with open(tmp, 'w') as f:
                    json.dump(result, f)
                os.replace(tmp, _REGIME_CACHE)
            except Exception:
                pass
            return result
    except Exception:
        pass
    try:
        if os.path.exists(_REGIME_CACHE):
            with open(_REGIME_CACHE) as f:
                return json.load(f)
    except Exception:
        pass
    return {'regime': '未知', 'confidence': 0, 'composite_score': 0}

# ── 基础工具 ──────────────────────────────────────────────
def is_closed(ts, tf):
    """检查指定时间戳的 K 线是否已收盘"""
    tf_secs = {'1D': 86400, '4H': 14400, '1H': 3600, '30m': 1800, '5m': 300}
    sec = tf_secs.get(tf, 3600)
    return (ts + sec) <= datetime.now(timezone.utc).timestamp()

def _fmt_price(v):
    if v is None: return '-'
    if abs(v) >= 1000: return f'{v:,.0f}'
    return f'{v:.2f}'

# ── 指标计算 ──────────────────────────────────────────────
def calc_rsi(closes, period=14):
    """RSI(period) — 相对强弱指数 (Wilder 平滑), 返回最后一个值"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    rs = avg_g / avg_l if avg_l > 0 else float('inf')
    return 100 - 100 / (1 + rs) if rs != float('inf') else 100.0

def calc_macd(closes, fast=12, slow=75, signal=9):
    """MACD — 返回 (macd_line_val, signal_val, histogram_val) 三个标量"""
    def _ema(data, period):
        if len(data) < period:
            return data[-1:] if data else [0]
        a = 2 / (period + 1)
        e = [data[0]]
        for i in range(1, len(data)):
            e.append(a * data[i] + (1 - a) * e[-1])
        return e
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    e_fast = _ema(closes, fast)
    e_slow = _ema(closes, slow)
    macd_vals = [e_fast[i] - e_slow[i] for i in range(len(closes))]
    sig_vals = _ema(macd_vals, signal)
    return macd_vals[-1], sig_vals[-1], macd_vals[-1] - sig_vals[-1]

def calc_adx(highs, lows, closes, period=14):
    if len(highs) < period + 1: return 0, 0, 0, 0
    tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(highs))]
    dm_up = [max(highs[i] - highs[i-1], 0) for i in range(1, len(highs))]
    dm_down = [max(lows[i-1] - lows[i], 0) for i in range(1, len(highs))]
    atr = sum(tr[:period]) / period
    di_plus = sum(dm_up[:period])
    di_minus = sum(dm_down[:period])
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        di_plus = di_plus * (period - 1) + dm_up[i]
        di_minus = di_minus * (period - 1) + dm_down[i]
    dx = (abs(di_plus - di_minus) / (di_plus + di_minus)) * 100 if (di_plus + di_minus) > 0 else 0
    atr_last = atr
    return round(dx, 1), round(di_plus, 1), round(di_minus, 1), round(atr_last, 2)

def calc_bollinger(closes, period=20, mult=2):
    if len(closes) < period: return None
    window = closes[-period:]
    mean = sum(window) / period
    std = (sum((x - mean) ** 2 for x in window) / period) ** 0.5
    upper = mean + mult * std
    lower = mean - mult * std
    latest = closes[-1]
    pct_b = (latest - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {'upper': round(upper, 2), 'middle': round(mean, 2), 'lower': round(lower, 2), 'pct_b': round(pct_b * 100, 1)}

def calc_obv(rows):
    if len(rows) < 2: return 0
    obv = 0
    for i in range(1, len(rows)):
        if rows[i][4] > rows[i-1][4]: obv += rows[i][5]
        elif rows[i][4] < rows[i-1][4]: obv -= rows[i][5]
    return round(obv, 0)

# ── K 线分析 ──────────────────────────────────────────────
def candle_body_label(row):
    if not row: return '-'
    o, h, l, c, v = row[1], row[2], row[3], row[4], row[5]
    body = abs(c - o)
    range_hl = h - l
    if range_hl == 0: return '十字星'
    body_pct = body / range_hl * 100
    if body_pct > 70:
        if c > o: return '大阳+'
        else: return '大阴-'
    elif body_pct > 40:
        if c > o: return '中阳'
        else: return '中阴'
    elif body_pct < 10:
        return '十字星'
    elif c > o:
        return '小阳'
    else:
        return '小阴'

def trend_direction(rows):
    if len(rows) < 5: return '数据不足'
    closes = [r[4] for r in rows[-10:]]
    if len(closes) < 5: return '数据不足'
    up = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    down = len(closes) - up
    if up >= down * 1.5: return '偏多'
    elif down >= up * 1.5: return '偏空'
    return '盘整'

def check_acceleration(days, tf='1D'):
    """检查加速下跌/减速"""
    if not days or len(days) < 3: return 'insufficient_data'
    closes = [r[4] for r in days]
    if len(closes) < 3: return 'insufficient_data'
    changes = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    last_change = changes[-1]
    avg_recent = sum(changes[-3:]) / min(3, len(changes))
    if last_change < -0.03 and avg_recent < -0.02:
        return 'accelerating_bear'
    elif last_change > -0.01 and avg_recent < -0.02:
        return 'decelerating'
    return 'steady'

def check_extreme_oversold(rsi_1d, fg_val):
    if rsi_1d is not None and rsi_1d < 20 and fg_val is not None and fg_val < 15:
        return True, '[X1] RSI<20+FG<15 → V反概率极高，空头信号降级为观望'
    return False, None

def check_data_event_window():
    """重大数据事件前48h+后12h → 方向置信度最低"""
    events = [
        ("FOMC", datetime(2026, 6, 18, 2, 0, tzinfo=BJT), "利率决议"),
        ("CPI", datetime(2026, 7, 15, 20, 30, tzinfo=BJT), "通胀数据"),
    ]
    now = datetime.now(BJT)
    for name, evt_dt, etype in events:
        hours = (evt_dt - now).total_seconds() / 3600
        if -12 <= hours <= 48:
            return True, f'[X3] {name} {evt_dt.strftime("%m/%d %H:%M")} BJ — 距公布{hours:.0f}h，方向置信度最低'
        elif -24 <= hours <= 0:
            return True, f'[X3] {name} {evt_dt.strftime("%m/%d %H:%M")} BJ — 数据后{abs(hours):.0f}h，TA仍在消化'
    return False, None

# ── 数据库查询 ────────────────────────────────────────────
def get_rows(conn, coin, tf, limit=100):
    """获取已收盘和未收盘的K线"""
    closed = conn.execute(
        f"SELECT ts, open, high, low, close, volume FROM klines "
        f"WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
        (coin, tf, limit)
    ).fetchall()
    closed.reverse()
    unclosed = []
    return closed, unclosed

def get_db_coins():
    conn = sqlite3.connect(_DB)
    coins = set()
    for row in conn.execute("SELECT DISTINCT coin FROM klines"):
        coins.add(row[0])
    conn.close()
    return coins

# ── 数据新鲜度 ────────────────────────────────────────────
def build_data_freshness(conn, coin):
    """检查各周期数据新鲜度"""
    freshness = {}
    for tf in ['1D', '4H', '1H']:
        row = conn.execute(
            "SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT 1",
            (coin, tf)
        ).fetchone()
        if row:
            age_min = (datetime.now(timezone.utc).timestamp() * 1000 - row[0]) / 60000
            freshness[tf] = f'{age_min:.0f}min ago'
        else:
            freshness[tf] = 'no_data'
    return freshness

# ── 单币种完整分析 ─────────────────────────────────────────
def analyze_single_coin(conn, coin, ticker, funding, fg_val, fg_label):
    """对单个币种执行完整分析"""
    TIMEFRAMES = ['1D', '4H', '1H', '30m', '5m']
    
    # Fallback: 如果 API 返回错误，从 DB 读最新收盘价
    if isinstance(ticker, dict) and ticker.get('_error'):
        latest = conn.execute(
            "SELECT close FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 1",
            (coin,)
        ).fetchone()
        if latest:
            ticker = {'last': str(latest[0]), '_fallback': True}
        else:
            ticker = {'last': '?', '_fallback': True}
    
    # 收盘状态
    close_status = {}
    for tf in TIMEFRAMES:
        closed, unclosed = get_rows(conn, coin, tf)
        close_status[tf] = {'closed': closed, 'n_closed': len(closed)}

    # 指标计算
    indicators = {}
    for tf in TIMEFRAMES:
        closed = close_status[tf]['closed']
        if len(closed) < 15:
            indicators[tf] = {'_skip': f'{len(closed)} candles, need 15+'}
            continue
        closes = [r[4] for r in closed]
        highs = [r[2] for r in closed]
        lows = [r[3] for r in closed]
        rsi = calc_rsi(closes, 14)
        mf, ms, msig = MACD_PARAMS.get(coin, (12, 75, 9))
        macd_l, macd_s, macd_h = calc_macd(closes, mf, ms, msig)
        adx, di_p, di_m, atr = calc_adx(highs, lows, closes, 14)
        bb = calc_bollinger(closes, 20, 2)
        obv = calc_obv(closed)
        trend_dir = trend_direction(closed)
        if trend_dir == '盘整' and macd_h is not None and macd_h > 0:
            if len(closes) >= 5 and closes[-1] >= max(closes[-5:]) * 0.98:
                trend_dir = '偏多'
        latest_label = candle_body_label(closed[-1]) if closed else '-'
        indicators[tf] = {
            'rsi': rsi, 'macd_l': macd_l, 'macd_s': macd_s, 'macd_h': macd_h,
            'adx': adx, 'di_p': di_p, 'di_m': di_m, 'atr': atr,
            'bb': bb, 'obv': obv, 'trend': trend_dir, 'label': latest_label,
            'last_close': closes[-1] if closes else None,
        }

    # 加速下跌
    closed_1d = close_status['1D']['closed']
    accel = check_acceleration(closed_1d) if len(closed_1d) >= 3 else 'insufficient_data'

    # 支撑阻力
    def get_levels(tf, n=20):
        cl = close_status.get(tf, {}).get('closed', [])
        if len(cl) < n:
            cl = close_status['1D']['closed']
        recent = cl[-n:]
        return {
            'highs': sorted(set(round(r[2], 1) for r in recent), reverse=True),
            'lows': sorted(set(round(r[3], 1) for r in recent))
        }
    levels_4h = get_levels('4H')

    # 底部研判
    rsi_1d = indicators['1D'].get('rsi')
    near_bottom = False
    bottom_note = '-'
    if rsi_1d is not None:
        if rsi_1d < 33 and fg_val is not None and fg_val < 25:
            if accel == 'accelerating_bear':
                bottom_note = '加速下跌 → near_bottom 禁用'
            elif accel == 'decelerating':
                bottom_note = '减速 → near_bottom 可讨论但未确认'
            else:
                bottom_note = 'near_bottom (RSI<33 + FG<25) — 观望，等放量阳线'
                near_bottom = True
        elif rsi_1d < 33:
            bottom_note = 'RSI<33 但 FG 未知 → level 未定'

    # 共振判断
    rsi_4h = indicators['4H'].get('rsi')
    rsi_1h = indicators['1H'].get('rsi')
    macd_h_1h = indicators['1H'].get('macd_h')
    macd_h_4h = indicators['4H'].get('macd_h')
    bb_1h = indicators['1H'].get('bb')
    pct_b = bb_1h['pct_b'] if bb_1h else 50
    score = 0
    if rsi_4h is not None:
        if rsi_4h > 55: score += 1
        elif rsi_4h < 45: score -= 1
    if macd_h_4h is not None:
        if macd_h_4h > 0: score += 1
        elif macd_h_4h < 0: score -= 1
    if pct_b < 30: score -= 1
    elif pct_b > 70: score += 1
    if score >= 2: resonance = '🟢偏强'
    elif score <= -2: resonance = '🔴偏弱'
    else: resonance = '🟡分歧'

    # 风险
    risks = []
    lessons_warnings = []
    rsi_1d_val = indicators['1D'].get('rsi')
    is_x1, x1_msg = check_extreme_oversold(rsi_1d_val, fg_val)
    if is_x1:
        lessons_warnings.append(x1_msg)
        if near_bottom:
            near_bottom = False
            bottom_note = f'{x1_msg} — near_bottom被复盘教训覆盖，强制观望'
    is_x3, x3_msg = check_data_event_window()
    if is_x3:
        lessons_warnings.append(x3_msg)
        risks.append(x3_msg)

    # K线收盘状态
    _now = datetime.now(BJT)
    _now_ts = _now.timestamp()
    _tf_close = {'1D': 86400, '4H': 14400, '1H': 3600, '30m': 1800, '5m': 300}
    data_selection_lines = [f'📐 K线收盘状态 [{_now.strftime("%m-%d %H:%M")} BJ]:']
    for _tf, _sec in _tf_close.items():
        _rows = conn.execute(
            f'SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT 2',
            (coin, _tf)
        ).fetchall()
        if not _rows:
            data_selection_lines.append(f'  {_tf}: 无数据')
            continue
        _ts = _rows[0][0] / 1000
        _end = _ts + _sec
        _end_bj = datetime.fromtimestamp(_end, tz=BJT)
        if _end <= _now_ts:
            _t_bj = datetime.fromtimestamp(_ts, tz=BJT)
            data_selection_lines.append(f'  {_tf}: {_t_bj.strftime("%m-%d %H:%M")} ✅')
        else:
            _t_bj = datetime.fromtimestamp(_ts, tz=BJT)
            if len(_rows) > 1:
                _prev_ts = _rows[1][0] / 1000
                _prev_bj = datetime.fromtimestamp(_prev_ts, tz=BJT)
                data_selection_lines.append(f'  {_tf}: {_prev_bj.strftime("%m-%d %H:%M")} ✅ | 今{_t_bj.strftime("%m-%d %H:%M")}形成中(→{_end_bj.strftime("%m-%d %H:%M")})')
            else:
                data_selection_lines.append(f'  {_tf}: 今{_t_bj.strftime("%m-%d %H:%M")}形成中(→{_end_bj.strftime("%m-%d %H:%M")})')

    data_freshness = build_data_freshness(conn, coin)

    # 方向判定 — 共振 + near_top + V反保护
    trend_1d = indicators['1D'].get('trend', '')
    rsi_1d_val = indicators['1D'].get('rsi')
    position_raw = '偏多' if '强' in str(resonance) else ('偏空' if '弱' in str(resonance) else '观望')
    # L1: near_top 做空捷径
    if position_raw == '观望' and rsi_1d_val and rsi_1d_val > 67 and trend_1d == '下降' and macd_h_4h is not None and macd_h_4h < 0:
        position_raw = '偏空'
    # L3: V反保护
    if position_raw == '偏空' and near_bottom:
        position_raw = '观望（near_bottom保护）'
    
    # ── 补全：威科夫 / Volume Profile / K线形态 / 日历 ──
    result_dict = {
        'coin': coin, 'close_status': close_status,
        'levels_4h': levels_4h, 'indicators': indicators,
        'accel': accel, 'near_bottom': near_bottom,
        'resonance': resonance, 'risks': risks,
    }
    vp_data = session_vp(coin, conn) or {}
    wyckoff_data = wyckoff_detect(result_dict) or {}
    kline_patterns = detect_kline_patterns(result_dict)
    calendar_events = get_jin10_key_events()
    # 提取 macro 数据（从 regime_cache，含 DXY/VIX/10Y/BTC.D）
    macro_external = {}
    try:
        import json as _json
        if os.path.exists(_REGIME_CACHE):
            with open(_REGIME_CACHE) as _f:
                _rc = _json.load(_f)
            me = _rc.get('macro_external', {})
            macro_external = me if isinstance(me, dict) else {}
    except Exception:
        pass
    
    return {
        'coin': coin,
        'ticker': ticker,
        'funding': funding,
        'close_status': close_status,
        'data_freshness': data_freshness,
        'data_selection': '\n'.join(data_selection_lines),
        'indicators': indicators,
        'accel': accel,
        'levels_4h': levels_4h,
        'bottom_note': bottom_note,
        'near_bottom': near_bottom,
        'resonance': resonance,
        'position': position_raw,
        'risks': risks,
        'rsi_4h': rsi_4h, 'rsi_1h': rsi_1h,
        'macd_h_4h': macd_h_4h, 'macd_h_1h': macd_h_1h,
        'pct_b': pct_b,
        'lessons_warnings': lessons_warnings,
        # 威科夫 / VP / 形态 / 日历（与 analysis_template.py 同步）
        'vp_data': vp_data,
        'wyckoff_data': wyckoff_data,
        'kline_patterns': kline_patterns,
        'calendar_events': calendar_events,
        'macro_external': macro_external,
    }

# ── 方向提取 ──────────────────────────────────────────────
def extract_direction(coin_a):
    """从分析结果提取交易方向简述"""
    if not coin_a: return ''
    resonance = coin_a.get('resonance', '')
    rsi_4h = coin_a.get('rsi_4h', 50)
    macd_h = coin_a.get('macd_h_4h', 0)
    if '强' in str(resonance) and macd_h > 0:
        return f"做多 RSI={rsi_4h:.0f} MACD={macd_h:.0f}"
    elif '弱' in str(resonance) or macd_h < -50:
        return f"做空 RSI={rsi_4h:.0f}"
    return '观望'

# ── 社交动态文案生成 ──────────────────────────────────────
def generate_social_draft(analyses, regime_result, fg_val, fg_label, review_text=''):
    """用分析数据填充社交动态模板，输出完整文案草稿。
    ⚠️ 当前仅支持 BTC/ETH 双币种。如扩展更多币种需重构此函数的硬编码字段访问。
    """
    now_bj = datetime.now(BJT)
    btc = next((a for a in analyses if a['coin'] == 'BTC'), {})
    eth = next((a for a in analyses if a['coin'] == 'ETH'), {})

    btc_p = btc.get('ticker', {}).get('last', '?')
    eth_p = eth.get('ticker', {}).get('last', '?')
    btc_rsi4 = btc.get('rsi_4h', '?')
    btc_macd4 = btc.get('macd_h_4h', 0)
    bt_resonance = btc.get('resonance', '')
    btc_near_s = btc.get('levels_4h', {}).get('lows', [])
    btc_near_r = btc.get('levels_4h', {}).get('highs', [])
    eth_near_s = eth.get('levels_4h', {}).get('lows', [])
    eth_near_r = eth.get('levels_4h', {}).get('highs', [])
    regime = regime_result.get('regime', '')
    confidence = regime_result.get('confidence', 0)
    composite = regime_result.get('composite_score', 0)

    s_btc = '/'.join(f'${x:,.0f}' for x in btc_near_s[:2]) if btc_near_s else '?'
    r_btc = '/'.join(f'${x:,.0f}' for x in btc_near_r[:2]) if btc_near_r else '?'
    s_eth = '/'.join(f'${x:,.0f}' for x in eth_near_s[:2]) if eth_near_s else '?'
    r_eth = '/'.join(f'${x:,.0f}' for x in eth_near_r[:2]) if eth_near_r else '?'

    lines = []

    # 标题
    if '强' in str(bt_resonance) and btc_macd4 > 200:
        title = f'BTC 4H共振偏强 MACD +{btc_macd4:.0f}——{regime}中的突破前夜？🔥'
    elif btc_macd4 > 0:
        title = f'BTC震荡偏多 RSI={btc_rsi4} {regime}——极度恐惧中谁在默默吃货'
    else:
        title = f'{regime}·FG={fg_val}极度恐惧——散户割肉时庄家在干嘛'
    lines.append(title)

    # 复盘
    if review_text:
        lines.append('')
        lines.append('📋 上条复盘')
        lines.append(review_text)

    # 时间
    bj_time_str = now_bj.strftime("%m-%d %H:%M")
    lines.append(f'')
    lines.append(f'🕐 BJ {bj_time_str} | BTC ${btc_p} | ETH ${eth_p} | FG:{fg_val}({fg_label})')

    # 大资金
    if composite < -10:
        wyckoff_line = f'💡 大资金：价格承压，{regime}格局中机构仍在防守。等Spring确认后再动。'
    elif composite < 10:
        wyckoff_line = f'💡 大资金：中性偏谨慎，{regime}格局未破。关键看能否放量站上{r_btc}。'
    else:
        wyckoff_line = f'💡 大资金：偏多蓄力中，{regime}守住{s_btc}就是多头阵地。'
    lines.append(wyckoff_line)

    # 盘面
    if btc_macd4 > 50:
        macd_summary = 'MACD柱扩大，加速看多'
    elif btc_macd4 < -50:
        macd_summary = 'MACD走弱，等确认'
    else:
        macd_summary = 'MACD待确认'
    lines.append(f'📐 结构：{regime}(置信度{confidence}%)。{macd_summary}')

    # 支撑阻力
    lines.append(f'')
    lines.append(f'📍 BTC 支撑 {s_btc} | 阻力 {r_btc}')
    lines.append(f'📍 ETH 支撑 {s_eth} | 阻力 {r_eth}')

    # 入场/止损/止盈/RR — P1b: 用 ATR 缓冲，区分多空
    try: btc_entry_f = float(btc_p) if btc_p != '?' else 0
    except Exception: btc_entry_f = 0
    try: eth_entry_f = float(eth_p) if eth_p != '?' else 0
    except Exception: eth_entry_f = 0
    
    btc_atr = btc.get('indicators', {}).get('4H', {}).get('atr', btc_entry_f * 0.02)
    eth_atr = eth.get('indicators', {}).get('4H', {}).get('atr', eth_entry_f * 0.02)
    btc_pos = btc.get('position', '观望')
    eth_pos = eth.get('position', '观望')
    
    def _calc_sl_tp_pair(entry, near_s, near_r, atr, pos_label):
        """方向感知 SL/TP 计算"""
        if not entry or not near_s or not near_r or not atr:
            return 0, 0, '?'
        if '空' in str(pos_label):
            sl = int(near_r[0] + atr * 0.5)
            tp = int(near_s[0])
        else:
            sl = int(near_s[0] - atr * 0.5)
            tp = int(near_r[0])
        if entry <= 0 or abs(entry - sl) <= 0:
            return sl, tp, '?'
        rr = abs(tp - entry) / abs(entry - sl)
        return sl, tp, f'{rr:.1f}' if rr > 0 else '?'
    
    btc_sl_val, btc_tp_val, btc_rr_str = _calc_sl_tp_pair(
        btc_entry_f, btc_near_s, btc_near_r, btc_atr, btc_pos)
    eth_sl_val, eth_tp_val, eth_rr_str = _calc_sl_tp_pair(
        eth_entry_f, eth_near_s, eth_near_r, eth_atr, eth_pos)
    
    btc_dir_label = '偏多' if '多' in str(btc_pos) else ('偏空' if '空' in str(btc_pos) else '观望')
    eth_dir_label = '偏多' if '多' in str(eth_pos) else ('偏空' if '空' in str(eth_pos) else '观望')
    
    lines.append(f'')
    # 观望时不输出 SL/TP 行
    if btc_dir_label != '观望' and btc_entry_f and btc_sl_val and btc_tp_val:
        rr_warn_btc = ' ⚠️' if btc_rr_str != '?' and float(btc_rr_str) < 1.5 else ''
        lines.append(f'🎯 BTC {btc_dir_label} | 入场{int(btc_entry_f):,} | 止损{btc_sl_val:,} | 止盈{btc_tp_val:,} | RR 1:{btc_rr_str}{rr_warn_btc}')
    if eth_dir_label != '观望' and eth_entry_f and eth_sl_val and eth_tp_val:
        rr_warn_eth = ' ⚠️' if eth_rr_str != '?' and float(eth_rr_str) < 1.5 else ''
        lines.append(f'🎯 ETH {eth_dir_label} | 入场{eth_entry_f:.0f} | 止损{eth_sl_val} | 止盈{eth_tp_val} | RR 1:{eth_rr_str}{rr_warn_eth}')

    # 结语 — 根据 BTC 方向动态切换
    lines.append(f'')
    if btc_dir_label == '偏空':
        lines.append(f'💬 {regime}共振偏弱，顺势而为不猜底。')
    elif btc_dir_label == '偏多':
        if fg_val and fg_val < 25:
            lines.append(f'💬 FG={fg_val}极度恐惧——历史上这个位置做空的都成了燃料。')
        else:
            lines.append(f'💬 {regime}共振偏强，顺势而为。')
    else:
        if fg_val and fg_val < 25:
            lines.append(f'💬 FG={fg_val}极度恐惧——等确认信号再入场。')
        else:
            lines.append(f'💬 {regime}不打逆风局，共振方向就是最小阻力线。')

    lines.append('')
    lines.append('🤖 五系统AI分析，仅供参考')
    return '\n'.join(lines)

# ── 配图风格自动选择 ──────────────────────────────────────
def select_chart_style(analyses, regime_result):
    """根据分析结果自动选择配图风格"""
    btc = next((a for a in analyses if a['coin'] == 'BTC'), {})
    macd_h_4h = btc.get('macd_h_4h', 0)
    resonance = btc.get('resonance', '')
    composite = regime_result.get('composite_score', 0)

    # 强共振 + 强MACD → Style 1 (营销K线)
    if '强' in str(resonance) and macd_h_4h > 200:
        return 1
    # 多指标共振 → Style 2 (仪表盘)
    if abs(composite) > 15:
        return 2
    # 威科夫结构明显 → Style 3 (结构标注)
    if btc.get('bottom_note', '').startswith('near_bottom') or 'Spring' in str(btc.get('bottom_note', '')):
        return 3
    # 默认 → Style 4 (方形卡片)
    return 4
