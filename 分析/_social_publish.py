#!/usr/bin/env python3
"""
社交动态文案生成库 — 从分析数据生成社交动态草稿。
从 analysis_template.py 中提取，保持独立可导入。
"""
import os, sys, json, re, subprocess, time
from datetime import datetime
from _shared import BJT, TRADE_DIR, _retry

# ── 共享常量 ──────────────────────────────────────────────
_DB = os.path.join(TRADE_DIR, 'okx_klines.db')
_REGIME_CACHE = os.path.join(TRADE_DIR, '.regime_cache.json')
_REGIME_CACHE_TTL = 120  # 2 min

# ── 从 analysis_template.py 导入所有分析函数（统一底层实现）──────────
from analysis_template import (
    session_vp, wyckoff_detect, get_jin10_key_events,
    # 指标计算
    is_closed, calc_rsi, calc_macd, calc_adx, calc_bollinger, calc_obv,
    # K线 / 趋势 / 风控
    candle_body_label, trend_direction, check_acceleration,
    # 数据库查询
    get_rows, build_data_freshness,
    # 主分析函数
    analyze_single_coin as _base_analyze,
    # 常量
    MACD_PARAMS, TIMEFRAMES,
)

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
        r = subprocess.run([sys.executable, os.path.join(TRADE_DIR, 'regime_detector.py')],
            capture_output=True, text=True, timeout=30, cwd=TRADE_DIR)
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
    except subprocess.TimeoutExpired:
        print(f'  ⚠️ regime_detector 超时', file=sys.stderr)
    except Exception as e:
        print(f'  ⚠️ regime_detector 失败: {e}', file=sys.stderr)
    try:
        if os.path.exists(_REGIME_CACHE):
            with open(_REGIME_CACHE) as f:
                return json.load(f)
    except Exception:
        pass
    return {'regime': '未知', 'confidence': 0, 'composite_score': 0}

