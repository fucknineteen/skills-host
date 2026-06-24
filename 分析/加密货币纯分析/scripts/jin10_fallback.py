#!/usr/bin/env python3
"""
Jin10 MCP 备用方案 — 缓存 + 硬编码关键事件
当金十 MCP 不可用时自动回退。

使用方式:
  from jin10_fallback import get_calendar_events, get_key_events_summary
  
  events = get_calendar_events()  # 尝试 Jin10 → 缓存 → 硬编码
  summary = get_key_events_summary()  # 人类可读摘要
"""

import json, os, time, logging, tempfile, uuid, ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from _shared import BJT as BJ

logging.basicConfig(level=logging.WARNING, format='%(name)s: %(message)s')
logger = logging.getLogger(__name__)


def _parse_sse_response(raw):
    """解析 SSE 响应，提取 data: 行内容。"""
    if raw.startswith("event:") or raw.startswith("data:"):
        data_lines = [l[5:].lstrip() for l in raw.strip().split('\n') if l.startswith('data:')]
        return '\n'.join(data_lines)
    return raw


def _rpc(headers, base, payload, extra_headers=None, context=None):
    """Unified JSON-RPC call helper. Reduces duplication between try_jin10_calendar and try_jin10_mcp_session.
    
    Args:
        headers: dict of HTTP headers
        base: MCP endpoint URL
        payload: JSON-RPC payload dict
        extra_headers: optional dict of extra headers (e.g., Mcp-Session-Id)
        context: optional SSL context
    """
    req = urllib.request.Request(base, data=json.dumps(payload).encode(), headers=headers)
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    resp = urllib.request.urlopen(req, timeout=15, context=context) if context else urllib.request.urlopen(req, timeout=15)
    raw = resp.read().decode()
    resp.close()
    raw = _parse_sse_response(raw)
    if not raw.strip():
        return {"result": "ok"}
    return json.loads(raw)


def _make_rpc_fn(headers, base, context=None, capture_sid=False):
    """Factory for rpc() closure used in try_jin10_calendar and try_jin10_mcp_session.
    
    Args:
        capture_sid: if True, captures Mcp-Session-Id from response headers
            and attaches _session_id to the result dict.
    """
    def rpc(method, params=None, is_notification=False):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not is_notification:
            payload["id"] = str(uuid.uuid4())[:8]
        if capture_sid:
            # Special handling: need to capture response headers
            req = urllib.request.Request(base, data=json.dumps(payload).encode(), headers=headers)
            resp = urllib.request.urlopen(req, timeout=15, context=context)
            raw = resp.read().decode()
            resp.close()
            sid = resp.headers.get("Mcp-Session-Id")
            raw = _parse_sse_response(raw)
            if not raw.strip():
                result = {"result": "ok"}
            else:
                result = json.loads(raw)
            if sid and 'result' in result:
                result['_session_id'] = sid
            return result
        return _rpc(headers, base, payload, context=context)
    return rpc

CACHE_PATH = os.path.expanduser("~/.hermes/trade_review/data/jin10_calendar_cache.json")
CACHE_MAX_AGE = 2 * 3600  # 2 hours

