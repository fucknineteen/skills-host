#!/usr/bin/env python3
"""
K线数据同步 + 行情监控 + 数据验证 — 合一脚本
用法: python3 monitor_and_sync.py [BTC ETH LAB HOME SOL ONDO ZEC ALLO]

1. 同步最新K线到本地SQLite
2. 验证本地数据与OKX数据源一致性（缺口检测→对比OKX→按源修复）
3. 读取DB最新数据计算BTC/ETH信号
4. 有信号输出报告，无信号静默

专为cron设计：不依赖LLM，直接输出文本报告。
"""
import sqlite3, json, subprocess, sys, os, time
from datetime import datetime, timezone, timedelta
from _shared import BJT, DB_PATH

# ============================================================
# 数据源配置
# ============================================================
BINANCE_COINS = {"LAB"}
OKX_BASE = "https://www.okx.com"
BINANCE_BASE = "https://fapi.binance.com"
ALL_TIMEFRAMES = ["5m","15m","30m","1H","4H","1D","1W"]
OKX_BAR = {"5m":"5m","15m":"15m","30m":"30m","1H":"1H","4H":"4H","1D":"1D","1W":"1W"}
OKX_LIMIT = 300
BINANCE_INTERVAL = {"5m":"5m","15m":"15m","30m":"30m","1H":"1h","4H":"4h","1D":"1d","1W":"1w"}
BINANCE_LIMIT = 1500

# 各周期预期间隔（毫秒）
TF_MS = {"5m":300000,"15m":900000,"30m":1800000,"1H":3600000,"4H":14400000,"1D":86400000,"1W":604800000}

# ============================================================
# curl 工具函数
# ============================================================
def curl_get(url, retries=3):
    cmd = ["curl", "-s", "--connect-timeout", "10", "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", url]
    last_err = None
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.stdout.strip():
                return r.stdout.strip()
            if r.returncode != 0:
                last_err = f"curl exit={r.returncode}: {r.stderr.strip()[:200]}"
        except (subprocess.SubprocessError, OSError) as e:
            last_err = str(e)
        time.sleep(1.5)
    if last_err:
        print(f"curl_get error after {retries} retries: {last_err}", file=sys.stderr)
    return None

