#!/usr/bin/env python3
"""
Trade review processor — 3-layer (6h/12h/72h) cron job.
Reads analyses.json (RO), reviews.json (RW), okx_klines.db.
Outputs only records with pending reviews.
"""
import json
import os
import sqlite3
import subprocess
import sys
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from _shared import BJT, DB_PATH as _SHARED_DB_PATH, TRADE_DIR, classify_price_path, get_klines, SOCIAL_ANALYSES_PATH

DB_PATH = _SHARED_DB_PATH
REVIEWS_PATH = f'{TRADE_DIR}/reviews.json'
ANALYSES_PATH = f'{TRADE_DIR}/analyses.json'          # flat_old: analysis_template.py
LESSONS_PATH = f'{TRADE_DIR}/lessons.json'
REGIME_DIR = f'{TRADE_DIR}/regimes'

# Regime-aware saving
def get_current_regime():
    """Get current regime from index, fallback to detector."""
    try:
        with open(f'{REGIME_DIR}/regime_index.json', 'r') as f:
            idx = json.load(f)
        active = [h for h in idx.get('regime_history', []) if h.get('status') == 'active']
        if active:
            return active[0]['regime']
    except Exception:
        pass
    return None

def save_lessons_regime_aware(lessons):
    """Save lessons to both lessons.json and regime-specific file.
    lessons.json = 所有行情类型的教训并集（累积，每次重建）
    regimes/{regime}_lessons.json = 单行情类型教训（追加，去重）"""
    
    # Ensure all known regime lesson files exist (empty array if missing)
    try:
        with open(f'{REGIME_DIR}/regime_definitions.json', 'r') as f:
            defs = json.load(f)
        known_regimes = list(defs.get('regime_types', {}).keys())
        for r in known_regimes:
            rpath = f'{REGIME_DIR}/{r}_lessons.json'
            if not os.path.exists(rpath):
                with open(rpath, 'w') as f:
                    json.dump([], f)
    except Exception:
        pass
    
    # Detect regime and save to regime file (累积追加)
    regime = get_current_regime()
    if regime:
        regime_path = f'{REGIME_DIR}/{regime}_lessons.json'
        try:
            with open(regime_path, 'r') as f:
                existing = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []
        
        # Tag new lessons with regime
        for l in lessons:
            if 'regime' not in l:
                l['regime'] = regime
        
        # Merge: only add lessons not already in existing (by coin+date+lesson key)
        existing_keys = {(e.get('coin',''), e.get('date',''), e.get('lesson','')) for e in existing}
        new_count = 0
        for l in lessons:
            key = (l.get('coin',''), l.get('date',''), l.get('lesson',''))
            if key not in existing_keys:
                existing.append(l)
                existing_keys.add(key)
                new_count += 1
        
        with open(regime_path, 'w') as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        
        if new_count > 0:
            print(f"   → 同步到行情类型文件: {regime}_lessons.json (+{new_count}条)")
    
    # Rebuild lessons.json from all regime files (并集，每次重建)
    all_lessons = []
    if os.path.isdir(REGIME_DIR):
        for fname in os.listdir(REGIME_DIR):
            if fname.endswith('_lessons.json'):
                try:
                    with open(os.path.join(REGIME_DIR, fname), 'r') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_lessons.extend(data)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
    
    with open(LESSONS_PATH, 'w') as f:
        json.dump(all_lessons, f, indent=2, ensure_ascii=False)

# Direction keywords — priority order matters for mixed signals
# Bullish indicators (higher priority when combined with bearish)
BULLISH_WORDS = ['bullish', '多头', '看多', '上升', '偏多', '反弹', 'recovering',
                 '多头排列', '筑底', '减速筑底', '震荡偏强', '短期多头', '同步筑底']
# Bearish indicators
BEARISH_WORDS = ['bearish', '空头', '看空', '下跌', 'oversold', '空头排列',
                 '下降', '盘整', '震荡偏弱']
# Conflict keywords: appear in both directions, handled by priority logic
CONFLICT_WORDS = ['反弹', '回调']  # e.g. "短期反弹遇阻" is bearish, "5连阳反弹" is bullish

def get_okx_server_time():
    """Get OKX server time via curl to avoid system clock drift."""
    try:
        r = subprocess.run(['curl', '-4', '-s', '--max-time', '10',
            'https://www.okx.com/api/v5/public/time'],
            capture_output=True, text=True, timeout=12)
        data = json.loads(r.stdout)
        ts = int(data['data'][0]['ts']) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def parse_timestamp(ts_str):
    """Parse ISO timestamp, handling naive (BJ time) and tz-aware (UTC) formats."""
    if ts_str is None:
        return None
    ts_str = ts_str.strip()
    if '+' in ts_str or ts_str.endswith('Z'):
        if ts_str.endswith('Z'):
            ts_str = ts_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Naive datetime → assumed BJ time (UTC+8)
        dt = datetime.fromisoformat(ts_str)
        return dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)

