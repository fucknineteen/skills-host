#!/usr/bin/env python3
"""
Regime Detector v2.7 — Candlestick-Enhanced (TA-Lib 18 patterns)

Usage:
  python3 regime_detector.py              # detect only
  python3 regime_detector.py --update     # detect + update index if regime changed
  python3 regime_detector.py --verbose    # full diagnostic output

Dimensions (backtest-optimized weights, sum=100%):
  1. Price Structure    (8%)  — 30-day path, recovery %, HH/HL pattern
  2. MA Dynamics       (16%)  — EMA(100) primary, EMA(200) slope context
  3. Synthetic FG       (2%)  — price-derived fear/greed (low discrimination)
  4. RSI Path          (12%)  — current RSI, recent extremes, recovery
  5. Volume Pattern    (12%)  — V-reversal vol, recovery vs decline vol
  6. ETH/BTC Ratio      (4%)  — ratio trend, structural baseline
  7. Path Narrative     (0%)  — qualitative pattern recognition (informational)
  8. MACD+ADX/Momentum (22%)  — histogram contraction + ADX trend (MOST DISCRIMINATIVE)
  9. 4H Structure       (4%)  — HH/HL/LH/LL pattern
 10. Historical         (2%)  — 8-dim feature vector (low info content)
 11. Order Flow         (7%)  — funding rate + taker buy/sell ratio
 12. Macro External     (8%)  — FRED data, DXY, VIX, BTC.D, 10Y
 13. Candlestick        (3%)  — TA-Lib 18 candlestick patterns
"""
import sqlite3
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from _shared import BJT as BJ, DB_PATH
from statistics import mean, stdev
import talib
import numpy as np
import time as _time

REGIME_INDEX_PATH = '/root/.hermes/trade_review/regimes/regime_index.json'

# Massive (Polygon.io) cross-verification
try:
    from massive_client import verify_price as massive_price
    MASSIVE_ENABLED = True
except Exception:
    MASSIVE_ENABLED = False



# ============================================================
# Data Layer
# ============================================================

def fetch_daily(coin, days=90):
    """Fetch daily OHLCV for a coin, newest first."""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT close, high, low, volume, ts, open FROM klines "
        "WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT ?",
        (coin, days)
    ).fetchall()
    db.close()
    return rows


def fetch_eth_btc_ratio(days=30):
    """Fetch ETH/BTC daily ratio by joining on timestamp."""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute("""
        SELECT a.close / b.close as ratio, a.ts
        FROM klines a
        JOIN klines b ON a.ts = b.ts
        WHERE a.coin='ETH' AND a.timeframe='1D'
          AND b.coin='BTC' AND b.timeframe='1D'
        ORDER BY a.ts DESC LIMIT ?
    """, (days,)).fetchall()
    db.close()
    return rows


def fetch_4h(coin='BTC', candles=96):
    """Fetch 4H OHLCV for structure analysis. 96 candles = 16 days."""
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT close, high, low, volume, ts FROM klines "
        "WHERE coin=? AND timeframe='4H' ORDER BY ts DESC LIMIT ?",
        (coin, candles)
    ).fetchall()
    db.close()
    return rows


# ============================================================
# Math Utilities
# ============================================================

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[:period]) / period


def ema(values, period):
    """Exponential moving average, newest first."""
    if len(values) < period * 2:
        return sma(values, period)
    k = 2.0 / (period + 1)
    result = sma(values[-period:], period)
    for v in reversed(values[:-period]):
        result = v * k + result * (1 - k)
    return result


def wilder_rsi(closes, period=14):
    """Wilder-smoothed RSI from daily closes (newest first)."""
    if len(closes) < period + 1:
        return None
    # Reverse to chronological order
    ordered = list(reversed(closes[:period + 1]))
    gains, losses = [], []
    for i in range(1, len(ordered)):
        diff = ordered[i] - ordered[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def linear_slope(values, n=20):
    """Linear regression slope over last n points (newest first)."""
    if len(values) < n:
        return 0
    ys = list(reversed(values[:n]))
    xs = list(range(n))
    n_ = float(n)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)
    slope = (n_ * sum_xy - sum_x * sum_y) / (n_ * sum_xx - sum_x * sum_x)
    # Normalize by average price for comparability
    avg_y = sum_y / n_
    return slope / avg_y * 100 if avg_y > 0 else 0


def consecutive_count(values, direction='up'):
    """Count consecutive days of given direction from index 0."""
    count = 0
    for i in range(len(values) - 1):
        if direction == 'up' and values[i] > values[i + 1]:
            count += 1
        elif direction == 'down' and values[i] < values[i + 1]:
            count += 1
        else:
            break
    return count


# ============================================================
# Dimension Scorers — each returns score -100 to +100
#   +100 = strong bull trend signal
#   -100 = strong bear trend signal
#   0 = neutral / ambiguous
# ============================================================

def score_price_structure(btc_rows):
    """
    Dimension 1 (8%): Price Structure
    - 30-day path analysis
    - Recovery % from trough (v2.5: early-stage recovery = bullish signal)
    - HH/HL pattern quality
    """
    closes = [r[0] for r in btc_rows[:30]]
    highs = [r[1] for r in btc_rows[:30]]
    lows = [r[2] for r in btc_rows[:30]]
    
    current = closes[0]
    high_30d = max(highs)
    low_30d = min(lows)
    high_7d = max(highs[:7])
    low_7d = min(lows[:7])
    
    drawdown = (high_30d - low_30d) / high_30d * 100
    recovery = (current - low_30d) / (high_30d - low_30d) * 100 if high_30d != low_30d else 50
    change_7d = (current - closes[6]) / closes[6] * 100 if len(closes) > 6 else 0
    
    up_streak = consecutive_count(closes, 'up')
    down_streak = consecutive_count(closes, 'down')
    
    details = []
    # v2.5: Always include raw data for verification
    details.append(f"price={current:.0f} high30d={high_30d:.0f} low30d={low_30d:.0f}")
    score = 0
    
    # 1a: Drawdown depth (bull correction = -15 to -30%, bear trend = > -30%)
    if drawdown < 10:
        score += 20
        details.append(f"dd={drawdown:.0f}% shallow")
    elif drawdown < 25:
        score += 10
        details.append(f"dd={drawdown:.0f}% typical-correction")
    elif drawdown < 40:
        score -= 15
        details.append(f"dd={drawdown:.0f}% deep")
    else:
        score -= 30
        details.append(f"dd={drawdown:.0f}% severe")
    
    # 1b: Recovery from trough (v2.5: INVERTED — early recovery = bullish signal)
    # Old logic rewarded completed V-reversals, penalized early ones.
    # Backtest showed opposite: weak recoveries predict better forward returns.
    # New logic: early-stage (20-45%) = best signal, late-stage = already priced in.
    if 20 <= recovery <= 45:
        score += 25  # early recovery = most room to run (backtest-verified)
        details.append(f"rec={recovery:.0f}% early-stage ↑")
    elif recovery > 45 and recovery <= 65:
        score += 10  # mid recovery = confirming but partially priced
        details.append(f"rec={recovery:.0f}% mid-recovery")
    elif recovery > 65:
        score += 0  # late recovery = already happened
        details.append(f"rec={recovery:.0f}% late-stage")
    elif recovery > 10:
        score -= 5  # very early, uncertain
        details.append(f"rec={recovery:.0f}% nascent")
    else:
        score -= 20  # no bounce = dead cat risk
        details.append(f"rec={recovery:.0f}% no-bounce")
    
    # 1c: Recent momentum (7-day)
    if change_7d > 5:
        score += 20
        details.append(f"7d={change_7d:+.1f}% strong-up")
    elif change_7d > 2:
        score += 10
        details.append(f"7d={change_7d:+.1f}% mild-up")
    elif change_7d > -2:
        score += 0
        details.append(f"7d={change_7d:+.1f}% flat")
    elif change_7d > -5:
        score -= 10
        details.append(f"7d={change_7d:+.1f}% mild-down")
    else:
        score -= 20
        details.append(f"7d={change_7d:+.1f}% strong-down")
    
    # 1d: Streak direction
    if up_streak >= 4:
        score += 15
        details.append(f"up-streak={up_streak}d")
    elif down_streak >= 4:
        score -= 15
        details.append(f"down-streak={down_streak}d")
    
    return max(-100, min(100, score)), ' | '.join(details)