# === 硬编码关键事件（每月更新一次即可） ===
# ⚠️ EXPIRY: 事件仅覆盖到2026-09。9月后需手动更新日期。
# 格式: (日期BJ, 标题, 星级, 重要性)
HARDCODED_EVENTS = [
    # CPI (每月中旬，周三/周四)
    ("2026-06-11", "美国5月CPI年率", 5, "通胀数据，直接决定加息预期"),
    ("2026-07-15", "美国6月CPI年率", 5, "通胀数据"),
    ("2026-08-12", "美国7月CPI年率", 5, "通胀数据"),
    ("2026-09-15", "美国8月CPI年率", 5, "通胀数据"),
    
    # FOMC (每6周)
    ("2026-06-18", "美联储利率决议 + 点阵图", 5, "利率路径指引，全年最重要"),
    ("2026-07-30", "美联储利率决议", 5, "利率决议"),
    ("2026-09-17", "美联储利率决议 + 点阵图", 5, "利率路径指引"),
    
    # 非农 (每月第一个周五)
    ("2026-06-05", "美国5月非农就业报告", 5, "就业数据，影响降息预期"),
    ("2026-07-03", "美国6月非农就业报告", 5, "就业数据"),
    ("2026-08-07", "美国7月非农就业报告", 5, "就业数据"),
    ("2026-09-04", "美国8月非农就业报告", 5, "就业数据"),
    
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
    Uses _jin10_mcp_session() to avoid duplicating token + session logic.
    """
    try:
        import json as _json, uuid
        
        session, err = _jin10_mcp_session()
        if not session:
            return None, err
        
        headers, ctx, base = session
        
        rpc = _make_rpc_fn(headers, base, context=ctx, capture_sid=True)
        
        # Get calendar
        cal = rpc("tools/call", {"name": "list_calendar", "arguments": {}})
        if 'error' in cal:
            return None, f"calendar failed: {cal['error']}"
        
        events = cal["result"]["structuredContent"]["data"]
        return events, None
        
    except Exception as e:
        logger.warning("try_jin10_calendar failed: %s", e)
        return None, str(e)[:100]


def update_cache(events):
    """将金十日历结果写入缓存文件（原子写入）。"""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    cache = {
        "updated_at": datetime.now(BJ).isoformat(),
        "updated_at_ts": time.time(),
        "source": "jin10_mcp",
        "event_count": len(events),
        "events": events
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     dir=os.path.dirname(CACHE_PATH),
                                     delete=False, encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
        tmp_path = f.name
    os.replace(tmp_path, CACHE_PATH)
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
    except Exception as e:
        logger.warning("read_cache failed: %s", e)
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
    if fresh:
        freshness = ""
    elif source == "hardcoded_fallback":
        freshness = " ⚠️(数据源不可用，使用硬编码备份)"
    else:
        freshness = " ⚠️(数据源不可用，使用过期缓存)"
    
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

# 加密+宏观关键词权重表（用于快讯关联度评分）
# weight: 3=高权重(直接相关), 1=中权重(间接相关)
CRYPTO_KEYWORD_WEIGHTS = {
    # 高权重 — 加密货币核心 + 宏观核心
    "BTC": 3, "ETH": 3, "比特币": 3, "以太坊": 3, "加密货币": 3, "区块链": 3,
    "美联储": 3, "加息": 3, "降息": 3, "通胀": 3, "非农": 3,
    "CPI": 3, "PPI": 3, "PCE": 3, "SEC": 3,
    "美元指数": 3, "DXY": 3, "中东": 3, "霍尔木兹": 3, "油价": 3, "原油": 3,
    # 中权重 — 加密生态 + 宏观外围
    "黄金": 1, "美股": 1, "纳指": 1, "标普": 1,
    "监管": 1, "合规": 1, "稳定币": 1, "DeFi": 1, "ETF": 1,
    "伊朗": 1, "地缘": 1,
    "清算": 1, "爆仓": 1, "交易所": 1, "币安": 1, "OKX": 1, "Coinbase": 1,
    "灰度": 1, "贝莱德": 1, "微策略": 1, "MicroStrategy": 1,
    "特朗普": 1, "关税": 1, "贸易战": 1,
    "日本央行": 1, "欧洲央行": 1, "英国央行": 1,
}

# 黑名单关键词（不关联加密的快讯排除）
FLASH_BLACKLIST = [
    "A股", "上证", "深证", "创业板", "科创板", "港股午评", "港股收评",
    "IPO", "新股", "打新", "上市辅导",
    "票房", "端午", "出游", "外卖",
    "芯片", "存储芯片", "半导体", "英韧科技",
    "固态硬盘", "宁德时代", "联想集团", "熊猫债",
    "BTC原油", "BTC管道", "杰伊汉", "阿塞拜疆",
]


def _jin10_mcp_session():
    """建立金十 MCP 会话，返回 (headers_dict, error_msg)。
    共享给 calendar 和 flash 调用，避免重复鉴权。"""
    try:
        import json as _json, uuid, ssl

        # Token resolution order: env var → config.yaml → fail
        token = os.environ.get('JIN10_TOKEN')
        if not token:
            config_path = os.path.expanduser("~/.hermes/config.yaml")
            try:
                import yaml
                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                auth = cfg.get("mcp_servers", {}).get("jin10", {}).get("headers", {}).get("Authorization", "")
                if auth.startswith("Bearer "):
                    token = auth[7:]
            except Exception as e:
                logger.warning("Failed to read token from config.yaml: %s", e)

        if not token or len(token) < 10:
            return None, "token not found or redacted"

        auth = f"Bearer {token}"
        base = "https://mcp.jin10.com/mcp"
        headers = {
            "Content-Type": "application/json",
            # Accept: SSE for MCP streaming protocol compatibility with jin10 backend
            "Accept": "application/json, text/event-stream",
            "Authorization": auth
        }
        ctx = ssl.create_default_context()

        rpc = _make_rpc_fn(headers, base, context=ctx, capture_sid=True)
        
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
        logger.warning("jin10_mcp_session init failed: %s", e)
        return None, str(e)[:100]


def try_jin10_flash_paginate(session, cursor=""):
    """拉取一页快讯列表。返回 (items, next_cursor, error)。"""
    headers, ctx, base = session
    try:
        import json as _json, uuid
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
        raw = _parse_sse_response(raw)
        result = _json.loads(raw)
        if 'error' in result:
            return [], "", f"flash failed: {result['error']}"
        data = result.get("result", {}).get("structuredContent", {}).get("data", {})
        items = data.get("items", [])
        next_cursor = data.get("next_cursor", "")
        return items, next_cursor, None
    except Exception as e:
        logger.warning("try_jin10_flash_paginate failed: %s", e)
        return [], "", str(e)[:100]


def try_jin10_search_flash(session, keyword):
    """搜索快讯。返回 (items, error)。"""
    headers, ctx, base = session
    try:
        import json as _json, uuid
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
        raw = _parse_sse_response(raw)
        result = _json.loads(raw)
        if 'error' in result:
            return [], f"search failed: {result['error']}"
        data = result.get("result", {}).get("structuredContent", {}).get("data", {})
        return data.get("items", []), None
    except Exception as e:
        logger.warning("try_jin10_search_flash(%s) failed: %s", keyword, e)
        return [], str(e)[:100]


def _flash_relevance_score(item):
    """计算快讯与加密市场的关联度分数。
    使用 CRYPTO_KEYWORD_WEIGHTS 作为关键词权重来源。
    """
    content = item.get("content", "")
    title = item.get("title", "")
    text = (title + " " + content).lower()

    score = 0
    for kw, weight in CRYPTO_KEYWORD_WEIGHTS.items():
        if kw.lower() in text:
            score += weight

    # 黑名单扣分 (case-insensitive match on original content)
    content_lower = content.lower()
    for kw in FLASH_BLACKLIST:
        if kw.lower() in content_lower:
            score -= 10

    return score


def fetch_flash_news():
    """拉取加密相关快讯。优先级：MCP实时 → 缓存 → 空列表。
    返回 (flash_items, source_label, is_fresh)。

    返回格式: [{content, time, url, relevance_score}, ...]
    按关联度排序，最多返回 15 条。仅保留最近 7 天快讯。"""
    # 时间截止：7天前
    cutoff = datetime.now(BJ) - timedelta(days=7)
    ts_parse_failures = 0

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
                            # ⚠️ 金十API返回的time字段格式不确定：可能是ISO带时区、ISO naive、或空格分隔格式
                            # naive时间戳默认按BJ时区解析（金十服务器为亚洲时区）
                            item_dt = datetime.fromisoformat(t)
                            if item_dt.tzinfo is None:
                                item_dt = item_dt.replace(tzinfo=BJ)
                            elif item_dt.tzinfo != BJ:
                                item_dt = item_dt.astimezone(BJ)
                            if item_dt < cutoff:
                                continue
                        except Exception as e:
                            ts_parse_failures += 1
                            logger.debug("flash paginate timestamp parse failed: %s", e)
                            continue
                        score = _flash_relevance_score(item)
                        if score > 0:
                            all_items[url] = {**item, "relevance_score": score}
                if not next_cursor:
                    break
                cursor = next_cursor
        except Exception as e:
            logger.warning("flash pagination fetch error: %s", e)

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
                            if item_dt.tzinfo is None:
                                item_dt = item_dt.replace(tzinfo=BJ)
                            elif item_dt.tzinfo != BJ:
                                item_dt = item_dt.astimezone(BJ)
                            if item_dt < cutoff:
                                continue
                        except Exception as e:
                            logger.debug("flash search timestamp parse failed: %s", e)
                        score = _flash_relevance_score(item) + 2  # 搜索命中加分
                        if score > 0:
                            all_items[url] = {**item, "relevance_score": score}
        except Exception as e:
            logger.warning("flash search fetch error: %s", e)

        if ts_parse_failures > 0:
            logger.info("flash timestamp parse failures: %d items skipped", ts_parse_failures)

        # 排序：关联度降序，取前 15（即使空列表也写入缓存，防止过期缓存被重复使用）
        sorted_items = sorted(all_items.values(), key=lambda x: x.get("relevance_score", 0), reverse=True)[:15] if all_items else []

        # 始终写入缓存（即使为空，防止过期缓存被重复使用）
        os.makedirs(os.path.dirname(FLASH_CACHE_PATH), exist_ok=True)
        cache = {
            "updated_at": datetime.now(BJ).isoformat(),
            "updated_at_ts": time.time(),
            "source": "jin10_mcp",
            "count": len(sorted_items),
            "items": sorted_items
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                         dir=os.path.dirname(FLASH_CACHE_PATH),
                                         delete=False, encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
            tmp_path = f.name
        os.replace(tmp_path, FLASH_CACHE_PATH)

        return sorted_items, "jin10_live", True

    else:
        if err:
            logger.warning("jin10 MCP session unavailable: %s", err)

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
        except Exception as e:
            logger.warning("flash cache read error: %s", e)

    # 3. 空回退
    return [], "empty", False


def get_flash_summary(max_items=8):
    """返回人类可读的快讯摘要（给分析报告/社交文案用）。"""
    items, source, fresh = fetch_flash_news()
    if not items:
        return "📰 快讯：暂无加密相关重要快讯"

    now_bj = datetime.now(BJ)
    lines = []
    freshness = "" if fresh else " ⚠️(数据源不可用，使用缓存)"

    for item in items[:max_items]:
        content = item.get("content", "")
        t = item.get("time", "")
        score = item.get("relevance_score", 0)
        # 截断过长的内容
        if len(content) > 120:
            content = content[:117] + "..."
        # 提取时间（转为北京时间，去掉秒）
        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BJ)
            elif dt.tzinfo != BJ:
                dt = dt.astimezone(BJ)
            time_str = dt.strftime("%m-%d %H:%M")
        except Exception as e:
            logger.debug("flash summary time parse failed: %s", e)
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
