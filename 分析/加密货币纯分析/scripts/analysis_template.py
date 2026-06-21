#!/usr/bin/env python3
"""
============================================================================
加密货币分析模版 v5.0 — 一键拉取 + 指标计算 + 结论输出
============================================================================
用法:
    python3 analysis_template.py 比特币        # 中文别名
    python3 analysis_template.py btc            # 英文代码(大小写不敏感)
    python3 analysis_template.py BTC ETH SOL    # 多币种
    python3 analysis_template.py --all          # BTC+ETH+SOL+DOGE 全量

支持别名: 比特币/大饼→BTC, 以太坊/以太→ETH, 索拉纳/sol→SOL, 狗币/狗狗币→DOGE
============================================================================
"""
import os, sqlite3, json, subprocess, sys, math, time
from datetime import datetime, timezone, timedelta
from _shared import BJT, TRADE_DIR

# ── Retry helper for flaky API calls ──────────────────────────────
def _retry(fn, max_retries=5, delay=1.5):
    """Exponential backoff retry for transient failures."""
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

# =========================== 配置 ===========================
DB = '/root/.hermes/trade_review/okx_klines.db'
NOW_UTC = datetime.now(timezone.utc)
NOW_BJ = NOW_UTC.astimezone(BJT)  # astimezone preserves correct tzinfo for datetime arithmetic
NOW_MS = int(NOW_UTC.timestamp() * 1000)

ANALYSES_FILE = f'{TRADE_DIR}/analyses.json'
REVIEWS_PATH = f'{TRADE_DIR}/reviews.json'

# 币安 API Key（提升限额 + 防 451）
BINANCE_API_KEY = '6Dg88CFpt5ELADagMU248s1f84wa7shjYtbTvIk0wOF1pASd1syNsYPnllnPm2Ku'

TF_MS = {
    '1D':  86400000,   # 日线
    '4H':  14400000,   # 4小时
    '1H':  3600000,    # 1小时
    '30m': 1800000,    # 30分钟
    '5m':  300000,     # 5分钟
}
TIMEFRAMES = ['1D', '4H', '1H', '30m', '5m']
COINS_LARGE = ['BTC', 'ETH', 'SOL']
COINS_ALL = ['BTC', 'ETH', 'SOL', 'DOGE']

# 复盘教训 — 按币种分类，分析时自动应用约束
# 来源：6次72h复盘 (06-05~06-09)，三币独立验证
COIN_LESSONS = {
    'BTC': {
        'constraints': [
            ('B1', 'RSI<20+FG<15=V反信号，非空头延续（+5.3~6.3%，2次验证）', 'RSI_1D<20+FG<15 → 强制降级观望'),
            ('B2', '数据事件后12h内TA框架失真', 'CPI前48h → 方向置信度标[极低]'),
            ('B3', 'crowded_long+FG<15=逼空燃料', 'bias≤-2+FG<15 → 不给出做空建议'),
            ('B4', 'RSI<20+FG<15做空是系统性错误', '硬编码：此条件下空头方向→自动降级'),
        ],
    },
    'ETH': {
        'constraints': [
            ('E1', 'V反有延迟：先触极端低点(-6.2%)→再V反(+14.6%)', '不做左侧抄底，等日线阳线确认'),
            ('E2', '小TF信号<24h失效（12h正确≠72h正确）', '中长线需日线SOS确认'),
            ('E3', '反弹弹性>BTC（+7.9% vs +6.3%, 2次确认）', '极端超卖做空ETH盈亏比更差'),
            ('E4', 'CPI前ETH/BTC相关性>0.95，弹性差异被压制', '宏观事件前弹性规律不适用'),
        ],
    },
    'SOL': {
        'constraints': [
            ('S1', 'RSI 19+FG 12+bias=-3= V反+6.1%', '第3币确认X1，同模式'),
            ('S2', 'S1精确触及=反弹起点（60.02→67.9,+11.8%）', '极端超卖+支撑触碰≠跌破'),
            ('S3', 'bias=-3在FG=12是强烈反向信号', '拥挤空头=逼空燃料'),
        ],
    },
}

# MACD 参数 — 按币种分开，基于4年日线全网格扫描优化
# BTC: 12/75/9 (5日Sharpe=3.40, 胜率56.9%)
# ETH: 12/75/9 (5日Sharpe=2.31, 胜率53.3%)
# 扫描范围：快线3~12 × 慢线8~75 × 信号3~9 = 84组合
MACD_PARAMS = {
    'BTC': (12, 75, 9),
    'ETH': (12, 75, 9),
    'SOL': (12, 75, 9),
    'DOGE': (12, 75, 9),
}
def check_extreme_oversold(rsi_1d, fg_val):
    """X1: RSI<20 + FG<15 → V反概率极高"""
    if rsi_1d is not None and rsi_1d < 20 and fg_val is not None and fg_val < 15:
        return True, '[X1] RSI<20+FG<15 → V反概率极高，空头信号降级为观望'
    return False, None

def check_data_event_window():
    """X3: 重大数据事件前48h+后12h → 方向置信度最低
    动态检测最近/即将发生的重大事件（CPI/FOMC/非农）"""
    from datetime import datetime as dt
    # 已知重大事件（手动维护，按时间倒序）
    events = [
        # (名称, 时间 BJ, 类型)
        ("FOMC", dt(2026, 6, 18, 2, 0, tzinfo=BJT), "利率决议"),
        ("CPI", dt(2026, 7, 15, 20, 30, tzinfo=BJT), "通胀数据"),
        ("PPI", dt(2026, 6, 11, 20, 30, tzinfo=BJT), "通胀数据"),
    ]
    for name, evt_dt, etype in events:
        hours = (evt_dt - NOW_BJ).total_seconds() / 3600
        if -12 <= hours <= 48:
            return True, f'[X3] {name} {evt_dt.strftime("%m/%d %H:%M")} BJ — 距公布{hours:.0f}h，方向置信度最低'
        elif -24 <= hours <= 0:
            return True, f'[X3] {name} {evt_dt.strftime("%m/%d %H:%M")} BJ — 数据后{abs(hours):.0f}h，TA仍在消化'
    
    # 过期事件自动清理：超过48h的不再提示
    return False, None

# 仓位管理 — 小账户高杠杆公式
# 原则：每笔风险 ≤ 账户2%，爆仓价远离止损
# 仓位(张) = (账户×风险%) / (|入场-止损| × 合约面值)
# BTC合约面值0.01, ETH合约面值0.1
CONTRACT_SIZE = {'BTC': 0.01, 'ETH': 0.1, 'SOL': 1.0, 'DOGE': 10.0}
MAX_RISK_PCT = 2.0  # 单笔最大风险占账户百分比
LEVERAGE = 20  # 默认杠杆倍数
ACCOUNT_USD = 100  # 小账户本金
# OKX 维护保证金率%（mmr）
MARGIN_MAINTENANCE = {
    'BTC': 0.5, 'ETH': 1.0,
    'SOL': 2.0, 'DOGE': 2.5,
}

def calc_position(coin, entry, sl, account_usd=None, risk_pct=None):
    """计算建议仓位张数 + 爆仓安全距离"""
    if account_usd is None:
        account_usd = ACCOUNT_USD
    if risk_pct is None:
        risk_pct = MAX_RISK_PCT
    cs = CONTRACT_SIZE.get(coin, 1.0)
    risk_usd = account_usd * risk_pct / 100
    sl_pct = abs(entry - sl) / entry * 100  # 止损距离%

    if sl_pct < 0.1:  # 止损太近不可靠
        return None

    # 最大仓位(USD) = 风险金额 / 止损距离%
    max_notional = risk_usd / (sl_pct / 100)
    contracts = max_notional / (entry * cs)

    # 爆仓距离估算：杠杆倍数 → 爆仓距 = 100/杠杆% - 维护保证金%
    mm_pct = MARGIN_MAINTENANCE.get(coin, 1.5)
    liq_pct = 100 / LEVERAGE - mm_pct  # BTC=4.5%, ETH=4.0%

    return {
        'risk_usd': risk_usd,
        'sl_pct': sl_pct,
        'max_notional': max_notional,
        'contracts': math.floor(contracts),  # P2: OKX 要求整张
        'contracts_raw': contracts,  # 保留原始值供参考
        'liq_safe': sl_pct < liq_pct,  # 止损在爆仓前
        'liq_pct': liq_pct,
    }

# 别名映射
COIN_ALIASES = {
    'btc': 'BTC', '比特币': 'BTC', '大饼': 'BTC', 'bitcoin': 'BTC',
    'eth': 'ETH', '以太坊': 'ETH', '以太': 'ETH', '二饼': 'ETH', 'ethereum': 'ETH',
    'sol': 'SOL', '索拉纳': 'SOL', 'solana': 'SOL',
    'doge': 'DOGE', '狗币': 'DOGE', '狗狗币': 'DOGE', 'doge': 'DOGE', 'dogecoin': 'DOGE',
    'lab': 'LAB', 'labusdt': 'LAB',
    'home': 'HOME', 'homeusdt': 'HOME',
    'allo': 'ALLO', 'allousdt': 'ALLO',
    'ondo': 'ONDO', 'ondousdt': 'ONDO',
    'zec': 'ZEC', 'zecusdt': 'ZEC',
}

def get_db_coins():
    """动态获取 DB 中实际存在的币种"""
    conn = sqlite3.connect(DB)
    coins = [r[0] for r in conn.execute('SELECT DISTINCT coin FROM klines').fetchall()]
    conn.close()
    return coins


# =========================== Tool Functions ===========================

def is_closed(ts, tf):
    """判读蜡烛是否已收盘：ts + tf_ms ≤ now_ms"""
    return (ts + TF_MS[tf]) <= NOW_MS

def bj_time(ts):
    """UTC毫秒 → BJ datetime 字符串"""
    return datetime.fromtimestamp(ts / 1000, BJT).strftime('%m-%d %H:%M')


def _fmt_price(v):
    """价格自适应精度：<1→4位, <100→2位, ≥100→整数"""
    if v < 1:
        return f'${v:.4f}'
    elif v < 100:
        return f'${v:.2f}'
    else:
        return f'${v:.0f}'


# ============================================================
# 指标计算函数 — 每个函数文档写了公式
# ============================================================

def calc_rsi(closes, period=14):
    """
    RSI(period) — 相对强弱指数 (Wilder 平滑)
    Δ = C[i] - C[i-1]
    Gain = max(Δ, 0)     Loss = max(-Δ, 0)
    AvgGain₀ = ΣGain[:period]/period
    AvgLoss₀ = ΣLoss[:period]/period
    AvgGain = (AvgGain×13 + Gain[i])/14
    AvgLoss = (AvgLoss×13 + Loss[i])/14
    RS = AvgGain / AvgLoss
    RSI = 100 - 100/(1 + RS)
    ◆ 使用：RSI>70=超买, RSI<30=超卖
    ◆ 需 >= period+1 根已收盘蜡烛
    """
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    rs = avg_g / avg_l if avg_l > 0 else float('inf')
    return 100 - 100 / (1 + rs) if rs != float('inf') else 100.0


