# Social Posting Workflow

社交动态发布7步流水线。从 K线同步到 Telegram 动态发布的完整标准化流程。

## 文件说明

| 文件 | 用途 |
|------|------|
| `SKILL.md` | 工作流完整说明：前置条件 → 7步流程 → 常见问题 → 发布清单 |
| `scripts/publish_social.py` | 7步流水线主入口。自动串行执行同步→分析→复盘→文案→核验→配图→保存 |
| `scripts/verify_social_post.py` | 文案数据核验（v4 架构）。从 analyses.json 读取真值，正则定位文案位置，精确比对 |
| `scripts/_social_publish.py` | 文案生成引擎。从分析数据生成符合社交动态模板的文案草稿 |
| `scripts/review_last_post.py` | 复盘上条动态。读 social_posts.json 最新一条，计算价格路径，判定方向+SL触发 |
| `scripts/save_social_post.py` | 保存动态记录到 social_posts.json（ID自增，去重检测，5级滚动备份） |
| `scripts/gen_charts.py` | 配图生成。4种风格（营销K线/仪表盘/结构标注/方形卡片），1小时缓存 |
| `templates/社交动态模板v5.1.md` | 社交动态文案输出模板（💡📐📊🌍 四段结构 + 金句收尾） |

## 7步发布流程

```
Step ①           Step ②          Step ③            Step ④
同步K线     →    分析行情     →   复盘上条      →   按模板写文案
monitor          analysis         review_last        社交动态模板
_and_sync        _template        _post --save       v5.1

Step ⑤           Step ⑥          Step ⑦            Step ⑧
核验文案     →   用户审核     →   生成配图      →   保存记录
verify_social     (手动)          gen_charts         save_social
_post                                                   _post
```

## 关键约束

- 核验不通过（exit 1）→ 保存被跳过，修正后必须重走完整流程
- 文案草稿自动缓存到 `/tmp/social_draft.txt`（丢失恢复来源）
- 与 `crypto-analysis-workflow` 是不同的流程，模板不可混用
- FOMC/CPI 前 48h 不发布方向性结论

## 环境依赖

- Python 3.11+
- OKX API 访问权限
- `analyses.json` 数据源（完整对象格式）
- Pillow（配图生成）
- Telegram 连接
