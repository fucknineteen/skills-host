#!/usr/bin/env python3
"""
社交动态一键发布 — 7步流水线
步骤：同步K线 → 分析 → 复盘上条 → 生成文案 → 核验 → 用户确认 → 生成配图 → 保存

用法:
    python3 publish_social.py BTC ETH          # 指定币种
    python3 publish_social.py --all             # 全量
    python3 publish_social.py --coins BTC ETH   # 指定币种（长格式）
    python3 publish_social.py --verify-only BTC # 仅分析+保存到 social_analyses.json（供核验回退用）
"""
import os, sys, json, subprocess, time, threading
from subprocess import TimeoutExpired
from datetime import datetime, timezone

from _shared import BJT, TRADE_DIR
sys.path.insert(0, TRADE_DIR)
from _social_publish import (
    fetch_fear_greed,
    get_regime_result,
    fetch_okx_ticker,
    fetch_okx_funding,
    analyze_single_coin,
    generate_social_draft,
    extract_direction,
    select_chart_style,
    calc_sl_tp,          # 统一 SL/TP 计算（五轮审计 #20）
)

# Dynamic _now_bj() — use function instead of module-level freeze
def _now_bj():
    return datetime.now(timezone.utc).astimezone(BJT)

# ── 常量 ──────────────────────────────────────────────────
MONITOR_SCRIPT = os.path.join(TRADE_DIR, 'monitor_and_sync.py')
REVIEW_SCRIPT = os.path.join(TRADE_DIR, 'scripts', 'review_last_post.py')
VERIFY_SCRIPT = os.path.join(TRADE_DIR, 'verify_social_post.py')
SAVE_SCRIPT = os.path.join(TRADE_DIR, 'scripts', 'save_social_post.py')
CHART_SCRIPT = os.path.join(TRADE_DIR, 'scripts', 'gen_charts.py')
DB_PATH = os.path.join(TRADE_DIR, 'okx_klines.db')


def step_sync(coins):
    """Step 1: 同步K线"""
    print(f'\n[1/7] 同步K线数据...')
    cmd = ['python3', MONITOR_SCRIPT] + coins
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=TRADE_DIR)
    except TimeoutExpired:
        print('  ❌ 同步超时，终止发布流程（违反STEP-1合同）')
        sys.exit(1)
    if r.returncode != 0:
        print(f'  ⚠️ 同步警告: {r.stderr[:200]}')
    print(f'  ✅ 同步完成')
    return r.stdout.strip()


def step_review():
    """Step 3: 复盘上条动态"""
    print('[3/7] 复盘上条动态...')
    try:
        env = os.environ.copy()
        env['PYTHONPATH'] = TRADE_DIR
        r = subprocess.run(['python3', REVIEW_SCRIPT, '--save'],
            capture_output=True, text=True, timeout=120, cwd=TRADE_DIR, env=env)
    except TimeoutExpired:
        print('  ❌ 复盘超时，终止发布流程（违反STEP-3合同）')
        sys.exit(1)
    review_text = r.stdout.strip()
    if review_text:
        # 提取关键行 — 保留发帖时间行（含"复盘上条:"）和数据行（含→且不含"复盘上条:"）
        time_line = ''
        lines = []
        for line in review_text.split('\n'):
            s = line.strip()
            if '复盘上条:' in s:
                time_line = s.replace('📋 复盘上条:', '📋 上条').strip()  # 提取时间
            elif s and '→' in s:
                lines.append(s)
        if not lines:
            lines = [review_text.split('\n')[-1].strip()] if review_text.strip() else []
        review_formatted = time_line + '\n' + '\n'.join(lines) if time_line else '\n'.join(lines)
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
    try:
        draft = generate_social_draft(analyses, regime_result, fg_val, fg_label, review_text)
        print(draft)
        return draft
    except Exception as e:
        print(f'  ❌ 生成文案草稿异常: {e}')
        sys.exit(1)


def step_verify(draft):
    """Step 5: 核验文案数据"""
    print('[5/7] 核验文案数据...')
    try:
        r = subprocess.run(['python3', VERIFY_SCRIPT], input=draft,
            capture_output=True, text=True, timeout=60, cwd=TRADE_DIR)
    except TimeoutExpired:
        print('  ⚠️ 核验超时，继续执行')
        return False, '核验超时'
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
        resp = input('> ').strip()
        if resp.lower() == 'confirm':
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
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=TRADE_DIR)
    except TimeoutExpired:
        print(f'  ⚠️ 配图生成超时，继续发送文案')
        return None, style
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