def calc_macd(closes, fast=12, slow=75, signal=9):
    """
    MACD(fast, slow, signal) — 指数平滑异同移动平均线
    EMA(p) = α×Price + (1-α)×EMA[-1]   α = 2/(p+1)
    MACD线 = EMA(fast) - EMA(slow)
    信号线 = EMA(MACD线, signal)
    柱 = MACD线 - 信号线
    ◆ 自定义参数: 12/75/9（经4年日线全网格验证，Sharpe 4.0）
    ◆ 柱>0 + MACD>信号 = 多头
    ◆ 需 >= slow+signal 根已收盘蜡烛
    """
    def _ema(data, period):
        a = 2 / (period + 1)
        e = [data[0]]
        for i in range(1, len(data)):
            e.append(a * data[i] + (1 - a) * e[-1])
        return e
    if len(closes) < slow + signal:
        return None, None, None
    e_fast = _ema(closes, fast)
    e_slow = _ema(closes, slow)
    macd_vals = [e_fast[i] - e_slow[i] for i in range(len(closes))]
    sig_vals = _ema(macd_vals, signal)
    return macd_vals[-1], sig_vals[-1], macd_vals[-1] - sig_vals[-1]


def calc_adx(highs, lows, closes, period=14):
    """
    ADX(period) — 平均趋向指数 (Wilder DMI)
    TR = max(H-L, |H-Cp|, |L-Cp|)   (真实波幅)
    DM+ = H-Hp 若↑>↓且↑>0 否则0
    DM- = Lp-L 若↓>↑且↓>0 否则0
    DI+ = ΣDM+[:period]/ΣTR[:period]×100
    DI- = ΣDM-[:period]/ΣTR[:period]×100
    DX = |DI+ - DI-|/(DI+ + DI-)×100
    ADX = WilderSmooth(DX, period)
    ◆ ADX>25=趋势强, ADX<20=震荡
    ◆ 衡量强度不是方向
    """
    if len(highs) < period + 1:
        return None, None, None, None
    tr_list, dm_p, dm_m = [], [], []
    for i in range(1, len(highs)):
        h, l, c_prev = highs[i], lows[i], closes[i-1]
        hp, lp = highs[i-1], lows[i-1]
        tr_list.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
        up, dn = h - hp, lp - l
        dm_p.append(max(up, 0) if up > dn else 0)
        dm_m.append(max(dn, 0) if dn > up else 0)
    atr_sum = sum(tr_list[:period])
    if atr_sum == 0: return None, None, None, None
    di_p_raw = sum(dm_p[:period]) / atr_sum * 100
    di_m_raw = sum(dm_m[:period]) / atr_sum * 100
    denom = di_p_raw + di_m_raw
    dx_val = abs(di_p_raw - di_m_raw) / denom * 100 if denom > 0 else 0
    return dx_val, di_p_raw, di_m_raw, atr_sum / period


def calc_bollinger(closes, period=20, mult=2):
    """
    布林带(period, mult) — 波动率通道
    中轨 = MA(C, period)
    σ = √(Σ(C-MA)² / period)
    上轨 = 中轨 + mult×σ
    下轨 = 中轨 - mult×σ
    %b = (价格 - 下轨) / (上轨 - 下轨) × 100
    ◆ 0%<价格<100% = 在带内
    ◆ >100% = 上破, <0% = 下破
    ◆ 需 >= period+1 根已收盘蜡烛
    """
    if len(closes) < period + 1:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    latest = closes[-1]
    upper = mean + mult * std
    lower = mean - mult * std
    pct_b = (latest - lower) / (upper - lower) * 100 if std > 0 else 50
    return {'upper': upper, 'mid': mean, 'lower': lower, 'pct_b': pct_b}


def calc_obv(rows):
    """
    OBV — 能量潮 (累积量价)
    if C[i] > C[i-1]: OBV += V[i]
    elif C[i] < C[i-1]: OBV -= V[i]
    else: OBV += 0
    ◆ 同向=趋势健康, 背离=趋势反转预警
    ◆ 仅用已收盘蜡烛
    """
    obv = 0
    for i in range(1, len(rows)):
        if rows[i][4] > rows[i-1][4]:
            obv += rows[i][5]
        elif rows[i][4] < rows[i-1][4]:
            obv -= rows[i][5]
    return obv


def candle_body_label(r):
    """
    K线形态量化 (必须计算, 禁止凭感觉)
    实体 = |C-O|   上影 = H-max(O,C)
    下影 = min(O,C)-L   总长 = H-L
    ◆ 实体>总长×0.7 → 大阳(阴)线 (方向性K线)
    ◆ 实体≤总长×0.1 → 十字星 (僵持)
    ◆ 下影≥实体×2 ∧ 上影≤实体×0.3 → 锤子线 (底部反转)
    ◆ 上影≥实体×2 ∧ 下影≤实体×0.3 → 射击之星 (顶部反转)
    """
    body = abs(r[4] - r[1])
    upper_shadow = r[2] - max(r[1], r[4])
    lower_shadow = min(r[1], r[4]) - r[3]
    total = r[2] - r[3]
    if total == 0:
        return '-'
    ratio = body / total
    dir_sign = '+' if r[4] > r[1] else '-'
    if ratio >= 0.7:
        return f'大阳{dir_sign}' if dir_sign == '+' else f'大阴{dir_sign}'
    if ratio <= 0.1:
        return '十字星'
    if lower_shadow >= body * 2 and upper_shadow <= body * 0.3 and body <= total * 0.35:
        return '锤子线'
    if upper_shadow >= body * 2 and lower_shadow <= body * 0.3 and body <= total * 0.35:
        return '射击之星'
    return f'普通{dir_sign}'


def trend_direction(rows):
    """道氏方向：HH/HL = 上升, LH/LL = 下降 (基于已收盘蜡烛收盘价)"""
    if len(rows) < 4:
        return '数据不足'
    prices = [r[4] for r in rows[-4:]]
    if prices[-1] > prices[-2] and prices[-3] > prices[-4]:
        return '上升'
    elif prices[-1] < prices[-2] and prices[-3] < prices[-4]:
        return '下降'
    else:
        return '盘整'


def check_acceleration(days, tf='1D'):
    """
    趋势加速检查 — 判断能否标 near_bottom
    日线实体逐根放大 = 加速下跌 → 禁用 near_bottom
    """
    if len(days) < 3:
        return 'normal'
    bodies = [abs(r[4] - r[1]) for r in days[-3:]]
    accelerating = all(bodies[i] >= bodies[i-1] * 0.7 for i in range(1, 3))
    all_bear = all(r[4] < r[1] for r in days[-3:])
    if accelerating and all_bear:
        return 'accelerating_bear'
    if bodies[-1] < bodies[-2] * 0.5:
        return 'decelerating'
    return 'steady'


# =========================== 数据拉取 ===========================

def fetch_okx_ticker(inst_id):
    """拉取 OKX 实时行情 (curl — urllib 被 CF 403)，含指数退避重试"""
    def _call():
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '10',
                f'https://www.okx.com/api/v5/market/ticker?instId={inst_id}'],
                capture_output=True, text=True, timeout=15)
            d = json.loads(r.stdout) if r.stdout else {}
            if d.get('code') == '0' and d.get('data'):
                return d['data'][0]
        except Exception as e:
            return {'_error': str(e)}
        return {'_error': 'no_data'}
    return _retry(_call)


def fetch_okx_funding(inst_id):
    """拉取 OKX 资金费率"""
    def _call():
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '10',
                f'https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}'],
                capture_output=True, text=True, timeout=15)
            d = json.loads(r.stdout) if r.stdout else {}
            if d.get('code') == '0' and d.get('data'):
                return d['data'][0]
        except Exception as e:
            return {'_error': str(e)}
        return {'_error': 'no_data'}
    return _retry(_call)


def fetch_fear_greed():
    """拉取恐惧贪婪指数 (curl — urllib 被 SSL EOF)"""
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '15',
            '-H', 'User-Agent: Mozilla/5.0',
            'https://api.alternative.me/fng/'],
            capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('data'):
            return int(d['data'][0]['value']), d['data'][0]['value_classification']
    except Exception as e:
        return None, str(e)
    return None, 'no_data'


# =========================== 币安实时拉取（DB无数据时使用） ===========================

# 内存缓存：{(coin, tf): [rows]} — 一次拉取全周期，不存 DB
_CACHE = {}
BINANCE_TF_MAP = {'1D': '1d', '4H': '4h', '1H': '1h', '30m': '30m', '5m': '5m'}
OKX_TF_MAP = {'1D': '1D', '4H': '4H', '1H': '1H', '30m': '30m', '5m': '5m'}


def fetch_okx_klines(coin, tf='1D', limit=300):
    """OKX K线拉取 — 优先于币安（更稳定）"""
    cache_key = (coin, tf)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '10',
            f'https://www.okx.com/api/v5/market/candles'
            f'?instId={coin}-USDT-SWAP&bar={OKX_TF_MAP.get(tf,"1D")}&limit={limit}'],
            capture_output=True, text=True, timeout=15)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('code') == '0' and d.get('data'):
            rows = [(int(e[0]), float(e[1]), float(e[2]), float(e[3]),
                     float(e[4]), float(e[5])) for e in d['data']]
            rows_asc = sorted(rows, key=lambda r: r[0])
            _CACHE[cache_key] = rows_asc
            return rows_asc
    except Exception:
        pass
    return []


def fetch_binance_klines(coin, tf='1D', limit=500):
    """
    从币安合约拉取K线 — OKX 失败时回退
    容错：空响应/非列表/限流均返回空列表
    """
    cache_key = (coin, tf)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    interval = BINANCE_TF_MAP.get(tf, '1d')
    url = (f'https://fapi.binance.com/fapi/v1/klines'
           f'?symbol={coin}USDT&interval={interval}&limit={limit}')

    for attempt in range(2):
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '12',
                '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}', url],
                capture_output=True, text=True, timeout=18)
            if not r.stdout or not r.stdout.strip():
                time.sleep(1)
                continue
            data = json.loads(r.stdout)
            if not isinstance(data, list):
                time.sleep(1)
                continue
            rows = [(int(d[0]), float(d[1]), float(d[2]), float(d[3]),
                     float(d[4]), float(d[5])) for d in data if isinstance(d, list)]
            if rows:
                _CACHE[cache_key] = rows
                return rows
        except (json.JSONDecodeError, ValueError, KeyError, IndexError):
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return []


def fetch_binance_ticker(coin):
    """币安 24H 完整行情 — 当 OKX 没有该币种时回退"""
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '8',
            '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}',
            f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={coin}USDT'],
            capture_output=True, text=True, timeout=12)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('lastPrice'):
            return {
                'last': d['lastPrice'],
                'open24h': d.get('openPrice', '?'),
                'high24h': d.get('highPrice', '?'),
                'low24h': d.get('lowPrice', '?'),
                'vol24h': d.get('volume', '?'),
                'quoteVol24h': d.get('quoteVolume', '?'),
                'change24h': d.get('priceChangePercent', '0'),
                '_source': 'binance',
            }
    except Exception:
        pass
    return {'_error': 'binance_ticker_failed'}


def fetch_binance_funding(coin):
    """币安资金费率"""
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '8',
            '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}',
            f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={coin}USDT'],
            capture_output=True, text=True, timeout=12)
        d = json.loads(r.stdout) if r.stdout else {}
        if isinstance(d, list) and d:
            return {'fundingRate': str(d[-1].get('fundingRate', '0')),
                    'fundingTime': d[-1].get('fundingTime', 0)}
        elif d.get('fundingRate'):
            return {'fundingRate': str(d['fundingRate'])}
    except Exception:
        pass
    return {'_error': 'binance_funding_failed'}


def fetch_binance_oi(coin):
    """币安持仓量"""
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '8',
            '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}',
            f'https://fapi.binance.com/fapi/v1/openInterest?symbol={coin}USDT'],
            capture_output=True, text=True, timeout=12)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('openInterest'):
            return {'openInterest': d['openInterest']}
    except Exception:
        pass
    return {}


