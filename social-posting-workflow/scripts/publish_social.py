#!/usr/bin/env python3
"""
社交动态一键发布 — 7步流水线
步骤：同步K线 → 分析 → 复盘上条 → 生成文案 → 核验 → 用户确认 → 生成配图 → 保存

用法:
    python3 publish_social.py BTC ETH          # 指定币种
    python3 publish_social.py --all             # 全量
    python3 publish_social.py --coins BTC ETH   # 指定币种（长格式）
"""
import os, sys, json, subprocess, time, threading
from datetime import datetime, timezone, timedelta

_TRADE_DIR = os.environ.get('TRADE_DIR', '/root/.hermes/trade_review')
sys.path.insert(0, _TRADE_DIR)

from _shared import BJT
from _social_publish import (
    fetch_fear_greed,
    get_regime_result,
    fetch_okx_ticker,
    fetch_okx_funding,
    analyze_single_coin,
    generate_social_draft,
    extract_direction,
    select_chart_style,
)

# Local NOW_BJ for timestamp matching
_NOW_UTC = datetime.now(timezone.utc)
NOW_BJ = _NOW_UTC.astimezone(BJT)

# ── 常量 ──────────────────────────────────────────────────
MONITOR_SCRIPT = os.path.join(_TRADE_DIR, 'monitor_and_sync.py')
REVIEW_SCRIPT = os.path.join(_TRADE_DIR, 'scripts', 'review_last_post.py')
VERIFY_SCRIPT = os.path.join(_TRADE_DIR, 'verify_social_post.py')
SAVE_SCRIPT = os.path.join(_TRADE_DIR, 'scripts', 'save_social_post.py')
CHART_SCRIPT = os.path.join(_TRADE_DIR, 'scripts', 'gen_charts.py')
DB_PATH = os.path.join(_TRADE_DIR, 'okx_klines.db')


def step_sync(coins):
    """Step 1: 同步K线"""
    print(f'\n[1/7] 同步K线数据...')
    cmd = ['python3', MONITOR_SCRIPT] + coins
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=_TRADE_DIR)
    if r.returncode != 0:
        print(f'  ⚠️ 同步警告: {r.stderr[:200]}')
    print(f'  ✅ 同步完成')
    return r.stdout.strip()


def step_review():
    """Step 3: 复盘上条动态"""
    print('[3/7] 复盘上条动态...')
    r = subprocess.run(['python3', REVIEW_SCRIPT, '--save'],
        capture_output=True, text=True, timeout=120, cwd=_TRADE_DIR)
    review_text = r.stdout.strip()
    if review_text:
        # 提取关键行
        lines = []
        for line in review_text.split('\n'):
            s = line.strip()
            if s and ('→' in s or 'No posts' in s or '复盘' in s):
                lines.append(s)
        if not lines:
            lines = [review_text.split('\n')[-1].strip()] if review_text.strip() else []
        review_formatted = '\n'.join(lines)
    else:
        review_formatted = ''
    if review_formatted:
        print(f'  📋 {review_formatted}')
    else:
        print('  ℹ️ 无可复盘内容')
    return review_formatted


def step_generate_draft(analyses, regime_result, fg_val, fg_label, review_text):
    """Step 4: 生成文案草稿"""
    print('[4/7] 生成文案草稿...')
    draft = generate_social_draft(analyses, regime_result, fg_val, fg_label, review_text)
    print(draft)
    return draft


def step_verify(draft):
    """Step 5: 核验文案数据"""
    print('[5/7] 核验文案数据...')
    r = subprocess.run(['python3', VERIFY_SCRIPT, draft],
        capture_output=True, text=True, timeout=60, cwd=_TRADE_DIR)
    if r.returncode != 0:
        print(f'  ❌ 核验失败: {r.stdout.strip()[:500]}')
        print(f'  ⚠️ 请修正文案后重新核验')
        return False, r.stdout
    print(f'  ✅ 数据核验通过')
    return True, r.stdout


def step_user_review(draft):
    """Step 5.5: 用户审核"""
    print('\n' + '=' * 50)
    print('  请审核以上文案')
    print('  输入 "confirm" 继续，或修改后粘贴新文案')
    print('=' * 50)
    try:
        resp = input('> ').strip().lower()
        if resp == 'confirm':
            return draft
        elif resp:
            print(f'  使用你输入的文案...')
            return resp
        else:
            print(f'  使用默认文案...')
            return draft
    except (EOFError, KeyboardInterrupt):
        print(f'\n  使用默认文案...')
        return draft


