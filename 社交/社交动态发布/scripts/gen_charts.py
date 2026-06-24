#!/usr/bin/env python3
"""
Multi-style chart generator for social media.
Usage: python3 scripts/gen_charts.py --style {1|2|3|4} [output.png]

Styles:
  1 - 营销K线 (OHLC+量, dark theme, 双币并排)
  2 - 动量仪表盘 (RSI+MACD+趋势强度, 双面板)
  3 - 结构标注 (威科夫Spring/SOS, 支撑阻力带)
  4 - 社交卡片 (极简, 价格+关键位+FG徽章, 方形)
"""
import sqlite3, sys, os, argparse, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from _shared import BJT
import numpy as np

DB = '/root/.hermes/trade_review/okx_klines.db'
UP, DN, HL = '#00e676', '#ff1744', '#ffaa00'
BG = '#060b14'


def _extract_fg(records_or_file):
    """Extract FG value and label from records list or file path.
    Unified helper to avoid duplication between Style 1 and Style 4.
    """
    fg_val, fg_label = '?', ''
    try:
        if isinstance(records_or_file, str) and os.path.exists(records_or_file):
            with open(records_or_file) as af:
                records = json.load(af)
        else:
            records = records_or_file
        
        if not isinstance(records, list):
            return fg_val, fg_label
        
        for r in records:
            if isinstance(r, dict) and r.get('coin') == 'FG':
                fg_val = r.get('fg_val') or '?'
                fg_label = (r.get('fg_label') or '').upper()
                break
    except Exception:
        pass
    return fg_val, fg_label


def _extract_fg_records(records):
    """Extract FG + macro_external data from records list (Style 4 footer)."""
    macro = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        me = r.get('macro_external', {})
        if r.get('coin') == 'FG':
            macro['FG'] = r.get('fg_val', '?')
            macro['FG_LABEL'] = r.get('fg_label', '')
        if me.get('btc_dominance') and 'BTC.D' not in macro:
            macro['BTC.D'] = me['btc_dominance']
        if me.get('dxy') and 'DXY' not in macro:
            macro['DXY'] = me['dxy']
        if me.get('vix') and 'VIX' not in macro:
            macro['VIX'] = me['vix']
    return macro

# ═══════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════

def _get_conn():
    return sqlite3.connect(DB)

def fetch(conn, coin, tf, n):
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM klines WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
        (coin, tf, n)
    ).fetchall()
    if not rows:
        return [], np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
    rows.reverse()
    dates = [datetime.fromtimestamp(r[0]/1000, BJT) for r in rows]
    o = np.array([r[1] for r in rows], dtype=float)
    h = np.array([r[2] for r in rows], dtype=float)
    l = np.array([r[3] for r in rows], dtype=float)
    c = np.array([r[4] for r in rows], dtype=float)
    v = np.array([r[5] for r in rows], dtype=float)
    return dates, o, h, l, c, v