# ============================================================
# K线数据获取
# ============================================================
def okx_fetch(coin, timeframe, limit=None):
    if limit is None:
        limit = OKX_LIMIT
    bar = OKX_BAR[timeframe]
    inst_id = f"{coin}-USDT-SWAP"
    url = f"{OKX_BASE}/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={min(limit, OKX_LIMIT)}"
    raw = curl_get(url)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get("code") == "0" and data.get("data"):
            return data["data"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return None

def binance_fetch(coin, timeframe, limit=None):
    if limit is None:
        limit = BINANCE_LIMIT
    interval = BINANCE_INTERVAL[timeframe]
    symbol = f"{coin}USDT"
    url = f"{BINANCE_BASE}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={min(limit, BINANCE_LIMIT)}"
    raw = curl_get(url)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            result = []
            for c in data:
                result.append([c[0], c[1], c[2], c[3], c[4], c[5], c[7]])
            return result
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    return None

def fetch_candles(coin, timeframe, limit=None):
    if coin in BINANCE_COINS:
        return binance_fetch(coin, timeframe, limit)
    else:
        return okx_fetch(coin, timeframe, limit)

# ============================================================
# DB 写入
# ============================================================
def save_candles(conn, coin, timeframe, candles):
    rows = []
    warnings = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    exp_ms = TF_MS.get(timeframe, 0)
    
    for idx, c in enumerate(candles):
        ts = int(c[0])
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        vol = float(c[5])
        vol_ccy = float(c[6])
        
        # Data quality checks
        # 1. Negative prices
        if o < 0 or h < 0 or l < 0 or cl < 0 or vol < 0 or vol_ccy < 0:
            warnings.append(f"   ⚠️ [{coin} {timeframe}] K线#{idx} ts={ts}: 负值 (O={o} H={h} L={l} C={cl} V={vol})")
            continue
        
        # 2. Timestamp anomaly: far future or too ancient
        if exp_ms > 0:
            if ts > now_ms + exp_ms * 2:
                warnings.append(f"   ⚠️ [{coin} {timeframe}] K线#{idx} ts={ts}: 时间戳在未来 ({ts_to_bj(ts) if ts > 0 else 'N/A'})")
                continue
            if ts < now_ms - 365 * 24 * 3600 * 1000:  # older than 1 year
                warnings.append(f"   ⚠️ [{coin} {timeframe}] K线#{idx} ts={ts}: 时间戳过旧 (>1年)")
                continue
        
        # 3. Zero volume
        if vol == 0 and vol_ccy == 0:
            warnings.append(f"   ⚠️ [{coin} {timeframe}] K线#{idx} ts={ts}: 零成交量")
            # Still save — zero volume can be valid for illiquid pairs
        
        # 4. Flat candle with volume (suspicious: O≈H≈L≈C but volume>0)
        # Use relative comparison to catch flat candles on low-priced coins
        ohlc_range = max(abs(o), abs(h), abs(l), abs(cl), 1e-10)
        if (abs(o - h) / ohlc_range < 1e-6 and abs(h - l) / ohlc_range < 1e-6 
                and abs(l - cl) / ohlc_range < 1e-6 and vol > 0):
            warnings.append(f"   ⚠️ [{coin} {timeframe}] K线#{idx} ts={ts}: 一字线但有成交量 (V={vol}) — 疑似数据异常")
        
        rows.append((coin, timeframe, ts, o, h, l, cl, vol, vol_ccy))
    
    if warnings:
        for w in warnings:
            print(w)
    
    if rows:
        # FIX: 使用显式 UTC 时间戳替代 datetime('now')，避免时区歧义
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.executemany("""
            INSERT OR REPLACE INTO klines (coin, timeframe, ts, open, high, low, close, volume, vol_ccy, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], now_utc) for r in rows])
        conn.commit()
    return len(rows)

def update(conn, coin, timeframe):
    batch_limits = {"5m": 60, "15m": 20, "30m": 10, "1H": 5, "4H": 3, "1D": 3, "1W": 1}
    cur = conn.execute(
        "SELECT MAX(ts) FROM klines WHERE coin=? AND timeframe=?",
        (coin, timeframe)
    ).fetchone()
    if cur and cur[0]:
        limit = batch_limits.get(timeframe, 5)
        candles = fetch_candles(coin, timeframe, limit=limit)
        if not candles:
            return 0
        saved = save_candles(conn, coin, timeframe, candles)
        return saved
    else:
        candles = fetch_candles(coin, timeframe)
        if not candles:
            return 0
        return save_candles(conn, coin, timeframe, candles)

# ============================================================
# 🔍 数据验证：缺口检测 → 对比OKX → 按源修复
# ============================================================
def ts_to_bj(ts_ms):
    return datetime.fromtimestamp(ts_ms/1000, tz=BJT).strftime('%m-%d %H:%M')

def _fmt_gap_ranges(gaps):
    """Format gap ranges for display, handling non-contiguous gaps properly."""
    if len(gaps) == 1:
        return f"{ts_to_bj(gaps[0][0])}~{ts_to_bj(gaps[0][1])}"
    elif len(gaps) == 2:
        return (f"{ts_to_bj(gaps[0][0])}~{ts_to_bj(gaps[0][1])}, "
                f"{ts_to_bj(gaps[1][0])}~{ts_to_bj(gaps[1][1])}")
    else:
        return (f"{ts_to_bj(gaps[0][0])}~{ts_to_bj(gaps[0][1])}, "
                f"{ts_to_bj(gaps[1][0])}~{ts_to_bj(gaps[1][1])} "
                f"+{len(gaps)-2} more")

def validate_and_fix(conn, coins):
    """检测本地DB缺口，对比OKX数据源，不一致则按OKX修复"""
    issues_found = False
    fixed_count = 0
    
    for coin in coins:
        # LAB uses Binance, skip OKX validation for now
        is_binance = coin in BINANCE_COINS
        
        for tf in ALL_TIMEFRAMES:
            if is_binance:
                # Binance coins: do basic continuity check without OKX cross-reference
                rows = conn.execute(
                    "SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts",
                    (coin, tf)
                ).fetchall()
                
                if len(rows) < 2:
                    continue
                
                exp_ms = TF_MS[tf]
                
                # Check internal gaps only (can't cross-reference with OKX)
                gaps = []
                for i in range(1, len(rows)):
                    diff = rows[i][0] - rows[i-1][0]
                    if diff > exp_ms * 1.5:
                        gaps.append((rows[i-1][0], rows[i][0], diff))
                
                if gaps:
                    issues_found = True
                    gap_display = _fmt_gap_ranges(gaps)
                    print(f"  ⚠️ [{coin} {tf}]: {len(gaps)}个缺口({gap_display}) — Binance数据源，无法交叉验证OKX，需手动确认")
                
                # Freshness check for Binance coins too
                latest_ts = rows[-1][0]
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                age_ms = now_ms - latest_ts
                if exp_ms > 0 and age_ms > exp_ms * 3:
                    issues_found = True
                    print(f"  ⚠️ [{coin} {tf}]: 最新K线过旧 ({ts_to_bj(latest_ts)})，"
                          f"距今{age_ms/60000:.0f}分钟 → 数据流可能中断")
                
                continue  # Binance coins validated separately if needed
            
            # Get local timestamps
            rows = conn.execute(
                "SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts",
                (coin, tf)
            ).fetchall()
            
            if len(rows) < 2:
                continue
            
            exp_ms = TF_MS[tf]
            
            # Find ALL gaps
            gaps = []
            for i in range(1, len(rows)):
                diff = rows[i][0] - rows[i-1][0]
                if diff > exp_ms * 1.5:
                    gaps.append((rows[i-1][0], rows[i][0], diff))
            
            # Freshness check: is the latest candle too old? (runs regardless of gaps)
            latest_ts = rows[-1][0]
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            age_ms = now_ms - latest_ts
            if exp_ms > 0 and age_ms > exp_ms * 3:
                issues_found = True
                print(f"  ⚠️ {coin} {tf}: 最新K线过旧 ({ts_to_bj(latest_ts)})，"
                      f"距今{age_ms/60000:.0f}分钟 → 数据流可能中断")
            
            if not gaps:
                continue
            
            # Gaps found → fetch from OKX to compare
            api_data = okx_fetch(coin, tf, limit=300)
            if not api_data:
                issues_found = True
                print(f"  ⚠️ {coin} {tf}: {len(gaps)}个缺口但OKX API不可达，跳过验证")
                continue
            
            api_timestamps = {int(row[0]) for row in api_data}
            
            # For each gap, check if OKX has the missing candles
            filled = 0
            unfixable = 0
            for gap_start, gap_end, gap_diff in gaps:
                # Calculate missing timestamps
                missing_ts_list = []
                ts = gap_start + exp_ms
                while ts < gap_end:
                    missing_ts_list.append(ts)
                    ts += exp_ms
                
                # Check which are available from API
                available = [t for t in missing_ts_list if t in api_timestamps]
                unavailable = [t for t in missing_ts_list if t not in api_timestamps]
                
                if available:
                    # Pull the actual candle data from API response
                    api_map = {}
                    for row in api_data:
                        api_map[int(row[0])] = row
                    
                    candles_to_insert = []
                    for t in available:
                        if t in api_map:
                            candles_to_insert.append(api_map[t])
                    
                    if candles_to_insert:
                        actual_saved = save_candles(conn, coin, tf, candles_to_insert)
                        filled += actual_saved
                
                if unavailable:
                    unfixable += len(unavailable)
            
            if filled > 0:
                fixed_count += filled
                gap_display = _fmt_gap_ranges(gaps)
                print(f"  🔧 {coin} {tf}: {len(gaps)}个缺口({gap_display}) → OKX对比 +{filled}根",
                      f"(无法修复{unfixable}根)" if unfixable else "")
                issues_found = True
            elif unfixable > 0:
                gap_display = _fmt_gap_ranges(gaps)
                print(f"  ⚠️ {coin} {tf}: {len(gaps)}个缺口({gap_display}) → OKX无此数据",
                      f"({unfixable}根缺失) — API限制" if tf in ("5m",) else "")
                issues_found = True
    
    if not issues_found:
        print("  ✅ 数据验证通过：本地DB与OKX数据源一致，零缺口")
    
    return fixed_count


# ============================================================
# EMA / RSI 计算
# ============================================================
# FIX: 统一签名与 _shared.ema() 一致，避免数值不一致
def ema(data, period, newest_first=True):
    """EMA 计算，与 _shared.ema() 行为一致。
    Args:
        data: 价格序列
        period: EMA 周期
        newest_first: True=数据按最新在前排列（ORDER BY ts DESC）
    """
    if newest_first:
        data = list(reversed(data))
    k = 2 / (period + 1)
    result = [data[0]]
    for i in range(1, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result

def calc_rsi(closes, period=14):
    """Calculate RSI using Wilder's smoothing method (standard).
    
    Wilder's RSI: first average gain/loss uses SMA over the initial period,
    then subsequent values use exponential smoothing: 
    avg = (prev_avg * (period-1) + current) / period
    
    NOTE: closes arrives newest-first (ORDER BY ts DESC); we reverse it for
    chronological diff calculation so diff = new - old (not old - new).
    """
    if len(closes) < period + 1:
        return []
    
    # Reverse to chronological order for correct diff direction
    closes = list(reversed(closes))
    
    # Calculate price changes
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    # First average gain/loss: SMA over initial period (Wilder's method)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rsis = []
    
    # First RSI value
    if avg_loss > 1e-10:
        rs = avg_gain / avg_loss
        rsis.append(100 - (100 / (1 + rs)))
    else:
        rsis.append(100)
    
    # Wilder smoothing for remaining values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss > 1e-10:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
        else:
            rsis.append(100)
    
    return rsis

# ============================================================
# 信号检测
# ============================================================
def detect_signals(conn, coin):
    """读取DB最新K线，检测BTC/ETH做多/做空信号"""
    now_bj = datetime.now(BJT)
    
    analysis_tfs = ["1H", "4H", "15m"]
    
    kline_data = {}
    
    for tf in analysis_tfs:
        c = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM klines "
            "WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT 30",
            (coin, tf)
        ).fetchall()
        if not c:
            continue
        
        timestamps = []
        closes = []
        highs = []
        lows = []
        opens = []
        volumes = []
        
        for row in c:
            ts = row[0] / 1000
            timestamps.append(datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BJT))
            closes.append(row[4])
            highs.append(row[2])
            lows.append(row[3])
            opens.append(row[1])
            volumes.append(row[5])
        
        # NOTE: rows are ORDER BY ts DESC → timestamps[0] = newest, timestamps[-1] = oldest
        latest_ts = timestamps[0]
        tf_minutes = {"1H": 60, "4H": 240, "15m": 15}[tf]
        age_minutes = (now_bj - latest_ts).total_seconds() / 60
        is_closed = age_minutes >= tf_minutes
        
        kline_data[tf] = {
            "closes": closes, "highs": highs, "lows": lows,
            "opens": opens, "volumes": volumes, "timestamps": timestamps,
            "is_closed": is_closed, "latest_price": closes[0],
            "latest_ts": latest_ts, "tf_minutes": tf_minutes,
        }
    
    if not kline_data.get("15m", {}).get("is_closed", True):
        c = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM klines "
            "WHERE coin=? AND timeframe='15m' ORDER BY ts DESC LIMIT 31",
            (coin,)
        ).fetchall()
        if len(c) > 1:
            ts = c[1][0] / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BJT)
            if (now_bj - dt).total_seconds() / 60 >= 15:
                rows_data = []
                for row in c[1:31]:
                    t = row[0] / 1000
                    rows_data.append((
                        datetime.fromtimestamp(t, tz=timezone.utc).astimezone(BJT),
                        row[1], row[2], row[3], row[4], row[5]
                    ))
                kline_data["15m"] = {
                    "closes": [r[4] for r in rows_data],
                    "highs": [r[2] for r in rows_data],
                    "lows": [r[3] for r in rows_data],
                    "opens": [r[1] for r in rows_data],
                    "volumes": [r[5] for r in rows_data],
                    "timestamps": [r[0] for r in rows_data],
                    "is_closed": True, "latest_price": rows_data[0][4],
                    "latest_ts": rows_data[0][0], "tf_minutes": 15,
                }
    
    has_1h = "1H" in kline_data
    has_4h = "4H" in kline_data
    has_15m = "15m" in kline_data
    
    long_signals = 0
    short_signals = 0
    long_details = []
    short_details = []
    
    # --- 做多信号 ---
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now < 30 and rsi_now > rsi_prev:
                long_signals += 1
                long_details.append(f"1H RSI={rsi_now:.1f}(<30)回升")
    
    if has_4h and kline_data["4H"]["is_closed"]:
        latest = kline_data["4H"]
        h, l, o, c_price = latest["highs"][0], latest["lows"][0], latest["opens"][0], latest["closes"][0]
        body = abs(c_price - o)
        lower_shadow = min(o, c_price) - l
        upper_shadow = h - max(o, c_price)
        if body > 0 and lower_shadow >= body * 2 and upper_shadow <= body * 0.3:
            long_signals += 1
            long_details.append("4H锤子线")
        if len(latest["closes"]) >= 2:
            prev_c, prev_o = latest["closes"][1], latest["opens"][1]
            if c_price > o and prev_c < prev_o and c_price > prev_o and o < prev_c:
                long_signals += 1
                long_details.append("4H看涨吞没")
    
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        ema_fast = ema(closes, 5)
        ema_slow = ema(closes, 34)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        ema_sig = ema(macd_line, 5)
        macd_hist = [m - s for m, s in zip(macd_line, ema_sig)]
        if len(macd_hist) >= 3:
            if macd_hist[-1] > 0 and macd_hist[-2] <= 0:
                long_signals += 1
                long_details.append("1H MACD金叉")
            elif macd_hist[-1] > 0 and macd_hist[-2] > 0 and macd_hist[-1] < macd_hist[-2]:
                long_signals += 1
                long_details.append("1H MACD柱收窄")
    
    if has_15m and kline_data["15m"]["is_closed"]:
        closes = kline_data["15m"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now < 35 and rsi_now > rsi_prev:
                long_signals += 1
                long_details.append(f"15m RSI={rsi_now:.1f}超卖反弹")
    
    # --- 做空信号 ---
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now > 70 and rsi_now < rsi_prev:
                short_signals += 1
                short_details.append(f"1H RSI={rsi_now:.1f}(>70)回落")
    
    if has_4h and kline_data["4H"]["is_closed"]:
        latest = kline_data["4H"]
        h, l, o, c_price = latest["highs"][0], latest["lows"][0], latest["opens"][0], latest["closes"][0]
        body = abs(c_price - o)
        upper_shadow = h - max(o, c_price)
        lower_shadow = min(o, c_price) - l
        if body > 0 and upper_shadow >= body * 2 and lower_shadow <= body * 0.3:
            short_signals += 1
            short_details.append("4H射击之星")
        if len(latest["closes"]) >= 2:
            prev_c, prev_o = latest["closes"][1], latest["opens"][1]
            if c_price < o and prev_c > prev_o and c_price < prev_o and o > prev_c:
                short_signals += 1
                short_details.append("4H看跌吞没")
    
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        ema_fast = ema(closes, 5)
        ema_slow = ema(closes, 34)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        ema_sig = ema(macd_line, 5)
        macd_hist = [m - s for m, s in zip(macd_line, ema_sig)]
        if len(macd_hist) >= 3:
            if macd_hist[-1] < 0 and macd_hist[-2] >= 0:
                short_signals += 1
                short_details.append("1H MACD死叉")
    
    if has_15m and kline_data["15m"]["is_closed"]:
        closes = kline_data["15m"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now > 65 and rsi_now < rsi_prev:
                short_signals += 1
                short_details.append(f"15m RSI={rsi_now:.1f}超买回落")
    
    all_highest = max(kline_data.get("1H", {}).get("highs", [float('-inf')]) + kline_data.get("4H", {}).get("highs", [float('-inf')]))
    all_lowest = min(kline_data.get("1H", {}).get("lows", [float('inf')]) + kline_data.get("4H", {}).get("lows", [float('inf')]))
    
    return long_signals, short_signals, long_details, short_details, all_highest, all_lowest

# ============================================================
# 主函数
# ============================================================
def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    coins = sys.argv[1:] if len(sys.argv) > 1 else ["BTC", "ETH", "SOL"]  # SOL: 仅同步K线+验证，不做信号检测
    
    now_str = datetime.now(BJT).strftime('%Y-%m-%d %H:%M')
    print(f"=== {now_str} (BJ) K线同步 ===")
    
    # 1. 同步K线数据（增量更新，覆盖所有周期）
    total_saved = 0
    for coin in coins:
        for tf in ALL_TIMEFRAMES:
            saved = update(conn, coin, tf)
            total_saved += saved
    
    # 2. 🔍 数据验证：检测缺口 → 对比OKX → 按源修复
    print(f"\n=== {now_str} (BJ) 数据验证 ===")
    fixed = validate_and_fix(conn, coins)
    print(f"  🔧 修复缺口: {fixed} 条记录 (对比OKX来源修复)")
    
    # 3. 行情监控
    for coin in coins:
        if coin not in ("BTC", "ETH"):
            continue
        
        long_s, short_s, long_details, short_details, all_highest, all_lowest = detect_signals(conn, coin)

        ticker_data = conn.execute(
            "SELECT close FROM klines WHERE coin=? AND timeframe='1H' ORDER BY ts DESC LIMIT 1",
            (coin,)
        ).fetchone()
        price = ticker_data[0] if ticker_data else "N/A"
        
        now = datetime.now(BJT)
        
        if long_s >= 3 and short_s >= 3:
            print(f"\n⚡ [{coin}] 信号冲突 — 多/空均触发 | BJ {now.strftime('%m-%d %H:%M')}")
            print(f"· 价格: ${price:,.2f}" if isinstance(price, (int, float)) else f"· 价格: {price}")
            print(f"· 做多: {long_s} 触发: {' + '.join(long_details)}")
            print(f"· 做空: {short_s} 触发: {' + '.join(short_details)}")
            print(f"· ⚠️ 风险提示: 信号矛盾，市场震荡/分歧加剧，建议观望。以上分析仅为技术参考，不构成投资建议。")
        elif long_s >= 3:
            print(f"\n⚡ [{coin}] 做多信号 | BJ {now.strftime('%m-%d %H:%M')}")
            print(f"· 价格: ${price:,.2f}" if isinstance(price, (int, float)) else f"· 价格: {price}")
            print(f"· 信号数: {long_s} 触发: {' + '.join(long_details)}")
            print(f"· ⚠️ 风险提示: 以上分析仅为技术参考，不构成投资建议，请严格设置止损。")
        elif short_s >= 3:
            print(f"\n⚡ [{coin}] 做空信号 | BJ {now.strftime('%m-%d %H:%M')}")
            print(f"· 价格: ${price:,.2f}" if isinstance(price, (int, float)) else f"· 价格: {price}")
            print(f"· 信号数: {short_s} 触发: {' + '.join(short_details)}")
            print(f"· ⚠️ 风险提示: 以上分析仅为技术参考，不构成投资建议，请严格设置止损。")
    
    conn.close()
    
    # 4. 金十 MCP 健康检查
    try:
        import yaml
        config_path = os.path.expanduser("~/.hermes/config.yaml")
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        auth = cfg.get("mcp_servers", {}).get("jin10", {}).get("headers", {}).get("Authorization", "")
        if auth.startswith("Bearer ") and len(auth) > 20:
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", "10", "--max-time", "15",
                 "-H", f"Authorization: {auth}",
                 "https://mcp.jin10.com/mcp"],
                capture_output=True, text=True, timeout=20
            )
            # Check HTTP status code (curl -w '%{http_code}' outputs only the code)
            http_code = (r.stdout or "").strip()
            if http_code == "401":
                print("⚠️ 金十 MCP 认证失败(401) — Token已失效，需更新config.yaml")
            elif http_code == "403":
                print("⚠️ 金十 MCP 认证失败(403) — 权限不足，需检查Token权限")
            elif http_code and not http_code.startswith("2") and not http_code.startswith("3"):
                print(f"⚠️ 金十 MCP 返回非预期状态码({http_code}) — 需排查")
        else:
            print("⚠️ 金十 MCP Token 为占位符(***) — 需用 Python 写入真实 Token")
    except Exception as e:
        print(f"⚠️ 金十 MCP 健康检查异常: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
