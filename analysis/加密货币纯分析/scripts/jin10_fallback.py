#!/usr/bin/env python3
"""
Jin10 MCP 备用方案 — 缓存 + 硬编码关键事件
当金十 MCP 不可用时自动回退。

使用方式:
  from jin10_fallback import get_calendar_events, get_key_events_summary
  
  events = get_calendar_events()  # 尝试 Jin10 → 缓存 → 硬编码
  summary = get_key_events_summary()  # 人类可读摘要
"""

import json, os, time
from datetime import datetime, timedelta, timezone
from _shared import BJT as BJ

CACHE_PATH = os.path.expanduser("~/.hermes/trade_review/data/jin10_calendar_cache.json")
CACHE_MAX_AGE = 6 * 3600  # 6 hours

# === 硬编码关键事件（每月更新一次即可） ===
# 格式: (日期BJ, 标题, 星级, 重要性)
HARDCODED_EVENTS = [
    # CPI (每月中旬，周三/周四)
    ("2026-06-11", "美国5月CPI年率", 5, "通胀数据，直接决定加息预期"),
    ("2026-07-15", "美国6月CPI年率", 5, "通胀数据"),
    ("2026-08-12", "美国7月CPI年率", 5, "通胀数据"),
    
    # FOMC (每6周)
    ("2026-06-18", "美联储利率决议 + 点阵图", 5, "利率路径指引，全年最重要"),
    ("2026-07-30", "美联储利率决议", 5, "利率决议"),
    ("2026-09-17", "美联储利率决议 + 点阵图", 5, "利率路径指引"),
    
    # 非农 (每月第一个周五)
    ("2026-06-05", "美国5月非农就业报告", 5, "就业数据，影响降息预期"),
    ("2026-07-03", "美国6月非农就业报告", 5, "就业数据"),
    ("2026-08-07", "美国7月非农就业报告", 5, "就业数据"),
    
    # PPI
    ("2026-06-12", "美国5月PPI年率", 4, "生产者通胀，CPI先行指标"),
    ("2026-07-14", "美国6月PPI年率", 4, "生产者通胀"),
    ("2026-08-13", "美国7月PPI年率", 4, "生产者通胀"),
    
    # GDP
    ("2026-06-25", "美国Q1 GDP终值", 4, "经济增长"),
    ("2026-07-30", "美国Q2 GDP初值", 4, "经济增长"),
    
    # PMI
    ("2026-06-23", "美国6月Markit制造业PMI初值", 3, "经济先行指标"),
    ("2026-07-24", "美国7月Markit制造业PMI初值", 3, "经济先行指标"),
    
    # 零售销售
    ("2026-06-16", "美国5月零售销售月率", 4, "消费数据"),
    ("2026-07-16", "美国6月零售销售月率", 4, "消费数据"),
]


def try_jin10_calendar():
    """尝试通过 Python 客户端直连金十 MCP 获取日历。
    返回 (events_list, error) — events_list 为 None 表示失败。
    """
    try:
        import urllib.request, json as _json, uuid, ssl
        
        # Read token from config
        config_path = os.path.expanduser("~/.hermes/config.yaml")
        token = None
        with open(config_path) as f:
            for line in f:
                if 'Authorization' in line and 'Bearer' in line:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        token = parts[-1]
                    break
        
        if not token or len(token) < 10:
            return None, "token not found or redacted"
        
        auth = f"Bearer {token}"
        base = "https://mcp.jin10.com/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": auth
        }
        ctx = ssl.create_default_context()
        
        def rpc(method, params=None, is_notification=False):
            payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if not is_notification:
                payload["id"] = str(uuid.uuid4())[:8]
            req = urllib.request.Request(base, data=_json.dumps(payload).encode(), headers=headers)
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            sid = resp.headers.get("Mcp-Session-Id")
            raw = resp.read().decode()
            resp.close()
            if raw.startswith("event:") or raw.startswith("data:"):
                data_lines = [l[6:] for l in raw.strip().split('\n') if l.startswith('data:')]
                raw = ''.join(data_lines)
            if not raw.strip():
                return {"result": "ok"}
            result = _json.loads(raw)
            if sid and 'result' in result:
                result['_session_id'] = sid
            return result
        
        # Initialize
        init = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, 
                                   "clientInfo": {"name": "hermes-fallback", "version": "1.0"}})
        if 'error' in init:
            return None, f"init failed: {init['error']}"
        
        sid = init.get('_session_id', '')
        headers["Mcp-Session-Id"] = sid
        
        # Send initialized notification
        rpc("notifications/initialized", {}, is_notification=True)
        
        # Get calendar
        cal = rpc("tools/call", {"name": "list_calendar", "arguments": {}})
        if 'error' in cal:
            return None, f"calendar failed: {cal['error']}"
        
        events = cal["result"]["structuredContent"]["data"]
        return events, None
        
    except Exception as e:
        return None, str(e)[:100]


