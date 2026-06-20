# 工作流审计清单

> 适用场景：技能文档和脚本经过多轮修改后，逐项核验一致性。

## 检查项

### 1. 数字/计数一致性
- SKILL.md 说"N个字段/步骤/函数" → 打开代码数一遍
- 常见陷阱：重构后多了一个 import，文档没更新计数

### 2. 死代码引用
- SKILL.md 提到的函数/行号 → `grep` 确认代码中存在
- 常见陷阱：函数被删除后文档仍说"L1544 有 generate_social_draft"

### 3. 过时状态描述
- SKILL.md 说"待修复/未集成/不行" → 检查代码是否已修
- 常见陷阱：功能已上线但文档 §已知坑 仍说未集成

### 4. 字段映射准确性
- SKILL.md 字段映射表 → JSON 实际字段名匹配
- 常见陷阱：wrapper 返回了字段但记录字典漏写（如 publish_social.py 缺 flash_news）

### 5. Section 标记清理
- 删除代码后连带删 section 注释（如 `# === 社交动态文案生成 ===`）
- 常见陷阱：函数删了但注释还在，误导后续维护

### 6. 引用文件存在性
- SKILL.md 中的 `references/xxx.md` → 确认文件存在
- `ls` 检查 references/ 目录

### 7. 两个 SKILL.md 交叉一致
- crypto 说"五个社交字段" → social 也说"五个"
- 两边计数、名称、引用相互一致

## 快速执行

```bash
# 1. 检查英文旧名是否残留
grep -rn 'crypto-analysis-workflow\|social-posting-workflow' ~/.hermes/skills/

# 2. 检查 reference 文件是否真实存在
for ref in $(grep -ohP 'references/[\w.-]+\.md' SKILL.md); do
  [ -f "$ref" ] || echo "MISSING: $ref"
done

# 3. 检查 5/6 等数字是否一致
grep -n '五个\|六个\|5个\|6个' SKILL.md _social_publish.py
```

## 本次审计发现（2026-06-20）

| 问题 | 位置 | 修复 |
|------|------|------|
| "五个社交字段" → 实际是六个 | crypto §2.3 + social §9 | 改为"六个" |
| "L1544 死代码 generate_social_draft" | crypto §6 | 删除过时说明 |
| "快讯/新闻未集成" | social §10.5 | 删除，改为已集成 |
| publish_social.py 漏写 flash_news | 记录字典 L308 | 添加字段 |