def fetch_binance_sentiment(coin):
    """币安多空比 + Taker 量比"""
    result = {}
    try:
        r1 = subprocess.run(['curl', '-s', '--max-time', '8',
            '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}',
            f'https://fapi.binance.com/fapi/v1/globalLongShortAccountRatio'
            f'?symbol={coin}USDT&period=5m&limit=1'],
            capture_output=True, text=True, timeout=12)
        d1 = json.loads(r1.stdout) if r1.stdout else []
        if isinstance(d1, list) and d1:
            result['longShortRatio'] = float(d1[-1].get('longShortRatio', 0))
        r2 = subprocess.run(['curl', '-s', '--max-time', '8',
            '-H', f'X-MBX-APIKEY: {BINANCE_API_KEY}',
            f'https://fapi.binance.com/fapi/v1/takerlongshortRatio'
            f'?symbol={coin}USDT&period=5m&limit=1'],
            capture_output=True, text=True, timeout=12)
        d2 = json.loads(r2.stdout) if r2.stdout else []
        if isinstance(d2, list) and d2:
            result['takerRatio'] = float(d2[-1].get('buySellRatio', 0))
    except Exception:
        pass
    return result


def prefetch_coin(coin):
    """拉取全周期K线：OKX 优先 → 币安回退"""
    fetched = 0
    for tf in TIMEFRAMES:
        limit = 500 if tf in ('5m', '30m') else 300
        rows = fetch_okx_klines(coin, tf, limit)
        if not rows:
            rows = fetch_binance_klines(coin, tf, limit)
        if rows:
            fetched += len(rows)
        time.sleep(0.2)
    return fetched


def get_rows(conn, coin, tf, limit=100):
    """
    统一数据获取：优先 DB，DB无数据则从币安缓存取
    返回: (closed_rows, unclosed_rows)
    """
    rows = conn.execute(
        f'SELECT ts, open, high, low, close, volume FROM klines '
        f'WHERE coin=? AND timeframe=? ORDER BY ts ASC',
        (coin, tf)
    ).fetchall()

    if rows:
        closed = [r for r in rows if is_closed(r[0], tf)]
        unclosed = [r for r in rows if not is_closed(r[0], tf)]
        return closed, unclosed

    cache_key = (coin, tf)
    if cache_key in _CACHE:
        rows = _CACHE[cache_key]
        rows_asc = sorted(rows, key=lambda r: r[0])
        closed = [r for r in rows_asc if is_closed(r[0], tf)]
        unclosed = [r for r in rows_asc if not is_closed(r[0], tf)]
        return closed, unclosed

    return [], []


# =========================== 主分析流程 ===========================

def build_data_freshness(conn, coin):
    """构建数据新鲜度字典，供LLM在报告中标注过期数据。
    
    Returns dict格式:
      {source: {'fetched_at': ISO str or 'N/A', 'age_minutes': int or -1, 'stale': bool, 'expire_minutes': int}}
    """
    from datetime import timezone, timedelta
    
    now = time.time()
    BJ = BJT  # use module-level BJT from _shared
    
    def check_file(filepath, expire_min):
        """检查缓存文件的新鲜度"""
        if os.path.exists(filepath):
            age = (now - os.path.getmtime(filepath)) / 60
            return {
                'fetched_at': datetime.fromtimestamp(os.path.getmtime(filepath), tz=BJ).strftime('%m-%d %H:%M'),
                'age_minutes': round(age, 1),
                'stale': age > expire_min,
                'expire_minutes': expire_min
            }
        return {'fetched_at': 'N/A', 'age_minutes': -1, 'stale': True, 'expire_minutes': expire_min}
    
    freshness = {}
    
    for tf in ['1D', '4H', '1H']:
        rows = conn.execute(
            f'SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT 1',
            (coin, tf)
        ).fetchall()
        if rows:
            ts = rows[0][0] / 1000
            t_bj = datetime.fromtimestamp(ts, tz=BJ)
            freshness[f'kline_{tf}'] = {
                'fetched_at': t_bj.strftime('%m-%d %H:%M'),
                'age_minutes': round((now - ts) / 60, 1),
                'stale': False,
                'expire_minutes': 0
            }
    
    regime_path = os.path.expanduser('~/.hermes/trade_review/.regime_cache.json')
    freshness['macro_external'] = check_file(regime_path, 24*60)
    
    freshness['fred'] = check_file(regime_path, 7*24*60)
    
    jin10_cache = os.path.expanduser('~/.hermes/trade_review/data/jin10_calendar_cache.json')
    freshness['jin10_calendar'] = check_file(jin10_cache, 6*60)
    
    freshness['jin10_mcp'] = {
        'fetched_at': '实时' if not os.path.exists(os.path.expanduser('~/.hermes/data/jin10_mcp_down.flag')) else '断连',
        'age_minutes': 0,
        'stale': os.path.exists(os.path.expanduser('~/.hermes/data/jin10_mcp_down.flag')),
        'expire_minutes': 0
    }
    
    return freshness


def analyze_single_coin(conn, coin, ticker, funding, fg_val, fg_label):
    """对单个币种执行完整分析"""

    # ──── 收盘状态 ────
    close_status = {}
    for tf in TIMEFRAMES:
        closed, unclosed = get_rows(conn, coin, tf)
        close_status[tf] = {'closed': closed,
                            'n_closed': len(closed)}

    # ──── 指标计算 (每个周期) ────
    indicators = {}
    for tf in TIMEFRAMES:
        closed = close_status[tf]['closed']
        if len(closed) < 15:
            indicators[tf] = {'_skip': f'{len(closed)} candles, need 15+'}
            continue
        closes = [r[4] for r in closed]
        highs = [r[2] for r in closed]
        lows = [r[3] for r in closed]
        rsi = calc_rsi(closes, 14)
        mf, ms, msig = MACD_PARAMS.get(coin, (12, 75, 9))
        macd_l, macd_s, macd_h = calc_macd(closes, mf, ms, msig)
        adx, di_p, di_m, atr = calc_adx(highs, lows, closes, 14)
        bb = calc_bollinger(closes, 20, 2)
        obv = calc_obv(closed)
        trend_dir = trend_direction(closed)
        if trend_dir == '盘整' and macd_h is not None and macd_h > 0:
            if len(closes) >= 5 and closes[-1] >= max(closes[-5:]) * 0.98:
                trend_dir = '偏多'
        latest_label = candle_body_label(closed[-1]) if closed else '-'
        indicators[tf] = {
            'rsi': rsi, 'macd_l': macd_l, 'macd_s': macd_s, 'macd_h': macd_h,
            'adx': adx, 'di_p': di_p, 'di_m': di_m, 'atr': atr,
            'bb': bb, 'obv': obv, 'trend': trend_dir, 'label': latest_label,
            'last_close': closes[-1] if closes else None,
            'last_o': closed[-1][1] if closed else None,
            'last_h': closed[-1][2] if closed else None,
            'last_l': closed[-1][3] if closed else None,
            'last_v': closed[-1][5] if closed else None,
            'last_ts': closed[-1][0] if closed else None,
        }

    # ──── 加速下跌检查 (日线) ────
    closed_1d = close_status['1D']['closed']
    accel = check_acceleration(closed_1d) if len(closed_1d) >= 3 else 'insufficient_data'

    # ──── 支撑阻力 (已收盘蜡烛) ────
    def get_levels(tf, n=20):
        closed = close_status.get(tf, {}).get('closed', [])
        if len(closed) < n:
            closed = close_status['1D']['closed']
        recent = closed[-n:]
        return {'highs': sorted(set(round(r[2], 1) for r in recent), reverse=True),
                'lows': sorted(set(round(r[3], 1) for r in recent))}

    levels_4h = get_levels('4H')

    # ──── 底部研判 ────
    rsi_1d = indicators['1D'].get('rsi')
    near_bottom = False
    bottom_note = '-'
    if rsi_1d is not None:
        if rsi_1d < 33 and fg_val is not None and fg_val < 25:
            if accel == 'accelerating_bear':
                bottom_note = '加速下跌 → near_bottom 禁用'
            elif accel == 'decelerating':
                bottom_note = '减速 → near_bottom 可讨论但未确认'
            else:
                bottom_note = 'near_bottom (RSI<33 + FG<25) — 观望，等放量阳线'
                near_bottom = True
        elif rsi_1d < 33:
            bottom_note = 'RSI<33 但 FG 未知 → level 未定'

    # ──── 共振判断 ────
    rsi_4h = indicators['4H'].get('rsi')
    rsi_1h = indicators['1H'].get('rsi')
    macd_h_1h = indicators['1H'].get('macd_h')
    macd_h_4h = indicators['4H'].get('macd_h')
    bb_1h = indicators['1H'].get('bb')
    pct_b = bb_1h['pct_b'] if bb_1h else 50

    score = 0
    if rsi_4h is not None:
        if rsi_4h > 55: score += 1
        elif rsi_4h < 45: score -= 1
    if macd_h_4h is not None:
        if macd_h_4h > 0: score += 1
        elif macd_h_4h < 0: score -= 1
    if pct_b < 30: score -= 1
    elif pct_b > 70: score += 1

    if score >= 2:
        resonance = '🟢偏强'
    elif score <= -2:
        resonance = '🔴偏弱'
    else:
        resonance = '🟡分歧'

    # ──── 风险数据判断 ────
    risks = []
    lessons_warnings = []

    rsi_1d_val = indicators['1D'].get('rsi')
    is_x1, x1_msg = check_extreme_oversold(rsi_1d_val, fg_val)
    if is_x1:
        lessons_warnings.append(x1_msg)
        if near_bottom:
            near_bottom = False
            bottom_note = f'{x1_msg} — near_bottom被复盘教训覆盖，强制观望'

    is_x3, x3_msg = check_data_event_window()
    if is_x3:
        lessons_warnings.append(x3_msg)
        risks.append(x3_msg)

    # ── 数据选取指令（脚本级判定，LLM必须遵守）──
    _bj = BJT  # use module-level BJT from _shared
    _now = datetime.now(_bj)
    _now_ts = _now.timestamp()
    data_selection_lines = [
        f'📐 K线收盘状态 [{_now.strftime("%m-%d %H:%M")} BJ]:'
    ]
    _tf_close = {'1D': 86400, '4H': 14400, '1H': 3600, '30m': 1800, '5m': 300}
    for _tf, _sec in _tf_close.items():
        _rows = conn.execute(
            f'SELECT ts FROM klines WHERE coin=? AND timeframe=? ORDER BY ts DESC LIMIT 2',
            (coin, _tf)
        ).fetchall()
        if not _rows:
            data_selection_lines.append(f'  {_tf}: 无数据')
            continue
        
        _ts = _rows[0][0] / 1000
        _end = _ts + _sec
        _end_bj = datetime.fromtimestamp(_end, tz=_bj)
        
        if _end <= _now_ts:
            _t_bj = datetime.fromtimestamp(_ts, tz=_bj)
            data_selection_lines.append(f'  {_tf}: {_t_bj.strftime("%m-%d %H:%M")} ✅')
        else:
            _t_bj = datetime.fromtimestamp(_ts, tz=_bj)
            if len(_rows) > 1:
                _prev_ts = _rows[1][0] / 1000
                _prev_bj = datetime.fromtimestamp(_prev_ts, tz=_bj)
                data_selection_lines.append(f'  {_tf}: {_prev_bj.strftime("%m-%d %H:%M")} ✅ | 今{_t_bj.strftime("%m-%d %H:%M")}形成中(→{_end_bj.strftime("%m-%d %H:%M")})')
            else:
                data_selection_lines.append(f'  {_tf}: 今{_t_bj.strftime("%m-%d %H:%M")}形成中(→{_end_bj.strftime("%m-%d %H:%M")})')

    data_freshness = build_data_freshness(conn, coin)
    
    return {
        'coin': coin,
        'ticker': ticker,
        'funding': funding,
        'close_status': close_status,
        'data_freshness': data_freshness,
        'data_selection': '\n'.join(data_selection_lines),
        'kline_patterns': detect_kline_patterns({'close_status': close_status}),
        'indicators': indicators,
        'accel': accel,
        'levels_4h': levels_4h,
        'bottom_note': bottom_note,
        'near_bottom': near_bottom,
        'resonance': resonance,
        'risks': risks,
        'rsi_4h': rsi_4h, 'rsi_1h': rsi_1h,
        'macd_h_4h': macd_h_4h, 'macd_h_1h': macd_h_1h,
        'pct_b': pct_b,
        'lessons_warnings': lessons_warnings,
    }