def update_cache(events):
    """将金十日历结果写入缓存文件。"""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    cache = {
        "updated_at": datetime.now(BJ).isoformat(),
        "updated_at_ts": time.time(),
        "source": "jin10_mcp",
        "event_count": len(events),
        "events": events
    }
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    return True


def read_cache():
    """读取缓存的日历数据。返回 (events, is_stale, age_hours)。"""
    if not os.path.exists(CACHE_PATH):
        return None, True, 999
    
    try:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        
        age = time.time() - cache.get("updated_at_ts", 0)
        age_hours = age / 3600
        is_stale = age > CACHE_MAX_AGE
        
        return cache.get("events", []), is_stale, round(age_hours, 1)
    except Exception:
        return None, True, 999


def get_hardcoded_events():
    """返回硬编码的关键事件列表（标准化为金十格式）。"""
    now = datetime.now(BJ)
    events = []
    for date_str, title, stars, note in HARDCODED_EVENTS:
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJ)
        except ValueError:
            continue
        
        # 只返回本月+下月的事件
        if event_date < now - timedelta(days=7):
            continue  # 跳过已过期的
        if event_date > now + timedelta(days=60):
            continue  # 太远的跳过
        
        events.append({
            "pub_time": date_str,
            "title": title,
            "star": stars,
            "note": note,
            "source": "hardcoded_fallback"
        })
    
    return events


def get_calendar_events(min_stars=0):
    """
    获取财经日历事件。优先级：金十实时 → 缓存 → 硬编码。
    返回 (events, source_label, is_fresh)
    """
    # 1. Try Jin10 MCP
    events, error = try_jin10_calendar()
    if events is not None:
        update_cache(events)  # 更新缓存
        filtered = [e for e in events if e.get("star", 0) >= min_stars]
        return filtered, "jin10_live", True
    
    # 2. Try cache
    cached, is_stale, age_h = read_cache()
    if cached is not None:
        filtered = [e for e in cached if e.get("star", 0) >= min_stars]
        label = f"cache({age_h}h old)" if is_stale else f"cache({age_h}h)"
        return filtered, label, not is_stale
    
    # 3. Hardcoded fallback
    hardcoded = get_hardcoded_events()
    filtered = [e for e in hardcoded if e.get("star", 0) >= min_stars]
    return filtered, "hardcoded_fallback", False


def get_key_events_summary():
    """返回人类可读的关键事件摘要（给分析报告用）。"""
    events, source, fresh = get_calendar_events(min_stars=4)
    
    if not events:
        return "本周无重大数据事件"
    
    lines = []
    freshness = "" if fresh else " ⚠️(数据源不可用，使用缓存)"
    
    for e in events[:6]:
        stars = "⭐" * e.get("star", 0)
        title = e.get("title", "未知")
        pub_time = e.get("pub_time", "")[:10]
        lines.append(f"  {stars} {pub_time} {title}")
    
    header = f"本周关键事件 ({source}{freshness}):"
    return header + "\n" + "\n".join(lines)


def check_jin10_health():
    """快速健康检查 — 返回 (healthy: bool, detail: str)。"""
    events, error = try_jin10_calendar()
    if events is not None:
        return True, f"connected ({len(events)} events)"
    
    cached, stale, age = read_cache()
    if cached is not None:
        return False, f"down, using cache ({age}h old, {len(cached)} events)"
    
    hardcoded = get_hardcoded_events()
    if hardcoded:
        return False, f"down, using hardcoded ({len(hardcoded)} events)"
    
    return False, f"down, no backup available: {error}"