# Direction keywords
def detect_direction_from_klines(candles, entry_price):
    """从K线数据客观判定方向，替代 detect_direction() 文本关键词匹配。
    基于窗口内最高/最低价相对于入场价的位置判定。
    """
    if not candles or not entry_price:
        return 'flat'
    
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    
    window_high = max(highs)
    window_low = min(lows)
    window_close = closes[-1]
    
    has_up = window_high > entry_price * 1.02
    has_down = window_low < entry_price * 0.98
    
    if has_up and not has_down:
        return 'bullish'
    elif has_down and not has_up:
        return 'bearish'
    elif has_up and has_down:
        # 双向波动 → 看收盘价相对入场价位置
        if window_close > entry_price * 1.01:
            return 'bullish'
        elif window_close < entry_price * 0.99:
            return 'bearish'
        else:
            return 'flat'


def detect_direction(trend_str):
    """Detect bullish/bearish from trend string (mixed CN/EN).
    DEPRECATED: Kept for backward compatibility with existing analyses.json.
    New code should use detect_direction_from_klines() instead.
    """
    trend_lower = (trend_str or '').lower()
    
    # Context-based overrides (bearish takes precedence)
    bearish_context = ['遇阻', '压制', '射击之星', '顶背离', '派发', 'UTAD', '空头']
    bullish_context = ['突破', '放量', 'SOS', '吸筹', '支撑', '上升', '偏多', '多头']
    
    has_bull = any(w in trend_lower for w in BULLISH_WORDS)
    has_bear = any(w in trend_lower for w in BEARISH_WORDS)
    has_conflict = any(w in trend_lower for w in CONFLICT_WORDS)
    
    if not has_bull and not has_bear and not has_conflict:
        return 'neutral'
    
    # Bearish context override (highest priority)
    if any(wc in trend_lower for wc in bearish_context):
        return 'bearish'
    # Bullish context override
    if any(bc in trend_lower for bc in bullish_context):
        return 'bullish'
    
    # Special: "bearish_" prefix
    if trend_lower.startswith('bearish_'):
        return 'bearish'
    
    # Both sides detected — fall through to keyword strength
    if has_bull and has_bear:
        bull_only = any(w in trend_lower for w in ['偏多', '上升', '多头', '突破'])
        bear_only = any(w in trend_lower for w in ['下降', '盘整', '震荡偏弱'])
        if bull_only and not bear_only:
            return 'bullish'
        if bear_only and not bull_only:
            return 'bearish'
        return 'mixed'
    
    # Only bullish side
    if has_bull:
        return 'bullish'
    
    # Only bearish side
    if has_bear:
        return 'bearish'
    
    # Only conflict words — no context found
    return 'neutral'

def find_analysis(analyses, coin, review_ts_str):
    """Find matching analysis by coin and closest timestamp."""
    review_dt = parse_timestamp(review_ts_str)
    if not review_dt:
        return None
    
    # Filter by coin
    coin_analyses = [a for a in analyses if a.get('coin') == coin]
    if not coin_analyses:
        return None
    
    # Sort by absolute time difference
    coin_analyses.sort(key=lambda a: abs(
        (parse_timestamp(a.get('timestamp', '')) or review_dt) - review_dt
    ).total_seconds())
    return coin_analyses[0]

def do_6h_review(review, analysis, now_utc, db):
    """6h preliminary review. Fixed window: [analysis-1h, analysis+7h]."""
    coin = review.get('coin', '')
    entry_price = review.get('entry_price', 0)
    
    review_ts = parse_timestamp(review.get('timestamp'))
    if review_ts is None:
        return None
    
    # Fixed window: analysis-1h to analysis+7h (total 8h, centered on 6h mark)
    since_ms = int((review_ts.timestamp() - 3600) * 1000)
    until_ms = int((review_ts.timestamp() + 7 * 3600) * 1000)
    candles_1h = get_klines(db, coin, '1H', since_ms, until_ms, limit=10)
    
    if not candles_1h:
        return None
    
    window_low = min(c[3] for c in candles_1h)
    window_high = max(c[2] for c in candles_1h)
    window_close = candles_1h[-1][4]
    
    # === 客观方向判定：从K线数据计算，替代文本关键词匹配 ===
    orig_dir = detect_direction_from_klines(candles_1h, entry_price)
    
    pct_change = (window_close - entry_price) / entry_price * 100 if entry_price else 0
    
    # Determine actual move — ±2% threshold
    actual_move = 'up' if pct_change > 2 else ('down' if pct_change < -2 else 'flat')
    
    # Judge correctness
    verdict_map = {
        ('flat',): ('横盘震荡', '横盘震荡：波动{pct:+.2f}%，方向未确立'),
        ('bearish', 'down'): ('正确', '方向正确：K线空头→实际跌{pct:+.2f}%'),
        ('bullish', 'up'): ('正确', '方向正确：K线多头→实际涨{pct:+.2f}%'),
        ('bullish', 'down'): ('错误', '方向错误：K线多头→实际跌{pct:+.2f}%'),
        ('bearish', 'up'): ('错误', '方向错误：K线空头→实际涨{pct:+.2f}%'),
    }
    verdict, reason_template = verdict_map.get((orig_dir, actual_move), ('横盘震荡', '横盘震荡：波动{pct:+.2f}%'))
    reason = reason_template.format(pct=pct_change)
    
    # Check near_bottom / Spring — use objective K-line data
    # Support both flat_old (sentient.spring_confirmed) and full_obj (near_bottom)
    spring_low = 0
    if analysis:
        spring = analysis.get('sentiment', {}).get('spring_confirmed', False)
        spring_low = (analysis.get('support') or [0])[0] if spring else 0
        # For full_obj format: use levels_4h.lows[0] as spring_low proxy
        if not spring and 'levels_4h' in analysis and analysis['levels_4h'].get('lows'):
            spring_low = analysis['levels_4h']['lows'][0]
    
    if spring_low and window_low < spring_low * 0.98:
        reason += f" | Spring低点{spring_low}被跌破(L={window_low:.1f})"
        if verdict == '正确':
            verdict = '错误'
    
    # Check for event-driven context
    key_events = analysis.get('macro', {}).get('key_events', []) if analysis else []
    event_note = ''
    for ev in key_events:
        ev_str = str(ev)
        if any(kw in ev_str for kw in ['ADP','非农','CPI','FOMC','ISM','NFP']):
            event_note = f" [数据事件: {ev_str}]"
    
    # === 价格路径分析 ===
    path_type, path_detail = classify_price_path(candles_1h, entry_price)
    path_note = f" | 路径:{path_type}(前{path_detail['first_half']['change_pct']:+.1f}%后{path_detail['second_half']['change_pct']:+.1f}% 振幅{path_detail['total_range_pct']:.1f}%)"
    
    review_ts_bj = review_ts.astimezone(BJT)
    
    note = (f"[6h初判] 分析BJ {review_ts_bj.strftime('%m-%d %H:%M')}，"
            f"现价{window_close:.1f}({pct_change:+.2f}%)。"
            f"K线{orig_dir}→实际{actual_move}→{verdict}。{reason}{path_note}{event_note}")
    
    return {
        'review_6h': verdict,
        'review_6h_note': note,
        'review_6h_path': path_type,
        'review_6h_path_detail': path_detail,
    }