def step_chart(draft, analyses, regime_result):
    """Step 6: 生成配图（1小时内同风格缓存复用）"""
    print('[6/7] 生成配图...')
    style = select_chart_style(analyses, regime_result)
    chart_path = '/tmp/social_chart.png'
    cache_meta = '/tmp/social_chart_meta.json'
    
    # 缓存检查：1小时内同风格复用
    if os.path.exists(chart_path) and os.path.exists(cache_meta):
        try:
            with open(cache_meta) as f:
                meta = json.load(f)
            age = time.time() - meta.get('ts', 0)
            if age < 3600 and meta.get('style') == style:
                print(f'  ✅ 配图缓存命中 (Style {style}, {age:.0f}s ago): {chart_path}')
                return chart_path, style
        except Exception:
            pass
    
    cmd = ['python3', CHART_SCRIPT, '--style', str(style), chart_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=_TRADE_DIR)
    if os.path.exists(chart_path):
        # 写入缓存元数据
        try:
            with open(cache_meta, 'w') as f:
                json.dump({'ts': time.time(), 'style': style}, f)
        except Exception:
            pass
        print(f'  ✅ 配图已生成 (Style {style}): {chart_path}')
        return chart_path, style
    else:
        print(f'  ⚠️ 配图生成失败，继续发送文案')
        return None, style


def step_save(draft, analyses, regime_result):
    """Step 7: 保存记录"""
    print('[7/7] 保存记录...')
    btc_a = next((a for a in analyses if a['coin'] == 'BTC'), None)
    eth_a = next((a for a in analyses if a['coin'] == 'ETH'), None)
    btc_p = btc_a.get('ticker', {}).get('last', '?') if btc_a else '?'
    eth_p = eth_a.get('ticker', {}).get('last', '?') if eth_a else '?'
    
    cmd = ['python3', SAVE_SCRIPT,
        '--btc', str(btc_p),
        '--eth', str(eth_p),
        '--fg', str(regime_result.get('composite_score', 0)),
        '--regime', regime_result.get('regime', ''),
        '--direction-btc', extract_direction(btc_a) if btc_a else '',
        '--direction-eth', extract_direction(eth_a) if eth_a else '',
        '--text', draft
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=_TRADE_DIR)
    print(f'  {r.stdout.strip()}')