def score_ma_dynamics(btc_rows):
    """
    Dimension 2 (11%): MA Dynamics
    - EMA(100) as primary: less distorted by 90-110K historical peaks
      (70/200 days >80K → EMA(200)≈SMA(200), no improvement)
    - EMA(200) for long-term slope context
    - 40-week EMA for secular trend check
    """
    closes = [r[0] for r in btc_rows[:200]]
    if len(closes) < 100:
        return 0, "insufficient data for MA"
    
    current = closes[0]
    details = []
    score = 0
    
    # 2a: EMA(100) — primary measure, recent-weighted
    ema100 = ema(closes, 100)
    if ema100 is None:
        ema100 = sma(closes[:100], 100)
    pct_from_100 = (current - ema100) / ema100 * 100
    
    if pct_from_100 > 10:
        score += 25
        details.append(f"vs-EMA100={pct_from_100:+.1f}% well-above")
    elif pct_from_100 > 3:
        score += 15
        details.append(f"vs-EMA100={pct_from_100:+.1f}% above")
    elif pct_from_100 > -3:
        score += 5
        details.append(f"vs-EMA100={pct_from_100:+.1f}% at-MA")
    elif pct_from_100 > -15:
        score += 0
        details.append(f"vs-EMA100={pct_from_100:+.1f}% correction-zone")
    elif pct_from_100 > -25:
        score -= 10
        details.append(f"vs-EMA100={pct_from_100:+.1f}% deep-below")
    else:
        score -= 25
        details.append(f"vs-EMA100={pct_from_100:+.1f}% far-below")
    
    # 2b: EMA(100) slope over 20 days
    if len(closes) > 120:
        ema100_20d_ago = ema(closes[20:120], 100)
        slope_100 = ((ema100 - ema100_20d_ago) / ema100_20d_ago * 100) if ema100_20d_ago else 0
        if slope_100 > 0.5:
            score += 10
            details.append(f"EMA100-slope={slope_100:+.2f}%/20d rising")
        elif slope_100 < -0.5:
            score -= 10
            details.append(f"EMA100-slope={slope_100:+.2f}%/20d falling")
        else:
            details.append(f"EMA100-slope={slope_100:+.2f}%/20d flat")
    
    # 2c: EMA(200) for long-term context only
    if len(closes) >= 200:
        ema200 = ema(closes, 200)
        pct_from_200 = (current - ema200) / ema200 * 100 if ema200 else 0
        # Only use EMA200 slope, not distance (distance polluted by 90-110K)
        ema200_40d_ago = ema(closes[40:240], 200) if len(closes) >= 240 else None
        if ema200_40d_ago:
            slope_200 = ((ema200 - ema200_40d_ago) / ema200_40d_ago * 100)
            if slope_200 < -1.0:
                score -= 10
                details.append(f"EMA200-slope={slope_200:+.2f}%/40d falling")
            elif slope_200 > 1.0:
                score += 5
                details.append(f"EMA200-slope={slope_200:+.2f}%/40d rising")
        # EMA200 distance: informational only, no scoring
        details.append(f"EMA200-ref={pct_from_200:+.1f}%")
    
    # 2d: Weekly secular trend (40-week ≈ 200-day)
    closes_weekly = [closes[i*7] for i in range(min(40, len(closes)//7))]
    if len(closes_weekly) >= 20:
        ema40w = ema(closes_weekly, 40) if len(closes_weekly) >= 40 else sma(closes_weekly, len(closes_weekly))
        if ema40w and current > ema40w:
            score += 10
            details.append(f"above-40w-EMA")
        elif ema40w:
            score -= 10
            details.append(f"below-40w-EMA")
    
    return max(-100, min(100, score)), ' | '.join(details)


def _get_recent_real_fg():
    """Try to read actual Fear & Greed from analyses.json (last 7 days)."""
    try:
        analyses_path = os.path.join(os.path.dirname(REGIME_INDEX_PATH), '..', 'analyses.json')
        if not os.path.exists(analyses_path):
            analyses_path = '/root/.hermes/trade_review/analyses.json'
        with open(analyses_path) as f:
            analyses = json.load(f)
        now = datetime.now(BJ)
        recent_fgs = []
        for a in analyses[-20:]:  # last 20 records
            ts_str = a.get('timestamp', '')
            fg = a.get('macro_external', {}).get('fg_actual')
            if fg is not None and ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str)
                    if (now - dt).days < 7:
                        recent_fgs.append(fg)
                except (ValueError, TypeError):
                    pass
            # Also check coin-level FG
            # NOTE: Coin-level FG lacks a timestamp field, so we cannot
            # guarantee recency. Stale coin data may pollute the average.
            # TODO: request coin FG with per-coin timestamp to prevent stale pollution.
            # ⚠️ DEPRECATED: Consider removing coin-level FG from this check
            # once all coins have timestamped FG in macro_external.
            for coin_data in a.get('analysis', {}).get('coins', {}).values():
                coin_fg = coin_data.get('macro_external', {}).get('fg_actual')
                if coin_fg is not None:
                    recent_fgs.append(coin_fg)
        if recent_fgs:
            return int(mean(recent_fgs[-5:]))  # avg of last 5
    except Exception:
        pass
    return None


def score_synthetic_fg(btc_rows):
    """
    Dimension 3 (15%): Synthetic Fear & Greed proxy
    - Distance from 30-day high
    - Recent volatility
    - Momentum consistency
    Mapped to approximate 0-100 scale (0=extreme fear, 100=extreme greed)
    """
    closes = [r[0] for r in btc_rows[:30]]
    highs = [r[1] for r in btc_rows[:30]]
    
    current = closes[0]
    high_30d = max(highs)
    low_30d_actual = min(r[2] for r in btc_rows[:30])
    
    # Component 1: Distance from 30-day high (0 at high, 100 at low → invert for FG)
    dist_from_high = (high_30d - current) / high_30d * 100
    fg_1 = max(0, 100 - dist_from_high * 5)  # -20% from high → FG=0
    
    # Component 2: Recent win rate (days up in last 10)
    up_days = sum(1 for i in range(min(10, len(closes)-1)) if closes[i] > closes[i+1])
    fg_2 = up_days * 10  # 5 up days = FG 50
    
    # Component 3: Recovery from trough
    trough_recovery = (current - low_30d_actual) / (high_30d - low_30d_actual) * 100 if high_30d != low_30d_actual else 50
    fg_3 = min(100, trough_recovery * 1.2)
    
    synthetic_fg = int((fg_1 * 0.4 + fg_2 * 0.3 + fg_3 * 0.3))
    synthetic_fg = max(5, min(95, synthetic_fg))
    
    # Blend with actual FG from analyses.json if available
    real_fg = _get_recent_real_fg()
    if real_fg is not None:
        # Blend: 60% real FG (if recent), 40% synthetic proxy
        blended_fg = int(real_fg * 0.6 + synthetic_fg * 0.4)
        fg_source = f"blended({real_fg}r+{synthetic_fg}p)"
    else:
        blended_fg = synthetic_fg
        fg_source = f"proxy({synthetic_fg})"
    
    # Map to score: FG < 20 = extreme fear (bearish signal but bounce risk)
    #               FG 20-40 = fear
    #               FG 40-60 = neutral
    #               FG > 60 = greed
    
    details = []
    score = 0
    
    if blended_fg < 15:
        score -= 10
        details.append(f"FG={fg_source} extreme-fear")
    elif blended_fg < 30:
        score += 5
        details.append(f"FG={fg_source} fear-recovering")
    elif blended_fg < 50:
        score += 15
        details.append(f"FG={fg_source} neutral-recovering")
    elif blended_fg < 70:
        score += 20
        details.append(f"FG={fg_source} greed-building")
    else:
        score += 10
        details.append(f"FG={fg_source} extreme-greed")
    
    return max(-100, min(100, score)), ' | '.join(details), blended_fg


def score_rsi_path(btc_rows):
    """
    Dimension 4 (15%): RSI Path
    - Current RSI(14) value
    - Recent extreme zone visits
    - RSI recovery trajectory
    """
    closes = [r[0] for r in btc_rows[:30]]
    rsi = wilder_rsi(closes[:15])  # current RSI from last 15 closes
    if rsi is None:
        return 0, "RSI insufficient data"
    
    # RSI 7 days ago (approximate)
    rsi_7d_ago = wilder_rsi(closes[7:22]) if len(closes) > 22 else None
    
    # RSI 14 days ago  
    rsi_14d_ago = wilder_rsi(closes[14:29]) if len(closes) > 29 else None
    
    details = []
    score = 0
    
    # 4a: Current RSI zone
    if rsi > 70:
        score += 20
        details.append(f"RSI={rsi:.0f} overbought")
    elif rsi > 55:
        score += 15
        details.append(f"RSI={rsi:.0f} bullish-momentum")
    elif rsi > 45:
        score += 10
        details.append(f"RSI={rsi:.0f} neutral-bullish")
    elif rsi > 35:
        score += 0
        details.append(f"RSI={rsi:.0f} neutral-bearish")
    elif rsi > 25:
        score -= 5
        details.append(f"RSI={rsi:.0f} oversold-recovering")
    else:
        score -= 15
        details.append(f"RSI={rsi:.0f} extreme-oversold")
    
    # 4b: RSI recovery (did it come from extremes?)
    # NOTE: score -= 5 (bearish) is intentional here — in a bull-market
    # correction context (the primary regime), bouncing from oversold
    # levels signals the correction may be ending (bearish for correction,
    # implicitly bullish for trend resumption). The overall regime weight
    # handles the net effect.
    if rsi_7d_ago and rsi_14d_ago:
        if rsi_7d_ago < 25 and rsi > 40:
            score -= 5  # came from extreme oversold → bull_correction V-reversal context
            details.append("recovery-from-RSI<25")
        elif rsi_14d_ago < 20:
            score -= 5
            details.append("recovery-from-RSI<20")
    
    return max(-100, min(100, score)), ' | '.join(details), round(rsi, 1)


def score_volume(btc_rows):
    """
    Dimension 5 (15%): Volume Pattern
    - V-reversal volume spike
    - Recovery volume vs decline volume
    - Recent volume trend
    """
    volumes = [r[3] for r in btc_rows[:30]]
    closes = [r[0] for r in btc_rows[:30]]
    
    if len(volumes) < 20:
        return 0, "insufficient volume data"
    
    # Identify V-reversal: find the lowest close in last 14 days
    trough_idx = closes[:14].index(min(closes[:14]))
    
    details = []
    score = 0
    
    # 5a: V-reversal volume spike
    if trough_idx > 0 and trough_idx < 13:
        vol_at_trough = volumes[trough_idx]
        vol_before = volumes[trough_idx + 1:trough_idx + 6]  # days before trough
        avg_vol_before = sum(vol_before) / len(vol_before) if vol_before else vol_at_trough
        spike_ratio = vol_at_trough / avg_vol_before if avg_vol_before > 0 else 1
        
        if spike_ratio > 2.5:
            score += 25  # strong V-reversal volume confirmation
            details.append(f"V-spike={spike_ratio:.1f}x strong")
        elif spike_ratio > 1.5:
            score += 15
            details.append(f"V-spike={spike_ratio:.1f}x moderate")
        elif spike_ratio > 1.0:
            score += 5
            details.append(f"V-spike={spike_ratio:.1f}x weak")
    else:
        details.append("no-clear-V-spike")
    
    # 5b: Recovery volume vs decline volume
    # Split into decline phase (before trough) and recovery phase (after trough)
    if trough_idx > 2:
        decline_vols = volumes[trough_idx:]
        recovery_vols = volumes[:trough_idx]
        if decline_vols and recovery_vols:
            avg_decline = sum(decline_vols) / len(decline_vols)
            avg_recovery = sum(recovery_vols) / len(recovery_vols)
            ratio = avg_recovery / avg_decline if avg_decline > 0 else 1
            
            if ratio > 1.3:
                score += 20  # recovery volume higher = bullish
                details.append(f"recov/decline-vol={ratio:.1f}x bullish")
            elif ratio > 0.8:
                score += 5
                details.append(f"recov/decline-vol={ratio:.1f}x neutral")
            else:
                score -= 10  # recovery volume lower = bearish
                details.append(f"recov/decline-vol={ratio:.1f}x bearish")
    
    # 5c: Volume trend (last 5 days vs prior 5)
    vol_5d = sum(volumes[:5]) / 5
    vol_5_10d = sum(volumes[5:10]) / 5
    if vol_5d > vol_5_10d * 1.3:
        score += 10
        details.append("volume-expanding")
    elif vol_5d < vol_5_10d * 0.7:
        details.append("volume-contracting")
    
    return max(-100, min(100, score)), ' | '.join(details)


def score_eth_btc(ratio_rows):
    """
    Dimension 6 (7%): ETH/BTC Ratio
    - Ratio trend over 14 days for risk appetite
    - v2.3: Structural baseline — distinguishes secular ETH decline from risk-off
      If decline matches long-term trend, it's structural (L2/SOL competition),
      not a risk-off signal → reduce negative weight.
    """
    if len(ratio_rows) < 14:
        return 0, "insufficient ratio data"
    
    ratios = [r[0] for r in ratio_rows[:14]]
    current = ratios[0]
    avg_14d = sum(ratios) / len(ratios)
    change_14d = (ratios[0] - ratios[-1]) / ratios[-1] * 100 if ratios[-1] != 0 else 0
    
    details = []
    score = 0
    
    # 6a: Ratio trend (same as before, but with structural adjustment)
    base_score = 0
    if change_14d > 3:
        base_score = 20
        trend_label = f"+{change_14d:.1f}% risk-on"
    elif change_14d > 1:
        base_score = 10
        trend_label = f"+{change_14d:.1f}% mild-risk-on"
    elif change_14d > -1:
        base_score = 0
        trend_label = f"{change_14d:+.1f}% flat"
    elif change_14d > -3:
        base_score = -10
        trend_label = f"{change_14d:+.1f}% risk-off"
    else:
        base_score = -20
        trend_label = f"{change_14d:+.1f}% strong-risk-off"
    
    # v2.3: Structural baseline check
    # If ETH/BTC has been declining at this rate for months, it's structural
    rows_90d = []
    try:
        with sqlite3.connect(DB_PATH) as db:
            rows_90d = db.execute("""
                SELECT a.close / b.close as ratio
                FROM klines a
                JOIN klines b ON a.ts = b.ts
                WHERE a.coin='ETH' AND a.timeframe='1D'
                  AND b.coin='BTC' AND b.timeframe='1D'
                ORDER BY a.ts DESC LIMIT 90
            """).fetchall()
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
        rows_90d = []
    
    if len(rows_90d) >= 60:
        ratios_90d = [r[0] for r in rows_90d]
        # 90-day linear slope
        n = len(ratios_90d)
        xs = list(range(n))
        ys = list(reversed(ratios_90d))
        n_f = float(n)
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x*y for x, y in zip(xs, ys))
        sum_xx = sum(x*x for x in xs)
        slope_90d = (n_f * sum_xy - sum_x * sum_y) / (n_f * sum_xx - sum_x * sum_x)
        norm_slope_90d = slope_90d / (sum_y / n_f) * 100  # % per day
        
        # Expected 14d change from structural trend
        expected_14d = norm_slope_90d * 14
        
        # Compare: how much of the 14d change is "structural" vs " cyclical"?
        if change_14d < -1 and expected_14d < -0.5:
            # Both showing decline → at least partially structural
            structural_pct = min(abs(expected_14d) / max(abs(change_14d), 0.5), 1.0)
            if structural_pct > 0.6:
                # Most of decline is structural → reduce negative score (conservative: 30% not 50%)
                discount = int(abs(base_score) * 0.3)
                score = base_score + discount
                details.append(f"ETH/BTC={current:.5f} {trend_label} (structural-{structural_pct:.0%})")
            elif structural_pct > 0.3:
                discount = int(abs(base_score) * 0.3)
                score = base_score + discount
                details.append(f"ETH/BTC={current:.5f} {trend_label} (partial-structural)")
            else:
                score = base_score
                details.append(f"ETH/BTC={current:.5f} {trend_label}")
        else:
            score = base_score
            details.append(f"ETH/BTC={current:.5f} {trend_label}")
    else:
        score = base_score
        details.append(f"ETH/BTC={current:.5f} {trend_label}")
    
    # 6b: Position vs 14-day average
    pct_from_avg = (current - avg_14d) / avg_14d * 100
    if pct_from_avg > 2:
        score += 10
        details.append(f"vs-avg={pct_from_avg:+.1f}%")
    
    return max(-100, min(100, score)), ' | '.join(details)


def score_path_narrative(btc_rows):
    """
    Dimension 7 (5%): Path Narrative
    - Qualitative pattern recognition from 30-day price path
    - Identifies: V-shape, staircase-down, flat-range, step-up
    """
    closes = [r[0] for r in btc_rows[:30]]
    highs = [r[1] for r in btc_rows[:30]]
    lows = [r[2] for r in btc_rows[:30]]
    
    high_30d = max(highs)
    low_30d = min(lows)
    current = closes[0]
    
    # Split into two halves: days 15-30 (earlier) and days 0-14 (later)
    first_half_high = max(highs[15:30]) if len(highs) > 15 else high_30d
    first_half_low = min(lows[15:30]) if len(lows) > 15 else low_30d
    second_half_low = min(lows[:15])
    
    details = []
    score = 0
    
    # Pattern detection
    drawdown = (high_30d - low_30d) / high_30d * 100 if high_30d != 0 else 0
    recovery = min(100, (current - second_half_low) / (high_30d - second_half_low) * 100) if high_30d != second_half_low else 50
    
    if drawdown > 15 and recovery > 50:
        # Deep drop + strong recovery = classic V-shape (bull correction)
        score += 30
        details.append("V-shape-recovery classic-bull-correction")
    elif drawdown > 15 and recovery < 30:
        # Deep drop + weak recovery = potential bear trend
        score -= 25
        details.append("deep-drop-weak-bounce bear-risk")
    elif drawdown > 10:
        # Moderate pullback
        if current > first_half_low * 1.05:
            score += 15
            details.append("moderate-pullback-recovering")
        else:
            score -= 10
            details.append("moderate-pullback-stalling")
    else:
        if all(abs(closes[i] - closes[i+1]) / closes[i+1] < 0.03 for i in range(5)):
            score += 0
            details.append("tight-range consolidation")
        else:
            score += 10
            details.append("shallow-pullback healthy")
    
    return max(-100, min(100, score)), ' | '.join(details)


# ============================================================
# Dimension 8 (12%): MACD + ADX Momentum
# ============================================================

def calc_macd_full(closes, fast=12, slow=75, signal=9):
    """Calculate MACD + histogram + 5-day & 10-day trends. Newest first.
    v2.3: Also returns hist_10d for acceleration calculation.
    v2.8: Fixed double data-order bug — ema() expects newest-first, so
    keep closes in newest-first order and don't reverse macd_vals."""
    if len(closes) < slow + signal + 11:
        return None, None, None, None, None
    ordered = closes[:slow + signal + 11]  # keep newest-first for ema()
    macd_vals = []
    for i in range(len(ordered) - slow + 1):
        f = ema(ordered[i:], fast)
        s = ema(ordered[i:], slow)
        if f is not None and s is not None:
            macd_vals.append(f - s)
    if len(macd_vals) < signal + 3:
        return None, None, None, None, None
    macd_recent = macd_vals  # already newest-first
    signal_now = ema(macd_recent, signal)
    if signal_now is None:
        return None, None, None, None, None
    hist_now = macd_recent[0] - signal_now
    # Histogram 5 days ago
    macd_5d_ago = macd_recent[5:]
    signal_5d = ema(macd_5d_ago, signal) if len(macd_5d_ago) >= signal else signal_now
    hist_5d = macd_5d_ago[0] - signal_5d if signal_5d else hist_now
    # Histogram 10 days ago
    macd_10d_ago = macd_recent[10:]
    signal_10d = ema(macd_10d_ago, signal) if len(macd_10d_ago) >= signal else signal_5d
    hist_10d = macd_10d_ago[0] - signal_10d if signal_10d else hist_5d
    # Normalize: divide by price for comparability across time
    avg_price = sum(closes[:slow]) / slow
    hist_norm = hist_now / avg_price * 10000  # basis points
    hist_5d_norm = hist_5d / avg_price * 10000
    hist_10d_norm = hist_10d / avg_price * 10000
    hist_change = hist_norm - hist_5d_norm
    return round(hist_norm, 1), round(hist_5d_norm, 1), round(hist_10d_norm, 1), round(hist_change, 1), macd_recent[0] > 0


def calc_adx_full(highs, lows, closes, period=14):
    """Calculate ADX + DI + 3-day ADX trend. Newest first."""
    if len(closes) < period * 2 + 3:
        return None, None, None, None, None
    ordered_h = list(reversed(highs[:period * 2 + 3]))
    ordered_l = list(reversed(lows[:period * 2 + 3]))
    ordered_c = list(reversed(closes[:period * 2 + 3]))
    
    def _adx_single(h, l, c):
        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(h)):
            tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
            tr_list.append(tr)
            up_move = h[i] - h[i-1]
            down_move = l[i-1] - l[i]
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        atr = sum(tr_list[:period]) / period
        atr_v, pdi_v, ndi_v = [atr], [sum(plus_dm[:period])/period], [sum(minus_dm[:period])/period]
        for i in range(period, len(tr_list)):
            atr = (atr*(period-1)+tr_list[i])/period
            atr_v.append(atr)
            pdi_v.append((pdi_v[-1]*(period-1)+plus_dm[i])/period)
            ndi_v.append((ndi_v[-1]*(period-1)+minus_dm[i])/period)
        dx_list = [abs(p-n)/(p+n)*100 if (p+n)>0 else 0 for p,n in zip(pdi_v, ndi_v)]
        adx = sum(dx_list[-period:])/period
        pdi = pdi_v[-1]/atr_v[-1]*100 if atr_v[-1]>0 else 0
        ndi = ndi_v[-1]/atr_v[-1]*100 if atr_v[-1]>0 else 0
        return adx, pdi, ndi
    
    adx_now, pdi_now, ndi_now = _adx_single(ordered_h, ordered_l, ordered_c)
    adx_3d, _, _ = _adx_single(ordered_h[3:], ordered_l[3:], ordered_c[3:])
    
    return round(adx_now,1), round(pdi_now,1), round(ndi_now,1), round(adx_3d,1), round(adx_now - adx_3d,1)