# ── 单币种完整分析 (wrapper) ─────────────────────────────────
def analyze_single_coin(conn, coin, ticker, funding, fg_val, fg_label, flash_news=None, calendar_events=None):
    """对单个币种执行完整分析。
    底层统一调用 analysis_template.analyze_single_coin，
    在此基础上补充 position / vp / wyckoff / calendar / macro / flash_news 六个社交专用字段。
    flash_news 和 calendar_events 可从外部预拉取传入（多币种共享），避免 per-coin 重复 MCP 调用。
    """
    
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
    
    # 调用 analysis_template 的基础分析（统一底层实现）
    result = _base_analyze(conn, coin, ticker, funding, fg_val, fg_label)
    
    # BUGFIX 6c: normalize coin field — strip USDT suffix for consistent naming
    if result.get('coin', '').endswith('USDT'):
        result['coin'] = result['coin'][:-4]
    
    # ── 计算 near_support/near_resistance (对齐 _format_coin_section L1325-1353) ──
    try: cp = float(ticker.get('last', 0))
    except (ValueError, TypeError): cp = 0
    closed_4h_sr = result['close_status']['4H']['closed']
    atr_4h_sr = result['indicators']['4H'].get('atr', cp * 0.02)
    if len(closed_4h_sr) >= 5:
        recent_sr = closed_4h_sr[-8:]
        atr_band_sr = max(atr_4h_sr * 2, cp * 0.01)
        if cp >= 1:
            highs_near_sr = sorted(set(round(r[2], 2) for r in recent_sr if r[2] > cp and r[2] - cp <= atr_band_sr), reverse=True)[:2]
            lows_near_sr = sorted(set(round(r[3], 2) for r in recent_sr if r[3] < cp and cp - r[3] <= atr_band_sr))[:2]
        else:
            highs_near_sr = sorted(set(r[2] for r in recent_sr if r[2] > cp and r[2] - cp <= atr_band_sr), reverse=True)[:2]
            lows_near_sr = sorted(set(r[3] for r in recent_sr if r[3] < cp and cp - r[3] <= atr_band_sr))[:2]
    else:
        highs_near_sr, lows_near_sr = [], []
    if not lows_near_sr:
        low_levels_sr = result.get('levels_4h', {}).get('lows', [])
        lows_near_sr = [x for x in low_levels_sr if x < cp][:2] if cp > 1 else low_levels_sr[:2]
    if not highs_near_sr:
        wider_sr = result['close_status']['4H']['closed'][-50:]
        all_highs_sr = sorted(set(round(r[2], 2) for r in wider_sr if r[2] > cp and r[2] - cp <= atr_band_sr * 2.5), reverse=True)
        highs_near_sr = all_highs_sr[:2] if all_highs_sr else []
        if not highs_near_sr:
            highs_near_sr = [round(cp * 1.02, 2)] if cp > 1 else []
    result['near_support'] = [float(x) for x in lows_near_sr]
    result['near_resistance'] = [float(x) for x in highs_near_sr]
    
    # ── 补全社交专用字段 ──
    resonance = result['resonance']
    rsi_1d_val = result['indicators']['1D'].get('rsi')
    trend_1d = result['indicators']['1D'].get('trend', '')
    macd_h_4h = result.get('macd_h_4h')
    near_bottom = result.get('near_bottom', False)
    
    # 方向判定 — 共振 + near_bottom/near_top + MACD/RSI 为辅 (与 _format_coin_section 对齐)
    # FIX BUG#1: near_bottom+偏弱共振+favorable MACD/RSI 应 → 偏多（在第一优先级检查，不会被V反降级）
    rsi_1h_val = result.get('rsi_1h', 50)
    if '强' in str(resonance) or (near_bottom and macd_h_4h is not None and macd_h_4h > -50 and rsi_1h_val is not None and rsi_1h_val < 45):
        position_raw = '偏多'
    elif '弱' in str(resonance) or (rsi_1d_val and rsi_1d_val > 65 and macd_h_4h is not None and macd_h_4h < -50):
        position_raw = '偏空'
    elif rsi_1d_val and rsi_1d_val > 67 and trend_1d in ('下降', '偏空') and macd_h_4h is not None and macd_h_4h < 0:
        position_raw = '偏空'
    elif near_bottom:
        position_raw = '偏多'
    else:
        position_raw = '观望（等确认）'

    # L3: V反保护 — 底部区域/反弹中禁止做空 (与 _format_coin_section 对齐)
    if position_raw == '偏空':
        if near_bottom:
            position_raw = '观望（near_bottom保护）'
        else:
            closed_4h = result.get('close_status', {}).get('4H', {}).get('closed', [])
            if closed_4h and len(closed_4h) >= 8:
                lows_8 = [r[3] for r in closed_4h[-8:]]
                min_low = min(lows_8)
                _last_val = ticker.get('last', closed_4h[-1][4])
                try:
                    current = float(_last_val) if _last_val and _last_val != '?' else 0
                except (ValueError, TypeError):
                    current = 0
                recovery_pct = (current - min_low) / min_low * 100 if min_low > 0 else 0
                if recovery_pct > 3:
                    position_raw = '观望（反弹{:.1f}%，V反保护）'.format(recovery_pct)
    result['position'] = position_raw
    
    # 威科夫 / Volume Profile / 日历 / 宏观
    result_dict = {
        'coin': coin, 'close_status': result['close_status'],
        'levels_4h': result['levels_4h'], 'indicators': result['indicators'],
        'accel': result['accel'], 'near_bottom': near_bottom,
        'resonance': resonance, 'risks': result.get('risks', []),
    }
    result['session_vp'] = session_vp(coin, conn) or {}
    result['wyckoff_data'] = wyckoff_detect(result_dict) or {}
    # kline_patterns 已在 _base_analyze 中计算，不覆盖
    result['calendar_events'] = calendar_events if calendar_events is not None else get_jin10_key_events()

    # 快讯 — 2026-06-20 新增 (外部传入则复用，否则自行拉取)
    if flash_news is not None:
        result['flash_news'] = flash_news
    else:
        _flash = []
        try:
            from jin10_fallback import fetch_flash_news as _fetch_flash
            flash_items, flash_source, flash_fresh = _fetch_flash()
            for item in flash_items[:8]:
                _flash.append({
                    'time': item.get('time', ''),
                    'content': item.get('content', ''),
                    'score': item.get('relevance_score', 0),
                })
        except Exception:
            pass
        result['flash_news'] = _flash
    
    # macro 数据（从 regime_cache，含 DXY/VIX/10Y/BTC.D）
    macro_external = {}
    try:
        if os.path.exists(_REGIME_CACHE):
            with open(_REGIME_CACHE) as _f:
                _rc = json.load(_f)
            me = _rc.get('dimensions', {}).get('macro_external', {})
            macro_external = me if isinstance(me, dict) else {}
    except Exception:
        pass
    result['macro_external'] = macro_external
    
    return result