def step_save(draft, analyses, regime_result, fg_val):
    """Step 7: 保存记录"""
    print('[7/7] 保存记录...')
    btc_a = next((a for a in analyses if a['coin'] == 'BTC'), None)
    eth_a = next((a for a in analyses if a['coin'] == 'ETH'), None)
    btc_p = btc_a.get('ticker', {}).get('last', '?') if btc_a else '?'
    eth_p = eth_a.get('ticker', {}).get('last', '?') if eth_a else '?'
    
    # Write draft to temp file to avoid ARG_MAX issues with long text
    draft_tmp = '/tmp/social_draft_tmp.txt'
    with open(draft_tmp, 'w') as f:
        f.write(draft)
    
    cmd = ['python3', SAVE_SCRIPT,
        '--btc', str(btc_p),
        '--eth', str(eth_p),
        '--fg', str(fg_val),
        '--regime', regime_result.get('regime', ''),
        '--direction-btc', extract_direction(btc_a) if btc_a else '',
        '--direction-eth', extract_direction(eth_a) if eth_a else '',
        '--text-file', draft_tmp
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=TRADE_DIR)
    except TimeoutExpired:
        print('  ⚠️ 保存记录超时')
        return
    finally:
        try: os.unlink(draft_tmp)
        except Exception: pass
    print(f'  {r.stdout.strip()}')


