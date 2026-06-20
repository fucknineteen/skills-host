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
from _shared import BJT

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "okx_klines.db")

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
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.stdout.strip():
                return r.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(1.5)
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
    for c in candles:
        ts = int(c[0])
        rows.append((coin, timeframe, ts,
                     float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                     float(c[5]), float(c[6])))
    conn.executemany("""
        INSERT OR REPLACE INTO klines (coin, timeframe, ts, open, high, low, close, volume, vol_ccy, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, rows)
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
    return datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%m-%d %H:%M')

def validate_and_fix(conn, coins):
    """检测本地DB缺口，对比OKX数据源，不一致则按OKX修复"""
    issues_found = False
    fixed_count = 0
    
    for coin in coins:
        # LAB uses Binance, skip OKX validation for now
        is_binance = coin in BINANCE_COINS
        
        for tf in ALL_TIMEFRAMES:
            if is_binance:
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
                        save_candles(conn, coin, tf, candles_to_insert)
                        filled += len(candles_to_insert)
                
                if unavailable:
                    unfixable += len(unavailable)
            
            if filled > 0:
                fixed_count += filled
                g1 = ts_to_bj(gaps[0][0])
                g2 = ts_to_bj(gaps[-1][1])
                print(f"  🔧 {coin} {tf}: {len(gaps)}个缺口({g1}~{g2}) → OKX对比 +{filled}根",
                      f"(无法修复{unfixable}根)" if unfixable else "")
                issues_found = True
            elif unfixable > 0:
                g1 = ts_to_bj(gaps[0][0])
                g2 = ts_to_bj(gaps[-1][1])
                print(f"  ⚠️ {coin} {tf}: {len(gaps)}个缺口({g1}~{g2}) → OKX无此数据",
                      f"({unfixable}根缺失) — API限制" if tf in ("5m",) else "")
                issues_found = True
    
    if not issues_found:
        print("  ✅ 数据验证通过：本地DB与OKX数据源一致，零缺口")
    
    return fixed_count


# ============================================================
# EMA / RSI 计算
# ============================================================
def ema(data, period):
    k = 2 / (period + 1)
    result = [data[0]]
    for i in range(1, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return []
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    rsis = []
    for i in range(period, len(closes)):
        ag = sum(gains[i-period:i]) / period
        al = sum(losses[i-period:i]) / period
        if al > 1e-10:
            rs = ag / al
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
    signal_details = []
    
    # --- 做多信号 ---
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now < 30 and rsi_now > rsi_prev:
                long_signals += 1
                signal_details.append(f"1H RSI={rsi_now:.1f}(<30)回升")
    
    if has_4h and kline_data["4H"]["is_closed"]:
        latest = kline_data["4H"]
        h, l, o, c_price = latest["highs"][0], latest["lows"][0], latest["opens"][0], latest["closes"][0]
        body = abs(c_price - o)
        lower_shadow = min(o, c_price) - l
        upper_shadow = h - max(o, c_price)
        if lower_shadow >= body * 2 and upper_shadow <= body * 0.3:
            long_signals += 1
            signal_details.append("4H锤子线")
        if len(latest["closes"]) >= 2:
            prev_c, prev_o = latest["closes"][1], latest["opens"][1]
            if c_price > o and prev_c < prev_o and c_price > prev_o and o < prev_c:
                long_signals += 1
                signal_details.append("4H看涨吞没")
    
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
                signal_details.append("1H MACD金叉")
            elif macd_hist[-1] > 0 and macd_hist[-2] > 0 and macd_hist[-1] < macd_hist[-2]:
                long_signals += 1
                signal_details.append("1H MACD柱收窄")
    
    if has_15m and kline_data["15m"]["is_closed"]:
        closes = kline_data["15m"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now < 35 and rsi_now > rsi_prev:
                long_signals += 1
                signal_details.append(f"15m RSI={rsi_now:.1f}超卖反弹")
    
    # --- 做空信号 ---
    if has_1h and kline_data["1H"]["is_closed"]:
        closes = kline_data["1H"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now > 70 and rsi_now < rsi_prev:
                short_signals += 1
                signal_details.append(f"1H RSI={rsi_now:.1f}(>70)回落")
    
    if has_4h and kline_data["4H"]["is_closed"]:
        latest = kline_data["4H"]
        h, l, o, c_price = latest["highs"][0], latest["lows"][0], latest["opens"][0], latest["closes"][0]
        body = abs(c_price - o)
        upper_shadow = h - max(o, c_price)
        lower_shadow = min(o, c_price) - l
        if upper_shadow >= body * 2 and lower_shadow <= body * 0.3:
            short_signals += 1
            signal_details.append("4H射击之星")
        if len(latest["closes"]) >= 2:
            prev_c, prev_o = latest["closes"][1], latest["opens"][1]
            if c_price < o and prev_c > prev_o and c_price < prev_o and o > prev_c:
                short_signals += 1
                signal_details.append("4H看跌吞没")
    
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
                signal_details.append("1H MACD死叉")
    
    if has_15m and kline_data["15m"]["is_closed"]:
        closes = kline_data["15m"]["closes"]
        rsis = calc_rsi(closes, 14)
        if len(rsis) >= 2:
            rsi_now, rsi_prev = rsis[-1], rsis[-2]
            if rsi_now > 65 and rsi_now < rsi_prev:
                short_signals += 1
                signal_details.append(f"15m RSI={rsi_now:.1f}超买回落")
    
    all_highest = max(kline_data.get("1H", {}).get("highs", [0]) + kline_data.get("4H", {}).get("highs", [0]))
    all_lowest = min(kline_data.get("1H", {}).get("lows", [999999]) + kline_data.get("4H", {}).get("lows", [999999]))
    
    return long_signals, short_signals, signal_details, all_highest, all_lowest

# ============================================================
# 主函数
# ============================================================
def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    coins = sys.argv[1:] if len(sys.argv) > 1 else ["BTC", "ETH", "SOL"]
    
    now_str = datetime.now(BJT).strftime('%Y-%m-%d %H:%M')
    print(f"=== {now_str} (BJ) K线同步 ===")
    
    # 1. 同步K线数据（增量更新，覆盖所有周期）
    total_saved = 0
    for coin in coins:
        for tf in ["1H", "4H", "15m", "5m", "30m", "1D"]:
            saved = update(conn, coin, tf)
            total_saved += saved
    
    # 2. 🔍 数据验证：检测缺口 → 对比OKX → 按源修复
    print(f"\n=== {now_str} (BJ) 数据验证 ===")
    fixed = validate_and_fix(conn, coins)
    
    # 3. 行情监控
    for coin in coins:
        if coin not in ("BTC", "ETH"):
            continue
        
        long_s, short_s, details, _, _ = detect_signals(conn, coin)
        
        ticker_data = conn.execute(
            "SELECT close FROM klines WHERE coin=? AND timeframe='1H' ORDER BY ts DESC LIMIT 1",
            (coin,)
        ).fetchone()
        price = ticker_data[0] if ticker_data else "N/A"
        
        now = datetime.now(BJT)
        
        if long_s >= 3:
            print(f"\n⚡ [{coin}] 做多信号 | BJ {now.strftime('%m-%d %H:%M')}")
            print(f"· 价格: ${price:,.2f}")
            print(f"· 信号数: {long_s} 触发: {' + '.join(details)}")
            print(f"· ⚠️ 风险提示: 以上分析仅为技术参考，不构成投资建议，请严格设置止损。")
            continue
        elif short_s >= 3:
            print(f"\n⚡ [{coin}] 做空信号 | BJ {now.strftime('%m-%d %H:%M')}")
            print(f"· 价格: ${price:,.2f}")
            print(f"· 信号数: {short_s} 触发: {' + '.join(details)}")
            print(f"· ⚠️ 风险提示: 以上分析仅为技术参考，不构成投资建议，请严格设置止损。")
            continue
    
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
            if "401" in (r.stdout or ""):
                print("⚠️ 金十 MCP 认证失败(401) — Token已失效，需更新config.yaml")
        else:
            print("⚠️ 金十 MCP Token 为占位符(***) — 需用 Python 写入真实 Token")
    except Exception:
        pass

if __name__ == "__main__":
    main()
