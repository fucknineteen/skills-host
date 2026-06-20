---
name: 社交管道迁移
description: 社交动态发布管道从 analysis_template.py --social 迁移到 publish_social.py 的迁移记录
---

# Pipeline Migration: analysis_template.py -s → publish_social.py

## Old Pipeline (DEPRECATED)
`python3 analysis_template.py BTC ETH -s` — 5 steps, uses local generate_social_draft copy

## New Pipeline (ACTIVE)
`python3 publish_social.py BTC ETH` — 7 steps, uses _social_publish.generate_social_draft

## Key Differences
- Steps: 5 vs 7 (new adds sync + user confirmation)
- Draft: AT uses near_support/near_resistance; SP uses levels_4h
- Retry: AT max_retries=3; SP max_retries=5 with more error types
- Chart: AT fixed Style 1; SP dynamic 1-4

## Safety
- Deleting analysis_template.py L1936-2003 (--social block) is safe
- analysis_template.py itself still needed for analysis (without -s)

See references/pipeline-migration-detail.md for full migration details.