# ============================================================
# Flash News (快讯) — 2026-06-20 新增
# ============================================================

FLASH_CACHE_PATH = os.path.expanduser("~/.hermes/trade_review/data/jin10_flash_cache.json")
FLASH_CACHE_TTL = 300  # 5 minutes

# 加密相关关键词（用于搜索+过滤）
CRYPTO_KEYWORDS = [
    "BTC", "ETH", "比特币", "以太坊", "加密货币", "区块链",
    "美联储", "加息", "降息", "通胀", "非农", "CPI", "PPI", "PCE",
    "SEC", "监管", "合规", "稳定币", "DeFi", "ETF",
    "美元指数", "DXY", "黄金", "美股", "纳指", "标普",
    "油价", "原油", "中东", "霍尔木兹", "伊朗",
    "清算", "爆仓", "交易所", "币安", "OKX", "Coinbase",
    "灰度", "贝莱德", "微策略", "MicroStrategy",
    "特朗普", "关税", "贸易战", "地缘",
    "日本央行", "欧洲央行", "英国央行",
]

# 黑名单关键词（不关联加密的快讯排除）
FLASH_BLACKLIST = [
    "A股", "上证", "深证", "创业板", "科创板", "港股午评", "港股收评",
    "IPO", "新股", "打新", "上市辅导",
    "票房", "端午", "出游", "外卖",
    "芯片", "存储芯片", "半导体" "英韧科技",
    "固态硬盘", "宁德时代", "联想集团", "熊猫债",
    "BTC原油", "BTC管道", "杰伊汉", "阿塞拜疆",
]


def _jin10_mcp_session():
    """建立金十 MCP 会话，返回 (headers_dict, error_msg)。
    共享给 calendar 和 flash 调用，避免重复鉴权。"""
    try:
        import urllib.request, json as _json, uuid, ssl

        config_path = os.path.expanduser("~/.hermes/config.yaml")
        token = None
        with open(config_path) as f:
            for line in f:
                if 'Authorization' in line and 'Bearer' in line:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        token = parts[-1]
                    break

        if not token or len(token) < 10:
            return None, "token not found or redacted"

        auth = f"Bearer {token}"
        base = "https://mcp.jin10.com/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": auth
        }
        ctx = ssl.create_default_context()

        def rpc(method, params=None, is_notification=False):
            payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if not is_notification:
                payload["id"] = str(uuid.uuid4())[:8]
            req = urllib.request.Request(base, data=_json.dumps(payload).encode(), headers=headers)
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            sid = resp.headers.get("Mcp-Session-Id")
            raw = resp.read().decode()
            resp.close()
            if raw.startswith("event:") or raw.startswith("data:"):
                data_lines = [l[6:] for l in raw.strip().split('\n') if l.startswith('data:')]
                raw = ''.join(data_lines)
            if not raw.strip():
                return {"result": "ok"}
            result = _json.loads(raw)
            if sid and 'result' in result:
                result['_session_id'] = sid
            return result

        # Initialize
        init = rpc("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "hermes-flash", "version": "1.0"}
        })
        if 'error' in init:
            return None, f"init failed: {init['error']}"

        sid = init.get('_session_id', '')
        headers["Mcp-Session-Id"] = sid
        rpc("notifications/initialized", {}, is_notification=True)

        return (headers, ctx, base), None
    except Exception as e:
        return None, str(e)[:100]


