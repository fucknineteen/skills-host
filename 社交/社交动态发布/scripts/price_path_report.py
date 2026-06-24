#!/usr/bin/env python3
"""
price_path_report.py — 价格走势路径分析报告

读取 okx_klines.db + analyses.json，针对每条复盘记录输出：
1. 入场后价格走势路径类型（先涨后跌/先跌后涨/单边上涨/单边下跌/横盘震荡等）
2. 各阶段涨跌幅与时间点
3. 极值点（最高/最低）出现的时间和幅度
4. 与入场方向的吻合度评估

用法: python price_path_report.py [--days N] [--coin COIN]
"""
import json
import os
import re
import sys
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import BJT, DB_PATH as _SHARED_DB_PATH, TRADE_DIR, classify_price_path, get_klines

DB_PATH = _SHARED_DB_PATH
ANALYSES_PATH = f'{TRADE_DIR}/analyses.json'
REVIEWS_PATH = f'{TRADE_DIR}/reviews.json'


def generate_report(review, candles, db):
    entry_price = review.get('entry_price', 0)
    coin = review.get('coin', '')
    ts = review.get('timestamp', '')
    if not entry_price or not candles:
        return None
    
    path_type, detail = classify_price_path(candles, entry_price)
    
    try:
        dt = datetime.fromisoformat(ts.replace('+0800', '+08:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BJT)
        review_bj = dt.astimezone(BJT).strftime('%m-%d %H:%M')
    except:
        review_bj = ts
    
    net = detail['net_change_pct']
    high = detail['global_high']
    low = detail['global_low']
    high_time = detail['global_high_time']
    low_time = detail['global_low_time']
    fh = detail['first_half']
    sh = detail['second_half']
    
    lines = [
        f"【{coin.replace('USDT','')} 价格路径分析】",
        f"  分析时间: {review_bj} | 入场价: {entry_price:,.2f}",
        f"  路径类型: {path_type}",
        f"  净涨跌: {net:+.2f}% | 振幅: {detail['total_range_pct']:.2f}%",
        f"  最高: {high:,.2f} ({high_time}) | 最低: {low:,.2f} ({low_time})",
        f"  前半段: {fh['change_pct']:+.2f}% (高{fh['high']:,.2f} 低{fh['low']:,.2f})",
        f"  后半段: {sh['change_pct']:+.2f}% (高{sh['high']:,.2f} 低{sh['low']:,.2f})",
    ]
    if detail['max_drawdown_pct'] > 2:
        lines.append(f"  ⚠️ 最大回撤: {detail['max_drawdown_pct']:.2f}%")
    if detail['max_rally_pct'] > 2:
        lines.append(f"  ⚠️ 最大反弹: {detail['max_rally_pct']:.2f}%")
    
    return {
        'coin': coin,
        'review_time': review_bj,
        'entry_price': entry_price,
        'path_type': path_type,
        'summary': '\n'.join(lines),
        'detail': detail,
    }


def main():
    parser = argparse.ArgumentParser(description='价格走势路径分析报告')
    parser.add_argument('--days', type=int, default=7, help='回溯天数 (默认7)')
    parser.add_argument('--coin', type=str, default=None, help='筛选特定币种')
    args = parser.parse_args()
    
    try:
        with open(REVIEWS_PATH, 'r') as f:
            reviews = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No reviews.json found."); return
    
    completed = [r for r in reviews if r.get('completed') and r.get('review_72h') != '待复盘']
    if not completed:
        print("No completed reviews found."); return
    
    if args.coin:
        completed = [r for r in completed if r.get('coin') == args.coin]
    
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    
    reports = []
    for review in completed:
        coin = review.get('coin', '')
        ts = review.get('timestamp', '')
        entry_price = review.get('entry_price', 0)
        if not entry_price or not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace('+0800', '+08:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BJT)
            review_ts = int(dt.timestamp() * 1000)
        except:
            continue
        
        since_ms = review_ts
        until_ms = min(review_ts + 73 * 3600 * 1000, int(datetime.now(timezone.utc).timestamp() * 1000))
        candles = get_klines(db, coin, '1H', since_ms, until_ms, limit=200)
        if not candles:
            continue
        
        report = generate_report(review, candles, db)
        if report:
            reports.append(report)
    
    db.close()
    
    print(f"{'='*70}")
    print(f"价格走势路径分析报告 | 回溯 {args.days} 天 | 共 {len(reports)} 条")
    print(f"{'='*70}\n")
    
    by_type = defaultdict(list)
    for r in reports:
        by_type[r['path_type']].append(r)
    
    for path_type, items in sorted(by_type.items()):
        print(f"\n{'─'*50}")
        print(f"【{path_type}】({len(items)}条)")
        print(f"{'─'*50}")
        for r in items:
            print(r['summary'])
            print()
    
    print(f"\n{'='*70}")
    print("路径类型分布:")
    for path_type, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        bar = '█' * len(items)
        print(f"  {path_type:12s} {len(items):3d} {bar}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
