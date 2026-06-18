---
name: trade-review-workflow
description: 加密货币分析→复盘→社交动态全工作流。覆盖：K线同步(monitor_and_sync.py)、分析(analysis_template.py)、保存动态(save_social_post.py)、数据核验(verify_social_post.py)、实盘挂单(place_live_orders.py)、复盘(review_last_post.py)。知识库在 Obsidian Vault (/root/.hermes/obsidian-vault/)。复盘只记每天第一条分析。
---

# Trade Review Workflow

全工作流由 5 个核心脚本 + 1 个共享模块 + 多个数据文件 组成。

## 目录结构

```
/root/.hermes/trade_review/
├── _shared.py                  # 共享：BJT时区、TRADE_DIR、DB_PATH
├── monitor_and_sync.py         # 数据采集：拉K线→存DB
├── analysis_template.py        # 分析：读DB→算指标→输出结论
├── save_social_post.py         # 保存动态：写入social_posts.json
├── verify_social_post.py       # 数据核验：比对文案vs分析数字
├── place_live_orders.py        # 实盘挂单：从social_posts.json下单
├── review_last_post.py         # 复盘：分析价格路径、SL触发
├── okx_klines.db               # SQLite：7周期×4币种K线
├── social_posts.json           # 社交动态记录
├── analyses.json               # 分析记录缓存
├── social_reviews.json         # 复盘记录
└── okx_live_config.json        # OKX API密钥
```

## 数据文件

### social_posts.json
- 数组，每项包含：`id`(递增)、`time`(BJ)、`btc_price`、`eth_price`、`btc_direction`、`eth_direction`、`fg`、`regime`、`text`
- 追加写入，有备份机制（.bak → .bak5）

### okx_klines.db
- SQLite，表 `klines`：`coin`(TEXT)、`timeframe`(TEXT)、`ts`(INTEGER ms)、`open/high/low/close`(REAL)、`volume`(REAL)、`vol_ccy`(REAL)、`updated_at`(TEXT)
- 表 `sync_log`：`coin`、`timeframe`、`last_ts`、`count`、`synced_at`
- 覆盖：BTC/ETH/SOL/DOGE（OKX）+ LAB（Binance）
- 周期：5m/15m/30m/1H/4H/1D/1W

### analyses.json
- 数组，每项包含：`timestamp`、`coin`、`coin_type`、`entry_price`、`trend_*`（1H/4H/3D）、`support`、`resistance`、`recommendation`、`risk`、`macro`、`scores`

### social_reviews.json
- 数组，每项包含：`post_id`、`post`（完整动态对象）、`btc_verdict`、`btc_change_pct`、`btc_high`、`btc_low`、`eth_verdict`、`eth_change_pct`、`eth_high`、`eth_low`、`reviewed_at`、`time`

## 工作流程

### 1. 数据采集 — monitor_and_sync.py
```bash
python3 monitor_and_sync.py [BTC ETH LAB HOME SOL ONDO ZEC ALLO]
```
- 从 OKX（BTC/ETH/SOL/DOGE）和 Binance（LAB）拉取K线
- 存入 okx_klines.db
- 增量同步：从 sync_log.last_ts 开始补缺失数据
- 含数据验证：缺口检测→对比OKX→按源修复
- 专为 cron 设计：不依赖 LLM，直接输出文本报告
- 定时执行

### 2. 分析 — analysis_template.py
```bash
python3 analysis_template.py 比特币          # 中文别名
python3 analysis_template.py btc            # 英文代码
python3 analysis_template.py BTC ETH SOL    # 多币种
python3 analysis_template.py --all          # 全量
```
- 从 DB 读取 K线，计算指标（RSI/MACD/ADX/%b/VP等）
- 结合宏观（FG/DXY/VIX/FOMC/CPI/非农）
- 输出分析结论到 analyses.json
- 支持币安别名：比特币/大饼→BTC，以太坊/以太→ETH，索拉纳/sol→SOL，狗币/狗狗币→DOGE
- 含 COIN_LESSONS 约束（如 BTC RSI<20+FG<15 强制降级）

### 3. 发动态 — save_social_post.py → verify_social_post.py
#### save_social_post.py
```bash
python3 scripts/save_social_post.py \
  --btc 65456 --eth 1716 --fg 20 \
  --direction-btc "做多 回踩65000" \
  --direction-eth "做多 1725" \
  --text "BTC连涨5天..." \
  --regime "牛市回调"
```
- 将分析结论+方向+入场区间+SL+TP 写入 social_posts.json
- ID 自动递增
- 追加写入，有备份

#### verify_social_post.py
```bash
python3 verify_social_post.py "动态文案"
echo "post text" | python3 verify_social_post.py -
```
- 从 analyses.json 缓存读取最新分析，不重跑分析
- 核验文案中每个数字是否与分析一致：
  - 价格 ±3%
  - 百分比 ±3%
  - 比率 ±0.5
  - 小时 ±2h
- 跳过 HH:MM 时间格式假阳性
- 不通过则退出并报告差异

### 4. 挂单 — place_live_orders.py
```bash
python3 place_live_orders.py           # 从最新post下单
python3 place_live_orders.py --view    # 查看挂单
python3 place_live_orders.py --cancel  # 撤销所有
```
- 读取 social_posts.json 最新一条的 btc_direction/eth_direction
- 用 `_parse_sl_from_direction` 解析 SL
- 用 `calc_order_price` 计算挂单价（区间中点-10%偏移，向下取整到5）
- 查 OKX 余额，按 2% 风险计算合约数
- 检查已有挂单避免重复
- 调用 OKX API 下单
- 支持 `--view` 和 `--cancel` 模式

### 5. 复盘 — review_last_post.py
```bash
python3 scripts/review_last_post.py [--save]
```
- 读取 social_posts.json 最新一条
- 从 DB 查入场后 1H K线（最多48根）
- 用实际挂单价（非帖子时价）作为 entry 基准
- 分析价格路径（先涨后跌/先跌后涨/窄幅震荡/单边）
- 用解析出的实际 SL 判断是否触发
- `--save` 写入 social_reviews.json

### 6. 共享 — _shared.py
- 定义 `BJT = timezone(UTC+8)` 统一时区
- 定义 `TRADE_DIR = '/root/.hermes/trade_review'`
- 定义 `DB_PATH = f'{TRADE_DIR}/okx_klines.db'`

## 使用场景

### 完整流程（每日一次）
1. 运行 `monitor_and_sync.py` 同步K线
2. 运行 `analysis_template.py --all` 生成分析
3. 人工阅读分析，撰写社交动态文案
4. 运行 `save_social_post.py` 保存动态
5. 运行 `verify_social_post.py` 核验数字
6. 通过核验后，运行 `place_live_orders.py` 挂单
7. 次日运行 `review_last_post.py --save` 复盘

### 复盘
```bash
python3 scripts/review_last_post.py --save
```

### 查看挂单
```bash
python3 place_live_orders.py --view
```

### 撤销挂单
```bash
python3 place_live_orders.py --cancel
```

## 注意事项
- 所有时间用 BJ(UTC+8)，不混用 UTC
- 复盘 entry 用 calc_order_price 计算的挂单价，不是帖子时价
- 复盘方向正确性必须观察价格路径，不能只看 ±2% 净值
- 发动态前必须经过 verify_social_post.py 核验
- OKX API 密钥在 okx_live_config.json，勿泄露
- 复盘只记每天第一条分析