# ── 方向提取 ──────────────────────────────────────────────
def extract_direction(coin_a):
    """从分析结果提取交易方向简述（含near_bottom/V反保护）"""
    if not coin_a: return ''
    resonance = coin_a.get('resonance', '')
    rsi_4h = coin_a.get('rsi_4h', 50)
    macd_h = coin_a.get('macd_h_4h', 0)
    near_bottom = coin_a.get('near_bottom', False)
    # V反检测（与 wrapper 中的 L3 对齐）
    # BUGFIX: use live ticker price instead of stale kline close
    closed_4h = (coin_a.get('close_status') or {}).get('4H', {}).get('closed', [])
    v_reversal = False
    if closed_4h and len(closed_4h) >= 8:
        lows_8 = [r[3] for r in closed_4h[-8:]]
        min_low = min(lows_8)
        try:
            current = float((coin_a.get('ticker') or {}).get('last', closed_4h[-1][4]))
        except (TypeError, ValueError):
            current = closed_4h[-1][4]
        recovery_pct = (current - min_low) / min_low * 100 if min_low > 0 else 0
        if recovery_pct > 3:
            v_reversal = True
    # near_bottom优先判断（对齐 wrapper position 判定 L170-179）
    # FIX: 与主分析逻辑一致 — '强'共振即可做多（不要求 macd_h > 0）
    #      near_bottom + favorable MACD/RSI → 偏多（不做空）
    rsi_1h_val = coin_a.get('rsi_1h', 50)
    if '强' in str(resonance) or (near_bottom and macd_h is not None and macd_h > -50 and rsi_1h_val is not None and rsi_1h_val < 45):
        return f"做多 RSI={rsi_4h:.0f} MACD={macd_h:.0f}"
    elif near_bottom and macd_h is not None and macd_h > -50:
        return f"观望（near_bottom）RSI={rsi_4h:.0f} MACD={macd_h:.0f}"
    elif ('弱' in str(resonance) or macd_h is not None and macd_h < -50) and not near_bottom and not v_reversal:
        return f"做空 RSI={rsi_4h:.0f}"
    return '观望'

# ── 恐惧贪婪简称 ──────────────────────────────────────────
def _fg_short(fg_val):
    """动态FG简称，不再硬编码'极度恐惧'"""
    if fg_val is None: return '未知'
    if fg_val < 25: return '极度恐惧'
    elif fg_val < 45: return '恐惧'
    elif fg_val < 55: return '中性'
    elif fg_val < 75: return '贪婪'
    else: return '极度贪婪'

# ── 社交动态文案生成 ──────────────────────────────────────
def calc_sl_tp(entry, near_s, near_r, atr, pos_label):
    """方向感知 SL/TP 计算，ATR 缓冲。统一模块级实现。
    BUGFIX 5a: 当数据不足但方向非观望时，返回估算值而非(0,0,'?')以避免文案误判。
    """
    if not entry or entry <= 0:
        return 0, 0, '?'
    if '观望' in str(pos_label):
        return 0, 0, '?'
    # Fallback: 如果 near_s/near_r 为空，基于 ATR 估算
    if not near_s or not near_r or not atr:
        if not atr:
            atr = entry * 0.03  # 默认 3% ATR
        if '空' in str(pos_label):
            sl = round(entry * 1.03) if not near_r else round(near_r[0] + atr * 0.5)
            tp = round(entry * 0.97) if not near_s else round(near_s[0])
        else:
            sl = round(entry * 0.97) if not near_s else round(near_s[0] - atr * 0.5)
            tp = round(entry * 1.03) if not near_r else round(near_r[0])
        if abs(entry - sl) <= 0:
            return sl, tp, '?'
        rr = abs(tp - entry) / abs(entry - sl)
        return sl, tp, f'{rr:.1f}' if rr > 0 else '?'
    if '空' in str(pos_label):
        sl = round(near_r[0] + atr * 0.5)
        tp = round(near_s[0])
    else:
        sl = round(near_s[0] - atr * 0.5)
        tp = round(near_r[0])
    if abs(entry - sl) <= 0:
        return sl, tp, '?'
    rr = abs(tp - entry) / abs(entry - sl)
    return sl, tp, f'{rr:.1f}' if rr > 0 else '?'