def score_momentum(btc_rows):
    """
    Dimension 8 (12%): MACD + ADX Momentum (CHANGE-focused)
    
    KEY INSIGHT: In V-reversal scenarios, MACD/ADX are lagging indicators.
    We score based on DIRECTION OF CHANGE, not absolute value:
    - MACD histogram narrowing = bullish (even if still negative)
    - MACD histogram widening = bearish (even if still positive)  
    - ADX falling = trend weakening (reversal signal)
    - ADX rising = trend strengthening (continuation signal)
    - +DI/-DI crossover direction matters more than absolute ADX level
    """
    closes = [r[0] for r in btc_rows[:100]]  # MACD 12/75/9 needs ~95
    highs = [r[1] for r in btc_rows[:100]]
    lows = [r[2] for r in btc_rows[:100]]
    
    hist_now, hist_5d, hist_10d, hist_change, macd_positive = calc_macd_full(closes)
    adx, pdi, ndi, adx_3d, adx_change = calc_adx_full(highs, lows, closes)
    
    details = []
    score = 0
    
    # 8a: MACD histogram CHANGE (most important for early reversal detection)
    # Sign-aware: positive hist shrinking = bullish fading, negative hist shrinking = bearish fading
    if hist_now is not None and hist_change is not None:
        abs_change = abs(hist_change)
        # Direction words: "contracting" (getting closer to zero) vs "expanding" (moving away from zero)
        contracting = (hist_now > 0 and hist_change < 0) or (hist_now < 0 and hist_change > 0)
        expanding = (hist_now > 0 and hist_change > 0) or (hist_now < 0 and hist_change < 0)
        
        if contracting:
            trend_word = 'contracting'
            label_suffix = '↓' if hist_now > 0 else '↑'
        elif expanding:
            trend_word = 'expanding'
            label_suffix = '↑' if hist_now > 0 else '↓'
        else:
            trend_word = 'flat'
            label_suffix = ''
        
        details.append(f"MACD-hist={hist_now:+.0f}bp ({trend_word} {abs_change:.0f}bp/5d)")
        
        # Sign-aware scoring:
        if hist_now > 0 and expanding:
            score += 30
            details[-1] += f" {label_suffix} bull-accelerating"
        elif hist_now > 0 and contracting:
            score -= 10
            details[-1] += f" {label_suffix} bull-fading"
        elif hist_now < 0 and expanding:
            score -= 30
            details[-1] += f" {label_suffix} bear-accelerating"
        elif hist_now < 0 and contracting:
            score += 15
            details[-1] += f" {label_suffix} bear-fading"
        else:
            score += 0
            details[-1] += f" {label_suffix} flat"
        
        # Bonus: MACD line positive (above zero) = underlying bullish structure
        if macd_positive:
            score += 10
            details[-1] += " MACD>0"
        
        # v2.3: Acceleration (2nd derivative) — is the change itself accelerating?
        if hist_10d is not None:
            hist_change_prev = hist_5d - hist_10d  # 1st derivative 5 days ago
            acceleration = hist_change - hist_change_prev  # 2nd derivative
            
            if contracting:
                # Contraction acceleration: direction-aware scoring
                if hist_now > 0:
                    # Bullish hist declining — accelerating decline = more bearish
                    if acceleration < -2:
                        score -= 10
                        details[-1] += " accel↓"
                    elif acceleration > 2:
                        score += 5
                        details[-1] += " accel↑"
                else:
                    # Bearish hist rising — accelerating rise = more bullish
                    if acceleration < -2:
                        score += 10
                        details[-1] += " accel↑"
                    elif acceleration > 2:
                        score -= 5
                        details[-1] += " accel↓"
            elif expanding:
                # Expansion accelerating = trend strengthening
                if abs(acceleration) > 3:
                    if hist_now > 0:
                        score += 5  # bull expansion accelerating
                        details[-1] += " accel↑"
                    else:
                        score -= 5  # bear expansion accelerating
                        details[-1] += " accel↑"
    else:
        details.append("MACD=insufficient-data")
    
    # 8b: ADX CHANGE + DI crossover
    if adx is not None and adx_change is not None:
        di_diff = pdi - ndi
        details.append(f"ADX={adx:.0f}({adx_change:+.0f}/3d) +DI={pdi:.0f} -DI={ndi:.0f}")
        
        # ADX falling = trend exhausting (potential reversal)
        if adx_change < -5:
            score += 20
            details[-1] += " trend-weakening"
        elif adx_change < -2:
            score += 10
            details[-1] += " slightly-weakening"
        elif adx_change > 5:
            # ADX rising strongly = trend continuation
            if di_diff > 0:
                score += 15  # strong uptrend
                details[-1] += " bull-trend strengthening"
            else:
                score -= 15  # strong downtrend
                details[-1] += " bear-trend strengthening"
        elif adx_change > 2:
            if di_diff > 0:
                score += 5
            else:
                score -= 10
        
        # DI crossover proximity
        if abs(di_diff) < 5 and pdi > ndi:
            score += 10
            details[-1] += " near-bullish-crossover"
        elif abs(di_diff) < 5 and ndi > pdi:
            score -= 5
            details[-1] += " near-bearish-crossover"
    else:
        details.append("ADX=insufficient-data")
    
    return max(-100, min(100, score)), ' | '.join(details), {
        'macd_hist_bp': hist_now, 'macd_hist_change': hist_change,
        'adx': adx, 'adx_change': adx_change, 'pdi': pdi, 'ndi': ndi
    }


