#!/usr/bin/env python3
"""
社交动态自动保存器
用法: python3 scripts/save_social_post.py --btc 65456 --eth 1716 --fg 20 \\
          --direction-btc "做多 回踩65000" --direction-eth "做多 1725" \\
          --text "BTC连涨5天..." --regime "牛市回调"
       --text-file PATH  从文件读取文案（避免 ARG_MAX 超限）

发送后立即调用，无需手动编辑 social_posts.json。
"""
import json, os, sys, argparse, shutil, tempfile
from datetime import datetime, timezone, timedelta
from _shared import BJT

POSTS_FILE = '/root/.hermes/trade_review/social_posts.json'

def main():
    p = argparse.ArgumentParser(description='Save social media post to social_posts.json')
    p.add_argument('--btc', type=float, help='BTC price at post time')
    p.add_argument('--eth', type=float, help='ETH price at post time')
    p.add_argument('--fg', type=int, help='Fear & Greed index')
    p.add_argument('--direction-btc', type=str, default='', help='BTC trading direction')
    p.add_argument('--direction-eth', type=str, default='', help='ETH trading direction')
    p.add_argument('--text', type=str, default=None, help='Post text content (short)')
    p.add_argument('--text-file', type=str, default=None, help='Path to file containing post text (for long text)')
    p.add_argument('--regime', type=str, default='', help='Current regime type')
    p.add_argument('--time', type=str, default='', help='Override timestamp (BJ)')
    args = p.parse_args()

    # Resolve text: prefer --text-file, fall back to --text
    text = None
    if args.text_file:
        if not os.path.exists(args.text_file):
            print(f"❌ --text-file 指定的文件不存在: {args.text_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.text_file) as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        print("❌ 需要 --text 或 --text-file", file=sys.stderr)
        sys.exit(1)

    # Read existing posts
    posts = []
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE) as f:
            posts = json.load(f)

    # Auto-increment ID
    next_id = max([p.get('id', 0) for p in posts], default=0) + 1

    # 去重：检查最近一条记录是否在文案、价格、方向上均与当前相同
    # 或文案完全一致且时间差 < 5分钟（防止同一时刻误发两次）
    if posts:
        last = posts[-1]
        last_text = last.get('text', '')
        last_btc = last.get('btc_price')
        last_eth = last.get('eth_price')
        last_btc_dir = last.get('btc_direction', '')
        last_eth_dir = last.get('eth_direction', '')
        last_time_str = last.get('time', '')
        # 尝试解析上条时间
        try:
            last_dt = datetime.strptime(last_time_str.replace(' BJ', ''), '%Y-%m-%d %H:%M')
            last_dt = last_dt.replace(tzinfo=BJT)
            now_dt = datetime.now(BJT)
            time_diff_min = (now_dt - last_dt).total_seconds() / 60
        except Exception:
            time_diff_min = 999
        # 文案相同 + 价格方向也相同 → 完全重复
        # 文案相同 + 时间差 < 5min → 疑似误发
        if (last_text == text
            and last_btc == args.btc
            and last_eth == args.eth
            and last_btc_dir == args.direction_btc
            and last_eth_dir == args.direction_eth):
            last_id = last.get('id')
            print(f'⏭️  Skip: 文案与上条 #{last_id} 完全相同（价格/方向均未变化），跳过保存')
            return
        if last_text == text and time_diff_min < 5:
            last_id = last.get('id')
            print(f'⏭️  Skip: 文案与上条 #{last_id} 完全相同（{time_diff_min:.0f}min前），疑似误发，跳过保存')
            return

    # Timestamp
    ts = args.time if args.time else datetime.now(BJT).strftime('%Y-%m-%d %H:%M BJ')

    record = {
        'id': next_id,
        'time': ts,
        'btc_price': args.btc,
        'eth_price': args.eth,
        'btc_direction': args.direction_btc,
        'eth_direction': args.direction_eth,
        'fg': args.fg,
        'regime': args.regime,
        'text': text,
    }

    posts.append(record)

    # Append-only guard: backup before write (max 5 rolling backups)
    if os.path.exists(POSTS_FILE):
        for i in range(4, 0, -1):
            src = f'{POSTS_FILE}.bak{i}' if i > 1 else f'{POSTS_FILE}.bak'
            dst = f'{POSTS_FILE}.bak{i+1}'
            if os.path.exists(src):
                shutil.copy2(src, dst)
        shutil.copy2(POSTS_FILE, f'{POSTS_FILE}.bak')

    # Atomic write
    tmp = POSTS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    os.replace(tmp, POSTS_FILE)

    print(f'✅ Post #{next_id} saved to social_posts.json ({len(text)} chars)')

if __name__ == '__main__':
    main()