def do_12h_review(review, analysis, now_utc, db):
    """12h deep review with scoring. Fixed window: [analysis-2h, analysis+13h]."""
    coin = review.get('coin', '')
    entry_price = review.get('entry_price', 0)
    review_ts = parse_timestamp(review.get('timestamp'))
    if review_ts is None:
        return None
    
    # Fixed window: analysis-2h to analysis+13h (total 15h, centered on 12h mark)
    since_ms = int((review_ts.timestamp() - 7200) * 1000)
    until_ms = int((review_ts.timestamp() + 13 * 3600) * 1000)
    candles_1h = get_klines(db, coin, '1H', since_ms, until_ms, limit=20)
    
    if not candles_1h:
        return None
    
    window_high = max(c[2] for c in candles_1h)
    window_low = min(c[3] for c in candles_1h)
    window_close = candles_1h[-1][4]
    pct_change = (window_close - entry_price) / entry_price * 100 if entry_price else 0
    
    review_ts_bj = review_ts.astimezone(BJT)
    
    # === Score 1: Dow Direction (+-1) — 客观K线判定 ===
    # 用K线数据替代文本关键词匹配
    orig_dir = detect_direction_from_klines(candles_1h, entry_price)
    
    # Check HH/HL structure for bullish, LH/LL for bearish
    direction_score = 0
    direction_detail = ""
    
    # Simple direction check — ±2% = 横盘震荡
    if abs(pct_change) <= 2:
        direction_score = 0
        direction_detail = f"横盘震荡：波动{pct_change:+.2f}%，方向未确立"
    elif orig_dir == 'bearish' and pct_change < -2:
        direction_score = 1
        direction_detail = f"空头方向正确：跌{pct_change:+.2f}%"
    elif orig_dir == 'bullish' and pct_change > 2:
        direction_score = 1
        direction_detail = f"多头方向正确：涨{pct_change:+.2f}%"
    elif orig_dir == 'bearish' and pct_change > 2:
        direction_score = -1
        direction_detail = f"空头方向错误：涨{pct_change:+.2f}%"
    elif orig_dir == 'bullish' and pct_change < -2:
        direction_score = -1
        direction_detail = f"多头方向错误：跌{pct_change:+.2f}%"
    
    # === Score 2: Wyckoff Signal (+-1) — 客观K线判定 ===
    wyckoff_score = 0
    wyckoff_detail = ""
    
    if analysis:
        sentiment = analysis.get('sentiment', {})
        spring = sentiment.get('spring_confirmed', False)
        
        # For full_obj format, check near_bottom as alternative signal
        if not spring:
            spring = analysis.get('near_bottom', False)
        
        if spring and pct_change > 2:
            wyckoff_score = 1
            wyckoff_detail = f"Spring确认有效：价格反弹{pct_change:+.2f}%"
        elif spring and pct_change < -2:
            wyckoff_score = -1
            wyckoff_detail = f"Spring疑似失败：价格续跌{pct_change:.2f}%"
        elif spring:
            wyckoff_detail = "横盘震荡：Spring待验证"
        else:
            # 用K线数据客观检测SOS/Spring/UTAD
            # SOS: 放量阳线突破前高
            has_sos = False
            if len(candles_1h) >= 3:
                last_vol = candles_1h[-1][5]
                prev_vols = [c[5] for c in candles_1h[-4:-1]]
                avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
                last_close = candles_1h[-1][4]
                last_open = candles_1h[-1][1]
                if avg_vol > 0 and last_vol > avg_vol * 1.5 and last_close > last_open:
                    # 放量阳线 → 检查是否突破前高
                    prev_high = max(c[2] for c in candles_1h[-4:-1])
                    if last_close > prev_high:
                        has_sos = True
                        if pct_change > 2:
                            wyckoff_score = 1
                            wyckoff_detail = "SOS放量突破信号被确认"
                        else:
                            wyckoff_detail = "横盘震荡：SOS信号待确认"
            
            if not has_sos:
                wyckoff_detail = "无明显威科夫信号"
    
    # === Score 3: Level Effectiveness (+-1) ===
    levels_score = 0
    levels_detail = ""
    # Support both flat_old (support/resistance) and full_obj (levels_4h.lows/highs)
    supports = analysis.get('support', []) if analysis else []
    resistances = analysis.get('resistance', []) if analysis else []
    if not supports and analysis and 'levels_4h' in analysis:
        supports = analysis['levels_4h'].get('lows', [])[:3]  # top 3 lowest
    if not resistances and analysis and 'levels_4h' in analysis:
        resistances = analysis['levels_4h'].get('highs', [])[:3]  # top 3 highest
    
    support_hits = 0
    resistance_hits = 0
    for s in supports:
        if isinstance(s, (int, float)):
            if abs(window_low - s) / s < 0.005:  # within 0.5%
                support_hits += 1
    for r in resistances:
        if isinstance(r, (int, float)):
            if abs(window_high - r) / r < 0.005:  # within 0.5%
                resistance_hits += 1
    
    if support_hits >= 1 or resistance_hits >= 1:
        levels_score = 1
        parts = []
        if support_hits >= 1:
            parts.append(f"{support_hits}支撑")
        if resistance_hits >= 1:
            parts.append(f"{resistance_hits}阻力")
        levels_detail = f"价位有效：{'+'.join(parts)}触及"
    elif supports or resistances:
        levels_detail = f"价位未触及（支撑最低{window_low},阻力最高{window_high}）"
    else:
        levels_detail = "无价位数据"
    
    total_score = direction_score + wyckoff_score + levels_score
    
    # Events check
    events = ""
    event_impact = ""
    if analysis:
        key_events = analysis.get('macro', {}).get('key_events', []) if 'macro' in analysis else []
        # For full_obj format, events may come from lessons_warnings
        if not key_events:
            key_events = analysis.get('lessons_warnings', [])
        events_str = ', '.join(str(e) for e in key_events) if key_events else '无明显事件'
        events = events_str
        has_event = any(kw in events_str for kw in ['ADP','非农','CPI','FOMC','ISM','NFP'])
        event_impact = "数据事件时段，TA参考价值有限" if has_event else "无明显数据事件，TA驱动"
    
    # === 价格路径分析 ===
    path_type, path_detail = classify_price_path(candles_1h, entry_price)
    
    result = {
        'review_12h': f"{total_score:+d}/3 (道氏:{direction_score:+d}/威科夫:{wyckoff_score:+d}/价位:{levels_score:+d})",
        'review_12h_result': {
            'period': f"[{review_ts_bj.strftime('%m-%d %H:%M')} -2h, +13h] (固定窗口)",
            'entry_price': entry_price,
            'actual': {
                'high_12h': window_high,
                'low_12h': window_low,
                'close_12h': window_close,
                'change_pct': round(pct_change, 2)
            },
            'events': events,
            'event_impact': event_impact,
            'direction_score': direction_score,
            'wyckoff_score': wyckoff_score,
            'levels_score': levels_score,
            'direction_detail': direction_detail,
            'wyckoff_detail': wyckoff_detail,
            'levels_detail': levels_detail,
            'lessons': [],
            'price_path': path_type,
            'price_path_detail': path_detail,
        }
    }
    
    # Extract lessons
    lessons = extract_lessons(direction_score, wyckoff_score, levels_score, pct_change,
                             analysis, coin, review_ts_bj.strftime('%Y-%m-%d'))
    result['review_12h_result']['lessons'] = lessons
    
    return result