# ══════════════════════════════════════════════════════════
#  威科夫阶段检测
# ══════════════════════════════════════════════════════════

def cvd_proxy(ticker, extra=None):
    """CVD方向代理 — Binance Taker 买卖比优先，OKX bid/ask回退。"""
    extra = extra or {}
    taker_ratio = extra.get('takerRatio')
    if taker_ratio and taker_ratio > 0:
        if taker_ratio > 1.2:
            strength = min(int((taker_ratio - 1) * 100), 100)
            return {'direction': 'bullish', 'strength': strength,
                    'detail': f'Taker买/卖={taker_ratio:.2f}(主动买入多)'}
        elif taker_ratio < 0.8:
            strength = min(int((1 - taker_ratio) * 100), 100)
            return {'direction': 'bearish', 'strength': strength,
                    'detail': f'Taker买/卖={taker_ratio:.2f}(主动卖出多)'}
        else:
            return {'direction': 'neutral', 'strength': 0,
                    'detail': f'Taker买/卖={taker_ratio:.2f}(均衡)'}
    try:
        bid = float(ticker.get('bidPx', 0))
        ask = float(ticker.get('askPx', 0))
        last = float(ticker.get('last', 0))
        if not last or not bid or not ask:
            return {'direction': 'neutral', 'strength': 0, 'detail': '数据不足'}
        mid = (bid + ask) / 2
        bias = (last - mid) / mid * 100
        spread = (ask - bid) / mid * 100
        if bias > 0.02:
            direction = 'bullish'
            strength = min(int(abs(bias) * 200), 100)
            detail = f'买方激进(价+{bias:.2f}% vs mid)'
        elif bias < -0.02:
            direction = 'bearish'
            strength = min(int(abs(bias) * 200), 100)
            detail = f'卖方激进(价{bias:.2f}% vs mid)'
        else:
            direction = 'neutral'
            strength = 0
            detail = f'中性(价{bias:+.2f}% vs mid, 价差{spread:.3f}%)'
        return {'direction': direction, 'strength': strength, 'detail': detail}
    except Exception:
        return {'direction': 'neutral', 'strength': 0, 'detail': '计算异常'}

def session_vp(coin, conn):
    """全天 Volume Profile（24h）— POC/VAH/VAL 从 15m K线计算。"""
    now_bj = datetime.now(BJT)
    end_ts = int(now_bj.timestamp() * 1000)
    start_ts = int((now_bj - timedelta(hours=24)).timestamp() * 1000)
    try:
        rows = conn.execute('''
            SELECT high, low, volume FROM klines
            WHERE coin=? AND timeframe='15m' AND ts >= ? AND ts <= ?
            ORDER BY ts ASC
        ''', (coin, start_ts, end_ts)).fetchall()
        if len(rows) < 8:
            return None
        prices = []
        for r in rows:
            prices.extend([r[0], r[1]])
        price_range = max(prices) - min(prices)
        if price_range <= 0:
            return None
        bin_step = price_range / 50
        if bin_step < 1 and max(prices) < 1:
            bin_step = max(prices) * 0.005
        bins = {}
        for r in rows:
            h, l, v = r[0], r[1], r[2]
            if h == l:
                k = round(h / bin_step) * bin_step if bin_step else h
                bins[k] = bins.get(k, 0) + v
            else:
                steps = max(1, int((h - l) / bin_step)) if bin_step else 1
                for i in range(steps):
                    p = l + (h - l) * (i + 0.5) / steps
                    k = round(p / bin_step) * bin_step if bin_step else p
                    bins[k] = bins.get(k, 0) + v / steps
        if not bins:
            return None
        total_vol = sum(bins.values())
        sorted_bins = sorted(bins.items(), key=lambda x: x[0])
        poc_val = max(bins, key=bins.get)
        cum = 0
        poc_idx = next(i for i, (p, _) in enumerate(sorted_bins) if abs(p - poc_val) < (bin_step * 0.5 if bin_step else 1))
        left, right = poc_idx, poc_idx
        cum = sorted_bins[poc_idx][1]
        target_vol = total_vol * 0.7
        while cum < target_vol and (left > 0 or right < len(sorted_bins) - 1):
            if left > 0 and (right >= len(sorted_bins) - 1 or 
                sorted_bins[poc_idx][0] - sorted_bins[left-1][0] <= sorted_bins[right+1][0] - sorted_bins[poc_idx][0]):
                left -= 1
                cum += sorted_bins[left][1]
            elif right < len(sorted_bins) - 1:
                right += 1
                cum += sorted_bins[right][1]
            else:
                break
        return {
            'hours': 24,
            'poc': poc_val,
            'vah': sorted_bins[right][0],
            'val': sorted_bins[left][0],
            'bars': len(rows)
        }
    except Exception:
        return None


def detect_kline_patterns(a, lookback=15):
    """威科夫K线形态识别 — 扫描最近N根4H收盘蜡烛。"""
    cs = a['close_status']
    patterns = []
    bars_used = {}
    for tf in ['4H', '1D']:
        closed = cs.get(tf, {}).get('closed', [])
        if len(closed) < 2:
            continue
        bars_used[tf] = len(closed)
        recent = closed[-lookback:] if len(closed) > lookback else closed
        for i in range(1, len(recent)):
            r = recent[i]
            prev = recent[i-1]
            o, h, l, c = r[1], r[2], r[3], r[4]
            po, pl = prev[1], prev[4]
            entity = c - o
            total_range = h - l if h > l else 0.0001
            entity_pct = abs(entity) / total_range * 100
            ts = r[0] / 1000
            t_bj = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
            date_str = t_bj.strftime('%m-%d %H:%M')
            range_low_all = min(x[3] for x in recent)
            range_high_all = max(x[2] for x in recent)
            range_span_all = range_high_all - range_low_all if range_high_all > range_low_all else 1
            in_lower = (c - range_low_all) < range_span_all * 0.33
            if (entity_pct > 60 and c > o and (c - l) > total_range * 0.3
                and entity > total_range * 0.5 and in_lower):
                vol = r[5] if len(r) > 5 else 0
                avg_vol = sum(x[5] for x in recent[-10:-1] if len(x) > 5) / 9 if recent else 0
                if avg_vol > 0 and vol > avg_vol * 1.5:
                    patterns.append((tf, date_str, 'SC', f'巨量{vol/avg_vol:.1f}x+长下影反弹'))
            if entity_pct > 85 and c > o:
                vol = r[5] if len(r) > 5 else 0
                avg_vol = sum(x[5] for x in recent[-10:-1] if len(x) > 5) / 9 if recent else 0
                if avg_vol > 0 and vol > avg_vol * 1.3:
                    patterns.append((tf, date_str, 'SOS', f'放量{vol/avg_vol:.1f}x光头阳线'))
                elif entity_pct > 90:
                    patterns.append((tf, date_str, 'SOS', '接近光头阳线'))
            range_low = min(x[3] for x in recent)
            range_high = max(x[2] for x in recent)
            range_span = range_high - range_low if range_high > range_low else 1
            vol = r[5] if len(r) > 5 else 0
            avg_vol = sum(x[5] for x in recent[-10:-1] if len(x) > 5) / max(1, len([x for x in recent[-10:-1] if len(x) > 5]))
            near_bottom = (l - range_low) < range_span * 0.10
            if (total_range > 0 and (c - l) > total_range * 0.70 and c > o
                and near_bottom and (avg_vol == 0 or vol > avg_vol * 0.8)):
                patterns.append((tf, date_str, 'Spring', f'下影{((c-l)/total_range*100):.0f}%底部反弹'))
            if (total_range > 0 and (h - max(c, o)) > total_range * 0.6):
                patterns.append((tf, date_str, 'UTAD', '冲高放量回落'))
            if (entity_pct < 30 and c < po):
                vol = r[5] if len(r) > 5 else 0
                avg_vol = sum(x[5] for x in recent[-10:-1] if len(x) > 5) / 9 if recent else 0
                if avg_vol > 0 and vol < avg_vol * 0.7:
                    patterns.append((tf, date_str, 'LPS', f'缩量{(1-vol/avg_vol)*100:.0f}%回踩'))
    seen = set()
    unique = []
    for p in patterns:
        key = (p[1], p[2])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    unique = unique[-5:]
    summary = ' | '.join(f'{p[2]}@{p[1]}' for p in unique) if unique else '无明显形态'
    return {'patterns': unique, 'summary': summary, 'bars_used': bars_used}