def calc_rsi(closes, period=14):
    if len(closes) < period: return np.full(len(closes), 50)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    rsi = np.full(len(closes), np.nan)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(closes)):
        avg_gain = (avg_gain * (period-1) + gains[i-1]) / period
        avg_loss = (avg_loss * (period-1) + losses[i-1]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi[i] = 100 - (100/(1+rs))
    return rsi

# NOTE: slow=75 is an intentional ultra-long-period MACD for identifying
# macro trend direction on 4H/1H charts (vs standard 26). This provides a
# longer-term momentum context well-suited for swing-trading timeframes.
def calc_macd(closes, fast=12, slow=75, signal=9):
    ema_fast = pd_ema(closes, fast)
    ema_slow = pd_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = pd_ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def pd_ema(data, span):
    ema = np.full(len(data), np.nan)
    if len(data) < span: return ema
    ema[span-1] = np.mean(data[:span])
    multiplier = 2/(span+1)
    for i in range(span, len(data)):
        ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
    return ema

# ═══════════════════════════════════════════
# STYLE 1: Marketing K-line (existing logic)
# ═══════════════════════════════════════════
def style_marketing(out_path):
    conn = _get_conn()
    bd, bo, bh, bl, bc, bv = fetch(conn, 'BTC', '4H', 40)
    ed, eo, eh, el, ec, ev = fetch(conn, 'ETH', '4H', 40)
    
    btc_30d_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("BTC",)).fetchone()
    btc_30d = btc_30d_row[0] if btc_30d_row and btc_30d_row[0] is not None else bc[-1] * 0.9
    btc_7d_h_row = conn.execute(
        "SELECT MAX(high) FROM (SELECT high FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 7)",
        ("BTC",)).fetchone()
    btc_7d_h = btc_7d_h_row[0] if btc_7d_h_row and btc_7d_h_row[0] is not None else bc[-1] * 1.05
    eth_30d_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("ETH",)).fetchone()
    eth_30d = eth_30d_row[0] if eth_30d_row and eth_30d_row[0] is not None else ec[-1] * 0.9
    eth_7d_h_row = conn.execute(
        "SELECT MAX(high) FROM (SELECT high FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 7)",
        ("ETH",)).fetchone()
    eth_7d_h = eth_7d_h_row[0] if eth_7d_h_row and eth_7d_h_row[0] is not None else ec[-1] * 1.05
    
    SPRING, RES = int(btc_30d), int(btc_7d_h)
    SUP = int(bc[-1] * 0.992)
    E_SPRING, E_RES = int(eth_30d), int(eth_7d_h)
    E_SUP = int(ec[-1] * 0.988)
    
    fig = plt.figure(figsize=(18, 11), facecolor=BG)
    gs = fig.add_gridspec(2, 2, height_ratios=[3.8, 1], hspace=0.06, wspace=0.16,
        left=0.05, right=0.97, top=0.91, bottom=0.08)
    ax_b = fig.add_subplot(gs[0, 0]); ax_e = fig.add_subplot(gs[0, 1])
    ax_bv = fig.add_subplot(gs[1, 0]); ax_ev = fig.add_subplot(gs[1, 1])
    
    fig.suptitle('BTC + ETH  4H  |  SPRING→BREAKOUT', color='#ccddee', fontsize=15, y=0.97, fontweight='bold')
    
    def plot_coin(ax, vax, dates, o, h, l, c, v, coin, price, spring, res, sup):
        w = 0.04
        for i in range(len(dates)):
            color = UP if c[i] >= o[i] else DN
            ax.plot([dates[i], dates[i]], [l[i], h[i]], color=color, lw=1.0, alpha=0.88, zorder=2)
            bh_bar = max(abs(c[i]-o[i]), abs(price*0.0003))
            by_val = min(o[i], c[i])
            dn = mdates.date2num(dates[i])
            ax.add_patch(plt.Rectangle((dn-w/2, by_val), w, bh_bar, facecolor=color, edgecolor='none', zorder=3, alpha=0.85))
        
        y_min, y_max = min(min(l), spring*0.98), max(max(h), res*1.03)
        pad = (y_max-y_min)*0.05
        ax.set_xlim(dates[0]-timedelta(hours=2), dates[-1]+timedelta(hours=8))
        ax.set_ylim(y_min-pad, y_max+pad)
        
        ax.axhspan(spring*0.992, spring*1.02, alpha=0.15, color=UP, zorder=0)
        ax.text(dates[2], spring*0.99, f'SPRING ${spring:,}', color=UP, fontsize=7, fontweight='bold', va='top')
        ax.axhspan(res*0.992, res*1.008, alpha=0.15, color=HL, zorder=0)
        ax.text(dates[-1]+timedelta(hours=1), res, f'RESIST ${res:,}', color=HL, fontsize=7, fontweight='bold', va='bottom')
        
        change = (price - o[-1]) / o[-1] * 100
        cf = UP if price > o[-1] else DN
        ax.text(0.02, 0.95, f'${price:,.0f}', transform=ax.transAxes, fontsize=18, color=cf, fontweight='bold', va='top', fontfamily='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#060b14cc', edgecolor='#1a2a3a', alpha=0.85))
        ax.text(0.02, 0.77, f'{change:+.1f}%', transform=ax.transAxes, fontsize=9, color=cf, va='top', fontfamily='monospace', alpha=0.8)
        
        ax.set_facecolor(BG); ax.tick_params(colors='#445566', labelsize=7.5)
        ax.grid(True, alpha=0.05, color='#ffffff')
        for s in ax.spines.values(): s.set_color('#0d1f33')
        ax.set_ylabel(f'{coin}/USDT', color='#8899aa', fontsize=10, fontweight='bold')
        
        vcolors = [UP if c[i]>=o[i] else DN for i in range(len(dates))]
        vax.bar(dates, v, width=0.04, color=vcolors, alpha=0.55, zorder=2)
        vax.set_facecolor(BG); vax.tick_params(colors='#445566', labelsize=6.5)
        vax.grid(True, alpha=0.05, color='#ffffff')
        for s in vax.spines.values(): s.set_color('#0d1f33')
    
    plot_coin(ax_b, ax_bv, bd, bo, bh, bl, bc, bv, 'BTC', bc[-1], SPRING, RES, SUP)
    plot_coin(ax_e, ax_ev, ed, eo, eh, el, ec, ev, 'ETH', ec[-1], E_SPRING, E_RES, E_SUP)
    
    for ax in [ax_b, ax_e, ax_bv, ax_ev]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d', tz=BJT))
        ax.xaxis.set_major_locator(mdates.DayLocator(tz=BJT))
    plt.setp(ax_b.get_xticklabels(), visible=False); plt.setp(ax_e.get_xticklabels(), visible=False)
    plt.setp(ax_bv.get_xticklabels(), rotation=0, ha='center', fontsize=6.5, color='#556677')
    plt.setp(ax_ev.get_xticklabels(), rotation=0, ha='center', fontsize=6.5, color='#556677')
    
    # 从真实数据构建徽章
    fg_val, fg_label = _extract_fg('/root/.hermes/trade_review/social_analyses.json')
    if fg_val == '?':
        fg_val, fg_label = _extract_fg('/root/.hermes/trade_review/analyses.json')
    fg_text = f'FG: {fg_val} {fg_label}' if fg_val != '?' else 'FG: ?'
    up_row = conn.execute(
        "SELECT COUNT(*) FROM (SELECT open,close FROM klines WHERE coin='BTC' AND timeframe='1D' ORDER BY ts DESC LIMIT 10) WHERE close>open"
    ).fetchone()
    up_days = up_row[0] if up_row else 5
    breached = 'BREAKOUT' if bc[-1] > RES else 'RANGE-BOUND'
    badges = [
        (fg_text, '#ff6666'),
        (f'{up_days}/10 UP DAYS', '#00e676'),
        (breached, '#ffaa00'),
    ]
    for i, (text, color) in enumerate(badges):
        fig.text(0.97-i*0.15, 0.015, text, fontsize=7, color=color, fontweight='bold', fontfamily='monospace', ha='right',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor=f'{color}15', edgecolor=f'{color}33', alpha=0.6))
    
    conn.close()
    plt.savefig(out_path, dpi=180, facecolor=BG, edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"SAVED (style 1): {out_path}")


