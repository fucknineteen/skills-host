#!/usr/bin/env python3
"""
社交动态文案生成库 — 从分析数据生成社交动态草稿。
从 analysis_template.py 中提取，保持独立可导入。
"""
import os, sys, json, subprocess, time
from datetime import datetime

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

# ── 从 analysis_template.py 导入所有分析函数（统一底层实现）──────────
from analysis_template import (
    session_vp, wyckoff_detect, detect_kline_patterns, get_jin10_key_events,
    # 指标计算
    is_closed, calc_rsi, calc_macd, calc_adx, calc_bollinger, calc_obv,
    # K线 / 趋势 / 风控
    candle_body_label, trend_direction, check_acceleration,
    check_extreme_oversold, check_data_event_window,
    # 数据库查询
    get_rows, build_data_freshness,
    # 主分析函数
    analyze_single_coin as _base_analyze,
    # 常量
    MACD_PARAMS, TIMEFRAMES,
)

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

# ── 单币种完整分析 (wrapper) ─────────────────────────────────
def analyze_single_coin(conn, coin, ticker, funding, fg_val, fg_label, flash_news=None):
    """对单个币种执行完整分析。
    底层统一调用 analysis_template.analyze_single_coin，
    在此基础上补充 position / vp / wyckoff / calendar / macro / flash_news 六个社交专用字段。
    flash_news 可从外部预拉取传入（多币种共享），避免 per-coin 重复 MCP 调用。
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
    
    # ── 补全社交专用字段 ──
    resonance = result['resonance']
    rsi_1d_val = result['indicators']['1D'].get('rsi')
    trend_1d = result['indicators']['1D'].get('trend', '')
    macd_h_4h = result.get('macd_h_4h')
    near_bottom = result.get('near_bottom', False)
    
    # 方向判定 — 共振 + near_top + V反保护 (含8根4H反弹检测)
    position_raw = '偏多' if '强' in str(resonance) else ('偏空' if '弱' in str(resonance) else '观望（等确认）')
    # L1: near_top 做空捷径（trend_1d 为 analysis_template 格式：上升/下降/盘整）
    if position_raw == '观望（等确认）' and rsi_1d_val and rsi_1d_val > 67 and trend_1d == '下降' and macd_h_4h is not None and macd_h_4h < 0:
        position_raw = '偏空'
    # L3: V反保护 — 底部区域/反弹中禁止做空 (与 _format_coin_section 对齐)
    if position_raw == '偏空':
        if near_bottom:
            position_raw = '观望（near_bottom保护）'
        else:
            closed_4h = result.get('close_status', {}).get('4H', {}).get('closed', [])
            if closed_4h and len(closed_4h) >= 8:
                lows_8 = [r[3] for r in closed_4h[-8:]]
                min_low = min(lows_8)
                current = float(ticker.get('last', closed_4h[-1][4]))
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
    result['calendar_events'] = get_jin10_key_events()

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
            me = _rc.get('macro_external', {})
            macro_external = me if isinstance(me, dict) else {}
    except Exception:
        pass
    result['macro_external'] = macro_external
    
    return result

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

    # 📰 快讯 — 2026-06-20 新增
    flash_items = btc.get('flash_news', [])
    if flash_items:
        top_flash = sorted(flash_items, key=lambda x: x.get('score', 0), reverse=True)[:3]
        flash_lines = []
        for fi in top_flash:
            content = fi.get('content', '')
            if len(content) > 80:
                content = content[:77] + '...'
            flash_lines.append(f'  · {content}')
        if flash_lines:
            lines.append(f'📰 快讯：')
            lines.extend(flash_lines)

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
    # 观望时输出提示，有方向时输出 SL/TP
    if btc_dir_label != '观望' and btc_entry_f and btc_sl_val and btc_tp_val:
        rr_warn_btc = ' ⚠️' if btc_rr_str != '?' and float(btc_rr_str) < 1.5 else ''
        lines.append(f'🎯 BTC {btc_dir_label} | 入场{int(btc_entry_f):,} | 止损{btc_sl_val:,} | 止盈{btc_tp_val:,} | RR 1:{btc_rr_str}{rr_warn_btc}')
    elif btc_dir_label == '观望':
        lines.append(f'🎯 BTC 观望 | 空仓等风')
    if eth_dir_label != '观望' and eth_entry_f and eth_sl_val and eth_tp_val:
        rr_warn_eth = ' ⚠️' if eth_rr_str != '?' and float(eth_rr_str) < 1.5 else ''
        lines.append(f'🎯 ETH {eth_dir_label} | 入场{eth_entry_f:.0f} | 止损{eth_sl_val} | 止盈{eth_tp_val} | RR 1:{eth_rr_str}{rr_warn_eth}')
    elif eth_dir_label == '观望':
        lines.append(f'🎯 ETH 观望 | 空仓等风')

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