def generate_social_draft(analyses, regime_result, fg_val, fg_label, review_text=''):
    """用分析数据填充社交动态模板v5.1，输出完整8段落文案草稿。10:30修改+24项修复版"""
    now_bj = datetime.now(BJT)
    btc = next((a for a in analyses if a['coin'] == 'BTC'), {})
    eth = next((a for a in analyses if a['coin'] == 'ETH'), {})

    btc_p = btc.get('ticker', {}).get('last', '?')
    eth_p = eth.get('ticker', {}).get('last', '?')
    try: btc_pf = float(btc_p) if btc_p != '?' else 0
    except: btc_pf = 0
    try: eth_pf = float(eth_p) if eth_p != '?' else 0
    except: eth_pf = 0

    btc_rsi4 = btc.get('rsi_4h', 50)
    btc_macd4 = btc.get('macd_h_4h', 0)
    bt_resonance = btc.get('resonance', '')
    eth_resonance = eth.get('resonance', '')

    # FIX #1: unify SL/TP data source — use near_support/near_resistance (same as _format_coin_section)
    btc_near_s = btc.get('near_support', []) or btc.get('levels_4h', {}).get('lows', [])
    btc_near_r = btc.get('near_resistance', []) or btc.get('levels_4h', {}).get('highs', [])
    eth_near_s = eth.get('near_support', []) or eth.get('levels_4h', {}).get('lows', [])
    eth_near_r = eth.get('near_resistance', []) or eth.get('levels_4h', {}).get('highs', [])

    regime = regime_result.get('regime', '')
    confidence = regime_result.get('confidence', 0)
    composite = regime_result.get('composite_score', 0)

    s_btc = '→'.join(f'{int(x)}' for x in btc_near_s[:2]) if btc_near_s else '?'
    r_btc = '→'.join(f'{int(x)}' for x in btc_near_r[:2]) if btc_near_r else '?'
    s_eth = '→'.join(f'{int(x)}' for x in eth_near_s[:2]) if eth_near_s else '?'
    r_eth = '→'.join(f'{int(x)}' for x in eth_near_r[:2]) if eth_near_r else '?'

    btc_ind = btc.get('indicators', {})
    eth_ind = eth.get('indicators', {})
    btc_vp = btc.get('session_vp', {}); eth_vp = eth.get('session_vp', {})
    btc_poc = btc_vp.get('poc'); btc_vah = btc_vp.get('vah'); btc_val = btc_vp.get('val')
    eth_poc = eth_vp.get('poc'); eth_vah = eth_vp.get('vah'); eth_val = eth_vp.get('val')
    btc_wy = btc.get('wyckoff_data', {}); eth_wy = eth.get('wyckoff_data', {})
    btc_wy_phase = btc_wy.get('phase', ''); btc_wy_conf = btc_wy.get('confidence', 0); btc_wy_detail = btc_wy.get('detail', '')
    eth_wy_phase = eth_wy.get('phase', ''); eth_wy_conf = eth_wy.get('confidence', 0)
    dim = regime_result.get('dimensions', {})
    order_flow = dim.get('order_flow', {})
    funding_rate_pct = order_flow.get('funding_rate_pct', 0) or 0
    taker_buy_ratio = order_flow.get('taker_buy_ratio', 0.5) or 0.5
    macro = dim.get('macro_external', {})
    dxy = macro.get('dxy'); vix = macro.get('vix'); yield10 = macro.get('yield10')
    btc_dom = macro.get('btc_dominance'); vix_change = macro.get('vix_change_pct')

    btc_pos = btc.get('position', '观望'); eth_pos = eth.get('position', '观望')
    btc_dir_label = '偏多' if '多' in str(btc_pos) else ('偏空' if '空' in str(btc_pos) else '观望')
    eth_dir_label = '偏多' if '多' in str(eth_pos) else ('偏空' if '空' in str(eth_pos) else '观望')
    btc_atr = btc_ind.get('4H', {}).get('atr', btc_pf * 0.02)
    eth_atr = eth_ind.get('4H', {}).get('atr', eth_pf * 0.02)
    btc_sl_val, btc_tp_val, btc_rr_str = calc_sl_tp(btc_pf, btc_near_s, btc_near_r, btc_atr, btc_pos)
    eth_sl_val, eth_tp_val, eth_rr_str = calc_sl_tp(eth_pf, eth_near_s, eth_near_r, eth_atr, eth_pos)

    lines = []

    if '强' in str(bt_resonance) and btc_macd4 > 200:
        title = f'BTC 4H共振偏强 MACD +{btc_macd4:.0f}——{regime}中的突破前夜？🔥'
    elif btc_macd4 > 0: title = f'BTC震荡偏多 RSI={btc_rsi4:.0f} {regime}——{_fg_short(fg_val)}中谁在默默吃货'
    else: title = f'{regime}·{_fg_short(fg_val)}——散户割肉时庄家在干嘛'
    lines.append(title)

    if review_text: lines.append(''); lines.append('📋 上条复盘'); lines.append(review_text)

    bj_time_str = now_bj.strftime("%m-%d %H:%M")
    lines.append(f''); lines.append(f'🕐 BJ {bj_time_str} | BTC ${btc_p} | ETH ${eth_p} | FG:{fg_val}({fg_label})')

    lines.append(f''); lines.append(f'💡 大资金在干嘛？')
    # ── 叙事化：威科夫阶段 + VP定位 + detail解析 → 连贯段落 ──
    wy_sent = []
    if btc_wy_phase:
        # BUGFIX 3d: handle all Wyckoff phase formats including Phase B, C->D, etc.
        phase_clean = re.sub(r'\s*\(Phase\s+([A-E](?:->[A-E])?)\)', r' \1', btc_wy_phase).strip()
        wy_sent.append(f'{phase_clean}（{btc_wy_conf}%置信）')
    if btc_wy_detail:
        detail = btc_wy_detail
        m_rng = re.search(r'区间:([\d.]+)-([\d.]+)\(([\d.]+)%\)', detail)
        if m_rng: detail = f'{int(float(m_rng.group(1)))}-{int(float(m_rng.group(2)))}（{int(float(m_rng.group(3)))}%宽）'
        m_pct = re.search(r'回升([\d.]+)%', btc_wy_detail)
        if m_pct: detail += f'，已反弹{m_pct.group(1)}%'
        if 'HH+HL' in btc_wy_detail: detail += '，HH+HL结构完好'
        elif 'LH+LL' in btc_wy_detail: detail += '，LH+LL结构偏弱'
        wy_sent.append(detail)
    if btc_poc and btc_pf:
        if btc_vah and btc_pf > btc_vah: wy_sent.append(f'现价{btc_pf:.0f}已站上VAH({btc_vah:.0f})——多头控盘')
        elif btc_pf > btc_poc: wy_sent.append(f'现价{btc_pf:.0f}踩在POC({btc_poc:.0f})上方，略偏多头')
        elif btc_val and btc_pf > btc_val: wy_sent.append(f'现价{btc_pf:.0f}在价值区中部POC({btc_poc:.0f})附近，多空拉锯')
        else: wy_sent.append(f'现价{btc_pf:.0f}跌破VAL({btc_val:.0f})，偏弱防守')
    if eth_wy_phase:
        # BUGFIX 3d: handle all Wyckoff phase formats including Phase B, C->D, etc.
        eth_clean = re.sub(r'\s*\(Phase\s+([A-E](?:->[A-E])?)\)', r' \1', eth_wy_phase).strip()
        wy_sent.append(f'ETH {eth_clean}（{eth_wy_conf}%置信）')
    if composite < -10: wy_sent.append(f'{regime}承压中，等Spring确认再动手')
    elif composite < 10: wy_sent.append(f'{regime}格局中性，关键看BTC能否放量站上{r_btc}')
    else: wy_sent.append(f'{regime}偏强，守住{s_btc}就是多头阵地')
    lines.append('，'.join(wy_sent).rstrip('。') + '。')

    lines.append(f''); lines.append(f'📐 盘面看了什么？')
    for coin_name, coin_obj in [('BTC', btc), ('ETH', eth)]:
        ind = coin_obj.get('indicators', {})
        i1d = ind.get('1D', {}); i4h = ind.get('4H', {}); i1h = ind.get('1H', {})
        rsi_1d = i1d.get('rsi'); macd_4h = i4h.get('macd_h'); macd_1h = i1h.get('macd_h')
        adx_4h = i4h.get('adx'); trend_1d = i1d.get('trend', ''); trend_4h = i4h.get('trend', ''); trend_1h = i1h.get('trend', '')
        rsi_1h = i1h.get('rsi')
        signals = []
        if trend_1d and rsi_1d is not None: signals.append(f'日线{trend_1d}（RSI={rsi_1d:.0f}）')
        if macd_4h is not None and adx_4h is not None:
            macd_word = '扩张中' if abs(macd_4h) > 50 else ('收缩中' if abs(macd_4h) < 30 else '平稳')
            signals.append(f'4H {trend_4h} MACD{macd_word}（ADX={adx_4h:.0f}，趋势{"明确" if adx_4h>40 else "温和"}）')
        if rsi_1h is not None and macd_1h is not None:
            state_1h = '偏强' if rsi_1h > 55 else ('偏弱' if rsi_1h < 45 else '中性盘整')
            signals.append(f'1H {state_1h}（RSI={rsi_1h:.0f} MACD={macd_1h:+.0f}）')
        if signals: lines.append(f'{coin_name}：{"。".join(signals)}')
    lines.append(f'共振：BTC={bt_resonance} ETH={eth_resonance}')

    lines.append(f''); lines.append(f'📊 钱堆在哪？')
    mp = []
    if btc_poc and btc_pf:
        if btc_vah and btc_pf > btc_vah:
            mp.append(f'BTC踩在VAH({btc_vah:.0f})上方——多头控盘，价值区上沿变支撑')
        elif btc_pf > btc_poc:
            d2v = btc_vah - btc_pf if btc_vah else 0
            mp.append(f'BTC在POC({btc_poc:.0f})与VAH({btc_vah:.0f})之间——偏强运行，距VAH仅{d2v:.0f}点' if d2v > 0 else f'BTC踩POC({btc_poc:.0f})上方，略偏多头')
        elif btc_val and btc_pf > btc_val:
            mp.append(f'BTC在VAL({btc_val:.0f})与POC({btc_poc:.0f})之间——价值区中部，多空拉锯')
        else: mp.append(f'BTC跌破VAL({btc_val:.0f})——偏弱，下方支撑吃紧')
    if funding_rate_pct:
        if abs(funding_rate_pct) < 0.01: mp.append('费率中性（多空均衡，没有极端仓位）')
        elif funding_rate_pct > 0: mp.append(f'费率{funding_rate_pct:+.4f}%（多头略付钱，不极端）')
        else: mp.append(f'费率{funding_rate_pct:+.4f}%（空头略付钱，不极端）')
    else: mp.append('费率中性')
    if taker_buy_ratio:
        if taker_buy_ratio > 1.05: mp.append('Taker买方吃单占优——主动买盘强')
        elif taker_buy_ratio > 0.97: mp.append('Taker买卖均衡——没人抢跑')
        elif taker_buy_ratio > 0.90: mp.append('Taker卖方略占优——主动卖盘稍多')
        else: mp.append('Taker卖方主导——主动抛压明显')
    lines.append('；'.join(mp) + '。')

    lines.append(f''); lines.append(f'🌍 宏观给不给面子？')
    mp2 = []
    if fg_val is not None:
        if fg_val < 25: mp2.append(f'FG={fg_val}极度恐惧——历史大底区，做空是逆势')
        elif fg_val < 45: mp2.append(f'FG={fg_val}恐惧——散户不敢买，机构默默收')
        elif fg_val < 55: mp2.append(f'FG={fg_val}中性——市场冷静期')
        elif fg_val < 75: mp2.append(f'FG={fg_val}贪婪——追高需谨慎')
        else: mp2.append(f'FG={fg_val}极度贪婪——过热信号')
    if dxy:
        if dxy < 99: mp2.append(f'DXY={dxy:.1f}偏弱——利好风险资产')
        elif dxy < 102: mp2.append(f'DXY={dxy:.1f}中性——不拖后腿')
        else: mp2.append(f'DXY={dxy:.1f}偏强——压制加密')
    if vix is not None:
        vix_str = f'VIX={vix:.1f}'
        if vix_change is not None: vix_str += f'(Δ={vix_change:+.1f}%)'
        if vix < 15: mp2.append(f'{vix_str}低位——市场安逸，没恐慌')
        elif vix < 20: mp2.append(f'{vix_str}正常——风险偏好尚可')
        elif vix < 25: mp2.append(f'{vix_str}偏高——资金偏谨慎')
        else: mp2.append(f'{vix_str}恐慌区——避险模式')
    if yield10:
        if yield10 < 4.0: mp2.append(f'10Y={yield10:.2f}%低利率——利好成长资产')
        elif yield10 < 4.5: mp2.append(f'10Y={yield10:.2f}%中性——不再收紧')
        else: mp2.append(f'10Y={yield10:.2f}%偏高——资金偏保守')
    if btc_dom:
        if btc_dom > 60: mp2.append(f'BTC.D={btc_dom:.1f}%——钱还在BTC，山寨季未到')
        elif btc_dom > 55: mp2.append(f'BTC.D={btc_dom:.1f}%偏高——BTC吸血中')
        else: mp2.append(f'BTC.D={btc_dom:.1f}%下降——资金开始外溢')
    calendar = btc.get('calendar_events', []) or []
    if calendar:
        ev_names = [e.get('title', '')[:25] for e in calendar[:2] if isinstance(e, dict) and e.get('title')]
        if ev_names: mp2.append(f'本周关注{"、".join(ev_names)}')
    lines.append('；'.join(mp2) + '。')

    flash_items = btc.get('flash_news', [])
    if flash_items:
        # 筛选加密相关快讯（关键词匹配）
        crypto_kw = ['BTC','ETH','加密','比特','以太','区块链','DeFi','Web3','NFT','稳定币','减半','ETF',
                     'Coinbase','Binance','OKX','美联储','加息','降息','CPI','PCE','非农','FOMC','DXY',
                     '美元','通胀','避险','风险资产','美股','纳指','标普',
                     'USDT','USDC','SOL','XRP','DOGE','LTC','ADA','BNB','AVAX','MATIC','POL','ARB','OP','SUI',
                     'NEAR','DOT','LINK','UNI','AAVE','TRON','APT','SEI','INJ','RUNE','RNDR','FET','AGIX',
                     'WLD','STRK','TIA','PEPE','SHIB','WIF','BONK','FLOKI','TON','ATOM','FIL','ICP','ALGO',
                     'VET','HBAR','STX','FTM','EGLD','FLOW','QNT','GRT','SAND','MANA','AXS','GALA','IMX',
                     'ENA','JUP','PYTH','JTO','W','TNSR','MEW','POPCAT','BOME','MYRO','币安','火币','Bybit',
                     'Upbit','DEX','CEX','Layer2','L2','L1','Altcoin','山寨','SOL','memecoin','memes',
                     '伊朗','中东','地缘','油','能源','制裁','黄金']
        # 黑名单：排除纯商品/行业类新闻 + 传统财经（A股/港股/上证/深证等）
        blacklist_kw = ['钢铁','螺纹钢','建筑钢','钢厂','大豆','棕榈','镍矿','锂企',
                        '压榨','开机率','油厂','毛棕榈','MPOC','钢银','Mysteel','LSEG',
                        'HMA','ESDM','基准价','精矿','津巴布韦锂','大豆产量','棕榈油理事会',
                        'A股','港股','上证','深证','沪深','创业板','科创板','北交所',
                        '恒生','恒指','国企指数','红筹','H股','上证综指','深证成指',
                        '中小板','新三板','ST股','涨停','跌停','打新','IPO审核',
                        '券商','公募','私募','信托','保险资管','银行理财',
                        '人民币','离岸','在岸','汇率','央行MLF','LPR','逆回购',
                        '房地产','房企','楼市','房价','房贷','公积金']
        crypto_flash = [fi for fi in flash_items
                        if any(kw in fi.get('content','') for kw in crypto_kw)
                        and not any(bk in fi.get('content','') for bk in blacklist_kw)]
        # 如果没有加密相关的，回退到全部（保底至少显示新闻）
        if not crypto_flash:
            crypto_flash = flash_items
        top_flash = sorted(crypto_flash, key=lambda x: x.get('score', 0), reverse=True)[:3]
        flash_lines = []
        for fi in top_flash:
            content = fi.get('content', '')
            if len(content) > 80: content = content[:77] + '...'
            flash_lines.append(f'  · {content}')
        if flash_lines: lines.append(f''); lines.append(f'📰 快讯：'); lines.extend(flash_lines)

    lines.append(f''); lines.append(f'---')
    if btc_dir_label == '偏多': concl = f'BTC偏强，不破{s_btc if s_btc != "?" else "现价"}支撑可试多。等放量阳线突破{r_btc if r_btc != "?" else "前高"}确认。'
    elif btc_dir_label == '偏空': concl = f'BTC共振偏弱，不追空等站上{r_btc if r_btc != "?" else "前高"}再判断。'
    else: concl = f'{regime}分歧市中多看少动。等放量阳线确认方向再入场。'
    lines.append(concl)

    lines.append(f''); lines.append(f'📍 BTC 支撑 {s_btc} | 阻力 {r_btc}'); lines.append(f'📍 ETH 支撑 {s_eth} | 阻力 {r_eth}')
    lines.append(f'')
    if btc_dir_label != '观望' and btc_pf and btc_sl_val and btc_tp_val:
        try: _btc_rr_f = float(btc_rr_str) if btc_rr_str and btc_rr_str != '?' else 0
        except (ValueError, TypeError): _btc_rr_f = 0
        rr_warn_btc = ' ⚠️' if _btc_rr_f is not None and _btc_rr_f < 1.5 else ''
        lines.append(f'🎯 BTC {btc_dir_label} | 入场{btc_pf:.0f} | 止损{btc_sl_val} | 止盈{btc_tp_val} | RR 1:{btc_rr_str}{rr_warn_btc}')
    elif btc_dir_label == '观望': lines.append(f'🎯 BTC 观望 | 空仓等风')
    else: lines.append(f'🎯 BTC {btc_dir_label} | 入场{btc_pf:.0f} | SL/TP待计算（数据不足）')
    if eth_dir_label != '观望' and eth_pf and eth_sl_val and eth_tp_val:
        try: _eth_rr_f = float(eth_rr_str) if eth_rr_str and eth_rr_str != '?' else 0
        except (ValueError, TypeError): _eth_rr_f = 0
        rr_warn_eth = ' ⚠️' if _eth_rr_f is not None and _eth_rr_f < 1.5 else ''
        lines.append(f'🎯 ETH {eth_dir_label} | 入场{eth_pf:.0f} | 止损{eth_sl_val} | 止盈{eth_tp_val} | RR 1:{eth_rr_str}{rr_warn_eth}')
    elif eth_dir_label == '观望': lines.append(f'🎯 ETH 观望 | 空仓等风')
    else: lines.append(f'🎯 ETH {eth_dir_label} | 入场{eth_pf:.0f} | SL/TP待计算（数据不足）')
    lines.append(f'')
    if btc_dir_label == '偏空':
        if '分歧' in str(bt_resonance):
            lines.append(f'💬 {regime}偏弱分歧，顺势而为不猜底。')
        else:
            lines.append(f'💬 {regime}共振偏弱，顺势而为不猜底。')
    elif btc_dir_label == '偏多':
        if fg_val is not None and fg_val < 25: lines.append(f'💬 FG={fg_val}极度恐惧——历史上这个位置做空的都成了燃料。')
        elif '分歧' in str(bt_resonance):
            lines.append(f'💬 {regime}偏多分歧，顺势而为。')
        else:
            lines.append(f'💬 {regime}共振偏强，顺势而为。')
    else:
        if fg_val is not None and fg_val < 25: lines.append(f'💬 FG={fg_val}极度恐惧——等确认信号再入场。')
        elif '分歧' in str(bt_resonance):
            lines.append(f'💬 {regime}分歧中不打逆风局。')
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
    if btc.get('near_bottom', False) or 'Spring' in str(btc.get('bottom_note', '')):
        return 3
    # 默认 → Style 4 (方形卡片)
    return 4