# ============================================================
# Dimension 9 (8%): 4H Structure (Multi-Timeframe Verification)
# ============================================================

def score_4h_structure(rows_4h):
    """
    Dimension 9 (8%): 4H Structure
    - Check if 4H is confirming or contradicting daily direction
    - Higher highs / higher lows pattern
    - Short-term momentum
    """
    if len(rows_4h) < 24:
        return 0, "insufficient 4H data", {}
    
    closes = [r[0] for r in rows_4h[:48]]
    highs = [r[1] for r in rows_4h[:48]]
    lows = [r[2] for r in rows_4h[:48]]
    volumes = [r[3] for r in rows_4h[:48]]
    
    details = []
    score = 0
    indicators = {}
    
    # 9a: Identify swing highs/lows in 4H
    # Use last 36 candles (6 days) for structure, last 12 (2 days) for recent
    recent_12_high = max(highs[:12])
    recent_12_low = min(lows[:12])
    prior_24_high = max(highs[12:36])
    prior_24_low = min(lows[12:36])
    
    current = closes[0]
    change_24h = (current - closes[5]) / closes[5] * 100 if len(closes) > 5 else 0  # 24h = 6 x 4H
    
    # 9b: Structure comparison
    higher_high = recent_12_high > prior_24_high
    higher_low = recent_12_low > prior_24_low
    lower_high = recent_12_high < prior_24_high
    lower_low = recent_12_low < prior_24_low
    
    if higher_high and higher_low:
        score += 35
        details.append("4H: HH+HL ↑ bullish-structure")
        indicators['4h_structure'] = 'bullish'
    elif lower_high and lower_low:
        score -= 35
        details.append("4H: LH+LL ↓ bearish-structure")
        indicators['4h_structure'] = 'bearish'
    elif higher_high and not higher_low:
        score += 10
        details.append("4H: HH but LL mixed (expanding-range)")
        indicators['4h_structure'] = 'mixed-bullish'
    elif not lower_high and lower_low:
        score -= 10
        details.append("4H: LH but HL mixed (contracting)")
        indicators['4h_structure'] = 'mixed-bearish'
    else:
        score += 0
        details.append("4H: inside-range consolidation")
        indicators['4h_structure'] = 'neutral'
    
    # 9c: Recent 24h momentum
    if change_24h > 2:
        score += 10
        details.append(f"24h={change_24h:+.1f}% up")
    elif change_24h > -2:
        details.append(f"24h={change_24h:+.1f}% flat")
    else:
        score -= 10
        details.append(f"24h={change_24h:+.1f}% down")
    
    # 9d: Volume confirmation on recent 4H candles
    recent_vol = sum(volumes[:6]) / 6
    prior_vol = sum(volumes[6:18]) / 12
    vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 1
    indicators['4h_vol_ratio'] = round(vol_ratio, 2)
    
    if vol_ratio > 1.5 and score > 0:
        score += 10
        details.append(f"4H-vol={vol_ratio:.1f}x confirming-bullish")
    elif vol_ratio > 1.5 and score < 0:
        score -= 10
        details.append(f"4H-vol={vol_ratio:.1f}x confirming-bearish")
    elif vol_ratio < 0.7:
        details.append(f"4H-vol={vol_ratio:.1f}x low-participation")
    else:
        details.append(f"4H-vol={vol_ratio:.1f}x normal")
    
    return max(-100, min(100, score)), ' | '.join(details), indicators


# ============================================================
# Dimension 10 (6%): Historical Analogy
# ============================================================

# Cache: avoid re-scanning DB on every call
_history_cache = None  # stores (current_features_hash, top3_matches)
_history_data_cache = None  # stores (cache_time, all_feats, all_meta, mins, maxs, ranges)
_orderflow_cache = None  # v2.3: cache API results, format: (timestamp, data, ttl_seconds)

def _extract_window_features(closes, highs, lows):
    """8-dim feature vector for a 30-day window. Data is newest-first."""
    peak = max(highs)
    trough = min(lows)
    drawdown = (peak - trough) / peak * 100
    current = closes[0]  # newest close is at index 0
    recovery = (current - trough) / (peak - trough) * 100 if peak != trough else 50
    trough_idx = lows.index(trough)
    days_since = trough_idx  # index 0 = today, so days_since = distance from newest
    rets = [(closes[i] - closes[i+1]) / closes[i+1] * 100 for i in range(len(closes)-1)]
    vol = stdev(rets) if len(rets) > 1 else 0
    n = len(closes)
    xs = list(range(n))
    slope = (n * sum(x*y for x,y in zip(xs,closes)) - sum(xs)*sum(closes)) / (n*sum(x*x for x in xs) - sum(xs)**2)
    norm_slope = slope / mean(closes) * 100
    ma20 = mean(closes[:20])
    vs_ma = (current - ma20) / ma20 * 100
    # Shape: skew of daily returns (negative = crash-like)
    ret_skew = 0
    if len(rets) > 2 and stdev(rets) > 0.01:
        r_mean = mean(rets)
        r_std = stdev(rets)
        ret_skew = mean((r - r_mean)**3 for r in rets) / (r_std**3) if r_std > 0 else 0
    # Shape: recovery/decline speed ratio
    if trough_idx > 0:
        # decline: from peak to trough (trough_idx days ago)
        decline_speed = (peak - trough) / max(trough_idx, 1)
        recovery_speed = (current - trough) / max(days_since + 1, 1) if days_since > 0 else 0
        speed_ratio = recovery_speed / decline_speed if decline_speed > 0 else 0
    else:
        speed_ratio = 0
    return [drawdown, recovery, days_since, vol, norm_slope, vs_ma, ret_skew, speed_ratio]


def _scan_history(current_features):
    """Scan all 30-day windows in DB, return top 3 matches with forward returns.
    Normalizes features to prevent single-dimension dominance.
    Caches results keyed by feature hash to avoid re-scanning."""
    global _history_cache
    
    # Check cache: if same features, return cached result
    feat_key = tuple(round(f, 2) for f in current_features)
    if _history_cache is not None and _history_cache[0] == feat_key:
        return _history_cache[1]
    
    global _history_data_cache

    # Check data cache: reuse pre-computed features if fresh (< 1 hour)
    _now = _time.time()
    if _history_data_cache is not None:
        data_time, all_feats, all_meta, mins, maxs, ranges = _history_data_cache
        if _now - data_time < 3600:
            n_dims = len(current_features)
        else:
            _history_data_cache = None
    
    if _history_data_cache is None:
        # Load all rows once
        db = sqlite3.connect(DB_PATH)
        all_rows = db.execute(
            "SELECT close, high, low, ts FROM klines WHERE coin='BTC' AND timeframe='1D' ORDER BY ts ASC"
        ).fetchall()
        db.close()
        
        # Extract all feature vectors first for normalization
        all_feats = []
        all_meta = []
        for start in range(len(all_rows) - 60):
            wc = [r[0] for r in reversed(all_rows[start:start+30])]
            wh = [r[1] for r in reversed(all_rows[start:start+30])]
            wl = [r[2] for r in reversed(all_rows[start:start+30])]
            feat = _extract_window_features(wc, wh, wl)
            fwd_30d = (all_rows[start+59][0] - all_rows[start+29][0]) / all_rows[start+29][0] * 100
            end_ts = all_rows[start+29][3]
            all_feats.append(feat)
            all_meta.append((end_ts, fwd_30d))
        
        # Min-max normalize each feature dimension
        n_dims = len(current_features)
        mins = [min(f[i] for f in all_feats) for i in range(n_dims)]
        maxs = [max(f[i] for f in all_feats) for i in range(n_dims)]
        ranges = [maxs[i] - mins[i] if maxs[i] != mins[i] else 1 for i in range(n_dims)]
        
        _history_data_cache = (_now, all_feats, all_meta, mins, maxs, ranges)
    
    # Normalize current features
    current_norm = [(current_features[i] - mins[i]) / ranges[i] for i in range(n_dims)]
    
    # Weight: recovery (index 1) + shape features (6,7) most discriminative
    weights = [1.0, 3.0, 1.2, 0.8, 0.5, 0.5, 1.5, 2.0]  # dd, recovery, days, vol, slope, vsMA, skew, speed_ratio
    
    matches = []
    for idx, feat in enumerate(all_feats):
        feat_norm = [(feat[i] - mins[i]) / ranges[i] for i in range(n_dims)]
        dist = sum(w * (a - b)**2 for w, a, b in zip(weights, current_norm, feat_norm)) ** 0.5
        
        # HARD FILTER: recovery must be within 0.3x-2.5x of current recovery
        # This prevents matching windows at completely different recovery stages
        cur_rec = current_features[1]
        match_rec = feat[1]
        if cur_rec > 5 and match_rec > 5:
            rec_ratio = match_rec / cur_rec
            if rec_ratio < 0.3 or rec_ratio > 2.5:
                dist += 100  # effectively disqualify
        
        end_ts, fwd_30d = all_meta[idx]
        matches.append((dist, end_ts, fwd_30d, feat))
    
    matches.sort(key=lambda x: x[0])
    
    # TIME DIVERSITY: skip matches within 60 days of an already-selected one
    top3 = []
    used_months = set()
    for m in matches:
        end_ts = m[1]
        if end_ts:
            dt = datetime.fromtimestamp(end_ts / 1000, tz=BJ)
            month_key = f"{dt.year}-{dt.month:02d}"
        else:
            month_key = str(len(top3))  # fallback
        
        if month_key not in used_months:
            top3.append(m)
            used_months.add(month_key)
        if len(top3) >= 3:
            break
    
    # Cache result
    _history_cache = (feat_key, top3)
    
    return top3


def score_historical_analogy(btc_rows):
    """
    Dimension 10 (6%): Historical Analogy
    - Finds 3 most similar 30-day windows in ~4 years of BTC history
    - Checks what happened 30 days after each match
    - Scores based on forward return distribution
    """
    closes = [r[0] for r in btc_rows[:30]]
    highs = [r[1] for r in btc_rows[:30]]
    lows = [r[2] for r in btc_rows[:30]]
    
    current_features = _extract_window_features(closes, highs, lows)
    top3 = _scan_history(current_features)
    
    if not top3:
        return 0, "no historical matches found", []
    
    # Filter catalyst-driven extremes: if fwd > 25% or < -20%, flag them
    # These are likely macro-event-driven, not pattern-driven
    fwd_returns = [m[2] for m in top3]
    
    # Separate normal vs extreme
    normal_returns = [f for f in fwd_returns if -20 <= f <= 25]
    extreme_count = len(fwd_returns) - len(normal_returns)
    
    if normal_returns:
        avg_fwd = mean(normal_returns)
        bull_count = sum(1 for f in normal_returns if f > 5)
        bear_count = sum(1 for f in normal_returns if f < -5)
        neutral_count = len(normal_returns) - bull_count - bear_count
    else:
        # All extreme — use cautiously
        avg_fwd = mean(fwd_returns)
        bull_count = sum(1 for f in fwd_returns if f > 5)
        bear_count = sum(1 for f in fwd_returns if f < -5)
        neutral_count = 0
    
    details = []
    score = 0
    
    # Score based on NORMAL returns distribution
    if bull_count >= 2:
        score += 35
        details.append(f"2+ bull outcomes")
    elif bull_count == 1 and bear_count == 0:
        score += 20
        details.append(f"1-bull {neutral_count}-neutral")
    elif bear_count >= 2:
        score -= 35
        details.append(f"2+ bear outcomes")
    elif bear_count == 1:
        score -= 15
        details.append(f"1-bear")
    else:
        score += 5
        details.append(f"all-neutral")
    
    if avg_fwd > 5:
        score += 15
        details.append(f"avg-30d-fwd={avg_fwd:+.1f}% bullish")
    elif avg_fwd > 2:
        score += 8
        details.append(f"avg-30d-fwd={avg_fwd:+.1f}% mild-bullish")
    elif avg_fwd > -2:
        score += 0
        details.append(f"avg-30d-fwd={avg_fwd:+.1f}% flat")
    elif avg_fwd > -5:
        score -= 8
        details.append(f"avg-30d-fwd={avg_fwd:+.1f}% mild-bearish")
    else:
        score -= 15
        details.append(f"avg-30d-fwd={avg_fwd:+.1f}% bearish")
    
    # Flag extreme matches
    if extreme_count > 0:
        details.append(f"⚠ {extreme_count} extreme-match(es) excluded from avg")
    
    # Build match summaries with dates
    match_summaries = []
    for dist, ts, fwd, feat in top3:
        dt = datetime.fromtimestamp(ts / 1000, tz=BJ)
        match_summaries.append({
            'date': dt.strftime('%Y-%m-%d'),
            'distance': round(dist, 2),
            'drawdown': round(feat[0], 1),
            'recovery': round(feat[1], 1),
            'forward_30d_pct': round(fwd, 1)
        })
    
    return max(-100, min(100, score)), ' | '.join(details), match_summaries

