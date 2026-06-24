#!/usr/bin/env python3
"""
山寨币日内复盘 v2
==================
15min 方向快判(±2%) + 1h 完整复盘(盈亏+评分追踪+教训)。
不设 24h——日内持仓最長 1h 结算。

用法:
    python3 review_altcoin.py                              # 查看待复盘
    python3 review_altcoin.py --add COIN score verdict dir entry  # 新增
    python3 review_altcoin.py --settle COIN exit_price triggered   # 结算出场
    python3 review_altcoin.py --quick COIN                        # 15min 快判
"""
import json, os, sys, subprocess
from datetime import datetime, timezone, timedelta
from _shared import BJT

NOW = datetime.now(BJT)
REVIEWS_FILE = '/root/.hermes/trade_review/data/altcoin_reviews.json'
LESSONS_DIR = '/root/.hermes/trade_review/altcoin_lessons/'
THRESHOLD = 2.0  # ±2%


def load():
    if not os.path.exists(REVIEWS_FILE): return []
    with open(REVIEWS_FILE) as f: return json.load(f)


def save(data):
    os.makedirs(os.path.dirname(REVIEWS_FILE), exist_ok=True)
    with open(REVIEWS_FILE, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)


def get_current_price(coin):
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '5',
            f'https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT-SWAP'],
            capture_output=True, text=True, timeout=8)
        d = json.loads(r.stdout) if r.stdout else {}
        if d.get('code') == '0' and d.get('data'):
            return float(d['data'][0]['last'])
    except Exception:
        pass
    return None


def add(coin, score, verdict, direction, entry_price=None):
    reviews = load()
    r = {
        'coin': coin, 'timestamp_bj': NOW.strftime('%Y-%m-%d %H:%M'),
        'entry_price': entry_price, 'score': int(score),
        'verdict': verdict, 'direction': direction,
        'review_15m': '⏳', 'review_1h': '⏳',
        'exit_price': None, 'pnl_pct': None,
        'score_accurate': None, 'triggered': None,
        'completed': False
    }
    reviews.append(r)
    save(reviews)
    print(f'✅ {coin} 已加入复盘 | 评分{score} | {verdict} | {direction}')


def quick_check(coin=None):
    """15min check: direction ±2%."""
    reviews = load()
    candidates = [r for r in reviews if r.get('review_15m') == '⏳' and not r.get('completed')]
    if coin:
        candidates = [r for r in candidates if r['coin'].upper() == coin.upper()]

    if not candidates:
        print('⚠️ 无待 15min 复盘记录')
        return

    for r in candidates:
        price = get_current_price(r['coin'])
        if not price:
            r['review_15m'] = '⚠️ API'
            continue

        entry = r.get('entry_price')
        if not entry:
            r['review_15m'] = '⚠️ 无价'
            continue

        change = (price - entry) / entry * 100
        if abs(change) <= THRESHOLD:
            r['review_15m'] = f'🟡{change:+.1f}%'
        elif r['direction'] in ('中性偏多', '做多') and change > THRESHOLD:
            r['review_15m'] = f'✅{change:+.1f}%'
        elif r['direction'] in ('中性偏空', '做空') and change < -THRESHOLD:
            r['review_15m'] = f'✅{change:+.1f}%'
        else:
            r['review_15m'] = f'❌{change:+.1f}%'

        print(f"  {r['coin']} 15min: {r['review_15m']} ({r['entry_price']}→{price})")

    save(reviews)


