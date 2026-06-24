#!/usr/bin/env python3
"""
方案二：文案数字交叉审计脚本 v2
v2 新增：语义上下文匹配、百分比推导（-1.2% → (close-open)/open）、分组输出
用法：
  python3 scripts/audit_draft.py --social /tmp/social_draft.txt
  python3 scripts/audit_draft.py --pure 分析结论.txt
  echo '文案' | python3 scripts/audit_draft.py --social -
"""

import json, os, sys, re, math
from pathlib import Path

TRADE_DIR = Path(os.environ.get('TRADE_DIR', '/root/.hermes/trade_review'))
SOCIAL_JSON = TRADE_DIR / 'social_analyses.json'
ANALYSES_JSON = TRADE_DIR / 'analyses.json'
REGIME_CACHE = TRADE_DIR / '.regime_cache.json'

# Tolerance constants
TOL_PCT = 0.03    # ±3% for prices
TOL_ABS = 1.5     # ±1.5 for integer indicators (RSI/ADX/MACD)

# ── 语义上下文映射（文案关键词 → JSON 字段路径偏好）──────────────
SEMANTIC_HINTS = [
    # (context_pattern, preferred_path_pattern, label)
    (r'DXY|美元指数', r'macro_external.*dxy(?!_)', 'DXY'),
    (r'(?<!\w)VIX(?!\w)', r'macro_external.*vix(?!_)', 'VIX'),
    (r'10Y|十年期|yield', r'macro_external.*yield10', '10Y'),
    (r'BTC\.D', r'macro_external.*btc_dominance', 'BTC.D'),
    (r'(?<!\w)FG(?!\w)|恐惧|贪婪', r'fg_val|fg_actual', 'FG'),
    (r'(?<!\w)POC(?!\w)', r'session_vp.*[Pp][Oo][Cc]', 'POC'),
    (r'(?<!\w)VAH(?!\w)', r'session_vp.*[Vv][Aa][Hh]', 'VAH'),
    (r'(?<!\w)VAL(?!\w)', r'session_vp.*[Vv][Aa][Ll]', 'VAL'),
    (r'收在|收盘|十字星收', r'indicators\.1D\.last_close|kline_table\.1D\.close', '1D收盘'),
    (r'MACD.*1D|1D.*MACD', r'indicators\.1D\.macd_h|kline_table\.1D\.macd_h', 'MACD_1D'),
    (r'MACD.*4H|4H.*MACD', r'indicators\.4H\.macd_h|kline_table\.4H\.macd_h', 'MACD_4H'),
    (r'MACD.*1H|1H.*MACD', r'indicators\.1H\.macd_h|kline_table\.1H\.macd_h', 'MACD_1H'),
    (r'RSI.*1D|1D.*RSI', r'indicators\.1D\.rsi|kline_table\.1D\.rsi', 'RSI_1D'),
    (r'RSI.*4H|4H.*RSI', r'indicators\.4H\.rsi|kline_table\.4H\.rsi', 'RSI_4H'),
    (r'RSI.*1H|1H.*RSI', r'indicators\.1H\.rsi|kline_table\.1H\.rsi', 'RSI_1H'),
    (r'ADX.*1D|1D.*ADX', r'indicators\.1D\.adx|kline_table\.1D\.adx', 'ADX_1D'),
    (r'ADX.*4H|4H.*ADX', r'indicators\.4H\.adx|kline_table\.4H\.adx', 'ADX_4H'),
    (r'ADX.*1H|1H.*ADX', r'indicators\.1H\.adx|kline_table\.1H\.adx', 'ADX_1H'),
    (r'费率|funding.rate', r'funding_rate_pct', '费率'),
    (r'Taker', r'taker_buy_ratio', 'Taker'),
    (r'支撑', r'levels_4h\.lows\[0\]|lows', '支撑S1'),
    (r'阻力', r'levels_4h\.highs\[0\]|highs', '阻力R1'),
    (r'涨跌|跌幅|涨幅|变化', r'', '涨跌幅(推导)'),
]