def main():
    # 解析参数
    args = sys.argv[1:]
    verify_only = '--verify-only' in args
    if verify_only:
        args = [a for a in args if a != '--verify-only']
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

    # 清理旧锁残留（防止 SIGKILL 后锁文件未释放）
    stale_lock_file = os.path.join(TRADE_DIR, 'social_analyses.json.lock')
    try:
        if os.path.exists(stale_lock_file):
            # 检查锁是否过期（> 5 分钟视为残留）
            lock_age = time.time() - os.path.getmtime(stale_lock_file)
            if lock_age > 300:
                os.unlink(stale_lock_file)
                print(f'  🔓 已清理残留锁文件 (age={lock_age:.0f}s)')
    except Exception:
        pass

    # Step 1: 同步
    step_sync(coins)

    # Step 2: 拉取宏观 + 执行分析（内置，无子进程冗余）
    print(f'\n[2/7] 拉取宏观数据 + 分析币种...')
    fg_val, fg_label = fetch_fear_greed()
    regime_result = get_regime_result()
    print(f'  ✅ FG={fg_val}({fg_label}), regime={regime_result.get("regime","?")}')
    
    # 快讯: 循环外拉取一次，多币种共享
    _shared_flash = []
    try:
        from jin10_fallback import fetch_flash_news as _ffn
        _fi, _fs, _ff = _ffn()
        for item in _fi[:8]:
            _shared_flash.append({
                'time': item.get('time', ''),
                'content': item.get('content', ''),
                'score': item.get('relevance_score', 0),
            })
    except Exception:
        pass
    
    try:
        conn = __import__('sqlite3').connect(DB_PATH)
    except Exception as e:
        print(f'  ❌ 数据库连接失败: {e}')
        print(f'  DB_PATH={DB_PATH}')
        sys.exit(1)
    analyses = []
    # 预拉取日历事件（多币种共享，避免 per-coin 重复调用）
    try:
        from _social_publish import get_jin10_key_events as _get_cal
        _shared_cal = _get_cal()
    except Exception:
        _shared_cal = None
    for i, coin in enumerate(coins):
        tres = [None]; fres = [None]
        def _ft(): tres[0] = fetch_okx_ticker(f'{coin}USDT')
        def _ff(): fres[0] = fetch_okx_funding(f'{coin}USDT')
        t1 = threading.Thread(target=_ft)
        t2 = threading.Thread(target=_ff)
        t1.start(); t2.start()
        t1.join(); t2.join()
        if tres[0] is None:
            print(f'  ⚠️ {coin}: ticker fetch failed, skipping')
            continue
        try:
            a = analyze_single_coin(conn, coin, tres[0], fres[0], fg_val, fg_label, flash_news=_shared_flash, calendar_events=_shared_cal)
            analyses.append(a)
            print(f'  ✅ {coin}: {a["ticker"].get("last", "?")}, resonance={a["resonance"]}')
        except Exception as e:
            print(f'  ❌ {coin}: analyze_single_coin() 异常: {e}')
            continue
        if i < len(coins) - 1:
            time.sleep(1)
    conn.close()

    # Step 3: 复盘
    review_text = step_review()

    # If we generated fresh analyses via fallback, write them to social_analyses.json for verification
    if analyses:
        analyses_file = os.path.join(TRADE_DIR, 'social_analyses.json')
        lock_file = analyses_file + '.lock'
        lock_fd = None
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
            
            # 读取已有记录（首次创建时文件不存在 → 空列表）
            try:
                with open(analyses_file, 'r') as f:
                    existing = json.load(f)
            except FileNotFoundError:
                existing = []
            
            # 去掉当天旧记录，追加新记录 + FG + REVIEW
            _today_str = _now_bj().strftime('%Y-%m-%d')  # 一次性快照，避免跨日边界
            merged = [r for r in existing if not r.get('timestamp', '').startswith(_today_str)]
            
            # 保存 fg_val/fg_label 供写入记录
            _fg_val = fg_val
            _fg_label = fg_label
            
            for a in analyses:  # analyses = full_obj from fallback
                coin_tag = a['coin']  # 'BTC' or 'ETH'
                # BUGFIX 6c: normalize coin field — strip USDT suffix for consistent naming
                if coin_tag.endswith('USDT'):
                    coin_tag = coin_tag[:-4]
                # FIX BUG#2: use near_support for SL/TP consistency with generate_social_draft
                lows = a.get('near_support', []) or a.get('levels_4h', {}).get('lows', [])
                highs = a.get('near_resistance', []) or a.get('levels_4h', {}).get('highs', [])
                # FIX BUG#3: handle ticker['last']='?' to avoid float('?') ValueError
                _last_val = a.get('ticker', {}).get('last', 0)
                try:
                    entry = float(_last_val) if _last_val and _last_val != '?' else 0
                except (ValueError, TypeError):
                    entry = 0
                
                # SL/TP v062309 — 优先使用 wrapper 已计算的值，回退到 calc_sl_tp
                pos_dir = a.get('position', '观望')
                if a.get('sl_val') is not None and a.get('sl_val') != 0:
                    sl_val, tp_val, rr_str = a['sl_val'], a['tp_val'], a['rr_str']
                else:
                    inds = a.get('indicators', {})
                    atr_4h_val = inds.get('4H', {}).get('atr', entry * 0.02)
                    sl_val, tp_val, rr_str = calc_sl_tp(entry, lows, highs, atr_4h_val, pos_dir)
                
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
                    'timestamp': _now_bj().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': coin_tag,
                    'ticker': a['ticker'],
                    'funding': a['funding'],
                    'funding_rate_pct': funding_rate_pct,  # 可核验的费率字段
                    'indicators': a['indicators'],
                    'levels_4h': a['levels_4h'],
                    'near_support': a.get('near_support', []),  # FIX: 写入 near_support/near_resistance 供 generate_social_draft 使用
                    'near_resistance': a.get('near_resistance', []),
                    'resonance': a['resonance'],
                    'position': a.get('position', '观望'),
                    'rsi_4h': a['rsi_4h'],
                    'macd_h_4h': a['macd_h_4h'],
                    'macd_h_1h': a['macd_h_1h'],
                    'pct_b': a['pct_b'],
                    'session_vp': a.get('session_vp', {}),
                    'wyckoff_data': a.get('wyckoff_data', {}),
                    'kline_patterns': a.get('kline_patterns', []),
                    'calendar_events': a.get('calendar_events', []),
                    'flash_news': a.get('flash_news', []),
                    'macro_external': a.get('macro_external', {}),
                    # order_flow 来自 regime cache（供复盘/核验使用）
                    'order_flow': regime_result.get('dimensions', {}).get('order_flow', {}),
                    # 底部研判（供 process_reviews 复盘用）
                    'near_bottom': a.get('near_bottom', False),
                    'bottom_note': a.get('bottom_note', '-'),
                    # 计算字段（供核验使用）
                    'sl_val': sl_val,
                    'tp_val': tp_val,
                    'rr_str': rr_str,
                    'entry_price': entry,
                }
                merged.append(record)
            # BUGFIX: fg_val=0 is falsy but valid; use is not None
            if _fg_val is not None:
                merged.append({
                    'timestamp': _now_bj().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': 'FG',
                    'fg_val': _fg_val,
                    'fg_label': _fg_label,
                })
            if review_text:
                merged.append({
                    'timestamp': _now_bj().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    'coin': 'REVIEW',
                    'review_text': review_text,
                })
            tmp = analyses_file + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            os.replace(tmp, analyses_file)
            has_saved_analyses = True
            print(f'  ✅ 已更新 social_analyses.json（完整对象格式）')
        except Exception as e:
            print(f'  ⚠️ 写入 social_analyses.json 失败: {e}')
        finally:
            try:
                if lock_fd is not None:
                    os.close(lock_fd)
                os.unlink(lock_file)
            except Exception:
                pass

    # --verify-only 模式：仅生成 social_analyses.json，跳过后续步骤
    if verify_only:
        print(f'  ✅ --verify-only 完成，已更新 social_analyses.json')
        return

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
    step_save(draft, analyses, regime_result, fg_val)

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
