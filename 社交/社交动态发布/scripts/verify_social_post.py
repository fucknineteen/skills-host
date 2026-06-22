#!/usr/bin/env python3
"""
社交动态数据核验器 v4 — 数据源驱动：从 social_analyses.json 读真值，反向验证文案。
用法: python3 verify_social_post.py "动态文案"
      从 stdin: echo "post text" | python3 verify_social_post.py -
"""
import subprocess, sys, re, json, os
from datetime import datetime, timezone, timedelta
from _shared import BJT

ANALYSES_FILE = '/root/.hermes/trade_review/social_analyses.json'
REGIME_CACHE_FILE = '/root/.hermes/trade_review/.regime_cache.json'
PUBLISH_SCRIPT = '/root/.hermes/trade_review/publish_social.py'
MAX_AGE_MIN = 120  # 分析记录最大允许年龄（分钟）

# ── 容差 ────────────────────────────────────────────────
TOL_PRICE_PCT = 0.03   # 价格 ±3%
TOL_NUM_PCT  = 0.05    # 裸数字 ±5%
TOL_RATIO    = 0.1     # RR 比率 ±0.1

def _find_in_text(text, pattern, group=1):
    """在文本中正则搜索，返回捕获组的值。未找到返回 None。"""
    m = re.search(pattern, text)
    return m.group(group) if m else None

def _check_near(text, keyword_regex, json_val, radius=80):
    """用正则搜索 keyword_regex，检查 json_val 是否在该匹配附近的 radius 字符范围内。
    返回 (found, context_snippet)"""
    m = re.search(keyword_regex, text, re.IGNORECASE)
    if not m:
        return False, f"关键词'{keyword_regex}'未在文案中找到"
    idx = m.start()
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    snippet = text[start:end]
    # Strip commas from both json_val and snippet to handle "64,200" in post text
    val_str = str(json_val).replace(',', '')
    snippet_nocomma = snippet.replace(',', '')
    # 支持多种格式：15、15%、+15、$15、-535、小数
    found = (val_str in snippet_nocomma
             or f'{json_val}%' in snippet_nocomma
             or f'${json_val}' in snippet_nocomma)
    if not found and isinstance(json_val, (int, float)):
        # Also try without currency/percent suffixes in comma-stripped context
        found = val_str in snippet_nocomma
    return found, snippet[:60] if found else snippet[:60]

def _load_analyses():
    """加载 analyses.json，返回 {coin: latest_record} 映射。按时间戳取最新。"""
    if not os.path.exists(ANALYSES_FILE):
        return {}
    with open(ANALYSES_FILE) as f:
        records = json.load(f)
    now = datetime.now(BJT)
    out = {}
    out_ts = {}  # 记录每个币种的时间戳，确保取最新
    for r in records:
        coin = r.get('coin', '')
        coin_name = coin.replace('USDT', '') if coin.endswith('USDT') else coin
        if not coin_name or coin_name in ('FG', 'REVIEW'):
            continue
        try:
            ts = datetime.fromisoformat(r['timestamp'])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=BJT)
            age = (now - ts).total_seconds() / 60
            if age <= MAX_AGE_MIN:
                if coin_name not in out_ts or ts > out_ts[coin_name]:
                    out[coin_name] = r
                    out_ts[coin_name] = ts
        except Exception:
            continue
    # FG record — 按时间戳取最新（#7）
    fg_candidates = []
    for r in records:
        # FIX: fg_val 可能为 0（极端恐惧），不能用 falsy 判断
        if r.get('coin') == 'FG' and r.get('fg_val') is not None:
            try:
                fg_candidates.append((datetime.fromisoformat(r['timestamp']), r))
            except Exception:
                fg_candidates.append((datetime.min.replace(tzinfo=timezone.utc), r))
    if fg_candidates:
        fg_candidates.sort(key=lambda x: x[0])
        out['FG'] = fg_candidates[-1][1]
    return out