def extract_lessons(dir_score, wyckoff_score, levels_score, pct_change,
                   analysis, coin, date_str):
    """Extract and categorize lessons.
    
    教训生成规则：
    - 总分≥2（+2或+3）：分析基本正确，不产生教训
    - 总分≤1：对每个评分≤0的维度生成教训
        - 评分=-1：明确错误 → 用"误判"语言
        - 评分=0：未确认/中性 → 用"未确认"语言
    """
    lessons = []
    if analysis is None:
        return lessons
    
    total = dir_score + wyckoff_score + levels_score
    if total >= 2:
        return lessons  # 分析基本正确，无需教训
    
    # direction_misjudge — use actual price change, not text keywords
    if dir_score <= 0:
        rsi = analysis.get('rsi_14', 0) or analysis.get('rsi_4h', 0)
        fg = analysis.get('macro', {}).get('fear_greed', 0) if 'macro' in analysis else 0
        
        if dir_score == -1:
            if rsi and rsi < 20 and fg and fg < 20:
                lessons.append({
                    'category': 'direction_misjudge',
                    'type': '方向误判',
                    'lesson': f"{coin}: 极端超卖(RSI={rsi},FG={fg})环境做空是系统性错误。实际涨{pct_change:+.1f}%。RSI<20+FG<20应自动降级为中性/观望。"
                })
            else:
                actual_label = '涨' if pct_change > 0 else '跌'
                lessons.append({
                    'category': 'direction_misjudge',
                    'type': '方向误判',
                    'lesson': f"{coin}: 方向误判。实际{actual_label}{pct_change:+.1f}%。"
                })
        else:  # dir_score == 0
            lessons.append({
                'category': 'direction_uncertain',
                'type': '方向未确认',
                'lesson': f"{coin}: 方向信号未在12h窗口内确认（波动{pct_change:+.1f}%）。入场时机或方向需重新评估。"
            })
    
    # signal_misread
    if wyckoff_score <= 0:
        if wyckoff_score == -1:
            lessons.append({
                'category': 'signal_misread',
                'type': '信号误判',
                'lesson': f"{coin}: 信号未被确认，价格续跌{pct_change:+.1f}%。极端超卖环境下的Spring需额外确认（放量阳线+二次回踩）。"
            })
        else:  # wyckoff_score == 0
            lessons.append({
                'category': 'signal_unconfirmed',
                'type': '信号未确认',
                'lesson': f"{coin}: 威科夫/形态信号在12h窗口内未得到价格确认。该信号在当前环境下效力不足，需更多K线验证。"
            })
    
    # levels_miss
    if levels_score <= 0:
        if levels_score == -1:
            lessons.append({
                'category': 'levels_miss',
                'type': '价位误判',
                'lesson': f"{coin}: 支撑/阻力位全部失效，价格突破关键价位{pct_change:+.1f}%。价位设定需参考更高级别结构。"
            })
        else:  # levels_score == 0
            lessons.append({
                'category': 'levels_unused',
                'type': '价位未触及',
                'lesson': f"{coin}: 12h窗口内未触及任何设定价位。支撑/阻力带可能过宽，需用ATR动态调整宽度。"
            })
    
    # acceleration_miss — use actual K-line drop, not text search
    # near_bottom: check if sentiment field has it (objective), or use K-line drop
    has_near_bottom = False
    if analysis:
        nb = analysis.get('near_bottom', False)
        if isinstance(nb, bool) and nb:
            has_near_bottom = True
    
    if has_near_bottom and pct_change < -3:
        lessons.append({
            'category': 'acceleration_miss',
            'type': '加速误判',
            'lesson': f"{coin}: near_bottom判断过早，价格续跌{pct_change:+.1f}%。趋势加速中禁用near_bottom标签——应等日线实体缩小或放量阳线确认。"
        })
    
    # event_miss
    key_events = analysis.get('macro', {}).get('key_events', []) if 'macro' in analysis else []
    if not key_events:
        key_events = analysis.get('lessons_warnings', [])
    events_str = ' '.join(str(e) for e in key_events)
    if any(kw in events_str for kw in ['ADP','非农','CPI','FOMC','ISM','NFP']):
        if dir_score in [1, -1]:
            lessons.append({
                'category': 'event_miss',
                'type': '事件遗漏',
                'lesson': f"{coin}: 数据事件前给出明确方向建议，TA信号在事件前后可靠性大幅下降。数据前应降级为观望/轻仓。"
            })
    
    return lessons