def try_jin10_flash_paginate(session, cursor=""):
    """拉取一页快讯列表。返回 (items, next_cursor, error)。"""
    headers, ctx, base = session
    try:
        import urllib.request, json as _json, uuid
        params = {}
        if cursor:
            params["cursor"] = cursor

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4())[:8],
            "method": "tools/call",
            "params": {"name": "list_flash", "arguments": params}
        }
        req = urllib.request.Request(base, data=_json.dumps(payload).encode(), headers=headers)
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        raw = resp.read().decode()
        resp.close()
        if raw.startswith("event:") or raw.startswith("data:"):
            data_lines = [l[6:] for l in raw.strip().split('\n') if l.startswith('data:')]
            raw = ''.join(data_lines)
        result = _json.loads(raw)
        if 'error' in result:
            return [], "", f"flash failed: {result['error']}"
        data = result.get("result", {}).get("structuredContent", {}).get("data", {})
        items = data.get("items", [])
        next_cursor = data.get("next_cursor", "")
        return items, next_cursor, None
    except Exception as e:
        return [], "", str(e)[:100]


def try_jin10_search_flash(session, keyword):
    """搜索快讯。返回 (items, error)。"""
    headers, ctx, base = session
    try:
        import urllib.request, json as _json, uuid
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4())[:8],
            "method": "tools/call",
            "params": {"name": "search_flash", "arguments": {"keyword": keyword}}
        }
        req = urllib.request.Request(base, data=_json.dumps(payload).encode(), headers=headers)
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        raw = resp.read().decode()
        resp.close()
        if raw.startswith("event:") or raw.startswith("data:"):
            data_lines = [l[6:] for l in raw.strip().split('\n') if l.startswith('data:')]
            raw = ''.join(data_lines)
        result = _json.loads(raw)
        if 'error' in result:
            return [], f"search failed: {result['error']}"
        data = result.get("result", {}).get("structuredContent", {}).get("data", {})
        return data.get("items", []), None
    except Exception as e:
        return [], str(e)[:100]


def _flash_relevance_score(item):
    """计算快讯与加密市场的关联度分数。"""
    content = item.get("content", "")
    title = item.get("title", "")
    text = (title + " " + content).lower()

    score = 0
    # 高权重关键词
    high_weight = ["美联储", "加息", "降息", "通胀", "cpi", "ppi", "pce",
                   "非农", "btc", "eth", "比特币", "以太坊", "加密货币",
                   "sec", "dxy", "美元指数", "中东", "霍尔木兹", "油价", "原油"]
    for kw in high_weight:
        if kw.lower() in text:
            score += 3

    # 中权重关键词
    mid_weight = ["黄金", "美股", "纳指", "标普", "地缘", "特朗普",
                  "监管", "关税", "贸易战", "日本央行", "欧洲央行",
                  "etf", "贝莱德", "微策略", "清算", "爆仓",
                  "交易所", "币安", "coinbase", "灰度"]
    for kw in mid_weight:
        if kw.lower() in text:
            score += 1

    # 黑名单扣分
    for kw in FLASH_BLACKLIST:
        if kw in content:
            score -= 10

    return score


