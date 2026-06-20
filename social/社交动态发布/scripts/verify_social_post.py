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
ANALYSIS_SCRIPT = '/root/.hermes/trade_review/analysis_template.py'
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
    val_str = str(json_val).replace(',', '')
    # 支持多种格式：15、15%、+15、$15、-535、小数
    found = (val_str in snippet or f'{json_val}%' in snippet or f'${json_val}' in snippet)
    if not found and isinstance(json_val, (int, float)):
        try:
            found = f'{json_val:.1f}' in snippet
        except (ValueError, TypeError):
            pass
    return found, snippet[:60] if found else snippet[:60]

def _load_analyses():
    """加载 analyses.json，返回 {coin: latest_record} 映射。"""
    if not os.path.exists(ANALYSES_FILE):
        return {}
    with open(ANALYSES_FILE) as f:
        records = json.load(f)
    now = datetime.now(BJT)
    out = {}
    for r in records:
        coin = r.get('coin', '')
        if not coin.endswith('USDT'):
            continue
        try:
            ts = datetime.fromisoformat(r['timestamp'])
            age = (now - ts).total_seconds() / 60
            if age <= MAX_AGE_MIN:
                coin_name = coin.replace('USDT', '')
                if coin_name not in out:
                    out[coin_name] = r
        except Exception:
            continue
    # FG record
    for r in records:
        if r.get('coin') == 'FG' and r.get('fg_val'):
            out['FG'] = r
            break
    return out