def flatten_json(obj, prefix=''):
    """Flatten nested JSON into {path: value} dict."""
    flat = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(flatten_json(v, f'{prefix}.{k}' if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat.update(flatten_json(v, f'{prefix}[{i}]'))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        flat[prefix] = obj
    elif isinstance(obj, str):
        try:
            val = float(obj.replace(',', '').replace('$', '').replace('%', ''))
            flat[prefix] = val
        except (ValueError, AttributeError):
            pass
    return flat


def extract_numbers(text):
    """Extract all numeric substrings, deduplicated, skipping date fragments.
    Returns list of (num_str, ctx, num_pos_in_ctx) tuples."""
    results = []
    seen = set()

    def _add(num_str, match_start, match_end):
        start, end = max(0, match_start - 30), min(len(text), match_end + 30)
        ctx = text[start:end].replace('\n', ' ').strip()
        num_pos = match_start - start
        key = (num_str, ctx[:40])
        if key not in seen:
            seen.add(key)
            results.append((num_str, ctx, num_pos))
        return ctx, num_pos

    # P1: standalone numbers
    for m in re.finditer(r'(?<![\d-])(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?)(?![\w%])', text):
        num_str = m.group(1)
        after = text[m.end():m.end()+3]
        if re.match(r'\.\d', after):
            continue
        ctx, _ = _add(num_str, m.start(), m.end())
        if re.search(r'\b\d{1,2}\b', num_str) and re.search(r'\d{2}:\d{2}', ctx):
            if len(num_str) <= 2 or (num_str.startswith('-') and len(num_str) <= 3):
                results.pop()
                seen.discard((num_str, ctx[:40]))
                continue

    # P1b: unformatted 4-6 digit numbers
    for m in re.finditer(r'(?<![\d-])(\d{4,6})(?![\d\w%])', text):
        num_str = m.group(1)
        ctx, _ = _add(num_str, m.start(), m.end())
        if re.search(r'\d{2}:\d{2}', ctx) and len(num_str) <= 2:
            results.pop()
            seen.discard((num_str, ctx[:40]))
            continue

    # P2: $ numbers
    for m in re.finditer(r'\$(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?)', text):
        _add(m.group(1), m.start(), m.end())

    # P3: percentages
    for m in re.finditer(r'(-?\d+\.\d+%)', text):
        _add(m.group(1), m.start(), m.end())

    # P4: negative numbers with sign
    for m in re.finditer(r'(?<=[\s(])-(\d{2,4})(?=[\s,.%+)]|$)', text):
        _add('-' + m.group(1), m.start(), m.end())

    return results


def find_semantic_hint(ctx, num_pos=None):
    """Return (label, preferred_path_regex) if context matches a semantic hint.
    When num_pos is given, returns the hint whose keyword is closest to that position."""
    best_label, best_path, best_dist = None, None, float('inf')
    for pattern, path_pattern, label in SEMANTIC_HINTS:
        for m in re.finditer(pattern, ctx, re.IGNORECASE):
            if num_pos is not None:
                dist = abs(m.start() - num_pos)
                if dist < best_dist:
                    best_dist = dist
                    best_label = label
                    best_path = path_pattern
            else:
                return label, path_pattern  # first match
    return (best_label, best_path) if best_label else (None, None)


def compute_derived_pct(num_str, ctx, json_sources, num_pos=None):
    """Try to compute a percentage change from JSON data.
    When num_pos is given, picks the timeframe closest to num_pos.
    Returns (status, path_info, value) or None."""
    if not num_str.endswith('%'):
        return None

    try:
        pct_val = float(num_str.replace('%', '').replace(',', ''))
    except ValueError:
        return None

    coin_match = re.search(r'(BTC|ETH|SOL|DOGE)', ctx, re.IGNORECASE)
    if not coin_match:
        return None
    if not re.search(r'[阴阳]|[涨跌]', ctx):
        return None

    coin = coin_match.group(1)

    # Find timeframe: closest to num_pos if available, otherwise first match
    tf = None
    if num_pos is not None:
        best_tf, best_dist = None, float('inf')
        for m in re.finditer(r'(1D|4H|1H|30m|5m)', ctx):
            dist = abs(m.start() - num_pos)
            if dist < best_dist:
                best_dist = dist
                best_tf = m.group(1)
        tf = best_tf
    if not tf:
        tf_match = re.search(r'(1D|4H|1H|30m|5m)', ctx)
        tf = tf_match.group(1) if tf_match else '1D'

    # Search JSON for the coin's open/close in that timeframe
    for src_name, src_flat in json_sources:
        close_key = None
        open_key = None
        for path in src_flat:
            # BUGFIX: substring match — 'ETH' in 'ETHEREUM' → false positive
            # Match only whole-coin names; 'ETH' should not match 'ETHEREUM'
            path_parts = path.replace('_', ' ').replace('-', ' ').replace('.', ' ').split()
            if coin in path_parts and tf in path:
                if 'last_close' in path or path.endswith('.close'):
                    close_key = path
                if 'last_o' in path or path.endswith('.o'):
                    open_key = path
        if close_key and open_key:
            close_val = src_flat[close_key]
            open_val = src_flat[open_key]
            if open_val and open_val != 0:
                derived = (close_val - open_val) / open_val * 100
                if abs(derived - pct_val) <= 1.0:  # ±1pp tolerance
                    return ('✅ 推导', f'{close_key} vs {open_key}', round(derived, 1))
    return None


def check_number_semantic(num_str, ctx, json_sources, num_pos=None):
    """Check a number against JSON, using semantic hints for better matching.
    Two-pass: first search only preferred fields, then global if not found.
    Returns (status, path_info, matched_value, deviation_pct, is_semantic)"""
    try:
        clean = num_str.replace(',', '').replace('%', '').replace('$', '')
        num_val = float(clean)
    except ValueError:
        return ('⏭️ 跳过', '', None, None, False)

    hint_label, hint_path_re = find_semantic_hint(ctx, num_pos)

    # Collect all items
    all_items = []  # [(src_name, path, val)]
    for src_name, src_flat in json_sources:
        for path, val in src_flat.items():
            if isinstance(val, (int, float)):
                all_items.append((src_name, path, val))

    if not all_items:
        return ('❌', '', None, None, False)

    # If semantic hint, split into preferred and rest
    preferred_items = []
    if hint_path_re:  # P1-2 FIX: was 'if hint_path_re and hint_path_re'
        preferred_items = [(s, p, v) for s, p, v in all_items
                          if re.search(hint_path_re, p, re.IGNORECASE)]
        # Only add macro_external as fallback if hint is macro-related
        if 'macro' in hint_path_re.lower() or not preferred_items:
            for s, p, v in all_items:
                if 'macro_external' in p and (s, p, v) not in preferred_items:
                    preferred_items.append((s, p, v))

    # Step 1: exact match in preferred fields
    for _, path, val in preferred_items:
        if val == num_val:
            return ('✅ 语义', path, val, 0.0, True)

    # Step 2: exact match globally
    for _, path, val in all_items:
        if val == num_val:
            return ('✅', path, val, 0.0, False)

    # Step 3: approximate — search preferred first; if preferred has ANY match
    # within tolerance, use it. Only fall back to global if preferred set is empty
    # or has nothing within tolerance.
    search_sets = [(preferred_items, True)] if preferred_items else []
    search_sets.append((all_items, False))

    best_path, best_val, best_dev, best_semantic = None, None, float('inf'), False

    for items, is_preferred in search_sets:
        for _, path, val in items:
            if val == 0:
                continue
            dev = abs(num_val - val) / max(abs(val), 0.001)
            effective_dev = dev * 0.3 if is_preferred else dev

            if effective_dev < best_dev:
                best_dev = effective_dev
                best_val = val
                best_path = path
                best_semantic = is_preferred

        # SEMANTIC OVERRIDE: if preferred items had ANY match within tolerance,
        # stop here and don't search global items
        if is_preferred and best_semantic:
            real_dev = abs(num_val - best_val) / max(abs(best_val), 0.001) if best_val else 0
            is_pct = num_str.endswith('%')
            is_integer_like = (not is_pct and num_val == int(num_val) and abs(num_val) < 200)
            tol = max(TOL_PCT, TOL_ABS / abs(num_val)) if (num_val != 0 and is_integer_like) else TOL_PCT
            if real_dev <= tol:
                return ('✅ 语义', best_path, best_val, round(real_dev * 100, 1), True)

    # Determine tolerance
    is_pct = num_str.endswith('%')
    is_integer_like = (not is_pct and num_val == int(num_val) and abs(num_val) < 200)
    if is_integer_like and num_val != 0:
        tol = max(TOL_PCT, TOL_ABS / abs(num_val))
    else:
        tol = TOL_PCT

    real_dev = abs(num_val - best_val) / max(abs(best_val), 0.001) if best_val else 0

    if real_dev <= tol:
        prefix = '✅ 语义' if best_semantic else '⚠️ 近似'
        return (prefix, best_path, best_val, round(real_dev * 100, 1), best_semantic)

    return ('❌', '', None, None, False)


def load_json(mode='auto'):
    """Load the appropriate JSON data source."""
    results = []

    if mode in ('auto', 'social') and SOCIAL_JSON.exists():
        with open(SOCIAL_JSON) as f:
            data = json.load(f)
        seen = {}
        for r in data:
            coin = r.get('coin', '')
            if coin.endswith('USDT') or coin == 'FG':
                seen[coin] = r
        results.append(('social_analyses.json', flatten_json(seen)))

    if mode in ('auto', 'pure') and ANALYSES_JSON.exists():
        with open(ANALYSES_JSON) as f:
            data = json.load(f)
        results.append(('analyses.json', flatten_json(data)))

    if REGIME_CACHE.exists():
        with open(REGIME_CACHE) as f:
            data = json.load(f)
        results.append(('regime_cache.json', flatten_json(data)))

    return results


def audit_draft(text, mode='auto'):
    """Main audit function."""
    numbers = extract_numbers(text)
    json_sources = load_json(mode)

    if not json_sources:
        return ['❌ 无可用 JSON 数据源'], len(numbers)

    lines = []
    lines.append('=' * 60)
    lines.append('🔍 文案数字交叉审计 v2（语义匹配）')
    lines.append(f'   提取数字: {len(numbers)} 个')
    src_names = ', '.join(name for name, _ in json_sources)
    lines.append(f'   数据源: {src_names}')
    lines.append('=' * 60)

    sections = {'exact': [], 'semantic': [], 'approx': [], 'derived': [], 'missing': []}

    for num_str, ctx, num_pos in numbers:
        # Check if semantic hint exists — if so, skip derived percentage
        # EXCEPT: if the number is a percentage and the hint is not 涨跌幅-related,
        # the hint is a false match (e.g. "POC" near "-1.2%")
        hint_label, _ = find_semantic_hint(ctx, num_pos)
        hint_is_pct_related = (hint_label and '涨跌幅' in str(hint_label))

        # Try derived percentage if no semantic hint, OR if number is % but hint isn't %-related
        derived = None
        if not hint_label or (num_str.endswith('%') and not hint_is_pct_related):
            derived = compute_derived_pct(num_str, ctx, json_sources, num_pos)

        if derived:
            status, path, val = derived
            sections['derived'].append((num_str, ctx, status, path, val))
            continue

        # Semantic check
        status, path, match_val, dev, is_sem = check_number_semantic(num_str, ctx, json_sources, num_pos)

        if status == '✅':
            sections['exact'].append((num_str, ctx, status, path, match_val))
        elif status.startswith('✅ 语义'):
            sections['semantic'].append((num_str, ctx, status, path, match_val, dev))
        elif status.startswith('⚠️'):
            sections['approx'].append((num_str, ctx, status, path, match_val, dev))
        else:
            sections['missing'].append((num_str, ctx, status, '', None))

    # Print results grouped by section
    def emit_section(title, items, marker):
        if not items:
            return
        lines.append(f'\n── {title} ({len(items)}个) ──')
        for item in items:
            num_str, ctx = item[0], item[1]
            status = item[2]
            path = item[3] if len(item) > 3 else ''
            match_val = item[4] if len(item) > 4 else None

            line = f'{marker}{num_str:>14}  '
            if path:
                line += f'→ {path}'
                if match_val is not None:
                    line += f' ({match_val})'
                    if len(item) > 5 and item[5] and item[5] > 0:
                        line += f' 偏差{item[5]}%'
            else:
                line += f'  ← 无来源'
            line += f'    [{ctx[:80]}]'
            lines.append(line)

    emit_section('✅ 精确匹配', sections['exact'], '  ')
    emit_section('✅ 语义匹配（上下文优先）', sections['semantic'], '  ')
    emit_section('🧮 推导百分比（从JSON K线计算）', sections['derived'], '  ')
    emit_section('⚠️ 近似匹配（无上下文偏好）', sections['approx'], '⚠️ ')
    emit_section('❌ 未找到', sections['missing'], '❌ ')

    lines.append('\n' + '─' * 60)
    ok = len(sections['exact']) + len(sections['semantic']) + len(sections['derived'])
    warn = len(sections['approx'])
    fail = len(sections['missing'])
    lines.append(f'  ✅ {ok} 精确/语义/推导 | ⚠️ {warn} 近似 | ❌ {fail} 未找到')

    if sections['missing']:
        # Classify missing items
        pct_only = [i for i in sections['missing'] if i[0].endswith('%')]
        if pct_only:
            lines.append(f'\n💡 {len(pct_only)} 个百分比（-X.X%）未能从K线推导 → 确认数值或手算')

    if fail > 0:
        lines.append(f'\n🚫 审计不通过 — {fail} 个数字无来源')
    elif warn > 0:
        lines.append(f'\n⚠️ 审计通过但 {warn} 个近似值需人工判断')
    else:
        lines.append(f'\n✅ 审计通过 — 所有数字可追溯')

    return lines, fail


if __name__ == '__main__':
    mode = 'auto'
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    for f in flags:
        if f == '--social':
            mode = 'social'
        elif f == '--pure':
            mode = 'pure'

    if args:
        arg = args[0]
        if os.path.exists(arg):
            with open(arg) as f:
                text = f.read()
        elif arg == '-':
            text = sys.stdin.read()
        else:
            text = arg
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("用法: python3 scripts/audit_draft.py [--social|--pure] <文案文件>")
        print("       echo '文案' | python3 scripts/audit_draft.py --social -")
        sys.exit(0)

    lines, fail = audit_draft(text, mode)
    for l in lines:
        print(l)
    sys.exit(0 if fail == 0 else 1)
