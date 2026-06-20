#!/usr/bin/env python3
"""
社交动态自动保存器
用法: python3 scripts/save_social_post.py --btc 65456 --eth 1716 --fg 20 \\
          --direction-btc "做多 回踩65000" --direction-eth "做多 1725" \\
          --text "BTC连涨5天..." --regime "牛市回调"

发送后立即调用，无需手动编辑 social_posts.json。
"""
import json, os, sys, argparse, shutil
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
    p.add_argument('--text', type=str, required=True, help='Post text content')
    p.add_argument('--regime', type=str, default='', help='Current regime type')
    p.add_argument('--time', type=str, default='', help='Override timestamp (BJ)')
    args = p.parse_args()

    # Read existing posts
    posts = []
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE) as f:
            posts = json.load(f)

    # Auto-increment ID
    next_id = max([p.get('id', 0) for p in posts], default=0) + 1

    # 去重：检查最近一条记录的文案是否完全相同
    if posts:
        last = posts[-1]
        if last.get('text', '') == args.text:
            last_id = last.get('id')
            print(f'⏭️  Skip: 文案与上条 #{last_id} 完全相同，跳过保存')
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
        'text': args.text,
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

    print(f'✅ Post #{next_id} saved to social_posts.json ({len(args.text)} chars)')

if __name__ == '__main__':
    main()