def settle(coin, exit_price, triggered):
    """Settle position, complete 1h review."""
    reviews = load()
    candidates = [r for r in reviews
                  if r['coin'].upper() == coin.upper() and not r.get('completed')]
    if not candidates:
        print(f'⚠️ {coin} 无待结算记录')
        return

    r = candidates[-1]
    entry = r.get('entry_price')
    exit_px = float(exit_price)
    pnl = (exit_px - entry) / entry * 100 if entry else 0

    r['exit_price'] = exit_px
    r['pnl_pct'] = round(pnl, 2)
    r['triggered'] = triggered

    # Score accuracy
    score = r.get('score', 0)
    if score >= 60 and pnl > 0:
        r['score_accurate'] = '✅ 高分盈利'
    elif score >= 60 and pnl < 0:
        r['score_accurate'] = '⚠️ 高分亏损'
    elif score <= 35 and pnl < 0:
        r['score_accurate'] = '✅ 低分亏损'
    elif score <= 35 and pnl > 0:
        r['score_accurate'] = '❌ 低分盈利(假阴)'
    else:
        r['score_accurate'] = '⚪ 中性'

    # 1h verdict
    if r['direction'] in ('中性偏多', '做多'):
        r['review_1h'] = f'✅+{pnl:.1f}%' if pnl > 0 else f'❌{pnl:.1f}%'
    elif r['direction'] in ('中性偏空', '做空'):
        r['review_1h'] = f'✅{pnl:.1f}%' if pnl < 0 else f'❌+{pnl:.1f}%'
    else:
        r['review_1h'] = f'{pnl:+.1f}%'

    r['completed'] = True
    save(reviews)

    print(f'✅ {coin} 已结算')
    print(f'   入场:{entry} → 出场:{exit_px}  P&L:{pnl:+.2f}%')
    print(f'   触发:{triggered}  评分追踪:{r["score_accurate"]}')

    # Auto-save notable lessons
    if score >= 75 and pnl < -2:
        save_lesson('高分失败', coin,
            f'评分{score}但亏损{pnl:.1f}% | {r["verdict"]} | 触发:{triggered}')
    elif score <= 35 and pnl > 2:
        save_lesson('低分逆转', coin,
            f'评分{score}但盈利+{pnl:.1f}% | 假阴性，庄家可能反向操作')


def save_lesson(category, coin, text):
    os.makedirs(LESSONS_DIR, exist_ok=True)
    path = os.path.join(LESSONS_DIR, f'{category}.json')
    lessons = []
    if os.path.exists(path):
        with open(path) as f: lessons = json.load(f)
    lessons.append({'coin': coin, 'timestamp_bj': NOW.strftime('%Y-%m-%d %H:%M'), 'lesson': text})
    with open(path, 'w') as f: json.dump(lessons, f, ensure_ascii=False, indent=2)


def show():
    reviews = load()
    pending = [r for r in reviews if not r.get('completed')]
    done = [r for r in reviews if r.get('completed')]
    print(f'📋 进行中:{len(pending)}  已完成:{len(done)}')
    print(f'  {"时间":<16s} {"币":<8s} {"分":>3s} {"入场":>10s} {"出场":>10s} {"P&L":>7s} 15m      1h')
    print(f'  {"─"*16} {"─"*8} {"─"*3} {"─"*10} {"─"*10} {"─"*7} {"─"*8} {"─"*8}')

    for r in sorted(reviews, key=lambda x: x.get('timestamp_bj',''), reverse=True)[:20]:
        s15 = r.get('review_15m','?')
        s1h = r.get('review_1h','?')
        ep = f"${r.get('entry_price','?'):.6f}" if r.get('entry_price') else '?'
        xp = f"${r['exit_price']:.6f}" if r.get('exit_price') else '-'
        pnl = f"{r['pnl_pct']:+.2f}%" if r.get('pnl_pct') is not None else '-'
        print(f'  {r.get("timestamp_bj","?"):<16s} {r["coin"]:<8s} {r.get("score",0):>3d} {ep:>10s} {xp:>10s} {pnl:>7s} {s15:<8s} {s1h:<8s}')


if __name__ == '__main__':
    if '--add' in sys.argv:
        try:
            idx = sys.argv.index('--add')
            coin, sc, v, d = sys.argv[idx+1:idx+5]
            ep = float(sys.argv[idx+5]) if idx+5 < len(sys.argv) else None
            add(coin, int(sc), v, d, ep)
        except:
            print('用法: --add COIN SCORE VERDICT DIRECTION [PRICE]')
    elif '--quick' in sys.argv:
        coin = sys.argv[sys.argv.index('--quick')+1] if len(sys.argv) > 2 else None
        quick_check(coin)
    elif '--settle' in sys.argv:
        try:
            idx = sys.argv.index('--settle')
            coin, px, trig = sys.argv[idx+1:idx+4]
            settle(coin, float(px), trig)
        except:
            print('用法: --settle COIN EXIT_PRICE TRIGGERED')
            print('  triggered: stop_loss / take_profit / manual / not_triggered')
    else:
        show()
