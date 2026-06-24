#!/usr/bin/env python3
"""
教训上下文注入器 — 轻量闭环
读取 regimes/*_lessons.json（若存在则跳过 lessons.json 避免双重加载），输出可供分析用的上下文摘要。
在 process_reviews.py 完成后运行（:05 每小时）。
输出到 stdout 供 cron context_from 消费；同时写入 .lesson_context.txt 供手动分析读取。
"""
import json
import os
import sys
from datetime import datetime
from _shared import BJT, TRADE_DIR
LESSONS_PATH = os.path.join(TRADE_DIR, 'lessons.json')
REGIME_DIR = os.path.join(TRADE_DIR, 'regimes')
OUTPUT_FILE = os.path.join(TRADE_DIR, '.lesson_context.txt')


def _load_json_file(filepath):
    """加载单个 JSON 文件，返回列表；失败时记录警告并返回空列表。"""
    try:
        with open(filepath) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            else:
                print(f"[WARN] {filepath}: expected a JSON array, got {type(data).__name__}", file=sys.stderr)
                return []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        # Bug 5: 记录 JSONDecodeError 而非静默吞掉（可能是写入中的竞态条件）
        print(f"[WARN] {filepath}: JSON decode error (possible race condition during write): {e}", file=sys.stderr)
        return []


def load_lessons():
    """加载所有教训。

    Bug 1 fix: 如果 regime 目录下有 *_lessons.json 文件，则跳过 lessons.json，
    避免同一课从两个来源重复加载。
    """
    all_lessons = []

    # 检查是否有按行情类型的教训文件
    regime_files = []
    if os.path.isdir(REGIME_DIR):
        regime_files = sorted(
            f for f in os.listdir(REGIME_DIR)
            if f.endswith('_lessons.json')
        )

    if regime_files:
        # Bug 1: 有 regime 文件时跳过 lessons.json，避免双重加载
        for fname in regime_files:
            all_lessons.extend(_load_json_file(os.path.join(REGIME_DIR, fname)))
    else:
        # 没有 regime 文件时回退到 lessons.json
        all_lessons = _load_json_file(LESSONS_PATH)

    return all_lessons


def _extract_coin(lesson):
    """Bug 3 fix: 读取 'coin'（字符串）或 'coins'（数组），数组用 '/' 连接。"""
    coin = lesson.get('coin', '')
    if coin:
        return coin
    coins = lesson.get('coins', [])
    if isinstance(coins, list) and coins:
        return '/'.join(str(c) for c in coins)
    return '?'


def _extract_lesson_text(lesson):
    """Bug 2 fix: 读取 'lesson' 或 'summary' 字段（bull_correction_lessons.json 使用 'summary'）。"""
    text = lesson.get('lesson', '')
    if text:
        return text
    return lesson.get('summary', '')


def build_context(lessons, now):
    """构建上下文摘要。

    Args:
        lessons: 教训列表
        now: 当前 datetime（Bug 6: 由调用方捕获一次传入）
    """
    if not lessons:
        return ""

    # Bug 4: ID-based 去重 — 跟踪已见过的 ID；无 ID 时回退到 (category, type, lesson) 元组哈希
    # 新增：lessons合并去重 — 跨regime文件相同内容归一化
    seen_ids = set()
    seen_tuples = set()
    seen_lessons = set()  # 新增：纯教程文本去重（归一化空白符）
    recent = []
    historical = []

    for l in lessons:
        # Bug 4: 按 id 去重
        lesson_id = l.get('id')
        if lesson_id:
            if lesson_id in seen_ids:
                continue
            seen_ids.add(lesson_id)
        else:
            # 回退去重：无 id 时按 (category, type, lesson) 元组去重
            dedup_key = (str(l.get('category', '')), str(l.get('type', '')), str(_extract_lesson_text(l)))
            if dedup_key in seen_tuples:
                continue
            seen_tuples.add(dedup_key)
        # 二级去重：归一化空白符的纯文本去重（跨regime文件相同内容）    
        lesson_norm = ' '.join(str(_extract_lesson_text(l)).split())
        if lesson_norm in seen_lessons:
            continue
        seen_lessons.add(lesson_norm)

        date_str = l.get('date', '')
        try:
            lesson_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=BJT)
            days_ago = (now - lesson_date).days
            if days_ago < 0:
                days_ago = 999  # future date → treat as not recent
        except (ValueError, TypeError):
            days_ago = 999

        entry = {
            'days_ago': days_ago,
            'coin': _extract_coin(l),
            'type': l.get('type') or l.get('category') or '?',
            'lesson': _extract_lesson_text(l),
            'regime': l.get('regime', ''),
            'level': l.get('level', ''),
        }

        if days_ago <= 14:
            recent.append(entry)
        else:
            historical.append(entry)

    lines = []

    if recent:
        lines.append("## 📚 近期教训（14天内）")
        lines.append("以下教训来自复盘引擎，分析时请注意避免同类错误：\n")
        for l in sorted(recent, key=lambda x: x['days_ago']):
            regime_tag = f" [{l['regime']}]" if l['regime'] else ""
            lines.append(f"- [{l['coin']}] {l['type']}{regime_tag}: {l['lesson']}")

    if historical:
        # 只统计，不逐条列出
        type_counts = {}
        for l in historical:
            t = l['type']
            type_counts[t] = type_counts.get(t, 0) + 1
        lines.append(f"\n📊 历史教训统计（>14天）：{', '.join(f'{k}×{v}' for k, v in type_counts.items())}")

    return '\n'.join(lines)


def main():
    # Bug 6: 只捕获一次 now，避免多次调用 datetime.now(BJT) 导致微小漂移
    now = datetime.now(BJT)
    lessons = load_lessons()
    context = build_context(lessons, now)

    timestamp_str = now.strftime('%Y-%m-%d %H:%M')

    if context:
        # 输出到 stdout（供 cron 消费）
        print(context)

        # 写入文件（供手动分析读取）
        with open(OUTPUT_FILE, 'w') as f:
            f.write(f"# 教训上下文 — 生成于 {timestamp_str} BJT\n\n")
            f.write(context)
            f.write(f"\n\n总计 {len(lessons)} 条教训\n")
    else:
        # 无教训时写空文件
        with open(OUTPUT_FILE, 'w') as f:
            f.write(f"# 教训上下文 — 生成于 {timestamp_str} BJT\n")
            f.write("暂无教训记录。\n")


if __name__ == '__main__':
    main()