def wyckoff_detect(a):
    """检测当前威科夫阶段和近期关键事件。"""
    levels = a.get('levels_4h', {})
    highs = levels.get('highs', [])
    lows = levels.get('lows', [])
    if not highs or not lows:
        return {'phase': '数据不足', 'events': [], 'confidence': 0, 'detail': '无4H摆动数据'}
    closed = a['close_status']['4H']['closed']
    if len(closed) < 50:
        return {'phase': '数据不足', 'events': [], 'confidence': 0, 'detail': '4H数据<50根'}
    recent = closed[-80:]
    all_highs = [r[2] for r in recent]
    all_lows = [r[3] for r in recent]
    range_high = max(all_highs)
    range_low = min(all_lows)
    range_size = range_high / range_low - 1 if range_low > 0 else 0
    avg_vol = sum(r[5] for r in recent) / len(recent)
    sc_idx = all_lows.index(range_low)
    sc_vol = recent[sc_idx][5]
    sc_vol_ratio = sc_vol / avg_vol
    recent_10 = recent[-10:]
    latest_close = recent[-1][4]
    events = []
    n_compare = min(30, len(recent) // 2)
    earlier = [r[4] for r in recent[:n_compare]]
    later = [r[4] for r in recent[-n_compare:]]
    avg_early = sum(earlier) / len(earlier)
    avg_later = sum(later) / len(later)
    trend_bias = (avg_later - avg_early) / avg_early * 100
    window_5 = recent[-15:-5]
    if window_5:
        recent_low_5 = min(r[3] for r in window_5)
        recent_low_10 = min(r[3] for r in recent_10)
        if recent_low_10 < recent_low_5 * 0.995:
            for r in recent_10:
                if r[3] < recent_low_5 * 0.995 and r[4] > r[3] * 1.005:
                    spring_vol = r[5]
                    if spring_vol < avg_vol * 0.7:
                        events.append('Spring#3(缩量)' + str(int(r[3])))
                    elif spring_vol < avg_vol * 1.5:
                        events.append('Spring#2(中量)' + str(int(r[3])))
                    else:
                        events.append('Spring#1(巨量)' + str(int(r[3])))
                    break
    if len(highs) >= 3:
        prev_high = max(highs[3:10]) if len(highs) > 10 else highs[-1]
        latest_high = highs[0]
        if latest_high > prev_high * 1.01:
            recent_5 = recent[-5:]
            sos_candle = next((r for r in reversed(recent_5) if r[2] >= latest_high * 0.999), None)
            if sos_candle and sos_candle[5] > avg_vol * 1.3:
                events.append('SOS(放量突破)' + str(int(latest_high)))
    if len(highs) >= 5:
        for r in recent_10:
            is_new_high = r[2] > max(highs[5:10]) * 1.005 if len(highs) > 10 else r[2] > max(h[2] for h in recent_10[:5]) * 1.005
            is_bearish_close = r[4] < r[1] * 0.995
            if is_new_high and is_bearish_close:
                events.append('UTAD(假突破)' + str(int(r[2])))
                break
    if 'SOS' in str(events) and len(lows) >= 3:
        last_3_lows = lows[:3]
        prev_break_level = max(highs[3:8]) if len(highs) > 8 else range_high
        for l in last_3_lows:
            if abs(l - prev_break_level) / prev_break_level < 0.01:
                lps_candle = next((r for r in recent_10 if abs(r[1] - l) < 50), None)
                if lps_candle and lps_candle[5] < avg_vol * 0.7:
                    events.append(f'LPS(缩量回踩){l:.0f}')
                    break
    recent_swing_lows = lows[:5] if len(lows) >= 5 else lows
    recent_swing_highs = highs[:5] if len(highs) >= 5 else highs
    hh_hl = len(recent_swing_highs) >= 3 and recent_swing_highs[0] > recent_swing_highs[1] > recent_swing_highs[2]
    hl_ok = len(recent_swing_lows) >= 3 and recent_swing_lows[0] > recent_swing_lows[1]
    recovery_pct = (latest_close - range_low) / range_low * 100 if range_low > 0 else 0
    if ('SOS' in str(events) or (hh_hl and recovery_pct > 8)):
        phase = 'Markup (Phase E)'
        confidence = 65 + min(int(recovery_pct), 25)
    elif recovery_pct > 5 and hh_hl:
        phase = 'Markup (Phase D->E)'
        confidence = 60
    elif any('Spring' in e for e in events) and recovery_pct > 3:
        phase = '吸筹->启动 (Phase C->D)'
        confidence = 55
    elif recovery_pct > 3 and trend_bias > -2:
        if latest_close > range_low * 1.05:
            phase = '吸筹 (Phase B)'
            confidence = 45
        else:
            phase = '底部筑底 (Phase A->B)'
            confidence = 40
    elif trend_bias < -3 and not hh_hl:
        phase = '派发/下跌 (Distribution)'
        confidence = 50
    else:
        phase = '横盘整理'
        confidence = 35
    detail_parts = []
    detail_parts.append('区间:' + format(range_low, '.4f') + '-' + format(range_high, '.4f') + '(' + format(range_size*100, '.1f') + '%)')
    if sc_vol_ratio > 2:
        detail_parts.append('SC巨量' + format(sc_vol_ratio, '.1f') + 'x')
    detail_parts.append('回升' + format(recovery_pct, '.1f') + '%')
    detail_parts.append('结构:' + ('HH+HL' if hh_hl else ('HL成立' if hl_ok else '整理中')))
    return {'phase': phase, 'events': events, 'confidence': confidence, 'detail': ' | '.join(detail_parts)}

def _format_coin_section(a):
    """Format one coin's output block. Mutates `a` with near_support/resistance/_pos_dir/_entry/_sl/_tp/_rr. Returns list of lines."""
    lines = []
    coin = a['coin']
    t = a.get('ticker', {})
    f = a.get('funding', {})
    ind = a.get('indicators', {})
    price = t.get('last', '?')
    if t.get('_source') == 'binance' and isinstance(price, str):
        p_str = price.rstrip('0')
        if p_str.endswith('.'):
            p_str += '0'
    else:
        try:
            pf = float(price)
            if pf >= 100:   p_str = f'{pf:.0f}'
            elif pf >= 1:   p_str = f'{pf:.2f}'
            elif pf >= 0.01: p_str = f'{pf:.4f}'
            else:           p_str = f'{pf:.6f}'
        except Exception:
            p_str = str(price)
    try: chg = (float(price)-float(t['open24h']))/float(t['open24h'])*100
    except Exception: chg = 0
    try: fr_s = f'{float(f.get("fundingRate","0"))*100:.4f}%'
    except Exception: fr_s = '?'
    lines.append(f'## {coin}  ${p_str} ({chg:+.1f}%) | FR:{fr_s}')
    lines.append('|TF|C|RSI|MACD_h|ADX|%b|形态|')
    lines.append('|--|--|--|--|--|--|--|')
    for tf in TIMEFRAMES:
        d = ind[tf]
        if '_skip' in d:
            lines.append(f'|{tf}|-|{d["_skip"]}|-|-|-|-|')
            continue
        rsi_s = f'{d["rsi"]:.0f}' if d['rsi'] else '-'
        macd_s = f'{d["macd_h"]:.0f}' if d['macd_h'] is not None else '-'
        adx_s = f'{d["adx"]:.0f}' if d['adx'] else '-'
        bb_s = f'{d["bb"]["pct_b"]:.0f}%' if d.get('bb') else '-'
        lbl = d['label'] if d['label'] not in ('普通+', '普通-', '-') else '·'
        c_val = d['last_close']
        if c_val >= 100:   c_s = f'{c_val:.0f}'
        elif c_val >= 1:   c_s = f'{c_val:.2f}'
        elif c_val >= 0.01: c_s = f'{c_val:.4f}'
        else:              c_s = f'{c_val:.6f}'
        lines.append(f'|{tf}|{c_s}|{rsi_s}|{macd_s}|{adx_s}|{bb_s}|{lbl}|')
    ds = a.get('data_selection', '')
    if ds:
        lines.append('')
        lines.append(ds)
    lines.append(f'道氏: 1D={ind["1D"].get("trend","-")} | 4H={ind["4H"].get("trend","-")} | 1H={ind["1H"].get("trend","-")} | 加速={a["accel"]}')
    extra = a.get('extra', {})
    of_parts = []
    for k, label in [('openInterest','OI'),('longShortRatio','多空'),('takerRatio','Taker')]:
        v = extra.get(k)
        if v:
            try:
                if k == 'openInterest': of_parts.append(f'{label}={float(v)/1e6:.1f}M')
                else: of_parts.append(f'{label}={float(v):.2f}')
            except Exception: pass
    cvd = cvd_proxy(t, extra)
    if cvd['strength'] > 0:
        of_parts.append(f'CVD={cvd["detail"]}')
    if of_parts:
        lines.append(f'订单流: {" | ".join(of_parts)}')
    else:
        try:
            bid = float(t.get('bidPx',0)); ask = float(t.get('askPx',0)); last = float(t.get('last',0))
            if bid and ask and last:
                mid = (bid+ask)/2; bias = (last-mid)/mid*100
                lines.append(f'订单流: 价差{(ask-bid)/mid*100:.3f}% | 偏移{bias:+.2f}%')
        except Exception: pass
    try: cp = float(price)
    except Exception: cp = 0
    closed_4h = a['close_status']['4H']['closed']
    atr_band = cp * 0.01
    if len(closed_4h) >= 5:
        recent = closed_4h[-8:]
        atr_4h = a['indicators']['4H'].get('atr', cp * 0.02)
        atr_band = max(atr_4h * 2, cp * 0.01)
        if cp >= 1:
            highs_near = sorted(set(round(r[2], 2) for r in recent if r[2] > cp and r[2] - cp <= atr_band), reverse=True)[:2]
            lows_near = sorted(set(round(r[3], 2) for r in recent if r[3] < cp and cp - r[3] <= atr_band))[:2]
        else:
            highs_near = sorted(set(r[2] for r in recent if r[2] > cp and r[2] - cp <= atr_band), reverse=True)[:2]
            lows_near = sorted(set(r[3] for r in recent if r[3] < cp and cp - r[3] <= atr_band))[:2]
    else:
        highs_near, lows_near = [], []
    if not lows_near:
        low_levels = a.get('levels_4h', {}).get('lows', [])
        lows_near = [x for x in low_levels if x < cp][:2] if cp > 1 else low_levels[:2]
    if not highs_near:
        wider = a['close_status']['4H']['closed'][-50:]
        all_highs = sorted(set(round(r[2], 2) for r in wider if r[2] > cp and r[2] - cp <= atr_band * 2.5), reverse=True)
        highs_near = all_highs[:2] if all_highs else []
        if not highs_near:
            highs_near = [round(cp * 1.02, 2)] if cp > 1 else []
    s_str = '/'.join(_fmt_price(x) for x in lows_near) if lows_near else '$?'
    r_str = '/'.join(_fmt_price(x) for x in highs_near) if highs_near else '$?'
    a['near_support'] = [float(x) for x in lows_near]
    a['near_resistance'] = [float(x) for x in highs_near]
    rsi4_s = f'{a["rsi_4h"]:.0f}' if a["rsi_4h"] is not None else '?'
    macd4_s = f'{a["macd_h_4h"]:.0f}' if a["macd_h_4h"] is not None else '?'
    lines.append(f'技术: S={s_str} | R={r_str}')
    lines.append(f'共振: {a["resonance"]} (4H_RSI={rsi4_s} MACD_h={macd4_s} %b={a["pct_b"]:.0f}%)')
    wk = wyckoff_detect(a)
    if wk['confidence'] > 0:
        events_str = ' | '.join(wk['events']) if wk['events'] else '无显著事件'
        lines.append(f'威科夫: {wk["phase"]}({wk["confidence"]}%) | {events_str}')
        lines.append(f'        {wk["detail"]}')
    kp = a.get('kline_patterns', {})
    if kp and kp.get('patterns'):
        pat_summary = ' | '.join(f'{p[2]}@{p[1]}' for p in kp['patterns'])
        lines.append(f'🕯️ K线形态: {pat_summary}')
    bars_info = ', '.join(f'{tf}={cnt}根' for tf, cnt in sorted(kp.get('bars_used', {}).items()))
    if bars_info:
        lines.append(f'   数据量: {bars_info}')
    dt_score = a.get('daytrade_score')
    if dt_score is not None:
        dt_flags = a.get('daytrade_flags', [])
        flag_str = ' | '.join(dt_flags) if dt_flags else ''
        lines.append(f'🔄 日内: 评分{dt_score}/100' + (f' | {flag_str}' if flag_str else ''))
        cvd = cvd_proxy(t, a.get('extra', {}))
        if cvd['strength'] > 0:
            lines.append(f'        Δ方向: {cvd["detail"]}')
    svp = a.get('session_vp')
    if svp:
        poc_s = f'${svp["poc"]:.4f}' if svp['poc'] < 1 else (f'${svp["poc"]:.2f}' if svp['poc'] < 100 else f'${svp["poc"]:.0f}')
        vah_s = f'${svp["vah"]:.4f}' if svp['vah'] < 1 else (f'${svp["vah"]:.2f}' if svp['vah'] < 100 else f'${svp["vah"]:.0f}')
        val_s = f'${svp["val"]:.4f}' if svp['val'] < 1 else (f'${svp["val"]:.2f}' if svp['val'] < 100 else f'${svp["val"]:.0f}')
        lines.append(f'📊 {svp.get("hours", "24")}h时段VP: POC={poc_s} | VAH={vah_s} | VAL={val_s} ({svp.get("bars", "?")}bar)')
    rsi_1d = ind['1D'].get('rsi', 50)
    rsi_1h_val = a.get('rsi_1h', 50)
    macd_4h_val = a.get('macd_h_4h', 0)
    atr_4h = ind['4H'].get('atr', 0)
    trend_1d = ind['1D'].get('trend', '')
    trend_4h = ind['4H'].get('trend', '')
    # 仓位方向判定：共振优先 + near_bottom/near_top + MACD/RSI 为辅
    resonance = a.get('resonance', '')
    if '强' in str(resonance) or (a['near_bottom'] and macd_4h_val > -50 and rsi_1h_val < 45):
        pos_dir = '试多'
    elif '弱' in str(resonance) or (rsi_1d > 65 and macd_4h_val < -50):
        pos_dir = '试空'
    elif rsi_1d > 67 and trend_1d == '下降' and macd_4h_val < 0:
        pos_dir = '试空'  # L1: near_top 做空捷径，跳过共振门槛
    else:
        pos_dir = '观望'
    # L3: V反保护 — 底部区域/反弹中禁止做空
    if pos_dir == '试空':
        if a.get('near_bottom'):
            pos_dir = '观望（near_bottom保护）'
        elif closed_4h and len(closed_4h) >= 8:
            # 检测过去 8 根 4H 是否从低点反弹 > 3%（Spring/V反特征）
            lows_8 = [r[3] for r in closed_4h[-8:]]
            min_low = min(lows_8)
            current = float(t.get('last', closed_4h[-1][4]))
            recovery_pct = (current - min_low) / min_low * 100 if min_low > 0 else 0
            if recovery_pct > 3:
                pos_dir = '观望（反弹{:.1f}%，V反保护）'.format(recovery_pct)
    entry = sl = tp = rr = None
    if pos_dir.startswith('试') and lows_near and highs_near and atr_4h:
        entry = float(t.get('last', cp))  # P1a: 用 ticker 现价
        if pos_dir == '试多':
            sl = lows_near[0] - atr_4h * 0.5
            tp = highs_near[0]
        else:  # 试空
            sl = highs_near[0] + atr_4h * 0.5
            tp = lows_near[0]
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        pos_line = f'仓位: {pos_dir} | 入场{_fmt_price(entry)} | SL{_fmt_price(sl)} | TP{_fmt_price(tp)} | 盈亏比 1:{rr:.1f}'
        if rr < 1.5:
            pos_line += ' ⚠️'
        pos = calc_position(coin, entry, sl)
        if pos and not pos['liq_safe']:
            pos_line += f' ⛔爆仓距{pos["liq_pct"]:.1f}%<SL距{pos["sl_pct"]:.1f}%'
            pos_dir = '观望'
    elif pos_dir.startswith('观望'):
        pos_line = '仓位: ' + pos_dir
    else:
        pos_line = f'仓位: {pos_dir}（数据不足，无法计算精确SL/TP）'
    a['_pos_dir'] = pos_dir
    a['_entry'] = entry
    a['_sl'] = sl
    a['_tp'] = tp
    a['_rr'] = rr
    lines.append(pos_line)
    if pos_dir.startswith('试') and '入场' in pos_line:
        pos = calc_position(coin, entry, sl)
        if pos:
            slp = pos["sl_pct"]
            c_s = f'{pos["contracts"]:.1f}张' if pos['contracts'] >= 1 else f'{pos["contracts"]:.2f}张'
            liq_price = entry * (1 - pos['liq_pct']/100) if pos_dir == '试多' else entry * (1 + pos['liq_pct']/100)
            safe = '✅' if pos['liq_safe'] else '⚠️爆仓距<止损'
            lines.append(f'  仓位公式: SL距{slp:.1f}% | 每$100(2%风险)可开{c_s} | 爆仓价{liq_price:.1f}(距{pos["liq_pct"]:.1f}%) | 安全{safe}')
    return lines


def _format_macro_section(analyses, fg_val, fg_label):
    """Format macro + calendar + risk lines. Returns list of lines."""
    lines = []
    try:
        regime = get_regime_result()
        macro_data = regime.get('dimensions', {}).get('macro_external', {})
        if macro_data:
            parts = []
            fg_a = macro_data.get('fg_actual')
            if fg_a:
                parts.append('FG=' + str(fg_a) + '(' + str(macro_data.get('fg_label', '')) + ')')
            dxy_v = macro_data.get('dxy')
            if dxy_v:
                dxy_s = 'DXY=' + format(dxy_v, '.1f')
                dxy_c = macro_data.get('dxy_change_pct')
                if dxy_c:
                    dxy_s += '(' + format(dxy_c, '+.1f') + '%)'
                parts.append(dxy_s)
            vix_v = macro_data.get('vix')
            if vix_v:
                vix_s = 'VIX=' + format(vix_v, '.1f')
                vix_c = macro_data.get('vix_change_pct')
                if vix_c:
                    vix_s += '(' + format(vix_c, '+.1f') + '%)'
                parts.append(vix_s)
            y10 = macro_data.get('yield10')
            if y10:
                parts.append('10Y=' + str(y10) + '%')
            bd = macro_data.get('btc_dominance')
            if bd:
                parts.append('BTC.D=' + str(bd) + '%')
            if parts:
                lines.append('')
                lines.append('🌍 宏观: ' + ' | '.join(parts[:5]))
            jin10_events = get_jin10_key_events()
            if jin10_events:
                lines.append('📅 金十日历: ' + ' | '.join(jin10_events))
            warnings = regime.get('transition_warnings', [])
            if warnings:
                lines.append('⚠️ 预警: ' + ' | '.join(warnings[:2]))
    except Exception:
        pass
    events = []
    for a in analyses:
        for risk in a.get('risks', []):
            if '[X3]' in risk:
                events.append(f'⚠️ {risk.replace("[X3] ", "", 1)}')
                break
        if events:
            break
    if events:
        lines.append(' | '.join(events))
    return lines


def _format_summary_section(analyses):
    """Format 做单汇总 + 仓位公式 + 复盘教训. Returns list of lines."""
    lines = []
    lines.append('')
    lines.append('=== 做单方向 & 仓位指导 ===')
    btc_guidance = None
    eth_guidance = None
    alt_lines = []
    for a in analyses:
        coin = a['coin']
        pos_dir = a.get('_pos_dir', '观望')
        entry = a.get('_entry')
        if pos_dir.startswith('试') and entry is not None:
            sl = a['_sl']
            tp = a['_tp']
            rr = a['_rr']
            guidance = f'{pos_dir} | 入场{_fmt_price(entry)} SL{_fmt_price(sl)} TP{_fmt_price(tp)} | 盈亏比 1:{rr:.1f}'
            if rr < 1.5:
                guidance += ' ⚠️'
        elif pos_dir.startswith('观望'):
            guidance = '观望（等放量阳线确认）'
        else:
            guidance = f'{pos_dir}（数据不足）'
        if coin == 'BTC':
            btc_guidance = guidance
        elif coin == 'ETH':
            eth_guidance = guidance
        else:
            alt_lines.append(f'  {coin}: {guidance}')
    if btc_guidance:
        lines.append(f'BTC: {btc_guidance}')
    if eth_guidance:
        lines.append(f'ETH: {eth_guidance}')
    if alt_lines:
        lines.append('山寨币:')
        lines.extend(alt_lines)
    lines.append('')
    lines.append('--- 仓位公式(小账户·每笔≤2%风险·20x杠杆) ---')
    for a in analyses:
        coin = a['coin']
        pos_dir = a.get('_pos_dir', '观望')
        entry = a.get('_entry')
        sl = a.get('_sl')
        if pos_dir.startswith('试') and entry and sl:
            pos = calc_position(coin, entry, sl)
            if pos:
                ref = '$100' if coin in ('BTC','ETH') else '$50'
                margin_1ct = entry * CONTRACT_SIZE.get(coin, 1.0) * 20
                c_s = f'{pos["contracts"]:.1f}张' if pos['contracts'] >= 1 else f'<1张(需${margin_1ct:.0f}保证金)'
                liq_price = entry * (1 - pos['liq_pct']/100) if pos_dir == '试多' else entry * (1 + pos['liq_pct']/100)
                safe = '✅' if pos['liq_safe'] else '⚠️'
                lines.append(f'  {coin}: {pos_dir} | SL距{pos["sl_pct"]:.1f}% | 每{ref}(2%风险)≈{c_s} | 爆仓价{liq_price:.1f}(距{pos["liq_pct"]:.1f}%) | 安全{safe}')
    lines.append('')
    all_warnings = []
    seen_w = set()
    for a in analyses:
        for w in a.get('lessons_warnings', []):
            if w not in seen_w:
                seen_w.add(w)
                all_warnings.append(w)
    for w in all_warnings:
        lines.append(f'  📌 {w}')
    return lines


def format_report(analyses, fg_val, fg_label):
    """紧凑格式 — token 最少化"""
    lines = []
    lines.append(f'⏰ BJ {NOW_BJ.strftime("%m-%d %H:%M")} | FG:{fg_val}({fg_label})' if fg_val
                 else f'⏰ BJ {NOW_BJ.strftime("%m-%d %H:%M")} | FG:N/A')
    lines.append('')
    for a in analyses:
        lines.extend(_format_coin_section(a))
    split_idx = len(lines)
    lines.extend(_format_macro_section(analyses, fg_val, fg_label))
    lines.extend(_format_summary_section(analyses))
    return '\\n'.join(lines[:split_idx]), '\\n'.join(lines[split_idx:])

def get_jin10_key_events():
    """从金十 MCP 实时获取日历 → 缓存 → 硬编码回退。返回今日+未来2天 4★+ 关键事件。"""
    from jin10_fallback import get_calendar_events
    events, source, fresh = get_calendar_events(min_stars=4)
    if not events:
        events, source, _ = get_calendar_events(min_stars=3)
    return events




REGIME_CACHE = os.path.join(TRADE_DIR, '.regime_cache.json')
REGIME_CACHE_TTL = 120  # 2 minutes — cron :02 refreshes every 2 min

def get_regime_result():
    """获取行情类型。优先读缓存（2min TTL），过期则重新运行 regime_detector"""
    try:
        if os.path.exists(REGIME_CACHE):
            mtime = os.path.getmtime(REGIME_CACHE)
            if time.time() - mtime < REGIME_CACHE_TTL:
                with open(REGIME_CACHE) as f:
                    cached = json.load(f)
                return cached
    except Exception:
        pass
    try:
        r = subprocess.run([sys.executable, os.path.join(TRADE_DIR, 'regime_detector.py')],
                          capture_output=True, text=True, timeout=30,
                          cwd=TRADE_DIR)
        if r.stdout:
            result = json.loads(r.stdout)
            try:
                with open(REGIME_CACHE + '.tmp', 'w') as f:
                    json.dump(result, f)
                os.replace(REGIME_CACHE + '.tmp', REGIME_CACHE)
            except Exception:
                pass
            return result
    except Exception:
        pass
    try:
        if os.path.exists(REGIME_CACHE):
            with open(REGIME_CACHE) as f:
                return json.load(f)
    except Exception:
        pass
    return {'regime': '未知', 'confidence': 0, 'composite_score': 0}


# =========================== 主入口 ===========================

def main():
    raw = sys.argv[1:]
    db_coins = get_db_coins()

    FORCE_SYNC = '--force-sync' in raw or '--sync' in raw
    NO_SYNC = '--no-sync' in raw
    raw_coins = [a for a in raw if not a.startswith('-')]
    
    if not raw_coins or '--all' in raw:
        coins = db_coins
    else:
        resolved = []
        unknown = []
        not_in_db = []
        for arg in raw_coins:
            key = arg.strip().lower()
            coin = None
            if key in COIN_ALIASES:
                coin = COIN_ALIASES[key]
            elif key.upper() in db_coins:
                coin = key.upper()
            elif arg in db_coins:
                coin = arg
            elif arg.upper() in COIN_ALIASES.values():
                coin = arg.upper()
            if coin:
                resolved.append(coin)
                if coin not in db_coins:
                    not_in_db.append(coin)
            else:
                unknown.append(arg)
        if unknown:
            print(f'⚠️ 未知币种: {unknown}', file=sys.stderr)
        if not resolved:
            print('未指定有效币种', file=sys.stderr)
            sys.exit(1)
        coins = resolved

    db_coins_to_sync = [c for c in coins if c in db_coins]
    if db_coins_to_sync:
        if NO_SYNC:
            print('  ⏭️ 跳过同步 (--no-sync)', file=sys.stderr)
            db_coins_to_sync = []
        elif not FORCE_SYNC:
            import sqlite3 as _sql
            _c = _sql.connect(DB)
            all_fresh = True
            age_ms = 0
            for c in db_coins_to_sync:
                row = _c.execute(
                    "SELECT ts FROM klines WHERE coin=? AND timeframe='1H' ORDER BY ts DESC LIMIT 1",
                    (c,)
                ).fetchone()
                if row:
                    age_ms = NOW_MS - row[0]
                    if age_ms > 300_000:
                        all_fresh = False
                        break
                else:
                    all_fresh = False
                    break
            _c.close()
            if all_fresh:
                print(f'  ⏭️ 跳过同步 (K线已是最新, {age_ms/1000:.0f}s)', file=sys.stderr)
                db_coins_to_sync = []
        if db_coins_to_sync:
            coins_str = ' '.join(db_coins_to_sync)
            print(f'  ⏳ 同步最新K线: {coins_str}...', file=sys.stderr)
            subprocess.run([sys.executable, os.path.join(TRADE_DIR, 'monitor_and_sync.py')] + db_coins_to_sync,
                           capture_output=(sys.stderr is None), check=False, timeout=300)
            print('  ✅ 同步完成', file=sys.stderr)

    conn = sqlite3.connect(DB)
    fg_val, fg_label = fetch_fear_greed()

    for coin in coins:
        in_db = conn.execute(
            'SELECT COUNT(*) FROM klines WHERE coin=?', (coin,)
        ).fetchone()[0]
        if in_db == 0:
            print(f'  ⏳ {coin} 不在DB中，自动拉取K线...', file=sys.stderr)
            n = prefetch_coin(coin)
            if n == 0:
                print(f'  ❌ {coin} 币安拉取失败，跳过', file=sys.stderr)
                continue
            print(f'  ✅ {coin} 拉取 {n} 根K线（不存DB）', file=sys.stderr)

    analyses = []
    for coin in coins:
        in_db = conn.execute(
            'SELECT COUNT(*) FROM klines WHERE coin=?', (coin,)
        ).fetchone()[0] > 0
        if in_db:
            ticker = fetch_okx_ticker(f'{coin}-USDT-SWAP')
            funding = fetch_okx_funding(f'{coin}-USDT-SWAP')
            extra = {}
        else:
            ticker = fetch_okx_ticker(f'{coin}-USDT-SWAP')
            funding = fetch_okx_funding(f'{coin}-USDT-SWAP')
            extra = {}
            if ticker.get('_error') or ticker.get('last') in (None, '?'):
                ticker = fetch_binance_ticker(coin)
            if funding.get('_error'):
                funding = fetch_binance_funding(coin)
            oi = fetch_binance_oi(coin)
            sentiment = fetch_binance_sentiment(coin)
            extra = {**oi, **sentiment}
        a = analyze_single_coin(conn, coin, ticker, funding, fg_val, fg_label)
        a['extra'] = extra
        if coin in db_coins:
            a['session_vp'] = session_vp(coin, conn)
        analyses.append(a)

    conn.close()

    _dt_cache = '/tmp/daytrade_coins.json'
    if os.path.exists(_dt_cache):
        try:
            with open(_dt_cache) as f:
                dt_data = json.load(f)
            dt_coins = {c['base'].upper(): c for c in dt_data.get('coins', [])}
            for a in analyses:
                if a['coin'] in dt_coins:
                    c = dt_coins[a['coin']]
                    a['daytrade_score'] = c.get('score', 0)
                    a['daytrade_flags'] = c.get('flags', [])
        except Exception:
            pass

    part1, part2 = format_report(analyses, fg_val, fg_label)
    
    # Build regime_result dict for record building (needed for macro_external, order_flow)
    regime_result = {}
    regime_path = os.path.join(os.environ.get('HOME', '/root'), '.hermes/trade_review/.regime_cache.json')
    if os.path.exists(regime_path):
        try:
            with open(regime_path) as rf:
                regime_result = json.load(rf)
        except Exception:
            pass
    
    print(part1)
    print()
    print('═' * 50)
    print('📋 [分段2/2: 仓位 + 宏观 + 风险]')
    print('═' * 50)
    print(part2)
    
    try:
        analyses_records = []
        if os.path.exists(ANALYSES_FILE):
            with open(ANALYSES_FILE) as f:
                analyses_records = json.load(f)
        existing_analyses_idx = {}  # (coin,date)→index in analyses_records
        for i, r in enumerate(analyses_records):
            try:
                dt = datetime.fromisoformat(r.get('timestamp', ''))
                existing_analyses_idx[(r.get('coin'), dt.strftime('%Y-%m-%d'))] = i
            except Exception:
                pass
        reviews_modified = False
        reviews_records = []
        if os.path.exists(REVIEWS_PATH):
            with open(REVIEWS_PATH) as f:
                reviews_records = json.load(f)
        # 今日已有 review 的 (coin, date) 集合
        existing_review_keys = set()
        for r in reviews_records:
            try:
                dt = datetime.fromisoformat(r.get('timestamp', ''))
                existing_review_keys.add((r.get('coin'), dt.strftime('%Y-%m-%d')))
            except Exception:
                pass
        
        new_review_count = 0
        
        # 快讯: 循环外拉取一次，多币种共享 (2026-06-20 优化)
        _all_flash_news = []
        try:
            from jin10_fallback import fetch_flash_news as _fetch_flash
            flash_items, flash_source, flash_fresh = _fetch_flash()
            for item in flash_items[:10]:
                _all_flash_news.append({
                    'time': item.get('time', ''),
                    'content': item.get('content', ''),
                    'score': item.get('relevance_score', 0),
                    'url': item.get('url', ''),
                })
        except Exception:
            pass
        
        for a in analyses:
            coin_key = f"{a['coin']}USDT"
            date_key = NOW_BJ.strftime('%Y-%m-%d')
            ind = a.get('indicators', {})
            ind_4h = ind.get('4H', {})
            ind_1h = ind.get('1H', {})
            levels_4h = a.get('levels_4h', {})
            entry_price = ind_4h.get('last_close', 0) or ind_1h.get('last_close', 0)
            ticker_price = None
            try: ticker_price = float(a.get('ticker', {}).get('last', 0))
            except Exception: pass
            sup = a.get('near_support', []) or [s for s in levels_4h.get('lows', [])[:2]]
            res = a.get('near_resistance', []) or [r for r in levels_4h.get('highs', [])[:2]]
            trend_1h = ind_1h.get('trend', '')
            trend_4h = ind_4h.get('trend', '')
            trend_1d = ind.get('1D', {}).get('trend', '')
            f_rate = a.get('funding', {})
            f_rate_val = f_rate.get('rate', '') if not f_rate.get('_error') else ''
            # Compute sl/tp/rr from levels_4h (full_obj format) — 区分多空
            levels_4h = a.get('levels_4h', {})
            lows = levels_4h.get('lows', [])
            highs = levels_4h.get('highs', [])
            entry_p = a.get('ticker', {}).get('last', 0)
            try: entry_f = float(entry_p) if entry_p and entry_p != '?' else 0
            except: entry_f = 0
            position = a.get('position', '观望')
            
            if '空' in str(position):
                # 做空: SL在阻力上方(highs[0]*1.01), TP在支撑(lows[0])
                sl_val = int(highs[0] * 1.01) if highs and entry_f else 0
                tp_val = int(lows[0]) if lows else 0
                rr_str = '?'
                if entry_f and sl_val and tp_val and sl_val > entry_f:
                    rr = round((entry_f - tp_val) / (sl_val - entry_f), 1)
                    rr_str = f'{rr:.1f}' if rr > 0 else '?'
            else:
                # 做多: SL在支撑下方, TP在阻力 (保留原逻辑)
                if a['coin'] == 'BTC':
                    sl_mult = 0.99
                    tp_mult = 1.0
                else:
                    sl_mult = 0.985
                    tp_mult = 1.0
                sl_val = int(lows[0] * sl_mult) if lows and entry_f else 0
                tp_val = int(highs[0] * tp_mult) if highs else 0
                rr_str = '?'
                if entry_f and sl_val and tp_val and entry_f > sl_val:
                    rr = round((tp_val - entry_f) / (entry_f - sl_val), 1)
                    rr_str = f'{rr:.1f}' if rr > 0 else '?'
            
            # Extract per-TF indicators for complete data storage
            ind = a.get('indicators', {})
            kline_table = {}
            for tf in ['1D', '4H', '1H', '30m', '5m']:
                tf_data = ind.get(tf, {})
                if tf_data:
                    kline_table[tf] = {
                        'close': tf_data.get('last_close', 0),
                        'rsi': tf_data.get('rsi', 0),
                        'macd_h': tf_data.get('macd_h', 0),
                        'adx': tf_data.get('adx', 0),
                        'pct_b': tf_data.get('bb', {}).get('pct_b', 0) if isinstance(tf_data.get('bb'), dict) else 0,
                        'shape': tf_data.get('label', ''),
                        'trend': tf_data.get('trend', ''),
                    }
            
            # Extract ticker data
            ticker_data = a.get('ticker', {})
            ticker_full = {}
            if ticker_data:
                ticker_full = {
                    'last': ticker_data.get('last', ''),
                    'bid': ticker_data.get('bid', ''),
                    'ask': ticker_data.get('ask', ''),
                    'vol': ticker_data.get('vol', ''),
                    'change_pct': ticker_data.get('change_pct', ''),
                }
            
            # Extract funding data
            funding_data = a.get('funding', {})
            funding_full = {}
            if funding_data:
                funding_full = {
                    'rate': funding_data.get('rate', ''),
                    'pos_ratio': funding_data.get('pos_ratio', ''),
                }
            
            # Extract levels_4h
            levels_4h = a.get('levels_4h', {})
            levels_full = {
                'lows': levels_4h.get('lows', []),
                'highs': levels_4h.get('highs', []),
            }
            
            # Extract close_status
            close_status = a.get('close_status', {})
            
            # Extract near_support/near_resistance
            near_support = a.get('near_support', [])
            near_resistance = a.get('near_resistance', [])
            
            # Extract lessons_warnings for event detection
            lessons_warnings = a.get('lessons_warnings', [])
            
            # Extract bottom_note
            bottom_note = a.get('bottom_note', '')
            
            # Extract risks
            risks = a.get('risks', [])
            
            # Extract data_selection
            data_selection = a.get('data_selection', {})
            
            # Extract change_pct from ticker (calculate from last and open24h)
            change_pct = ''
            if ticker_data:
                try:
                    last = float(ticker_data.get('last', 0))
                    open24h = float(ticker_data.get('open24h', 0))
                    if open24h > 0:
                        change_pct = round((last - open24h) / open24h * 100, 1)
                except Exception:
                    pass
            
            # Extract macro_external from regime_result (loaded above)
            macro_external = {}
            if regime_result:
                regime_dim = regime_result.get('dimensions', {})
                macro_ext = regime_dim.get('macro_external', {})
                if isinstance(macro_ext, dict):
                    macro_external = {k: v for k, v in macro_ext.items() if v is not None}
            
            # Extract order_flow from regime_result
            order_flow = {}
            if regime_result:
                regime_dim = regime_result.get('dimensions', {})
                of_data = regime_dim.get('order_flow', {})
                if isinstance(of_data, dict):
                    order_flow = {
                        'funding_rate_pct': of_data.get('funding_rate_pct', ''),
                        'fr_pos_ratio': of_data.get('fr_pos_ratio', ''),
                        'fr_avg_8d': of_data.get('fr_avg_8d', ''),
                        'taker_buy_ratio': of_data.get('taker_buy_ratio', ''),
                        'detail': of_data.get('detail', ''),
                    }
            
            # Extract calendar events from cache or API
            calendar_events = []
            try:
                cal_json = os.path.join(os.environ.get('HOME', '/root'), '.hermes/trade_review/data/jin10_calendar_cache.json')
                if os.path.exists(cal_json):
                    with open(cal_json) as cf:
                        cal_data = json.load(cf)
                        # Cache is dict with 'events' key, not a list directly
                        events = cal_data.get('events', []) if isinstance(cal_data, dict) else cal_data
                        if isinstance(events, list):
                            for item in events[:10]:
                                if isinstance(item, dict) and 'title' in item:
                                    calendar_events.append(f"{item.get('pub_time', '')} {item['title']}")
                else:
                    # Fallback: try jin10 API
                    import urllib.request as _urllib
                    cal_url = 'https://data-api.jin10.com/jin10/calendar'
                    req = _urllib.Request(cal_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with _urllib.urlopen(req, timeout=5) as resp:
                        cal_data = json.loads(resp.read())
                        if isinstance(cal_data, dict) and 'data' in cal_data:
                            for item in cal_data['data'][:10]:
                                if isinstance(item, dict) and 'title' in item:
                                    calendar_events.append(f"{item.get('time', '')} {item['title']}")
            except Exception:
                pass

            # Fetch flash news (快讯) — 2026-06-20 新增 (循环外拉取一次，多币种共享)
            flash_news = _all_flash_news

            # Extract VP data
            vp_data = {}
            if 'vp_POC' in a:
                vp_data = {
                    'POC': a.get('vp_POC', ''),
                    'VAH': a.get('vp_VAH', ''),
                    'VAL': a.get('vp_VAL', ''),
                    'bar': a.get('vp_bar', ''),
                }
            
            # Extract order flow data from a (spread/offset) and merge into regime-based order_flow
            if 'order_spread' in a:
                order_flow.update({
                    'spread': a.get('order_spread', ''),
                    'offset': a.get('order_offset', ''),
                })
            
            # Extract wyckoff data
            wyckoff_data = {}
            for wf in ['phase', 'spring', 'range', 'SC', 'recovery', 'structure']:
                val = a.get(f'wyckoff_{wf}', '')
                if val:
                    wyckoff_data[wf] = val
            
            # Extract kline pattern times
            kline_pattern_times = {}
            for pf in ['Spring', 'LPS1', 'LPS2', 'LPS3']:
                val = a.get(f'kline_pattern_{pf}', '')
                if val:
                    kline_pattern_times[pf] = val
            
            # Extract data volume
            data_vol = {}
            if 'data_vol' in a:
                data_vol = a.get('data_vol', {})
            
            # Call session_vp if not already in a
            vp_result = a.get('session_vp')
            if vp_result:
                vp_data = {
                    'session': vp_result.get('session', ''),
                    'POC': vp_result.get('poc', 0),
                    'VAH': vp_result.get('vah', 0),
                    'VAL': vp_result.get('val', 0),
                    'bars': vp_result.get('bars', 0),
                }
            else:
                vp_data = {}
            
            # Call wyckoff_detect
            wk_result = wyckoff_detect(a) if 'indicators' in a and 'levels_4h' in a else {}
            wyckoff_data = {}
            if wk_result:
                wyckoff_data = {
                    'phase': wk_result.get('phase', ''),
                    'confidence': wk_result.get('confidence', 0),
                    'detail': wk_result.get('detail', ''),
                    'events': wk_result.get('events', []),
                }
            
            # Call detect_kline_patterns for Spring/LPS/SOS times
            kline_pattern_times = {}
            kline_patterns_result = detect_kline_patterns(a) if 'close_status' in a else {}
            if kline_patterns_result and 'patterns' in kline_patterns_result:
                for pat in kline_patterns_result['patterns']:
                    tf, time_str, ptype, desc = pat
                    if ptype == 'Spring':
                        kline_pattern_times['Spring'] = f'@{time_str}'
                    elif ptype == 'LPS':
                        count = len([p for p in kline_patterns_result['patterns'] if p[2] == 'LPS'])
                        kline_pattern_times[f'LPS{count}'] = f'@{time_str}'
                    elif ptype == 'SOS':
                        kline_pattern_times['SOS'] = f'@{time_str}'
                    elif ptype == 'SC':
                        kline_pattern_times['SC'] = f'@{time_str}'
            
            # Extract macro alert from lessons_warnings or resonance
            macro_alert = a.get('macro_alert', '')
            if not macro_alert and 'lessons_warnings' in a:
                for lw in a.get('lessons_warnings', []):
                    if 'Breakout' in str(lw) or 'Breakdown' in str(lw):
                        macro_alert = str(lw)
                        break
            
            # Extract position suggestion from resonance + near_bottom/near_top + V反保护 (含8根4H反弹检测)
            position = a.get('position', '')
            if not position:
                resonance = a.get('resonance', '')
                near_bottom = a.get('near_bottom', False)
                rsi_1d = (a.get('indicators', {}).get('1D', {}).get('rsi') or 50)
                trend_1d = a.get('indicators', {}).get('1D', {}).get('trend', '')
                macd_4h = a.get('macd_h_4h', 0)
                if '强' in str(resonance) or near_bottom:
                    position = '偏多'
                elif '弱' in str(resonance):
                    position = '偏空'
                else:
                    position = '观望（等确认）'
                # L1: near_top 做空捷径
                if position == '观望（等确认）' and rsi_1d > 67 and trend_1d == '下降' and macd_4h < 0:
                    position = '偏空'
                # L3: V反保护 — 底部区域/反弹中禁止做空 (与 _format_coin_section 对齐)
                if position == '偏空':
                    if near_bottom:
                        position = '观望（near_bottom保护）'
                    else:
                        closed_4h = a.get('close_status', {}).get('4H', {}).get('closed', [])
                        if closed_4h and len(closed_4h) >= 8:
                            lows_8 = [r[3] for r in closed_4h[-8:]]
                            min_low = min(lows_8)
                            ticker = a.get('ticker', {})
                            current = float(ticker.get('last', closed_4h[-1][4]))
                            recovery_pct = (current - min_low) / min_low * 100 if min_low > 0 else 0
                            if recovery_pct > 3:
                                position = '观望（反弹{:.1f}%，V反保护）'.format(recovery_pct)
            
            # Extract macro_external from extra data (fallback if regime_result not available)
            if not macro_external and 'extra' in a:
                extra = a.get('extra', {})
                macro_external = {k: v for k, v in extra.items() if k in ('DXY', 'VIX', '10Y', 'BTC.D', 'gold')}
            
            # Extract macro alert (already extracted above from extra logic)
            # Extract position suggestion (already extracted above)
            
            record = {
                "timestamp": NOW_BJ.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "coin": coin_key,
                "coin_type": "large",
                "trigger": "manual",
                "entry_price": float(entry_price) if entry_price else 0,
                "ticker_price": ticker_price,
                "change_pct": change_pct,
                "trend_1h": trend_1h,
                "trend_4h": trend_4h,
                "trend_3d": trend_1d,
                "support": [float(s) for s in sup] if sup else [],
                "resistance": [float(r) for r in res] if res else [],
                "near_support": [float(s) for s in near_support] if near_support else [],
                "near_resistance": [float(r) for r in near_resistance] if near_resistance else [],
                "recommendation": a.get('resonance', ''),
                "risk": a.get('risk', ''),
                "resonance": a.get('resonance', ''),
                "risk_warnings": risks,
                "near_bottom": a.get('near_bottom', False),
                "bottom_note": bottom_note,
                "accel": a.get('accel', ''),
                "rsi_14": a.get('rsi_4h'),
                "rsi_1h": a.get('rsi_1h'),
                "macd_h_4h": a.get('macd_h_4h'),
                "macd_h_1h": a.get('macd_h_1h'),
                "pct_b": a.get('pct_b'),
                "macd_trend": "bullish" if a.get('macd_h_4h', 0) > 0 else "bearish",
                "data_freshness": a.get('data_freshness', {}),
                "data_selection": data_selection,
                "close_status": close_status,
                # Full indicator table (per-TF)
                "kline_table": kline_table,
                # Full ticker data
                "ticker_full": ticker_full,
                # Full funding data
                "funding_full": funding_full,
                # Levels 4H
                "levels_4h": levels_full,
                # Macro external data
                "macro_external": macro_external,
                # Calendar events
                "calendar_events": calendar_events,
                # Flash news (快讯) — 2026-06-20 新增
                "flash_news": flash_news,
                # VP data
                "session_vp": vp_data,
                # Order flow
                "order_flow": order_flow,
                # Wyckoff data
                "wyckoff_data": wyckoff_data,
                # Kline pattern times
                "kline_pattern_times": kline_pattern_times,
                # Data volume
                "data_vol": data_vol,
                # Macro alert
                "macro_alert": macro_alert,
                # Position suggestion
                "position": position,
                # Lessons warnings
                "lessons_warnings": lessons_warnings,
                # Calculated fields for verification
                "sl_val": sl_val,
                "tp_val": tp_val,
                "rr_str": rr_str,
            }
            
            # ── 写入 analyses.json ──
            is_overwrite = (coin_key, date_key) in existing_analyses_idx
            if is_overwrite:
                old_idx = existing_analyses_idx[(coin_key, date_key)]
                old = analyses_records[old_idx]
                print(f"  ⚠️  同日同币覆盖: {a['coin']} 旧分析 {old.get('timestamp','?')[:19]} → 新分析 {NOW_BJ.strftime('%H:%M:%S')}", file=sys.stderr)
                analyses_records[old_idx] = record
            else:
                analyses_records.append(record)
                existing_analyses_idx[(coin_key, date_key)] = len(analyses_records) - 1
            
            # ── 同步写入 reviews.json（仅当天第一条分析） ──
            if (coin_key, date_key) not in existing_review_keys:
                # 首次创建复盘占位
                reviews_records.append({
                    'coin': coin_key,
                    'timestamp': NOW_BJ.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'entry_price': float(entry_price) if entry_price else 0,
                    'review_6h': '待复盘',
                    'review_12h': '待复盘',
                    'review_72h': '待复盘',
                    'completed': False
                })
                existing_review_keys.add((coin_key, date_key))
                new_review_count += 1
                reviews_modified = True
            # 同日覆盖：不更新复盘，保持第一条的时间戳和 entry_price
        
        # 文件锁：防止与 publish_social.py 并发写入
        lock_file = ANALYSES_FILE + '.lock'
        lock_fd = None
        for _ in range(20):
            try:
                lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                time.sleep(0.5)
        else:
            raise TimeoutError('获取文件锁超时')
        try:
            tmp_path = ANALYSES_FILE + '.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(analyses_records, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, ANALYSES_FILE)
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
                try: os.unlink(lock_file)
                except: pass
        
        if reviews_modified:
            review_tmp = REVIEWS_PATH + '.tmp'
            with open(review_tmp, 'w') as f:
                json.dump(reviews_records, f, indent=2, ensure_ascii=False)
            os.replace(review_tmp, REVIEWS_PATH)
            print(f"  ✅ 已创建复盘占位: +{new_review_count} 条", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Failed to save analyses.json: {e}", file=sys.stderr)


if __name__ == '__main__':
    main()