def fetch_flash_news():
    """拉取加密相关快讯。优先级：MCP实时 → 缓存 → 空列表。
    返回 (flash_items, source_label, is_fresh)。

    返回格式: [{content, time, url, relevance_score}, ...]
    按关联度排序，最多返回 15 条。仅保留最近 7 天快讯。"""
    # 时间截止：7天前
    cutoff = datetime.now(BJ) - timedelta(days=7)

    # 1. 尝试 MCP 实时
    session, err = _jin10_mcp_session()
    if session:
        all_items = {}
        
        # 拉取最新 2 页 (独立try块)
        try:
            cursor = ""
            for _ in range(2):
                items, next_cursor, ferr = try_jin10_flash_paginate(session, cursor)
                if ferr:
                    break
                for item in items:
                    url = item.get("url", "")
                    t = item.get("time", "")
                    if url and url not in all_items:
                        # 时间过滤
                        try:
                            item_dt = datetime.fromisoformat(t)
                            if item_dt < cutoff:
                                continue
                        except Exception:
                            pass
                        score = _flash_relevance_score(item)
                        if score > 0:
                            all_items[url] = {**item, "relevance_score": score}
                if not next_cursor:
                    break
                cursor = next_cursor
        except Exception:
            pass

        # 搜索高优先级关键词 (独立try块，不影响分页结果)
        try:
            for kw in ["美联储", "比特币", "以太坊", "加密货币", "BTC"]:
                s_items, serr = try_jin10_search_flash(session, kw)
                if serr:
                    continue
                for item in s_items[:5]:
                    url = item.get("url", "")
                    t = item.get("time", "")
                    if url and url not in all_items:
                        try:
                            item_dt = datetime.fromisoformat(t)
                            if item_dt < cutoff:
                                continue
                        except Exception:
                            pass
                        score = _flash_relevance_score(item) + 2  # 搜索命中加分
                        if score > 0:
                            all_items[url] = {**item, "relevance_score": score}
        except Exception:
            pass

        # 有结果才继续
        if all_items:
            # 排序：关联度降序，取前 15
            sorted_items = sorted(all_items.values(), key=lambda x: x.get("relevance_score", 0), reverse=True)[:15]

            # 写入缓存
            os.makedirs(os.path.dirname(FLASH_CACHE_PATH), exist_ok=True)
            cache = {
                "updated_at": datetime.now(BJ).isoformat(),
                "updated_at_ts": time.time(),
                "source": "jin10_mcp",
                "count": len(sorted_items),
                "items": sorted_items
            }
            with open(FLASH_CACHE_PATH, 'w') as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)

            return sorted_items, "jin10_live", True

    # 2. 读缓存
    if os.path.exists(FLASH_CACHE_PATH):
        try:
            with open(FLASH_CACHE_PATH) as f:
                cache = json.load(f)
            age = time.time() - cache.get("updated_at_ts", 0)
            age_min = round(age / 60, 1)
            items = cache.get("items", [])
            is_fresh = age < FLASH_CACHE_TTL
            label = f"cache({age_min}m old)"
            return items, label, is_fresh
        except Exception:
            pass

    # 3. 空回退
    return [], "empty", False


def get_flash_summary(max_items=8):
    """返回人类可读的快讯摘要（给分析报告/社交文案用）。"""
    items, source, fresh = fetch_flash_news()
    if not items:
        return "📰 快讯：暂无加密相关重要快讯"

    now_bj = datetime.now(BJ)
    lines = []
    freshness = "" if fresh else f" ⚠️({source})"

    for item in items[:max_items]:
        content = item.get("content", "")
        t = item.get("time", "")
        score = item.get("relevance_score", 0)
        # 截断过长的内容
        if len(content) > 120:
            content = content[:117] + "..."
        # 提取时间（去掉秒和时区）
        try:
            dt = datetime.fromisoformat(t)
            time_str = dt.strftime("%m-%d %H:%M")
        except Exception:
            time_str = t[:16] if len(t) >= 16 else t
        lines.append(f"  [{time_str}] {content}")

    header = f"📰 加密相关快讯 ({len(items)}条{source}{freshness}):"
    return header + "\n" + "\n".join(lines)


# === CLI ===
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--health":
        ok, detail = check_jin10_health()
        print(f"Jin10: {'✅' if ok else '❌'} {detail}")
    
    elif len(sys.argv) > 1 and sys.argv[1] == "--sync":
        events, error = try_jin10_calendar()
        if events:
            update_cache(events)
            print(f"✅ Cached {len(events)} events from Jin10")
        else:
            print(f"❌ Sync failed: {error}")
    
    elif len(sys.argv) > 1 and sys.argv[1] == "--summary":
        print(get_key_events_summary())

    elif len(sys.argv) > 1 and sys.argv[1] == "--flash":
        items, source, fresh = fetch_flash_news()
        print(f"Flash Source: {source} ({'fresh' if fresh else 'stale'})")
        print(f"Items: {len(items)}")
        for item in items:
            score = item.get("relevance_score", 0)
            content = item.get("content", "")[:100]
            t = item.get("time", "")[:16]
            print(f"  [s={score}] {t} | {content}")

    elif len(sys.argv) > 1 and sys.argv[1] == "--flash-summary":
        print(get_flash_summary())

    else:
        events, source, fresh = get_calendar_events()
        print(f"Source: {source} ({'fresh' if fresh else 'stale'})")
        print(f"Events: {len(events)}")
        for e in events[:10]:
            star = e.get("star", 0)
            title = e.get("title", "")
            pub_time = e.get("pub_time", "")[:10]
            print(f"  ⭐{star} {pub_time} | {title}")