# ============================================================
# Dimension 11 (7%): Order Flow (NEW v2.3)
# ============================================================

def fetch_orderflow():
    """Fetch OKX order flow data: funding rate trend + taker volume ratio.
    Returns (current_fr_pct, fr_history, taker_ratios, error_str).
    v2.3: Caches results for 5 minutes to avoid repeated API calls.
    """
    global _orderflow_cache

    # Check cache: TTL varies (300s success, 30s error)
    now = _time.time()
    if _orderflow_cache is not None:
        cache_time, cached_data, cache_ttl = _orderflow_cache
        if now - cache_time < cache_ttl:
            return cached_data
    try:
        # Current funding rate
        req_fr = urllib.request.Request(
            'https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP',
            headers={'User-Agent': 'regime-detector/2.3'}
        )
        fr_data = json.loads(urllib.request.urlopen(req_fr, timeout=10).read())
        current_fr = float(fr_data['data'][0]['fundingRate']) * 100  # to %
        
        # Funding rate history (24 periods = 8 days)
        req_frh = urllib.request.Request(
            'https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=24',
            headers={'User-Agent': 'regime-detector/2.3'}
        )
        frh_data = json.loads(urllib.request.urlopen(req_frh, timeout=10).read())
        fr_history = [float(r['realizedRate']) * 100 for r in frh_data['data']]
        
        # Taker volume ratio (last 5 days)
        req_tv = urllib.request.Request(
            'https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy=BTC&instType=CONTRACTS&period=1D&limit=5',
            headers={'User-Agent': 'regime-detector/2.3'}
        )
        tv_data = json.loads(urllib.request.urlopen(req_tv, timeout=10).read())
        
        taker_ratios = []
        for entry in tv_data['data']:
            buy_vol = float(entry[1])
            sell_vol = float(entry[2])
            if sell_vol > 0:
                taker_ratios.append(buy_vol / sell_vol)
        
        result = (current_fr, fr_history, taker_ratios, None)
        _orderflow_cache = (now, result, 300)
        return result
    except Exception as e:
        error_result = (0.0, [], [], str(e)[:80])
        _orderflow_cache = (now, error_result, 30)
        return error_result


def score_orderflow(btc_rows):
    """
    Dimension 11 (7%): Order Flow
    - Funding rate: consistent negative = bullish (short pays long = squeeze fuel)
                    consistent positive = bearish (crowded longs)
    - Funding trend: flipping = uncertain, consistent = conviction
    - Taker buy/sell ratio: >1 = taker buying dominance, <1 = selling dominance
    - Divergence detection: price up but funding negative = strong bullish
    """
    current_fr, fr_history, taker_ratios, error = fetch_orderflow()
    
    if error and not fr_history:
        return 0, f"orderflow-API-error: {error}", {}
    
    details = []
    score = 0
    indicators = {}
    
    # 11a: Current funding rate level
    indicators['funding_rate_pct'] = round(current_fr, 5)
    if current_fr < -0.005:  # strongly negative
        score += 15
        details.append(f"FR={current_fr:+.4f}% deep-negative")
    elif current_fr < -0.001:
        score += 10
        details.append(f"FR={current_fr:+.4f}% negative")
    elif current_fr < 0.001:
        score += 5
        details.append(f"FR={current_fr:+.4f}% near-neutral")
    elif current_fr < 0.005:
        score -= 5
        details.append(f"FR={current_fr:+.4f}% mildly-positive")
    elif current_fr < 0.01:
        score -= 10
        details.append(f"FR={current_fr:+.4f}% positive")
    else:
        score -= 20
        details.append(f"FR={current_fr:+.4f}% crowded-long")
    
    # 11b: Funding rate consistency over last 8 days
    if len(fr_history) >= 12:
        positive_count = sum(1 for r in fr_history[:12] if r > 0)
        negative_count = 12 - positive_count
        avg_fr = sum(fr_history[:12]) / 12
        indicators['fr_pos_ratio'] = round(positive_count / 12, 2)
        indicators['fr_avg_8d'] = round(avg_fr, 5)
        
        # Recent trend (last 3 periods = 24h)
        recent_3 = fr_history[:3]
        recent_flipping = sum(1 for i in range(len(recent_3)-1) 
                             if recent_3[i] * recent_3[i+1] < 0)
        
        if negative_count >= 10:  # very consistent negative
            score += 15
            details.append(f"consistently-negative({negative_count}/12) squeeze-setup")
        elif positive_count >= 10:  # very consistent positive
            score -= 15
            details.append(f"consistently-positive({positive_count}/12) crowded-longs")
        elif negative_count >= 8:
            score += 10
            details.append(f"mostly-negative({negative_count}/12)")
        elif positive_count >= 8:
            score -= 10
            details.append(f"mostly-positive({positive_count}/12)")
        elif recent_flipping >= 2:
            score += 0
            details.append(f"flipping({recent_flipping}/3) uncertain")
        else:
            score += 3
            details.append(f"mixed({positive_count}/12-positive)")
        
        # 11c: Funding divergence with price
        closes = [r[0] for r in btc_rows[:7]]
        if len(closes) >= 7:
            change_7d = (closes[0] - closes[6]) / closes[6] * 100
            # Price up + funding negative = bullish divergence (stronger)
            if change_7d > 2 and avg_fr < -0.001:
                score += 10
                details.append(f"bullish-divergence(price+{change_7d:.1f}%,FR{avg_fr:+.4f}%)")
            # Price down + funding positive = bearish divergence
            elif change_7d < -2 and avg_fr > 0.003:
                score -= 10
                details.append(f"bearish-divergence(price{change_7d:.1f}%,FR{avg_fr:+.4f}%)")
    
    # 11d: Taker buy/sell volume ratio (last 3 days average)
    if taker_ratios and len(taker_ratios) >= 3:
        recent_taker = taker_ratios[:3]
        avg_taker = sum(recent_taker) / len(recent_taker)
        indicators['taker_buy_ratio'] = round(avg_taker, 3)
        
        if avg_taker > 1.1:  # strong buying
            score += 20
            details.append(f"taker-buy/sell={avg_taker:.2f} strong-buying")
        elif avg_taker > 1.03:
            score += 10
            details.append(f"taker-buy/sell={avg_taker:.2f} mild-buying")
        elif avg_taker > 0.97:
            score += 0
            details.append(f"taker-buy/sell={avg_taker:.2f} balanced")
        elif avg_taker > 0.9:
            score -= 10
            details.append(f"taker-buy/sell={avg_taker:.2f} mild-selling")
        else:
            score -= 20
            details.append(f"taker-buy/sell={avg_taker:.2f} strong-selling")
        
        # Taker trend: improving or deteriorating?
        if len(recent_taker) >= 3:
            taker_trend = recent_taker[0] - recent_taker[-1]
            if taker_trend > 0.05:
                score += 5
                details.append(f"taker-improving({taker_trend:+.2f})")
            elif taker_trend < -0.05:
                score -= 5
                details.append(f"taker-deteriorating({taker_trend:+.2f})")
    
    return max(-100, min(100, score)), ' | '.join(details), indicators


# ============================================================
# Dimension 12 (8%): Macro External (NEW v2.6)
# ============================================================

_macro_cache = None  # (timestamp, result_tuple)
_fred_cache = None    # (timestamp, dict)


def fetch_fred_data():
    """Fetch key macro indicators from FRED API.
    Returns dict with ffr, unemployment, spread_10y2y, cpi_latest.
    Cached for 24 hours (monthly data, no benefit from frequent polling).
    """
    global _fred_cache

    now = _time.time()
    if _fred_cache is not None:
        cache_time, cached_result = _fred_cache
        if now - cache_time < 86400:  # 24 hours
            return cached_result
    
    FRED_KEY = os.environ.get('FRED_API_KEY', '')
    if not FRED_KEY:
        return {'ffr': None, 'unemployment': None, 'spread_10y2y': None, 'cpi': None}
    series = {
        'DFF': 'ffr',
        'UNRATE': 'unemployment',
        'T10Y2Y': 'spread_10y2y',
        'CPIAUCSL': 'cpi',
    }
    result = {}
    try:
        import urllib.request, json as _json
        for sid, key in series.items():
            try:
                url = f'https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={FRED_KEY}&limit=2&sort_order=desc&file_type=json'
                data = _json.loads(urllib.request.urlopen(url, timeout=10).read())
                obs = data['observations']
                vals = [o['value'] for o in obs if o['value'] != '.']
                if vals:
                    result[key] = float(vals[0])
                    if key == 'cpi' and len(vals) >= 2:
                        # Compute CPI YoY — need 12-month diff, approximate with MoM×12
                        # For proper YoY we'd need 13 months of data; use simple diff
                        result['cpi_prev'] = float(vals[1])
            except Exception:
                result[key] = None
        
        _fred_cache = (now, result)
        return result
    except Exception:
        return {'ffr': None, 'unemployment': None, 'spread_10y2y': None, 'cpi': None}


