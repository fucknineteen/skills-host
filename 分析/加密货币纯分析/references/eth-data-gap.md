# ETH K线数据缺失问题

## 现象
2026-06-20 发现 ETH 在 `okx_klines.db` 中仅有 5m/15m/30m 周期数据，**缺少 1h/4h/1d**。

```
5m:  06-06 11:10 ~ 06-20 22:25  ✅
15m: 05-17 23:30 ~ 06-20 22:15  ✅
30m: 05-04 20:00 ~ 06-20 22:00  ✅
1h:  无数据                      ❌
4h:  无数据                      ❌
1d:  无数据                      ❌
```

BTC/SOL/DOGE 各周期完整。

## 可能原因
1. `monitor_and_sync.py` 同步逻辑对 ETH 的 inst_id 匹配有问题
2. OKX API 对 ETH 的某些周期返回空或格式不同
3. 数据库 schema 中 coin 字段存储为 'ETH' 但同步脚本查询时使用不同格式

## 排查方法
```bash
cd /root/.hermes/trade_review
python3 -c "
import sqlite3
conn = sqlite3.connect('okx_klines.db')
c = conn.cursor()
c.execute('SELECT DISTINCT coin, timeframe FROM klines WHERE coin LIKE \"%ETH%\" ORDER BY timeframe')
for row in c.fetchall():
    print(row)
conn.close()
"
```

## 影响
- 无法获取 4H MACD、1D RSI 等关键指标
- 道氏方向分析（需1D/4H/1H）不完整
- 共振评分缺少大周期数据

## 临时方案
- 纯 5m/15m/30m 分析可勉强进行，但结论可靠性降低
- 如需完整分析，手动运行 `monitor_and_sync.py ETH` 并检查日志

## 状态
✅ 已修复 — 2026-06-21 确认数据已由 hourly cron `monitor_and_sync.py BTC ETH` 自动补齐。ETH 全周期（5m/15m/30m/1H/4H/1D）与 BTC 完全对齐，零缺口。

**当前数据（2026-06-21）：**
```
5m:  4283 根 | 最新 06-21 08:00  ✅
15m: 3299 根 | 最新 06-21 08:00  ✅
30m: 2281 根 | 最新 06-21 08:00  ✅
1H:  1905 根 | 最新 06-21 08:00  ✅
4H:  1557 根 | 最新 06-21 08:00  ✅
1D:  1465 根 | 最新 06-21 00:00  ✅
```

BTC/ETH/SOL/DOGE 四币种全周期完整，零异常蜡烛，30m 以上时效 ≤46 分钟。

## 根因
初始缺口的原因无法复现 —— `monitor_and_sync.py` 的 `update()` 函数在 DB 无数据时走全量拉取（L125-129），OKX API `instId=ETH-USDT-SWAP` 正常返回。疑似 2026-06-20 之前的某次同步中 API 临时异常导致 ETH 大周期拉取失败，后续 hourly cron 补齐。