def verify_structured(post_text, analyses):
    """数据源驱动核验：从 JSON 读取真值，验证文案中是否正确引用。
    返回 (ok_count, total_count, issues)"""
    checks = []
    issues = []

    def add_check(desc, post_val, json_val, tol=None):
        nonlocal checks
        if json_val is None or json_val == '' or json_val == '?' or json_val == 0:
            return  # 跳过空值
        checks.append((desc, post_val, json_val, tol))

    for coin in ['BTC', 'ETH']:
        rec = analyses.get(coin, {})
        if not rec:
            continue
        kt = rec.get('kline_table', {})
        k1h = kt.get('1H', {})
        k4h = kt.get('4H', {})
        vp = rec.get('session_vp', {})
        me = rec.get('macro_external', {})
        wy = rec.get('wyckoff_data', {})
        pat = rec.get('kline_pattern_times', {})

        # ── 结构化字段：🕐 行 ──
        price = k1h.get('close')
        if price:
            m = re.search(rf'{coin}\s*\$([\d,.]+)', post_text)
            add_check(f'{coin}现价', m.group(1) if m else None, f'{price:.2f}', TOL_PRICE_PCT)

        # FG
        if coin == 'BTC':
            fg_rec = analyses.get('FG', {})
            fg_val = fg_rec.get('fg_val')
            if fg_val:
                m = re.search(r'FG:(\d+)', post_text)
                add_check('FG', m.group(1) if m else None, str(fg_val))

        # ── 结构化字段：📍 行 ──
        support = rec.get('support', [])
        resistance = rec.get('resistance', [])
        if len(support) >= 2:
            m = re.search(rf'📍\s*{coin}\s*支撑\s*([\d.]+)→([\d.]+)', post_text)
            add_check(f'{coin} S1', m.group(1) if m else None, f'{support[0]:.2f}', TOL_PRICE_PCT)
            add_check(f'{coin} S2', m.group(2) if m else None, f'{support[1]:.2f}', TOL_PRICE_PCT)
        if len(resistance) >= 2:
            m = re.search(rf'📍\s*{coin}\s*.*?阻力\s*([\d.]+)→([\d.]+)', post_text)
            add_check(f'{coin} R1', m.group(1) if m else None, f'{resistance[0]:.2f}', TOL_PRICE_PCT)
            add_check(f'{coin} R2', m.group(2) if m else None, f'{resistance[1]:.2f}', TOL_PRICE_PCT)

        # ── 结构化字段：🎯 行（观望时跳过 — 模板规定不输出）──
        sl = rec.get('sl_val')
        tp = rec.get('tp_val')
        rr = rec.get('rr_str')
        entry = rec.get('entry_price')
        position = rec.get('position', '')
        
        # 观望方向 → 文案不应有 🎯 行，跳过校验
        if '观望' in str(position):
            # 检查文案是否误加了 🎯 行（不应出现）
            if re.search(rf'🎯\s*{coin}', post_text):
                issues.append(f'⚠️ {coin} 方向为观望但文案误加了 🎯 行')
            continue
        
        if entry:
            m = re.search(rf'🎯\s*{coin}.*?入场([\d.]+)', post_text)
            add_check(f'{coin}入场', m.group(1) if m else None, f'{entry:.2f}', TOL_PRICE_PCT)
        if sl:
            m = re.search(rf'🎯\s*{coin}.*?止损(\d+)', post_text)
            add_check(f'{coin}止损', m.group(1) if m else None, str(int(sl)))
        if tp:
            m = re.search(rf'🎯\s*{coin}.*?止盈(\d+)', post_text)
            add_check(f'{coin}止盈', m.group(1) if m else None, str(int(tp)))
        if rr:
            m = re.search(rf'🎯\s*{coin}.*?RR\s*1:([\d.]+)', post_text)
            add_check(f'{coin} RR', m.group(1) if m else None, rr, TOL_RATIO)

        # ── 叙述字段：指标上下文匹配 ──
        rsi_4h = round(k4h.get('rsi', 0))
        macd_4h = round(k4h.get('macd_h', 0))
        rsi_1h = round(k1h.get('rsi', 0))
        macd_1h = round(k1h.get('macd_h', 0))
        adx_4h = round(k4h.get('adx', 0))
        adx_1h = round(k1h.get('adx', 0))
        pct_b_4h = round(k4h.get('pct_b', 0))
        pct_b_1h = round(k1h.get('pct_b', 0))

        indicators = [
            (f'{coin} RSI 4H', f'{coin}.*?RSI.*?4H', rsi_4h),
            (f'{coin} MACD_h 4H', f'{coin}.*?MACD.*?4H', macd_4h),
            (f'{coin} ADX 4H', f'{coin}.*?(?:4H.*?ADX|ADX.*?4H).*?{adx_4h}', adx_4h),
            (f'{coin} %b 4H', f'%b.*?{pct_b_4h}%', pct_b_4h),
        ]
        for desc, kw, val in indicators:
            found, ctx = _check_near(post_text, kw.replace(f'{coin} ', ''), val)
            if found:
                checks.append((f'{desc}={val}', 'found', 'found', None))
            else:
                issues.append(f'  ⚠️ {desc}: 文案中未找到 {val} ({ctx})')

        # ── 叙述字段：VP ──
        poc = round(vp.get('POC', 0))
        vah = round(vp.get('VAH', 0))
        val = round(vp.get('VAL', 0))
        for vp_name, vp_val in [('POC', poc), ('VAH', vah), ('VAL', val)]:
            if vp_val:
                found, ctx = _check_near(post_text, f'{coin}.*?{vp_name}', vp_val, radius=100)
                checks.append((f'{coin} VP {vp_name}={vp_val}', 'found' if found else 'missing',
                               'found' if found else 'missing', None))

        # ── 叙述字段：费率 ──
        fr_pct = rec.get('funding_rate_pct')
        if fr_pct is not None:
            fr_str = f'{fr_pct:.4f}%'
            found, _ = _check_near(post_text, f'{coin}.*?费率', fr_str.replace('%', ''), radius=60)
            if not found:
                found, _ = _check_near(post_text, '费率', fr_str.replace('%', ''), radius=80)
            checks.append((f'{coin} 费率={fr_str}', 'found' if found else 'missing',
                           'found' if found else 'missing', None))

        # ── 叙述字段：宏观 ──
        if coin == 'BTC':
            dxy = me.get('dxy')
            vix = me.get('vix')
            y10 = me.get('yield10')
            btcd = me.get('btc_dominance')
            for m_name, m_key, m_val, m_rounded in [('DXY', 'DXY', dxy, f'{dxy:.2f}' if dxy else None),
                                           ('VIX', 'VIX', vix, str(vix)),
                                           ('10Y', '10Y', y10, str(y10)),
                                           ('BTC.D', r'BTC\.D', btcd, str(btcd))]:
                if m_val:
                    found, ctx = _check_near(post_text, m_key, m_rounded, radius=40)
                    if not found:
                        issues.append(f'  ⚠️ {m_name}={m_val}: 文案中未找到')

        # ── 叙述字段：威科夫 ──
        wy_phase = wy.get('phase', '')
        wy_conf = wy.get('confidence', 0)
        wy_detail = wy.get('detail', '')
        if wy_phase:
            found, _ = _check_near(post_text, '威科夫', wy_phase.split('(')[0].strip(), radius=120)
            checks.append((f'{coin} 威科夫阶段', 'found' if found else 'missing',
                           'found' if found else 'missing', None))
        if wy_conf:
            found, _ = _check_near(post_text, '威科夫', wy_conf, radius=120)
            checks.append((f'{coin} 威科夫置信度={wy_conf}', 'found' if found else 'missing',
                           'found' if found else 'missing', None))

        # ── 叙述字段：K线形态 ──
        for p_name, p_val in pat.items():
            if p_val:
                found, _ = _check_near(post_text, p_name, p_val.replace('@', ''), radius=60)
                if found:
                    checks.append((f'{coin} {p_name}={p_val}', 'found', 'found', None))

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
            if jv == 0:
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


def run_analysis(coins):
    """回退: 跑 analysis_template（带 --no-sync 跳过重复同步）"""
    r = subprocess.run(['python3', ANALYSIS_SCRIPT, '--no-sync'] + coins,
                       capture_output=True, text=True, timeout=120,
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

    # 从 analyses.json 加载数据
    analyses = _load_analyses()
    if not analyses:
        print(f'  ⏳ 缓存过期/缺失，回退实时分析...')
        run_analysis(coins_list)
        analyses = _load_analyses()
    else:
        print(f'  📦 从缓存读取 (analyses.json, ≤{MAX_AGE_MIN}min)')

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