def do_72h_review(review, analysis, now_utc, db):
    """72h final review using 1H candles (fixed window: [analysis-4h, analysis+73h])."""
    coin = review.get('coin', '')
    entry_price = review.get('entry_price', 0)
    review_ts = parse_timestamp(review.get('timestamp'))
    if review_ts is None:
        return None
    
    # Fixed window: analysis-4h to analysis+73h (total 77h)
    since_ms = int((review_ts.timestamp() - 4 * 3600) * 1000)
    until_ms = int((review_ts.timestamp() + 73 * 3600) * 1000)
    # Use 1H candles as the sole data source
    candles_1h = get_klines(db, coin, '1H', since_ms, until_ms, limit=80)
    
    if not candles_1h:
        return None
    
    # Split 77h window into 3 periods of ~25h each
    n = len(candles_1h)
    third = max(n // 3, 1)
    part1 = candles_1h[:third]
    part2 = candles_1h[third:third*2]
    part3 = candles_1h[third*2:]
    
    day1_close = part1[-1][4]
    day2_close = part2[-1][4] if part2 else part1[-1][4]
    day3_close = part3[-1][4] if part3 else part2[-1][4]
    
    # High/Low across the entire 72h window
    high_3d = max(c[2] for c in candles_1h)
    low_3d = min(c[3] for c in candles_1h)
    
    # Period label from 1H candles
    period_label = f"{datetime.fromtimestamp(candles_1h[0][0]/1000, tz=timezone.utc).strftime('%m-%d')} ~ {datetime.fromtimestamp(candles_1h[-1][0]/1000, tz=timezone.utc).strftime('%m-%d')}"
    
    net_pct = (day3_close - entry_price) / entry_price * 100 if entry_price else 0
    
    # === 3D trend scoring — 客观1H K线判定 ===
    orig_3d_dir = detect_direction_from_klines(candles_1h, entry_price)
    
    # Determine actual 3D trend
    if net_pct > 2:
        actual_3d = 'bullish'
    elif net_pct < -2:
        actual_3d = 'bearish'
    else:
        actual_3d = 'flat'
    
    trend_3d_score = 0
    if orig_3d_dir == 'bearish' and actual_3d == 'bearish':
        trend_3d_score = 1
    elif orig_3d_dir == 'bullish' and actual_3d == 'bullish':
        trend_3d_score = 1
    elif orig_3d_dir == 'bearish' and actual_3d == 'bullish':
        trend_3d_score = -1
    elif orig_3d_dir == 'bullish' and actual_3d == 'bearish':
        trend_3d_score = -1
    
    # Get existing scores from 12h review
    dir_score = review.get('review_12h_result', {}).get('direction_score', 0)
    wyckoff_score = review.get('review_12h_result', {}).get('wyckoff_score', 0)
    levels_score = review.get('review_12h_result', {}).get('levels_score', 0)
    
    total = dir_score + wyckoff_score + levels_score + trend_3d_score
    
    review_ts_bj = review_ts.astimezone(BJT)
    
    detail = (f"K线3D方向(1H): {orig_3d_dir}；实际72h: {actual_3d} "
              f"(entry={entry_price}, close={day3_close}, {net_pct:+.1f}%, H={high_3d}, L={low_3d})")
    
    annotations = []
    lessons = []
    
    # Check consecutive misjudgment
    review_6h = review.get('review_6h', '')
    review_12h = review.get('review_12h', '')
    if review_6h == '错误' and ('-1' in str(review_12h) or review_12h == '错误'):
        annotations.append('[WARN] 连续误判: 6h错误+12h错误, 检查分析框架')
    
    # near_bottom check — use structured field, not text search
    has_near_bottom = False
    if analysis:
        nb = analysis.get('near_bottom', False)
        if isinstance(nb, bool) and nb:
            has_near_bottom = True
    
    if has_near_bottom and low_3d < entry_price * 0.92:  # -8% from entry
        annotations.append('底部判断失败: near_bottom标注后72h内跌幅>8%')
        lessons.append({
            'category': 'acceleration_miss',
            'type': '加速误判',
            'lesson': f"{coin}: near_bottom严重失败——72h从{entry_price}跌至{low_3d}({net_pct:+.1f}%)。趋势加速中禁用near_bottom。"
        })
    
    # Data event check — support both flat_old and full_obj formats
    key_events = analysis.get('macro', {}).get('key_events', []) if 'macro' in analysis else []
    if not key_events:
        key_events = analysis.get('lessons_warnings', [])
    events_str = ' '.join(str(e) for e in key_events)
    if any(kw in events_str for kw in ['ADP','非农','CPI','FOMC','ISM','NFP']):
        annotations.append('数据驱动，TA参考价值有限')
    
    # Direction misjudge for 3D
    if trend_3d_score == -1:
        if orig_3d_dir == 'bearish' and net_pct > 3:
            # Support both flat_old (rsi_14) and full_obj (rsi_4h)
            rsi = analysis.get('rsi_14', 0) or analysis.get('rsi_4h', 0) if analysis else 0
            fg = analysis.get('macro', {}).get('fear_greed', 0) if analysis and 'macro' in analysis else 0
            if rsi and rsi < 20 and fg and fg < 20:
                lessons.append({
                    'category': 'direction_misjudge',
                    'type': '方向误判',
                    'lesson': f"{coin}: 3D空头完全错误——72h涨{net_pct:+.1f}%。"
                              f"RSI={rsi}+FG={fg}极端超卖是V反信号而非做空信号。"
                })
    
    # === 价格路径分析（72h全窗口） ===
    path_type, path_detail = classify_price_path(candles_1h, entry_price)
    
    result = {
        'review_72h': f"{total:+d}/4",
        'review_72h_result': {
            'period': f"[{review_ts_bj.strftime('%m-%d %H:%M')} -4h, +73h] (固定窗口, {period_label})",
            'entry_price': entry_price,
            'actual_3d': {
                'day1': {'close': day1_close},
                'day2': {'close': day2_close},
                'day3': {'close': day3_close, 'high': high_3d, 'low': low_3d},
                'net_change_pct': round(net_pct, 2),
                'high_3d': high_3d,
                'low_3d': low_3d
            },
            'trend_3d_score': trend_3d_score,
            'trend_3d_detail': detail,
            'final_score': f"{total:+d}/4",
            'direction_score_12h': dir_score,
            'wyckoff_score_12h': wyckoff_score,
            'levels_score_12h': levels_score,
            'annotations': annotations,
            'verdict': '已完成',
            'new_lessons': lessons,
            'reviewed_at': now_utc.astimezone(BJT).strftime('%Y-%m-%d %H:%M BJ'),
            'price_path': path_type,
            'price_path_detail': path_detail,
        },
        'completed': True
    }
    
    return result

def dedup_lessons(existing, new):
    """Dedup by lesson text similarity."""
    existing_texts = {e.get('lesson', '') for e in existing}
    result = []
    for n in new:
        if n.get('lesson', '') not in existing_texts:
            result.append(n)
    return result

def main():
    # Get current time
    now_utc = get_okx_server_time()
    now_bj = now_utc.astimezone(BJT)

    # Load data
    try:
        with open(REVIEWS_PATH, 'r') as f:
            reviews = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[ERROR] Cannot read reviews.json: {e}", file=sys.stderr)
        return
    
    try:
        with open(ANALYSES_PATH, 'r') as f:
            analyses = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        analyses = []
    
    # Also load social_analyses.json and merge (social records take priority for same coin+date)
    try:
        with open(SOCIAL_ANALYSES_PATH, 'r') as f:
            social_analyses = json.load(f)
        # Merge: social entries appended; dedup by (coin, date) where social wins
        social_keys = set()
        for sa in social_analyses:
            ts = parse_timestamp(sa.get('timestamp', ''))
            if ts:
                social_keys.add((sa.get('coin', ''), ts.strftime('%Y-%m-%d')))
        # Remove duplicate records from analyses that also appear in social
        analyses = [a for a in analyses if (
            (ts := parse_timestamp(a.get('timestamp', ''))) and 
            (a.get('coin', ''), ts.strftime('%Y-%m-%d')) not in social_keys
        )]
        analyses.extend(social_analyses)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    
    try:
        with open(LESSONS_PATH, 'r') as f:
            lessons = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        lessons = []
    
    # === Step 0: Sync + Dedup (single pass through reviews) ===
    modified = False
    existing_dates = set()
    # Group reviews by (coin, date) for dedup in same pass
    groups = defaultdict(list)
    for r in reviews:
        r_dt = parse_timestamp(r.get('timestamp', ''))
        if r_dt:
            key = (r.get('coin', ''), r_dt.strftime('%Y-%m-%d'))
            existing_dates.add(key)
            groups[key].append(r)
    
    # Dedup within each (coin, date) group: keep the most complete one
    dedup_removed = 0
    for key, items in groups.items():
        if len(items) > 1:
            best = max(items, key=lambda r: (
                r.get('completed', False),
                sum(1 for k in ['review_6h','review_12h','review_72h']
                    if '待复盘' not in str(r.get(k, '')))
            ))
            others = [r for r in items if r is not best]
            for r in others:
                reviews.remove(r)
                dedup_removed += 1
                print(f"  [Step 0] 去重删除: {r.get('coin')} @ {r.get('timestamp')}")
    
    if dedup_removed > 0:
        modified = True
        print(f"  Step 0 去重完成: -{dedup_removed} 条")
    
    # Sync new analyses to reviews (only first per coin per day)
    # Skip non-coin records (FG, REVIEW) written by publish_social.py for verification
    SKIP_COINS = {'FG', 'REVIEW'}
    new_count = 0
    for a in analyses:
        coin = a.get('coin', '')
        ts = a.get('timestamp', '')
        if not coin or not ts or coin in SKIP_COINS:
            continue
        dt = parse_timestamp(ts)
        date_key = dt.strftime('%Y-%m-%d') if dt else None
        if date_key and (coin, date_key) in existing_dates:
            continue  # 同日同币已有复盘记录，跳过
        existing_dates.add((coin, date_key))
        
        # Create pending review entry
        entry_price = a.get('entry_price', 0)
        # For full_obj format, entry_price may be 0; use ticker.last as fallback
        if not entry_price and 'ticker' in a:
            try:
                entry_price = float(a['ticker'].get('last', 0) or 0)
            except (ValueError, TypeError):
                pass
        if not entry_price:
            continue  # No valid entry price → skip
        entry = {
            'coin': coin,
            'timestamp': ts,
            'entry_price': entry_price,
            'review_6h': '待复盘',
            'review_12h': '待复盘',
            'review_72h': '待复盘',
            'completed': False
        }
        reviews.append(entry)
        new_count += 1
        print(f"  [Step 0] 同步新分析: {coin} @ {ts} | 入${entry_price}")
    
    if new_count > 0:
        modified = True
        print(f"  Step 0 同步完成: +{new_count} 条新分析")
    
    db = sqlite3.connect(DB_PATH)
    
    output_lines = []
    
    for idx, review in enumerate(reviews):
        coin = review.get('coin', '')
        ts = review.get('timestamp', '')
        review_ts = parse_timestamp(ts)
        
        if review_ts is None:
            continue
        
        elapsed_h = (now_utc - review_ts).total_seconds() / 3600
        review_ts_bj = review_ts.astimezone(BJT)
        
        # Skip already completed
        if review.get('completed'):
            continue
        
        entry_price = review.get('entry_price', 0)
        analysis = find_analysis(analyses, coin, ts)
        
        has_output = False
        record_header = f"【复盘 #{idx+1} — {coin.replace('USDT','')} {review_ts_bj.strftime('%m-%d %H:%M')} 分析】入场价 {entry_price:,}"
        
        # === 6h Review ===
        if review.get('review_6h') == '待复盘' and elapsed_h >= 6:
            result = do_6h_review(review, analysis, now_utc, db)
            if result:
                review['review_6h'] = result['review_6h']
                review['review_6h_note'] = result['review_6h_note']
                if not has_output:
                    output_lines.append(record_header)
                    has_output = True
                output_lines.append(f"[PASS] 6h初判: {result['review_6h']}")
                output_lines.append(f"   理由: {result['review_6h_note']}")
                modified = True
        
        # === 12h Review ===
        if review.get('review_12h') == '待复盘' and elapsed_h >= 12:
            result = do_12h_review(review, analysis, now_utc, db)
            if result:
                # result dict keys (review_12h, review_12h_result, completed) merge into review
                review.update(result)
                if not has_output:
                    output_lines.append(record_header)
                    has_output = True
                output_lines.append(f"[PASS] 12h深复盘: {result['review_12h']}")
                detail = result.get('review_12h_result', {})
                output_lines.append(f"   道氏: {detail.get('direction_detail','')}")
                output_lines.append(f"   威科夫: {detail.get('wyckoff_detail','')}")
                output_lines.append(f"   价位: {detail.get('levels_detail','')}")
                path_type = detail.get('price_path', '')
                if path_type:
                    pd = detail.get('price_path_detail', {})
                    fh = pd.get('first_half', {})
                    sh = pd.get('second_half', {})
                    output_lines.append(f"   路径: {path_type}(净{pd.get('net_change_pct',0):+.1f}% 振幅{pd.get('total_range_pct',0):.1f}%)"
                                       f" 前{fh.get('change_pct',0):+.1f}% 后{sh.get('change_pct',0):+.1f}%")
                
                # Add lessons
                new_lessons = detail.get('lessons', [])
                if new_lessons:
                    deduped = dedup_lessons(lessons, new_lessons)
                    for l in deduped:
                        l['coin'] = coin
                        l['date'] = review_ts_bj.strftime('%Y-%m-%d')
                        l['level'] = '12h'
                        l['source'] = 'review_12h_result'
                        lessons.append(l)
                    if deduped:
                        output_lines.append(f"   新增教训: {len(deduped)}条")
                modified = True
        
        # === 72h Review ===
        if review.get('review_72h') == '待复盘' and elapsed_h >= 72:
            result = do_72h_review(review, analysis, now_utc, db)
            if result:
                # result dict keys (review_72h, review_72h_result, completed) merge into review
                review.update(result)
                if not has_output:
                    output_lines.append(record_header)
                    has_output = True
                output_lines.append(f"[PASS] 72h终局: {result['review_72h']} [OK] 已完成")
                rd = result.get('review_72h_result', {})
                output_lines.append(f"   3D趋势: {rd.get('trend_3d_detail','')}")
                # 价格路径分析
                path_type = rd.get('price_path', '')
                if path_type:
                    pd = rd.get('price_path_detail', {})
                    fh = pd.get('first_half', {})
                    sh = pd.get('second_half', {})
                    output_lines.append(f"   72h路径: {path_type}(净{pd.get('net_change_pct',0):+.1f}% 振幅{pd.get('total_range_pct',0):.1f}%)"
                                       f" 前{fh.get('change_pct',0):+.1f}% 后{sh.get('change_pct',0):+.1f}%")
                annotations = rd.get('annotations', [])
                for a in annotations:
                    output_lines.append(f"   {a}")
                
                new_lessons = rd.get('new_lessons', [])
                if new_lessons:
                    deduped = dedup_lessons(lessons, new_lessons)
                    for l in deduped:
                        l['coin'] = coin
                        l['date'] = review_ts_bj.strftime('%Y-%m-%d')
                        l['level'] = '72h'
                        l['source'] = 'review_72h_result'
                        lessons.append(l)
                    if deduped:
                        output_lines.append(f"   新增教训: {len(deduped)}条")
                modified = True
    
    # Archive completed records >30 days old to reviews_archive.json (always run)
    archive_path = REVIEWS_PATH.replace('.json', '_archive.json')
    try:
        archive = []
        if os.path.exists(archive_path):
            with open(archive_path) as f:
                archive = json.load(f)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        archivable = []
        keep = []
        for r in reviews:
            if r.get('completed'):
                try:
                    ts = parse_timestamp(r.get('timestamp'))
                    if ts and ts.replace(tzinfo=None) < cutoff:
                        archivable.append(r)
                        continue
                except Exception:
                    pass
            keep.append(r)
        if archivable:
            archive.extend(archivable)
            with open(archive_path, 'w') as f:
                json.dump(archive, f, indent=2, ensure_ascii=False)
            reviews = keep
            modified = True
            print(f"   📦 归档 {len(archivable)} 条旧记录到 reviews_archive.json ({len(archive)} 总计)")
    except Exception:
        pass
    
    # Save atomically
    if modified:
        tmp_path = REVIEWS_PATH + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(reviews, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, REVIEWS_PATH)
        save_lessons_regime_aware(lessons)
        
        if output_lines:
            print(f"=== 复盘处理 | BJ {now_bj.strftime('%Y-%m-%d %H:%M:%S')} (UTC {now_utc.strftime('%Y-%m-%d %H:%M:%S')}) ===\\n")
            print('\n'.join(output_lines))
            print(f"\n=== 已更新 reviews.json 和 lessons.json ===")
        # else: nothing to deliver (empty stdout = cron SILENT)
    
    db.close()

if __name__ == '__main__':
    main()