# ═══════════════════════════════════════════
# STYLE 2: Momentum Dashboard
# ═══════════════════════════════════════════
def style_dashboard(out_path):
    conn = _get_conn()
    bd, bo, bh, bl, bc, bv = fetch(conn, 'BTC', '4H', 100)
    ed, eo, eh, el, ec, ev = fetch(conn, 'ETH', '4H', 100)
    
    btc_rsi = calc_rsi(bc, 14)
    eth_rsi = calc_rsi(ec, 14)
    btc_macd, btc_sig, btc_hist = calc_macd(bc, 12, 75, 9)
    eth_macd, eth_sig, eth_hist = calc_macd(ec, 12, 75, 9)
    
    fig = plt.figure(figsize=(18, 12), facecolor=BG)
    gs = fig.add_gridspec(6, 2, height_ratios=[2.5,1,0.8,1,0.8,0.4], hspace=0.3, wspace=0.2,
        left=0.06, right=0.97, top=0.93, bottom=0.05)
    
    title = f'BTC + ETH  Momentum Dashboard  |  RSI(14) + MACD(12/75/9)  |  BJ {datetime.now(BJT).strftime("%m-%d %H:%M")}'
    fig.suptitle(title, color='#ccddee', fontsize=13, y=0.98, fontweight='bold')
    
    def plot_momentum(ax_p, ax_r, ax_m, dates, c, rsi, macd, sig, hist, coin, price):
        # Price subplot
        ax_p.plot(dates, c, color='#448aff', lw=1.8, zorder=2)
        ax_p.fill_between(dates, c, c[0], where=(c>=c[0]), color=f'{UP}22', alpha=0.3)
        ax_p.fill_between(dates, c, c[0], where=(c<c[0]), color=f'{DN}22', alpha=0.3)
        ax_p.set_facecolor(BG); ax_p.tick_params(colors='#556677', labelsize=7)
        ax_p.grid(True, alpha=0.05, color='#fff')
        for s in ax_p.spines.values(): s.set_color('#0d1f33')
        ax_p.text(0.01, 0.93, f'{coin} ${price:,.0f}', transform=ax_p.transAxes, fontsize=14, color=UP if c[-1]>c[-2] else DN, fontweight='bold', fontfamily='monospace')
        
        # RSI subplot
        ax_r.plot(dates, rsi, color=HL, lw=1.2, zorder=2)
        ax_r.axhline(70, color=DN, lw=0.6, ls='--', alpha=0.5)
        ax_r.axhline(30, color=UP, lw=0.6, ls='--', alpha=0.5)
        ax_r.fill_between(dates, 30, 70, alpha=0.03, color='#ffffff')
        ax_r.set_ylim(0, 100)
        ax_r.set_facecolor(BG); ax_r.tick_params(colors='#445566', labelsize=6.5)
        ax_r.grid(True, alpha=0.05, color='#fff')
        for s in ax_r.spines.values(): s.set_color('#0d1f33')
        rsi_val = rsi[-1]
        rsi_display = f'{rsi_val:.0f}' if (rsi_val is not None and not (isinstance(rsi_val, float) and rsi_val != rsi_val)) else 'N/A'
        ax_r.text(0.01, 0.85, f'RSI({len(c)}): {rsi_display}', transform=ax_r.transAxes, fontsize=8, color=HL if (isinstance(rsi_val, (int, float)) and 30<rsi_val<70) else DN, fontfamily='monospace')
        
        # MACD subplot
        colors_hist = [UP if h>=0 else DN for h in hist[-len(dates):]]
        ax_m.bar(dates, hist[-len(dates):], width=0.04, color=colors_hist, alpha=0.6, zorder=2)
        ax_m.plot(dates, macd[-len(dates):], color='#448aff', lw=1.0, alpha=0.8, zorder=3, label='MACD')
        ax_m.plot(dates, sig[-len(dates):], color=HL, lw=0.8, alpha=0.8, zorder=3, label='Signal')
        ax_m.set_facecolor(BG); ax_m.tick_params(colors='#445566', labelsize=6.5)
        ax_m.grid(True, alpha=0.05, color='#fff')
        for s in ax_m.spines.values(): s.set_color('#0d1f33')
        ax_m.legend(loc='upper left', fontsize=6, framealpha=0.3, facecolor=BG, edgecolor='#1a2a3a', labelcolor='#8899aa')
        
        for ax in [ax_p, ax_r, ax_m]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d', tz=BJT))
            ax.xaxis.set_major_locator(mdates.DayLocator(tz=BJT))
        plt.setp(ax_p.get_xticklabels(), visible=False)
        plt.setp(ax_r.get_xticklabels(), visible=False)
    
    ax_bp = fig.add_subplot(gs[0,0]); ax_br = fig.add_subplot(gs[1,0]); ax_bm = fig.add_subplot(gs[2,0])
    ax_ep = fig.add_subplot(gs[0,1]); ax_er = fig.add_subplot(gs[1,1]); ax_em = fig.add_subplot(gs[2,1])
    
    plot_momentum(ax_bp, ax_br, ax_bm, bd, bc, btc_rsi[-len(bd):], btc_macd, btc_sig, btc_hist, 'BTC', bc[-1])
    plot_momentum(ax_ep, ax_er, ax_em, ed, ec, eth_rsi[-len(ed):], eth_macd, eth_sig, eth_hist, 'ETH', ec[-1])
    
    conn.close()
    plt.savefig(out_path, dpi=150, facecolor=BG, edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"SAVED (style 2): {out_path}")