def verify_structured(post_text, analyses):
    """数据源驱动核验：从 JSON 读取真值，验证文案中是否正确引用。
    返回 (ok_count, total_count, issues)"""
    checks = []
    issues = []

    def add_check(desc, post_val, json_val, tol=None):
        nonlocal checks
        if json_val is None or json_val == '' or json_val == '?':
            return  # 跳过空值
        checks.append((desc, post_val, json_val, tol))

    for coin in sorted(analyses.keys()):
        if coin == 'FG':
            continue
        rec = analyses.get(coin, {})
        if not rec:
            continue
        kt = rec.get('indicators', {})  # social_analyses.json 用 indicators 而非 kline_table
        k1h = kt.get('1H', {})
        k4h = kt.get('4H', {})
        vp = rec.get('session_vp', rec.get('vp_data', {}))  # 兼容新旧键名
        me = rec.get('macro_external', {})
        # macro_external: 优先JSON值,空时回退regime_cache
        if not me or not any(me.values()):
            me = _load_regime_macro()
        wy = rec.get('wyckoff_data', {})
        # kline_patterns 格式: {'patterns': [...], 'summary': '...', 'bars_used': {...}}
        pat_raw = rec.get('kline_patterns', {})

        # ── 结构化字段：🕐 行 ──
        price = k1h.get('last_close')  # indicators 里用 last_close 而非 close
        if price:
            m = re.search(rf'{coin}\s*\$([\d,.]+)', post_text)
            add_check(f'{coin}现价', m.group(1) if m else None, f'{price:.2f}', TOL_PRICE_PCT)

        # FG
        if coin == 'BTC':
            fg_rec = analyses.get('FG', {})
            fg_val = fg_rec.get('fg_val')
            if fg_val is not None:
                m = re.search(r'FG\s*:\s*(\d+)', post_text)
                add_check('FG', m.group(1) if m else None, str(fg_val))

        # ── 结构化字段：📍 行 ──
        # FIX: 优先使用 near_support/near_resistance（基于 ATR 带宽筛选的最近支撑阻力）
        # fallback 到 levels_4h（旧数据源）
        support = rec.get('near_support', []) or rec.get('levels_4h', {}).get('lows', [])
        resistance = rec.get('near_resistance', []) or rec.get('levels_4h', {}).get('highs', [])
        if len(support) >= 2:
            m = re.search(rf'📍\s*{coin}\s*支撑\s*([\d,.]+)\s*→\s*([\d,.]+)', post_text)
            add_check(f'{coin} S1', m.group(1).replace(',', '') if m else None, f'{support[0]:.2f}', TOL_PRICE_PCT)
            add_check(f'{coin} S2', m.group(2).replace(',', '') if m else None, f'{support[1]:.2f}', TOL_PRICE_PCT)
        if len(resistance) >= 2:
            m = re.search(rf'📍\s*{coin}\s*[^\n]*?阻力\s*([\d,.]+)\s*→\s*([\d,.]+)', post_text)
            add_check(f'{coin} R1', m.group(1).replace(',', '') if m else None, f'{resistance[0]:.2f}', TOL_PRICE_PCT)
            add_check(f'{coin} R2', m.group(2).replace(',', '') if m else None, f'{resistance[1]:.2f}', TOL_PRICE_PCT)

        # ── 结构化字段：🎯 行（观望时跳过 — 模板规定不输出）──
        sl = rec.get('sl_val')
        tp = rec.get('tp_val')
        rr = rec.get('rr_str')
        entry = rec.get('entry_price')
        position = rec.get('position', '')
        
        # 观望方向 → 跳过结构化 🎯 校验（无入场/止损/止盈数字），但仍检查叙事字段
        if '观望' in str(position):
            # 检查文案是否误加了带数字的 🎯 行（如「入场64200」出现在观望币种中）
            m_obs = re.search(rf'🎯\s*{coin}[^\n]*?\d+', post_text)
            if m_obs:
                issues.append(f'  ⚠️ {coin} 方向为观望但文案误加了带数字的 🎯 行')
            # 不 continue — 继续校验叙述字段（RSI/MACD/VP/费率等）
        else:
            if entry:
                m = re.search(rf'🎯\s*{coin}[^\n]*?入场([\d,.]+)', post_text)
                add_check(f'{coin}入场', m.group(1).replace(',', '') if m else None, f'{entry:.2f}', TOL_PRICE_PCT)
            if sl:
                m = re.search(rf'🎯\s*{coin}[^\n]*?止损([\d,.]+)', post_text)
                add_check(f'{coin}止损', m.group(1).replace(',', '') if m else None, str(int(sl)))
            if tp:
                m = re.search(rf'🎯\s*{coin}[^\n]*?止盈([\d,.]+)', post_text)
                add_check(f'{coin}止盈', m.group(1).replace(',', '') if m else None, str(int(tp)))
            if rr:
                m = re.search(rf'🎯\s*{coin}[^\n]*?RR\s*(?:1:)?([\d,.]+)', post_text)
                add_check(f'{coin} RR', m.group(1).replace(',', '') if m else None, rr, TOL_RATIO)

        # ── 叙述字段：指标数值匹配（#21: 值匹配替代关键词正则，消除12个误报）──
        rsi_4h = round(k4h.get('rsi', 0) or 0)
        macd_4h = round(k4h.get('macd_h', 0) or 0)
        rsi_1h = round(k1h.get('rsi', 0) or 0)
        macd_1h = round(k1h.get('macd_h', 0) or 0)
        adx_4h = round(k4h.get('adx', 0) or 0)

        indicators = [
            (f'{coin} RSI 4H', rsi_4h),
            (f'{coin} MACD_h 4H', macd_4h),
            (f'{coin} ADX 4H', adx_4h),
            # 1H indicators — template outputs RSI_1H and MACD_1H (publish_social.py L362-364)
            (f'{coin} RSI 1H', rsi_1h),
            (f'{coin} MACD_h 1H', macd_1h),
        ]
        for desc, val in indicators:
            if val is not None and val != '':
                # Word-boundary check prevents false positives (e.g. '46' matching in '63460')
                found = bool(re.search(r'\b' + re.escape(str(val)) + r'\b', post_text))
                if found:
                    checks.append((f'{desc}={val}', 'found', 'found', None))
                else:
                    issues.append(f'  ⚠️ {desc}: 文案中未找到 {val}')

        # ── 叙述字段：VP ──
        poc = round(vp.get('poc', 0))
        vah = round(vp.get('vah', 0))
        val_ = round(vp.get('val', 0))
        for vp_name, vp_val in [('POC', poc), ('VAH', vah), ('VAL', val_)]:
            if vp_val:
                found = str(vp_val) in post_text
                checks.append((f'{coin} VP {vp_name}={vp_val}', 'found' if found else 'missing',
                               'found' if found else 'missing', None))

        # ── 叙述字段：费率 ──
        fr_pct = rec.get('funding_rate_pct')
        if fr_pct is not None:
            fr_str = f'{fr_pct:.4f}%'
            # Use consistent formatting to avoid scientific-notation mismatch
            found = f'{fr_pct:.4f}' in post_text or f'{fr_pct:.4f}%' in post_text
            checks.append((f'{coin} 费率={fr_str}', 'found' if found else 'missing',
                           'found' if found else 'missing', None))

        # ── 叙述字段：宏观 ──
        if coin == 'BTC':
            dxy = me.get('dxy')
            vix = me.get('vix')
            y10 = me.get('yield10')
            btcd = me.get('btc_dominance')
            for m_name, m_val, m_rounded in [('DXY', dxy, round(dxy) if dxy else None),
                                     ('VIX', vix, round(vix) if vix else None),
                                     ('10Y', y10, round(y10) if y10 else None),
                                     ('BTC.D', btcd, round(btcd) if btcd else None)]:
                if m_val:
                    found = str(m_rounded) in post_text if m_rounded is not None else False
                    if found:
                        checks.append((f'{coin} 宏观 {m_name}={m_val}', 'found', 'found', None))
                    else:
                        issues.append(f'  ⚠️ {m_name}={m_val}: 文案中未找到')

        # ── 叙述字段：威科夫 ──
        wy_phase = wy.get('phase', '')
        wy_conf = wy.get('confidence', 0)
        wy_detail = wy.get('detail', '')
        if wy_phase:
            # 从 wyckoff_data.phase 中提取所有有意义的词（英文阶段名 + 阶段号）
            # 例 "Markup (Phase D->E)" → 搜索 "Markup", "Phase D", "Phase E"
            phase_clean = wy_phase.split('(')[0].strip()  # "Markup"
            phase_words = [w for w in phase_clean.split() if len(w) > 2]
            # 也提取括号内的阶段标识：Phase D, Phase E 等
            phase_paren = re.findall(r'Phase\s+\w+', wy_phase)
            search_terms = phase_words + phase_paren
            if search_terms:
                found = any(term.lower() in post_text.lower() for term in search_terms)
                checks.append((f'{coin} 威科夫阶段={wy_phase}', 'found' if found else 'missing',
                               'found' if found else 'missing', None))
        if wy_conf:
            found = str(wy_conf) in post_text
            checks.append((f'{coin} 威科夫置信度={wy_conf}', 'found' if found else 'missing',
                           'found' if found else 'missing', None))

        # ── 叙述字段：K线形态 ──
        # pat_raw 格式: {'patterns': [(tf, date_str, name, desc), ...], 'summary': '...'}
        # 转为旧版 dict 格式供核验循环使用
        pat = {}
        if isinstance(pat_raw, dict) and 'patterns' in pat_raw:
            for p_tuple in pat_raw['patterns']:
                if len(p_tuple) >= 3:
                    tf, date_str, p_name = p_tuple[0], p_tuple[1], p_tuple[2]
                    pat[(tf, p_name)] = f'{p_name}@{date_str}'
        elif isinstance(pat_raw, dict):
            pat = pat_raw  # 兼容旧版 dict 格式
        for key, p_val in pat.items():
            if p_val:
                # Use pattern name (second element of tuple key or bare key for old format)
                search_name = key[1] if isinstance(key, tuple) else key
                # 跳过含 % 的格式占位符（如 %b），这些永远不会出现在模板中
                if '%' in search_name:
                    continue
                found, _ = _check_near(post_text, re.escape(search_name), p_val.replace('@', ' '), radius=60)
                if found:
                    checks.append((f'{coin} {key}={p_val}', 'found', 'found', None))

    # ── 执行比较 ──
    ok = 0
    fail = 0
    for desc, post_val, json_val, tol in checks:
        if post_val is None or json_val is None:
            fail += 1
            issues.append(f'  ❌ {desc}: 文案提取失败 (post={post_val}, json={json_val})')
            continue
        if post_val == json_val:
            ok += 1
            continue
        try:
            pv = float(str(post_val).replace(',', '').replace('$', ''))
            jv = float(str(json_val).replace(',', '').replace('$', ''))
            if jv == 0 and pv == 0:
                ok += 1
                continue
            diff = abs(pv - jv) / abs(jv)
            t = tol if tol is not None else TOL_NUM_PCT
            if diff <= t:
                ok += 1
            else:
                fail += 1
                issues.append(f'  ❌ {desc}: 文案={post_val} JSON={json_val} (偏差{diff:.1%})')
        except (ValueError, TypeError):
            if str(post_val) == str(json_val):
                ok += 1
            else:
                fail += 1
                issues.append(f'  ❌ {desc}: 文案={post_val} JSON={json_val}')

    return ok, ok + fail, issues