def _yahoo_close(symbol, range_d='5d', timeout=8):
    """Fetch latest close price from Yahoo Finance chart API."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_d}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
    closes = [c for c in closes if c is not None]
    if not closes:
        return None, 0
    val = closes[-1]
    change = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0
    return val, change


def fetch_macro_external():
    """Fetch independent macro data: F&G, DXY, BTC.D, VIX, 10Y Yield.
    Returns (fg, fg_label, dxy, dxy_Δ%, btc_dom, vix, vix_Δ%, yield10, yield10_Δbp, error).
    v2.9: Added VIX + 10Y Treasury yield (Yahoo Finance).
    """
    global _macro_cache

    now = _time.time()
    if _macro_cache is not None:
        cache_time, cached_result = _macro_cache
        if now - cache_time < 300:  # 5 minutes
            return cached_result
    
    try:
        import urllib.request, json as _json
        
        # 1. Fear & Greed Index (alternative.me)
        req = urllib.request.Request(
            'https://api.alternative.me/fng/?limit=1',
            headers={'User-Agent': 'regime-detector/2.9'}
        )
        fg_data = _json.loads(urllib.request.urlopen(req, timeout=8).read())
        fg_val = int(fg_data['data'][0]['value'])
        fg_label = fg_data['data'][0]['value_classification']
        
        # 2. DXY (Yahoo Finance)
        dxy, dxy_change = _yahoo_close('DX-Y.NYB')
        
        # 3. BTC Dominance (CoinGecko) — with retry for 429
        cg_url = 'https://api.coingecko.com/api/v3/global'
        btc_dom = None
        for attempt in range(3):
            try:
                req3 = urllib.request.Request(cg_url, headers={'User-Agent': 'Mozilla/5.0'})
                cg_data = _json.loads(urllib.request.urlopen(req3, timeout=8).read())
                btc_dom = cg_data['data']['market_cap_percentage']['btc']
                break
            except Exception:
                if attempt < 2:
                    _time.sleep(1.5 * (attempt + 1))
        if btc_dom is None:
            if _macro_cache and _macro_cache[1][4] is not None:
                btc_dom = _macro_cache[1][4]
        
        # 4. VIX Volatility Index (Yahoo Finance)
        vix, vix_change = _yahoo_close('%5EVIX')  # ^VIX
        
        # 5. 10Y US Treasury Yield (Yahoo Finance, ^TNX)
        yield10, yield10_change_pct = _yahoo_close('%5ETNX')  # ^TNX
        # Convert percentage change to basis points (e.g., 4.00%→4.20% is +20bp, not +5%)
        if yield10 is not None and yield10_change_pct != 0:
            yield10_old = yield10 / (1 + yield10_change_pct / 100)
            yield10_change = (yield10 - yield10_old) * 100  # basis points
        else:
            yield10_change = 0
        
        # 6. FRED macro data (Fed Funds Rate, Unemployment, 10Y-2Y spread, CPI)
        fred = fetch_fred_data()
        ffr = fred.get('ffr')
        unemployment = fred.get('unemployment')
        spread_10y2y = fred.get('spread_10y2y')
        cpi_latest = fred.get('cpi')
        
        result = (fg_val, fg_label, dxy, round(dxy_change, 2),
                  round(btc_dom, 1) if btc_dom is not None else None,
                  round(vix, 1) if vix else None,
                  round(vix_change, 2), round(yield10, 2) if yield10 else None,
                  round(yield10_change, 2),
                  round(ffr, 2) if ffr is not None else None,
                  round(unemployment, 1) if unemployment is not None else None,
                  round(spread_10y2y, 2) if spread_10y2y is not None else None,
                  round(cpi_latest, 1) if cpi_latest is not None else None,
                  None)
        _macro_cache = (now, result)
        return result
    except Exception as e:
        return None, None, None, 0, None, None, 0, None, 0, None, None, None, None, str(e)[:80]


def score_macro_external(btc_rows):
    """
    Dimension 12 (8%): Macro External (v2.9 — 7 data sources)
    Independent data sources not derived from price:
    - F&G Index: <20 extreme fear → bounce fuel
    - DXY: rising = risk-off, falling = risk-on for BTC
    - BTC.D: rising = alt weakness, capital concentration
    - VIX: <15 calm → risk-on, >25 fear → risk-off
    - 10Y Yield: rising = tighter liquidity, risk-off
    - FRED (NEW v2.9): Fed Funds Rate, Unemployment, 10Y-2Y spread, CPI
    """
    fg_val, fg_label, dxy, dxy_change, btc_dom, vix, vix_change, yield10, yield10_change, ffr, unemployment, spread_10y2y, cpi, error = fetch_macro_external()
    
    if error and fg_val is None:
        return 0, f"macro-API-error: {error}", {}
    
    details = []
    score = 0
    indicators = {}
    
    # 12a: Actual Fear & Greed Index
    if fg_val is not None:
        indicators['fg_actual'] = fg_val
        indicators['fg_label'] = fg_label
        
        if fg_val < 15:  # Extreme fear
            score += 15
            details.append(f"F&G={fg_val} extreme-fear (contrarian-bullish)")
        elif fg_val < 25:
            score += 10
            details.append(f"F&G={fg_val} fear")
        elif fg_val < 40:
            score += 5
            details.append(f"F&G={fg_val} fear-neutral")
        elif fg_val < 60:
            score += 0
            details.append(f"F&G={fg_val} neutral")
        elif fg_val < 75:
            score -= 5
            details.append(f"F&G={fg_val} greed")
        else:
            score -= 10
            details.append(f"F&G={fg_val} extreme-greed (crowded)")
    
    # 12b: DXY (US Dollar Index)
    if dxy is not None:
        indicators['dxy'] = dxy
        indicators['dxy_change_pct'] = dxy_change
        
        if dxy > 106:
            score -= 15
            details.append(f"DXY={dxy:.1f} strong-dollar headwind")
        elif dxy > 103:
            score -= 8
            details.append(f"DXY={dxy:.1f} moderately-strong")
        elif dxy > 100:
            score -= 3
            details.append(f"DXY={dxy:.1f} neutral-high")
        elif dxy > 97:
            score += 5
            details.append(f"DXY={dxy:.1f} neutral-low tailwind")
        else:
            score += 12
            details.append(f"DXY={dxy:.1f} weak-dollar tailwind")
        
        if dxy_change < -0.3:
            score += 5
            details.append(f"DXY-Δ={dxy_change:+.2f}% falling-fast")
        elif dxy_change > 0.3:
            score -= 5
            details.append(f"DXY-Δ={dxy_change:+.2f}% rising")
    
    # 12c: BTC Dominance
    if btc_dom is not None:
        indicators['btc_dominance'] = btc_dom
        
        if btc_dom > 62:
            score -= 5
            details.append(f"BTC.D={btc_dom:.1f}% high-risk-off")
        elif btc_dom > 56:
            score -= 3
            details.append(f"BTC.D={btc_dom:.1f}% elevated")
        elif btc_dom > 50:
            score += 3
            details.append(f"BTC.D={btc_dom:.1f}% neutral")
        else:
            score += 8
            details.append(f"BTC.D={btc_dom:.1f}% alt-season risk-on")
    
    # 12d: VIX Volatility Index (NEW v2.9)
    if vix is not None:
        indicators['vix'] = vix
        indicators['vix_change_pct'] = vix_change
        
        if vix > 30:
            score -= 15
            details.append(f"VIX={vix:.1f} panic-fear (risk-off)")
        elif vix > 25:
            score -= 8
            details.append(f"VIX={vix:.1f} elevated-fear")
        elif vix > 20:
            score -= 3
            details.append(f"VIX={vix:.1f} moderate-fear")
        elif vix > 15:
            score += 5
            details.append(f"VIX={vix:.1f} low-vol calm")
        else:
            score += 10
            details.append(f"VIX={vix:.1f} complacency (risk-on)")
        
        if vix_change < -5:
            score += 5
            details.append(f"VIX-Δ={vix_change:+.1f}% falling-fast (calming)")
        elif vix_change > 5:
            score -= 5
            details.append(f"VIX-Δ={vix_change:+.1f}% spiking (fear)")
    
    # 12e: 10Y US Treasury Yield (NEW v2.9)
    if yield10 is not None:
        indicators['yield10'] = yield10
        indicators['yield10_change_pct'] = yield10_change
        
        if yield10 > 5.0:
            score -= 10
            details.append(f"10Y={yield10:.2f}% hawkish-tight")
        elif yield10 > 4.5:
            score -= 5
            details.append(f"10Y={yield10:.2f}% elevated")
        elif yield10 > 4.0:
            score += 0
            details.append(f"10Y={yield10:.2f}% neutral-high")
        elif yield10 > 3.5:
            score += 5
            details.append(f"10Y={yield10:.2f}% moderate-dovish")
        else:
            score += 10
            details.append(f"10Y={yield10:.2f}% dovish (risk-on)")
        
        if yield10_change < -3:
            score += 5
            details.append(f"10Y-Δ={yield10_change:+.0f}bp falling (dovish)")
        elif yield10_change > 3:
            score -= 5
            details.append(f"10Y-Δ={yield10_change:+.0f}bp rising (hawkish)")
    
    # 12f: FRED — Fed Funds Rate (NEW v2.9)
    if ffr is not None:
        indicators['ffr'] = ffr
        
        if ffr > 5.0:
            score -= 10
            details.append(f"FFR={ffr:.2f}% highly-restrictive")
        elif ffr > 4.0:
            score -= 5
            details.append(f"FFR={ffr:.2f}% restrictive")
        elif ffr > 3.0:
            score += 0
            details.append(f"FFR={ffr:.2f}% neutral-high")
        elif ffr > 2.0:
            score += 5
            details.append(f"FFR={ffr:.2f}% accommodative")
        else:
            score += 8
            details.append(f"FFR={ffr:.2f}% dovish (risk-on)")
    
    # 12g: FRED — Unemployment (NEW v2.9)
    if unemployment is not None:
        indicators['unemployment'] = unemployment
        
        if unemployment > 5.5:
            score -= 8
            details.append(f"失业率={unemployment:.1f}% recessionary")
        elif unemployment > 4.5:
            score -= 3
            details.append(f"失业率={unemployment:.1f}% elevated")
        elif unemployment > 3.5:
            score += 3
            details.append(f"失业率={unemployment:.1f}% healthy")
        else:
            score += 5
            details.append(f"失业率={unemployment:.1f}% tight-labor")
    
    # 12h: FRED — 10Y-2Y Spread (NEW v2.9)
    if spread_10y2y is not None:
        indicators['spread_10y2y'] = spread_10y2y
        
        if spread_10y2y < -0.5:
            score -= 12
            details.append(f"10Y-2Y={spread_10y2y:+.2f}% deep-inversion (recession-warning)")
        elif spread_10y2y < 0:
            score -= 6
            details.append(f"10Y-2Y={spread_10y2y:+.2f}% inverted")
        elif spread_10y2y < 0.5:
            score += 2
            details.append(f"10Y-2Y={spread_10y2y:+.2f}% flat-curve")
        elif spread_10y2y < 1.0:
            score += 5
            details.append(f"10Y-2Y={spread_10y2y:+.2f}% normalizing")
        else:
            score += 8
            details.append(f"10Y-2Y={spread_10y2y:+.2f}% steep (risk-on)")
    
    # 12i: FRED — CPI Level (NEW v2.9)
    if cpi is not None:
        indicators['cpi'] = cpi
        # CPI > 330 → elevated; trend is what matters more
        if cpi > 340:
            score -= 5
            details.append(f"CPI={cpi:.1f} elevated-inflation")
        elif cpi > 330:
            score -= 2
            details.append(f"CPI={cpi:.1f} above-trend")
        else:
            score += 3
            details.append(f"CPI={cpi:.1f} moderating")
    
    return max(-100, min(100, score)), ' | '.join(details), indicators


# ============================================================
# Dimension 13 (3%): Candlestick Patterns (TA-Lib, NEW v2.7)
# ============================================================

def score_candlestick_patterns(btc_rows):
    """
    Dimension 13 (3%): Candlestick Pattern Recognition
    Uses TA-Lib to scan last 5 daily candles for reversal/continuation patterns.
    Patterns weighted by recency and type:
    - Reversal patterns (engulfing, hammer, morning star, etc.)
    - Indecision (doji) = neutral with directional bias
    - Each candle can trigger multiple patterns → stronger signal
    """
    closes = np.array([r[0] for r in btc_rows[:10]], dtype=np.float64)
    highs  = np.array([r[1] for r in btc_rows[:10]], dtype=np.float64)
    lows   = np.array([r[2] for r in btc_rows[:10]], dtype=np.float64)
    opens  = np.array([r[5] for r in btc_rows[:10]], dtype=np.float64)
    
    details = []
    score = 0
    
    # Scan last 5 candles (index 0 = most recent)
    # Weight: most recent = 3x, second = 2x, rest = 1x
    recency_weights = [3, 2, 1, 1, 1]
    
    # Key reversal patterns to detect
    # NOTE: CDLHARAMI is a single TA-Lib function that returns positive
    # for bullish harami and negative for bearish harami. Direction is
    # derived from candle_val sign at runtime — see below.
    patterns = {
        # Bullish reversal
        'CDLHAMMER':         ('hammer', 15),
        'CDLINVERTEDHAMMER': ('inv-hammer', 10),
        'CDLMORNINGSTAR':    ('morning-star', 20),
        'CDLMORNINGDOJISTAR':('morning-doji-star', 25),
        'CDLENGULFING':      ('bull-engulf', 20),
        'CDLPIERCING':       ('piercing', 15),
        'CDLHARAMI':         ('harami', 10),
        'CDL3WHITESOLDIERS': ('3-white-soldiers', 30),
        # Bearish reversal
        'CDLSHOOTINGSTAR':   ('shooting-star', -15),
        'CDLEVENINGSTAR':    ('evening-star', -20),
        'CDLEVENINGDOJISTAR':('evening-doji-star', -25),
        'CDLDARKCLOUDCOVER': ('dark-cloud', -15),
        'CDL3BLACKCROWS':    ('3-black-crows', -30),
        'CDLHANGINGMAN':     ('hanging-man', -15),
        # Indecision
        'CDLDOJI':           ('doji', 0),
        'CDLLONGLEGGEDDOJI': ('long-doji', 0),
        'CDLSPINNINGTOP':    ('spinning-top', 0),
    }
    
    found_patterns = []
    
    for i in range(min(5, len(closes) - 3)):
        # Slice for this candle (need context candles before/after)
        idx = i
        o = opens[idx:idx+5][::-1] if idx+5 <= len(opens) else opens[idx:][::-1]
        h = highs[idx:idx+5][::-1] if idx+5 <= len(highs) else highs[idx:][::-1]
        l = lows[idx:idx+5][::-1] if idx+5 <= len(lows) else lows[idx:][::-1]
        c = closes[idx:idx+5][::-1] if idx+5 <= len(closes) else closes[idx:][::-1]
        
        if len(o) < 3:
            continue
        
        for func_name, (label, point_val) in patterns.items():
            try:
                func = getattr(talib, func_name)
                result = func(o, h, l, c)
                if result is None or len(result) == 0:
                    continue
                # Check the candle at position corresponding to current index
                # Use result[-1] for the most recent candle in the slice
                candle_val = result[-1] if len(result) >= 1 else 0
                if candle_val != 0:
                    w = recency_weights[min(i, len(recency_weights)-1)]
                    # Harami direction comes from TA-Lib result sign,
                    # not from the static point_val (which is just magnitude)
                    if func_name == 'CDLHARAMI':
                        is_bullish = candle_val > 0
                        direction = 1 if is_bullish else -1
                        score += abs(point_val) * direction * w
                        found_patterns.append(f"{'bull-' if is_bullish else 'bear-'}{label}(d-{i})")
                    else:
                        is_bullish = point_val > 0
                        score += point_val * w
                        found_patterns.append(f"{label}{'+' if is_bullish else ''}(d-{i})" if point_val != 0 else f"{label}(d-{i})")
            except Exception:
                pass
    
    if found_patterns:
        # Deduplicate: show unique patterns
        unique = list(dict.fromkeys(found_patterns))
        details.append(f"patterns: {', '.join(unique[:6])}")
    else:
        details.append("no clear patterns")
    
    # Clamp score
    score = max(-100, min(100, score))
    
    # Scale: if score is small, it's weak signal → reduce impact
    if abs(score) < 15:
        details.append("weak-signal")
    
    return score, ' | '.join(details), {'patterns': found_patterns}


# ============================================================
# Main Detection Logic
# ============================================================

def detect_regime(verbose=False):
    """
    Multi-dimensional regime detection.
    Returns {regime, confidence, scores, dimensions, overlay, warnings}
    """
    # Fetch data
    btc_rows = fetch_daily('BTC', 200)
    ratio_rows = fetch_eth_btc_ratio(30)
    rows_4h = fetch_4h('BTC', 96)
    
    if len(btc_rows) < 30:
        return {'regime': 'unknown', 'confidence': 0, 'error': 'insufficient BTC data'}
    
    # Score all dimensions (13 dimensions, weights sum to 100)
    # v2.7: Added candlestick patterns (TA-Lib), removed path_narrative weight
    dims = {}
    
    score1, detail1 = score_price_structure(btc_rows)
    dims['price_structure'] = {'score': score1, 'detail': detail1, 'weight': 8}
    
    score2, detail2 = score_ma_dynamics(btc_rows)
    dims['ma_dynamics'] = {'score': score2, 'detail': detail2, 'weight': 16}
    # Include EMA200 for transition warning display
    closes_200 = [r[0] for r in btc_rows[:200]]
    if len(closes_200) >= 200:
        e200 = ema(closes_200, 200)
        if e200:
            dims['ma_dynamics']['ma200'] = round(e200)
    
    score3, detail3, fg = score_synthetic_fg(btc_rows)
    dims['synthetic_fg'] = {'score': score3, 'detail': detail3, 'weight': 2, 'fg_proxy': fg}
    
    score4, detail4, rsi_val = score_rsi_path(btc_rows)
    dims['rsi_path'] = {'score': score4, 'detail': detail4, 'weight': 12, 'rsi': rsi_val}
    
    score5, detail5 = score_volume(btc_rows)
    dims['volume'] = {'score': score5, 'detail': detail5, 'weight': 12}
    
    score6, detail6 = score_eth_btc(ratio_rows)
    dims['eth_btc'] = {'score': score6, 'detail': detail6, 'weight': 4}
    
    score7, detail7 = score_path_narrative(btc_rows)
    dims['path_narrative'] = {'score': score7, 'detail': detail7, 'weight': 0}  # DEAD: weight=0
    
    score8, detail8, mom_data = score_momentum(btc_rows)
    dims['momentum'] = {'score': score8, 'detail': detail8, 'weight': 22}
    if mom_data:
        dims['momentum'].update({k: v for k, v in mom_data.items() if v is not None})
    
    score9, detail9, h4_data = score_4h_structure(rows_4h)
    dims['4h_structure'] = {'score': score9, 'detail': detail9, 'weight': 4}
    if h4_data:
        dims['4h_structure'].update(h4_data)
    
    score10, detail10, history_matches = score_historical_analogy(btc_rows)
    dims['historical_analogy'] = {'score': score10, 'detail': detail10, 'weight': 2}
    if history_matches:
        dims['historical_analogy']['matches'] = history_matches
    
    score11, detail11, of_data = score_orderflow(btc_rows)
    dims['order_flow'] = {'score': score11, 'detail': detail11, 'weight': 7}
    if of_data:
        dims['order_flow'].update(of_data)
    
    score12, detail12, macro_data = score_macro_external(btc_rows)
    dims['macro_external'] = {'score': score12, 'detail': detail12, 'weight': 8}
    if macro_data:
        dims['macro_external'].update(macro_data)
    
    score13, detail13, cdl_data = score_candlestick_patterns(btc_rows)
    dims['candlestick'] = {'score': score13, 'detail': detail13, 'weight': 3}
    if cdl_data:
        dims['candlestick'].update(cdl_data)
    
    # Weighted composite score
    total_score = sum(d['score'] * d['weight'] for d in dims.values()) / 100
    
    # Map composite score to regime
    # The score ranges from -100 (bear_trend) to +100 (bull_trend)
    # But V-reversal patterns score moderate-positive even in correction
    regime, confidence = _score_to_regime(total_score, dims)
    
    # Check event overlay
    overlay = check_event_overlay()
    
    # Generate transition warnings
    closes = [r[0] for r in btc_rows[:30]]
    current_price = closes[0]
    low_30d = min(r[2] for r in btc_rows[:30])
    high_30d = max(r[1] for r in btc_rows[:30])
    
    warnings = []
    # Use 7d range for more actionable thresholds
    high_7d = max(r[1] for r in btc_rows[:7])
    low_7d = min(r[2] for r in btc_rows[:7])
    
    if regime == '牛市回调':
        warnings.append(f"If price breaks below {low_30d:.0f} (30d low) → 熊市趋势")
        # Use 7d high as more realistic bull trigger than 30d high
        bull_trigger = max(high_7d, current_price * 1.03)
        warnings.append(f"If price breaks above {bull_trigger:.0f} (7d high or +3%) with volume → 牛市主升")
        if dims.get('synthetic_fg', {}).get('fg_proxy', 100) is not None and dims['synthetic_fg']['fg_proxy'] < 15:
            warnings.append("Extreme fear proxy: V-reversal risk high, avoid bearish bets")
        if dims['rsi_path'].get('rsi', 50) < 25:
            warnings.append("RSI extreme oversold: bounce probability elevated")
    elif regime == '熊市趋势':
        warnings.append(f"If price recovers above 200MA ({dims['ma_dynamics'].get('ma200','?')}) → 熊市反弹 or 横盘震荡")
    elif regime == '横盘震荡':
        warnings.append(f"Breakout above {high_30d:.0f} → 牛市主升 or 牛市回调")
        warnings.append(f"Breakdown below {low_30d:.0f} → 熊市趋势")
    
    result = {
        'regime': regime,
        'confidence': confidence,
        'composite_score': round(total_score, 1),
        'dimensions': dims,
        'overlay': overlay,
        'transition_warnings': warnings,
        'indicators': {
            'current_price': current_price,
            '30d_high': high_30d,
            '30d_low': low_30d,
            'synthetic_fg': fg,
            'rsi_14': rsi_val,
            'eth_btc_ratio': ratio_rows[0][0] if ratio_rows else None
        }
    }
    
    # Massive (Polygon.io) price cross-verification
    if MASSIVE_ENABLED and current_price > 0:
        try:
            mv_price = massive_price('BTC')
            if mv_price and mv_price > 0:
                mv_diff = round(current_price - mv_price, 2)
                mv_diff_pct = round(mv_diff / mv_price * 100, 3)
                result['massive_verify'] = {
                    'price': mv_price,
                    'okx_price': current_price,
                    'diff': mv_diff,
                    'diff_pct': mv_diff_pct,
                    'status': 'ok' if abs(mv_diff_pct) < 0.5 else 'divergence'
                }
        except Exception:
            pass
    
    return result


def _score_to_regime(total_score, dims):
    """
    Map composite score to regime type with confidence.
    v2.5: Lowered bear thresholds (was -35, now -20 bear_trend).
    Added transition zone for ambiguous composites.
    Uses dimension scores as tie-breakers.
    """
    ps = dims.get('price_structure', {}).get('score', 0)
    ma = dims.get('ma_dynamics', {}).get('score', 0)
    vol = dims.get('volume', {}).get('score', 0)
    narr = dims.get('path_narrative', {}).get('score', 0)
    mom = dims.get('momentum', {}).get('score', 0)
    h4 = dims.get('4h_structure', {}).get('score', 0)
    of_score = dims.get('order_flow', {}).get('score', 0)
    eth = dims.get('eth_btc', {}).get('score', 0)
    macro = dims.get('macro_external', {}).get('score', 0)
    
    # --- v2.5: Regime mapping with lower bear thresholds ---
    if total_score > 45:
        regime, base_conf = '牛市主升', min(90, int(50 + total_score * 0.5))
    elif total_score > 20:
        regime, base_conf = '牛市回调', min(85, int(55 + total_score * 0.7))
    elif total_score > 5:
        # Mild positive — could be weak correction or range with bullish tilt
        if abs(ps) < 30 and abs(mom) < 30 and abs(h4) < 20:
            regime, base_conf = '横盘震荡', int(40 + total_score * 2)
        else:
            regime, base_conf = '牛市回调', int(40 + total_score)
    elif total_score > -10:
        # Transition zone — direction pending
        # v2.6: macro_ext and eth score included in voting
        bullish_votes = sum(1 for d in [ps, ma, vol, mom, of_score, macro] if d > 5)
        bearish_votes = sum(1 for d in [ps, ma, eth, narr, macro, h4] if d < -5)
        if bullish_votes >= 3 and bearish_votes <= 1:
            regime, base_conf = '牛市回调', int(35 + abs(total_score) * 1.5)
        elif bearish_votes >= 3 and bullish_votes <= 1:
            regime, base_conf = '熊市反弹', int(35 + abs(total_score) * 1.5)
        elif abs(ps) < 25 and abs(ma) < 25 and abs(mom) < 25:
            regime, base_conf = '横盘震荡', int(35 + abs(total_score) * 1.5)
        else:
            regime, base_conf = '横盘震荡', int(30 + abs(total_score) * 1.2)
    elif total_score > -20:
        # Bearish transition — could be bear_rally or early bear_trend
        if vol > 5 and mom > 0:
            regime, base_conf = '熊市反弹', int(45 + abs(total_score) * 0.8)
        else:
            regime, base_conf = '熊市趋势', int(40 + abs(total_score) * 0.8)
    elif total_score > -30:
        regime, base_conf = '熊市趋势', int(45 + abs(total_score) * 0.6)
    else:
        regime, base_conf = '熊市趋势', min(90, int(50 + abs(total_score) * 0.5))
    
    # --- v2.3: Dimension dispersion discount ---
    # Weighted standard deviation of dimension scores
    # High dispersion = dimensions disagree → lower confidence
    scores = []
    weights = []
    for d in dims.values():
        scores.append(d['score'])
        weights.append(d['weight'])
    
    w_mean = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    w_var = sum(w * (s - w_mean)**2 for s, w in zip(scores, weights)) / sum(weights)
    w_std = w_var ** 0.5
    
    # Dispersion ratio: std / max_possible_std
    # For scores in [-100, 100], max std ≈ 100 (all at extremes split evenly)
    # Typical: 20-40 = good consensus, 40-60 = moderate disagreement, 60+ = high disagreement
    dispersion = w_std / 100.0  # normalize to [0, 1]
    
    # Discount: high dispersion → lower confidence
    # dispersion < 0.25: minimal discount (0-5%)
    # dispersion 0.25-0.40: moderate discount (5-15%)
    # dispersion 0.40-0.55: significant discount (15-25%)
    # dispersion > 0.55: heavy discount (25-35%)
    if dispersion < 0.25:
        discount = int(dispersion * 20)  # 0-5%
    elif dispersion < 0.40:
        discount = int(5 + (dispersion - 0.25) * 66)  # 5-15%
    elif dispersion < 0.55:
        discount = int(15 + (dispersion - 0.40) * 66)  # 15-25%
    else:
        discount = int(25 + min(dispersion - 0.55, 0.25) * 40)  # 25-35%
    
    confidence = max(10, base_conf - discount)
    
    # --- v2.4: Regime-specific confidence refinement ---
    # Different regimes care about different dimensions
    # This adjusts confidence up if the "right" dimensions agree
    regime_dims = {
        '牛市主升': ['ma_dynamics', 'momentum', 'volume'],      # trend continuation
        '牛市回调': ['momentum', 'volume', 'rsi_path'],     # reversal signals
        '熊市趋势': ['ma_dynamics', 'price_structure', 'eth_btc'], # breakdown
        '熊市反弹': ['momentum', 'rsi_path', 'volume'],          # bounce strength
        '横盘震荡': ['price_structure', '4h_structure', 'order_flow'], # range edges
    }
    
    if regime in regime_dims:
        key_dims = regime_dims[regime]
        present = [d for d in key_dims if d in dims]
        if present:
            # Check if key dimensions agree with regime direction
            if regime == '横盘震荡':
                regime_polarity = 0
            else:
                regime_polarity = 1 if regime in ('牛市主升', '牛市回调', '熊市反弹') else -1
            agreement = 0
            for d in present:
                dim_score = dims[d]['score']
                if regime_polarity > 0 and dim_score > 10:
                    agreement += 1
                elif regime_polarity < 0 and dim_score < -10:
                    agreement += 1
                elif abs(dim_score) < 10:
                    agreement += 0.5  # neutral = partial agreement
            
            if len(present) >= 2:
                agree_ratio = agreement / len(present)
                if agree_ratio >= 0.8:
                    confidence = min(90, confidence + 8)   # strong agreement
                elif agree_ratio >= 0.6:
                    confidence = min(90, confidence + 4)   # moderate agreement
                elif agree_ratio <= 0.2:
                    confidence = max(10, confidence - 5)   # disagreement
    
    return regime, confidence


def check_event_overlay():
    """Check if current time is within CPI/NFP/FOMC pre-48h or post-12h window.
    Reads event dates from regimes/event_dates.json (updated from 金十 news)."""
    now = datetime.now(BJ)
    events_path = os.path.join(os.path.dirname(REGIME_INDEX_PATH), 'event_dates.json')
    
    try:
        with open(events_path, 'r') as f:
            event_data = json.load(f)
        known_events = event_data.get('events', [])
    except (FileNotFoundError, json.JSONDecodeError):
        # Fallback if file missing
        known_events = [
            {'name': 'FOMC', 'date': '2026-06-18', 'time': '02:00'},
            {'name': 'CPI', 'date': '2026-07-15', 'time': '20:30'},
        ]
    
    for event in known_events:
        try:
            event_str = f"{event.get('date', '')} {event.get('time', '')}"
            event_dt = datetime.strptime(event_str, '%Y-%m-%d %H:%M')
            event_dt = event_dt.replace(tzinfo=BJ)
        except (ValueError, KeyError):
            continue
        
        hours_to = (event_dt - now).total_seconds() / 3600
        hours_since = (now - event_dt).total_seconds() / 3600
        
        if 0 <= hours_to <= 48:
            return {'overlay': '事件驱动', 'event': event.get('name', 'unknown'), 'status': 'pre', 'hours_until': round(hours_to, 1)}
        if 0 <= hours_since <= 12:
            return {'overlay': '事件驱动', 'event': event.get('name', 'unknown'), 'status': 'post', 'hours_since': round(hours_since, 1)}
    
    return None


def update_regime_index(result):
    """Update regime_index.json if regime changed."""
    try:
        with open(REGIME_INDEX_PATH, 'r') as f:
            index = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        index = {'regime_history': [], 'regime_transitions': []}
    
    history = index.get('regime_history', [])
    active_regimes = [h for h in history if h.get('status') == 'active']
    
    if active_regimes:
        last_active = active_regimes[-1]
        if last_active['regime'] == result['regime']:
            last_active['last_checked'] = datetime.now(BJ).isoformat()
            last_active['confidence'] = result['confidence']
            index['last_checked'] = datetime.now(BJ).isoformat()
            with open(REGIME_INDEX_PATH, 'w') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            return False
        else:
            # Confidence gate: only switch if confidence >= 60
            if result['confidence'] < 60:
                return False  # don't switch on low confidence
            
            last_active['status'] = 'ended'
            last_active['end_date'] = datetime.now(BJ).strftime('%Y-%m-%d')
            transitions = index.get('regime_transitions', [])
            transitions.append({
                'from_regime': last_active['regime'],
                'to_regime': result['regime'],
                'date': datetime.now(BJ).strftime('%Y-%m-%d'),
                'confidence': result['confidence'],
                'trigger': f"composite={result['composite_score']}, price={result['indicators']['current_price']}"
            })
            index['regime_transitions'] = transitions
    
    # Only add new regime if confidence is sufficient
    if result['confidence'] >= 60:
        new_entry = {
            'regime': result['regime'],
            'overlay': result['overlay'].get('overlay') if result['overlay'] else None,
            'start_date': datetime.now(BJ).strftime('%Y-%m-%d'),
            'end_date': None,
            'status': 'active',
            'confidence': result['confidence'],
            'composite_score': result['composite_score'],
            'description': f"Composite={result['composite_score']}, 13-dim weighted score",
            'entry_conditions': result['indicators'],  # 注: 字段名为entry_conditions但存储的是indicators数据
            'lesson_count': 0,
            'core_lessons': [],
            'transition_watch': result['transition_warnings']
        }
        index['regime_history'].append(new_entry)
    
    index['last_checked'] = datetime.now(BJ).isoformat()
    
    with open(REGIME_INDEX_PATH, 'w') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    
    return True


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    verbose = '--verbose' in sys.argv
    
    result = detect_regime(verbose=verbose)
    
    if verbose:
        print("=" * 60)
        print("REGIME DETECTOR v2.7 — Candlestick-Enhanced")
        print("=" * 60)
        print(f"\n  Regime:     {result['regime']} (confidence: {result['confidence']}%)")
        print(f"  Composite:  {result['composite_score']:+.1f}")
        print(f"  Overlay:    {result['overlay']}")
        print(f"\n  Dimension Scores:")
        for name, d in result['dimensions'].items():
            bar = '█' * (abs(d['score']) // 5)
            sign = '+' if d['score'] >= 0 else ''
            print(f"    {name:20s} [{sign}{d['score']:+.0f}] {bar}  (w={d['weight']}%)")
            print(f"      {d['detail']}")
        
        # v2.6: DATA TRACE — raw computed values for verification
        print(f"\n  📊 DATA TRACE (raw computed values):")
        dims = result['dimensions']
        ps_detail = dims.get('price_structure', {}).get('detail', '')
        print(f"    price_structure: {ps_detail}")
        ma_detail = dims.get('ma_dynamics', {}).get('detail', '')
        print(f"    ma_dynamics:     {ma_detail}")
        fg = dims.get('synthetic_fg', {}).get('fg_proxy', '?')
        print(f"    synthetic_fg:    FG={fg}")
        rsi = dims.get('rsi_path', {}).get('rsi', '?')
        print(f"    rsi_path:        RSI(14)={rsi}")
        mom = dims.get('momentum', {})
        if mom.get('macd_hist_bp') is not None:
            print(f"    momentum:        MACD-hist={mom['macd_hist_bp']}bp Δ={mom.get('macd_hist_change','?')}bp/5d ADX={mom.get('adx','?')} +DI={mom.get('pdi','?')} -DI={mom.get('ndi','?')}")
        of = dims.get('order_flow', {})
        if of.get('funding_rate_pct') is not None:
            print(f"    order_flow:      FR={of['funding_rate_pct']:.5f}% avg8d={of.get('fr_avg_8d','?'):.5f}% taker={of.get('taker_buy_ratio','?')}")
        eth_detail = dims.get('eth_btc', {}).get('detail', '')
        print(f"    eth_btc:         {eth_detail}")
        macro = dims.get('macro_external', {})
        if macro.get('fg_actual') is not None:
            print(f"    macro_external:  F&G={macro.get('fg_actual','?')} ({macro.get('fg_label','?')}) DXY={macro.get('dxy','?')} BTC.D={macro.get('btc_dominance','?')}% VIX={macro.get('vix','?')} 10Y={macro.get('yield10','?')}% FFR={macro.get('ffr','?')}% 失业率={macro.get('unemployment','?')}% 利差={macro.get('spread_10y2y','?')}% CPI={macro.get('cpi','?')}")
        comp = sum(d['score'] * d['weight'] for d in dims.values()) / 100
        print(f"    ─────────────────────────────────────")
        print(f"    composite check: {comp:.1f} (should = {result['composite_score']})")
        
        # Show historical matches if available
        ha = result['dimensions'].get('historical_analogy', {})
        matches = ha.get('matches', [])
        if matches:
            print(f"\n  Historical Matches (top 3 similar 30d windows in 4yr history):")
            for i, m in enumerate(matches):
                print(f"    #{i+1} {m['date']}: dd={m['drawdown']:.1f}% rec={m['recovery']:.1f}% -> 30d later {m['forward_30d_pct']:+.1f}%  (dist={m['distance']:.1f})")
        print(f"\n  Transition Warnings:")
        for w in result['transition_warnings']:
            print(f"    ⚠ {w}")
        print(f"\n  Indicators:")
        for k, v in result['indicators'].items():
            print(f"    {k}: {v}")
        # Massive cross-verification
        mv = result.get('massive_verify')
        if mv:
            icon = 'OK' if mv['status'] == 'ok' else 'WARN'
            print(f"\n  Massive Verify:  {icon} {mv['status']}")
            print(f"    OKX:     ${mv['okx_price']:,.2f}")
            print(f"    Massive: ${mv['price']:,.2f}")
            print(f"    Diff:    {mv['diff']:+.2f} ({mv['diff_pct']:+.3f}%)")
        else:
            print(f"\n  Massive Verify:  disabled or failed")
        print()
    else:
        output = {k: v for k, v in result.items()}
        if '--update' in sys.argv:
            switched = update_regime_index(result)
            output['regime_switched'] = switched
        print(json.dumps(output, ensure_ascii=False, indent=2))