# ═══════════════════════════════════════════
# STYLE 3: Structure Annotated
# ═══════════════════════════════════════════
def style_structure(out_path):
    conn = _get_conn()
    bd, bo, bh, bl, bc, bv = fetch(conn, 'BTC', '4H', 50)
    ed, eo, eh, el, ec, ev = fetch(conn, 'ETH', '4H', 50)
    
    spring_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("BTC",)).fetchone()
    spring = int(spring_row[0]) if spring_row and spring_row[0] is not None else int(bc[-1] * 0.9)
    btc_peak = int(max(bh))
    e_spring_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("ETH",)).fetchone()
    e_spring = int(e_spring_row[0]) if e_spring_row and e_spring_row[0] is not None else int(ec[-1] * 0.9)
    
    fig, (ax_b, ax_e) = plt.subplots(1, 2, figsize=(18, 10), facecolor=BG)
    fig.suptitle('BTC + ETH  Structure Map  |  SPRING → SOS → RESISTANCE', color='#ccddee', fontsize=14, y=0.97, fontweight='bold')
    
    def plot_struct(ax, dates, o, h, l, c, v, coin, price, spring, peak):
        w = 0.04
        for i in range(len(dates)):
            color = UP if c[i] >= o[i] else DN
            ax.plot([dates[i], dates[i]], [l[i], h[i]], color=color, lw=1.2, alpha=0.85, zorder=2)
            bh_bar = max(abs(c[i]-o[i]), abs(price*0.0003))
            by_val = min(o[i], c[i])
            dn = mdates.date2num(dates[i])
            ax.add_patch(plt.Rectangle((dn-w/2, by_val), w, bh_bar, facecolor=color, edgecolor='none', zorder=3, alpha=0.8))
        
        # Spring zone
        ax.axhspan(spring*0.985, spring*1.015, alpha=0.2, color=UP, zorder=0)
        ax.annotate(f'SPRING\n${spring:,}', xy=(dates[5], spring*0.97), fontsize=9, color=UP, fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.4', facecolor=f'{UP}20', edgecolor=UP, alpha=0.7))
        
        # SOS arrow annotation on biggest green candle
        up_idx = [i for i in range(len(c)) if c[i] > o[i]]
        if up_idx:
            sos_i = up_idx[np.argmax([c[i]-o[i] for i in up_idx])]
            ax.annotate('SOS', xy=(dates[sos_i], h[sos_i]), xytext=(dates[sos_i], h[sos_i]*1.025),
                       fontsize=9, color=UP, fontweight='bold', ha='center',
                       arrowprops=dict(arrowstyle='->', color=UP, lw=1.5))
        
        # Resistance line
        ax.axhline(peak, color=HL, lw=1.5, ls='--', alpha=0.7, zorder=1)
        ax.text(dates[-3], peak*1.005, f'RESIST ${peak:,}', color=HL, fontsize=8, fontweight='bold', va='bottom')
        
        # Current price label
        change = (price - o[-1]) / o[-1] * 100
        ax.text(0.02, 0.93, f'{coin} ${price:,.0f} ({change:+.1f}%)', transform=ax.transAxes, fontsize=14,
                color=UP if change>0 else DN, fontweight='bold', fontfamily='monospace',
                bbox=dict(boxstyle='round,pad=0.4', facecolor='#060b14cc', edgecolor='#1a2a3a', alpha=0.85))
        
        ax.set_facecolor(BG); ax.tick_params(colors='#445566', labelsize=8)
        ax.grid(True, alpha=0.04, color='#fff')
        for s in ax.spines.values(): s.set_color('#0d1f33')
        ax.set_ylabel(coin, color='#8899aa', fontsize=11, fontweight='bold')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d', tz=BJT))
        ax.xaxis.set_major_locator(mdates.DayLocator(tz=BJT))
    
    plot_struct(ax_b, bd, bo, bh, bl, bc, bv, 'BTC', bc[-1], spring, btc_peak)
    plot_struct(ax_e, ed, eo, eh, el, ec, ev, 'ETH', ec[-1], e_spring, int(max(eh)))
    
    conn.close()
    plt.savefig(out_path, dpi=150, facecolor=BG, edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"SAVED (style 3): {out_path}")


# ═══════════════════════════════════════════
# STYLE 4: Minimalist Social Card
# ═══════════════════════════════════════════
def style_card(out_path):
    conn = _get_conn()
    btc_p_row = conn.execute(
        "SELECT close FROM klines WHERE coin=? AND timeframe='1H' ORDER BY ts DESC LIMIT 1",
        ("BTC",)).fetchone()
    btc_p = btc_p_row[0] if btc_p_row else 0
    eth_p_row = conn.execute(
        "SELECT close FROM klines WHERE coin=? AND timeframe='1H' ORDER BY ts DESC LIMIT 1",
        ("ETH",)).fetchone()
    eth_p = eth_p_row[0] if eth_p_row else 0
    if not btc_p or not eth_p:
        conn.close()
        print(f"WARNING: Missing price data (BTC={btc_p}, ETH={eth_p}), skipping style_card")
        return
    
    # Get key levels
    b7h_row = conn.execute(
        "SELECT MAX(high) FROM (SELECT high FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 7)",
        ("BTC",)).fetchone()
    b7h = b7h_row[0] if b7h_row and b7h_row[0] is not None else int(btc_p * 1.05)
    b30l_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("BTC",)).fetchone()
    b30l = b30l_row[0] if b30l_row and b30l_row[0] is not None else int(btc_p * 0.9)
    e7h_row = conn.execute(
        "SELECT MAX(high) FROM (SELECT high FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 7)",
        ("ETH",)).fetchone()
    e7h = e7h_row[0] if e7h_row and e7h_row[0] is not None else int(eth_p * 1.05)
    e30l_row = conn.execute(
        "SELECT MIN(low) FROM (SELECT low FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 30)",
        ("ETH",)).fetchone()
    e30l = e30l_row[0] if e30l_row and e30l_row[0] is not None else int(eth_p * 0.9)
    
    # Count up days
    daily = conn.execute(
        "SELECT open,close FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 10",
        ("BTC",)).fetchall()
    up = sum(1 for o,c in daily if c>o)
    
    fig, ax = plt.subplots(figsize=(8, 8), facecolor=BG)
    ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    ax.axis('off')
    
    # Title badge
    ax.text(5, 9.5, 'CRYPTO MARKET SNAPSHOT', ha='center', fontsize=11, color='#556677', fontfamily='monospace', fontweight='bold')
    
    # BTC section
    btc_change = (btc_p - daily[0][0]) / daily[0][0] * 100 if daily else 0  # vs today open
    ax.text(1, 8.2, 'BTC', fontsize=28, color='#ffffff', fontweight='bold', fontfamily='monospace')
    ax.text(1, 7.3, f'${btc_p:,.0f}', fontsize=36, color=UP if btc_change>0 else DN, fontweight='bold', fontfamily='monospace')
    ax.text(6.5, 7.5, f'{btc_change:+.1f}% today', fontsize=12, color=UP if btc_change>0 else DN, fontfamily='monospace')
    
    # BTC levels
    ax.text(1, 6.5, f'R: ${b7h:,.0f}  |  S: ${int(btc_p*0.992):,}  |  Spring: ${b30l:,.0f}', fontsize=10, color='#8899aa', fontfamily='monospace')
    # 加载 social_analyses.json（优先）或 analyses.json（回退）
    analyses_file = '/root/.hermes/trade_review/social_analyses.json'
    if not os.path.exists(analyses_file):
        analyses_file = '/root/.hermes/trade_review/analyses.json'
    records = []
    if os.path.exists(analyses_file):
        try:
            with open(analyses_file) as af:
                records = json.load(af)
            if not isinstance(records, list):
                records = []
        except Exception:
            pass

    # BTC daily stats — 从 JSON 读取 RSI 1D 和费率
    btc_rsi_1d = 50  # default
    btc_fr = 0
    for r in records:
        if isinstance(r, dict) and r.get('coin') == 'BTCUSDT':
            # 兼容两种格式：social_analyses.json→indicators, analyses.json→kline_table
            kt = r.get('indicators') or r.get('kline_table', {})
            k1d = kt.get('1D', {})
            btc_rsi_1d = round(k1d.get('rsi', 50), 1)
            # funding_rate_pct: social_analyses.json 在顶层，analyses.json 在 order_flow 内
            btc_fr = r.get('funding_rate_pct')
            if btc_fr is None:
                btc_fr = r.get('order_flow', {}).get('funding_rate_pct', 0)
            break
    
    btc_fr_str = f'{btc_fr:.4f}%' if btc_fr else '0.0000%'
    ax.text(1, 6.1, f'{up}/10 上涨日  |  RSI 1D: {btc_rsi_1d}  |  FR: {btc_fr_str}', 
            fontsize=9, color='#00e676', fontfamily='monospace')
    
    # Divider
    ax.axhline(5.5, color='#1a2a3a', lw=1)
    
    # ETH section
    eth_open_row = conn.execute(
        "SELECT open FROM klines WHERE coin=? AND timeframe='1D' ORDER BY ts DESC LIMIT 1",
        ("ETH",)).fetchone()
    eth_open = eth_open_row[0] if eth_open_row else eth_p * 0.99
    eth_change = (eth_p - eth_open) / eth_open * 100 if eth_open else 0
    ax.text(1, 4.5, 'ETH', fontsize=28, color='#ffffff', fontweight='bold', fontfamily='monospace')
    ax.text(1, 3.6, f'${eth_p:,.0f}', fontsize=36, color=UP if eth_change>0 else DN, fontweight='bold', fontfamily='monospace')
    ax.text(6.5, 3.8, f'{eth_change:+.1f}% today', fontsize=12, color=UP if eth_change>0 else DN, fontfamily='monospace')
    
    ax.text(1, 2.8, f'R: ${e7h:,.0f}  |  S: ${int(eth_p*0.988):,}  |  Spring: ${e30l:,.0f}', fontsize=10, color='#8899aa', fontfamily='monospace')
    # ETH RSI 1D — 兼容两种 JSON 格式
    eth_rsi_1d = 50
    for r in records:
        if isinstance(r, dict) and r.get('coin') == 'ETHUSDT':
            kt = r.get('indicators') or r.get('kline_table', {})
            k1d = kt.get('1D', {})
            eth_rsi_1d = round(k1d.get('rsi', 50), 1)
            break
    
    ax.text(1, 2.4, f'ETH/BTC: {eth_p/btc_p:.4f}  |  RSI 1D: {eth_rsi_1d}', fontsize=9, color='#ffaa00', fontfamily='monospace')
    
    # Footer badges — 复用上方已加载的 records
    macro = _extract_fg_records(records)
    btcd = macro.get('BTC.D', '?')
    dxy = macro.get('DXY', '?')
    vix = macro.get('VIX', '?')
    fg_val = macro.get('FG', '?')
    fg_label = macro.get('FG_LABEL', '')
    
    badges = [
        (f'FG: {fg_val} {fg_label.upper()}', '#ff6666'),
        (f'BTC.D: {btcd}%' if btcd != '?' else 'BTC.D: ?', '#448aff'),
        (f'DXY: {dxy:.1f}' if dxy != '?' else 'DXY: ?', '#8899aa'),
        (f'VIX: {vix:.1f}' if vix != '?' else 'VIX: ?', '#ffaa00'),
    ]
    for i, (text, color) in enumerate(badges):
        ax.text(1 + i*2.3, 1.0, text, fontsize=8, color=color, fontweight='bold', fontfamily='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor=f'{color}15', edgecolor=f'{color}33', alpha=0.6))
    
    ax.text(5, 0.3, 'Powered by hermes-agent · 5-system AI analysis', ha='center', fontsize=7, color='#334455', fontfamily='monospace')
    
    conn.close()
    plt.savefig(out_path, dpi=200, facecolor=BG, edgecolor='none', bbox_inches='tight')
    plt.close()
    print(f"SAVED (style 4): {out_path}")


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--style', '-s', type=int, default=1, choices=[1,2,3,4], help='Chart style 1-4')
    parser.add_argument('output', nargs='?', default=None, help='Output path')
    args = parser.parse_args()
    
    if args.output is None:
        args.output = f'/tmp/chart_style{args.style}.png'
    
    styles = {1: style_marketing, 2: style_dashboard, 3: style_structure, 4: style_card}
    styles[args.style](args.output)