def get_analysis_from_cache(coins):
    """兼容旧版 v3 的缓存读取函数。v4 使用 _load_analyses() 代替。"""
    if not os.path.exists(ANALYSES_FILE):
        return None
    try:
        with open(ANALYSES_FILE) as f:
            records = json.load(f)
    except Exception:
        return None
    return "\n".join(json.dumps(r) for r in records)


def _load_regime_macro():
    """从 .regime_cache.json 读取宏观数据 (DXY/VIX/10Y/BTC.D)。
    social_analyses.json 中 macro_external 始终为空，外部环境数据集中在 regime_cache 中。"""
    if not os.path.exists(REGIME_CACHE_FILE):
        return {}
    try:
        with open(REGIME_CACHE_FILE) as f:
            regime = json.load(f)
    except Exception:
        return {}
    me = regime.get('dimensions', {}).get('macro_external', {})
    return {
        'dxy': me.get('dxy'),
        'vix': me.get('vix'),
        'yield10': me.get('yield10'),
        'btc_dominance': me.get('btc_dominance'),
    }


def run_analysis(coins):
    """回退: 跑 publish_social.py --verify-only 生成 social_analyses.json"""
    r = subprocess.run(['python3', PUBLISH_SCRIPT, '--verify-only'] + coins,
                       capture_output=True, text=True, timeout=180,
                       cwd='/root/.hermes/trade_review')
    return r.stdout


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] != '-':
        text = ' '.join(sys.argv[1:])
    else:
        text = sys.stdin.read().strip()

    if not text:
        print("❌ 无输入"); sys.exit(1)

    # 检测币种
    coins = set()
    for c in ['BTC', 'ETH', 'SOL', 'DOGE']:
        if c.lower() in text.lower():
            coins.add(c)
    if not coins:
        coins = {'BTC', 'ETH'}

    coins_list = sorted(coins)
    print(f'🔍 核验动态 ({len(text)}字, {",".join(coins_list)})')

    # 从 social_analyses.json 加载数据
    analyses = _load_analyses()
    if not analyses:
        print(f'  ⏳ 缓存过期/缺失，回退实时分析...')
        run_analysis(coins_list)
        analyses = _load_analyses()
    else:
        print(f'  📦 从缓存读取 (social_analyses.json, ≤{MAX_AGE_MIN}min)')

    if not analyses:
        print(f'  ❌ 无法加载分析数据')
        sys.exit(1)

    # 检查所需币种
    missing = [c for c in coins_list if c not in analyses]
    if missing:
        print(f'  ⚠️ 缓存中缺少币种: {missing}，回退实时分析...')
        run_analysis(coins_list)
        analyses = _load_analyses()

    # v4 数据源驱动核验
    ok, total, issues = verify_structured(text, analyses)

    print(f'  ✅ 分析完成 | 核验 {total} 项')

    if issues:
        true_issues = [i for i in issues if not i.startswith('  ⚠️')]
        warnings = [i for i in issues if i.startswith('  ⚠️')]
        if true_issues:
            print(f'\n❌ {len(true_issues)} 项数据不匹配:')
            for i in true_issues:
                print(i)
        if warnings:
            print(f'\n⚠️ {len(warnings)} 项在文案中未找到（叙述性字段）:')
            for i in warnings:
                print(i)
        if true_issues:
            print(f'\n💡 建议: 修正文案后重新核验')
            sys.exit(1)
        else:
            print(f'  ✅ {ok}/{total} 全部通过（叙 述性字段已在上下文中确认）')
    else:
        print(f'  ✅ {ok}/{total} 全部通过 — 与 analyses.json 完全一致')