def main():
    # 解析参数
    args = sys.argv[1:]
    if '--all' in args:
        coins = ['BTC', 'ETH', 'SOL', 'DOGE']
    elif '--coins' in args:
        idx = args.index('--coins')
        coins = args[idx+1:args.index('--all') if '--all' in args[idx+1:] else len(args)]
    else:
        coins = [a for a in args if not a.startswith('-')]
        if not coins:
            coins = ['BTC', 'ETH']

    print(f'\n🚀 社交动态发布流水线')
    print(f'   币种: {coins}')
    print(f'   时间: {datetime.now(BJT).strftime("%Y-%m-%d %H:%M")} BJ')

    # Step 1: 同步
    step_sync(coins)

    # Step 2: 拉取宏观 + 执行分析（内置，无子进程冗余）
    print(f'\n[2/7] 拉取宏观数据 + 分析币种...')
    fg_val, fg_label = fetch_fear_greed()
    regime_result = get_regime_result()
    print(f'  ✅ FG={fg_val}({fg_label}), regime={regime_result.get("regime","?")}')
    
    conn = __import__('sqlite3').connect(DB_PATH)
    analyses = []
    for i, coin in enumerate(coins):
        tres = [None]; fres = [None]
        def _ft(): tres[0] = fetch_okx_ticker(f'{coin}USDT')
        def _ff(): fres[0] = fetch_okx_funding(f'{coin}USDT')
        t1 = threading.Thread(target=_ft)
        t2 = threading.Thread(target=_ff)
        t1.start(); t2.start()
        t1.join(); t2.join()
        a = analyze_single_coin(conn, coin, tres[0], fres[0], fg_val, fg_label)
        analyses.append(a)
        print(f'  ✅ {coin}: {a["ticker"].get("last", "?")}, resonance={a["resonance"]}')
        if i < len(coins) - 1:
            time.sleep(1)
    conn.close()
    has_saved_analyses = True

    # Step 3: 复盘
    review_text = step_review()

    # If we generated fresh analyses via fallback, write them to social_analyses.json for verification
    if has_saved_analyses:
        analyses_file = os.path.join(_TRADE_DIR, 'social_analyses.json')
        lock_file = analyses_file + '.lock'
        try:
            # 获取文件锁（最多等待 10 秒）
            for _ in range(20):
                try:
                    lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    break
                except FileExistsError:
                    time.sleep(0.5)
            else:
                raise TimeoutError('获取文件锁超时')
            
            with open(analyses_file, 'r') as f:
                existing = json.load(f)
            merged = [r for r in existing if not r.get('timestamp', '').startswith(NOW_BJ.strftime('%Y-%m-%d'))]
            
            # 保存 fg_val/fg_label 供写入记录
            _fg_val = fg_val
            _fg_label = fg_label
            
            for a in analyses:  # analyses = full_obj from fallback
                coin_tag = a['coin']  # 'BTC' or 'ETH'
                lows = a.get('levels_4h', {}).get('lows', [])
                highs = a.get('levels_4h', {}).get('highs', [])
                entry = float(a.get('ticker', {}).get('last', 0) or 0)
                
                # 计算止损/止盈 — P1b: ATR缓冲 + 区分多空
                inds = a.get('indicators', {})
                atr_4h_val = inds.get('4H', {}).get('atr', entry * 0.02)
                pos_dir = a.get('position', '观望')
                
                if lows and highs and entry and atr_4h_val:
                    if '空' in str(pos_dir):
                        sl_val = int(highs[0] + atr_4h_val * 0.5)
                        tp_val = int(lows[0])
                    else:
                        sl_val = int(lows[0] - atr_4h_val * 0.5)
                        tp_val = int(highs[0])
                else:
                    sl_val = 0
                    tp_val = 0
                
                # 计算 RR
                rr_str = '?'
                if entry and sl_val and tp_val:
                    rr = round((tp_val - entry) / (entry - sl_val), 1)
                    rr_str = f'{rr:.1f}' if rr > 0 else '?'
                
                # 提取费率（从 OKX funding API 响应）
                funding_raw = a.get('funding', {})
                funding_rate_pct = None
                if isinstance(funding_raw, dict) and not funding_raw.get('_error'):
                    try:
                        fr = float(funding_raw.get('fundingRate', '0'))
                        funding_rate_pct = round(fr * 100, 4)  # 转为百分比
                    except (ValueError, TypeError):
                        pass
                
                record = {
                    'timestamp': NOW_BJ.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': f"{coin_tag}USDT",
                    'ticker': a['ticker'],
                    'funding': a['funding'],
                    'funding_rate_pct': funding_rate_pct,  # 可核验的费率字段
                    'indicators': a['indicators'],
                    'levels_4h': a['levels_4h'],
                    'resonance': a['resonance'],
                    'position': a.get('position', '观望'),
                    'rsi_4h': a['rsi_4h'],
                    'macd_h_4h': a['macd_h_4h'],
                    'macd_h_1h': a['macd_h_1h'],
                    'pct_b': a['pct_b'],
                    'vp_data': a.get('vp_data', {}),
                    'wyckoff_data': a.get('wyckoff_data', {}),
                    'kline_patterns': a.get('kline_patterns', []),
                    'calendar_events': a.get('calendar_events', []),
                    'macro_external': a.get('macro_external', {}),
                    # 计算字段（供核验使用）
                    'sl_val': sl_val,
                    'tp_val': tp_val,
                    'rr_str': rr_str,
                    'entry_price': entry,
                }
                merged.append(record)
            # 在每个币种记录后追加 FG 值
            if _fg_val:
                merged.append({
                    'timestamp': NOW_BJ.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': 'FG',
                    'fg_val': _fg_val,
                    'fg_label': _fg_label,
                })
            if review_text:
                merged.append({
                    'timestamp': NOW_BJ.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': 'REVIEW',
                    'review_text': review_text,
                })
            with open(analyses_file, 'w') as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            print(f'  ✅ 已更新 social_analyses.json（完整对象格式）')
        except Exception as e:
            print(f'  ⚠️ 写入 social_analyses.json 失败: {e}')
        finally:
            try:
                os.close(lock_fd)
                os.unlink(lock_file)
            except Exception:
                pass

    # 宏观数据已在并行阶段拉取（FG + regime）

    # Step 4: 生成文案
    draft = step_generate_draft(analyses, regime_result, fg_val, fg_label, review_text)

    # Step 5: 核验
    passed, verify_output = step_verify(draft)
    if not passed:
        print('\n❌ 核验未通过，终止发布流程')
        sys.exit(1)

    # Step 5.5: 用户审核
    draft = step_user_review(draft)

    # Step 6: 配图
    chart_path, chart_style = step_chart(draft, analyses, regime_result)

    # Step 7: 保存
    step_save(draft, analyses, regime_result)

    # 输出最终结果
    print('\n' + '=' * 50)
    print('  ✅ 发布流程完成')
    print(f'  文案: {len(draft)} 字符')
    if chart_path:
        print(f'  配图: {chart_path} (Style {chart_style})')
    print('  发送给用户后，附上配图')
    print('=' * 50)

    # 返回配图路径供外部使用
    if chart_path:
        print(f'\nMEDIA:{chart_path}')


if __name__ == '__main__':
    main()
