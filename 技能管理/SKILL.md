---
name: skill-management
description: Safe skill installation, deletion, and inventory management. Covers bulk operations, hub vs local skills distinction, and disaster recovery.
---

# Skill Management

Safe procedures for installing, deleting, and recovering skills in Hermes.

## Critical Rule: Never Bulk-Delete Without Whitelist

**ALWAYS verify what will be deleted before executing.** A loop over `os.listdir(skills_dir)` that deletes everything not in a keep list will wipe custom/user-created skills that aren't in the official hub.

### Safe deletion pattern:

```python
# WRONG — deletes everything except listed names
for name in os.listdir(skills_dir):
    if name not in keep_list:
        shutil.rmtree(os.path.join(skills_dir, name))

# CORRECT — only delete explicitly listed names
for name in delete_list:
    path = os.path.join(skills_dir, name)
    if os.path.exists(path):
        shutil.rmtree(path)
```

### Before any bulk operation:

1. **List current skills**: `hermes skills list` — see what's actually installed
2. **Check source type**: hub-installed vs local vs bundled. Local/custom skills cannot be recovered from hub
3. **Verify backups exist**: check for `.curator_backups/`, git history, or manifest
4. **Dry-run first**: print what would be deleted/changed before executing

## Recovering Lost Skills

### If skills were deleted:

1. **Check `hermes skills list`** — some may still show as installed
2. **Check `.bundled_manifest`** — lists all official skills with hashes
3. **Try reinstalling from hub**: `hermes skills install <name> --yes`
4. **If not in hub** (custom/local skills): they are **gone** unless backed up
5. **Check `.curator_backups/`** directory for any saved copies

### Identifying skill source type:

- **Hub-installed**: shows `clawhub` or `official` as source in `hermes skills list`
- **Local**: shows `local` as source — these are user-created or manually placed
- **Bundled**: shipped with Hermes, cannot be edited

### Referencing deleted skills:
- `references/deleted-skills-inventory.md` — full list of known deleted skills with recovery status AND root cause analysis

### Custom skills (crypto-trading-analysis, social-dynamic, xuanxue-divination, trade-review-workflow, etc.):

- Created by the user, stored locally in `~/.hermes/skills/<name>/`
- **NOT in official hub** — cannot be reinstalled
- **NO git history** for skills directory (verified: `~/.hermes` has no git repo for skills)
- **NO automatic backups** unless `.curator_backups/` exists
- Recovery requires: user recreates from memory, or finds external backup

### ⚠️ Pitfall: Custom skills must be at top-level, NOT nested in subdirectories

Custom skills placed in subdirectories like `~/.hermes/skills/social-media/social-dynamic/` or `~/.hermes/skills/research/crypto-trading-analysis/` are **vulnerable to agent cleanup**. When an agent session runs a bulk-delete or directory scan, it will wipe the entire subtree including all nested skills.

**Correct placement:** `~/.hermes/skills/<name>/SKILL.md` (top-level, same depth as hub skills)

**Why this matters:** In June 2026, three custom skills were wiped by a single agent cleanup session:
- `social-dynamic` at `social-media/social-dynamic/`
- `crypto-trading-analysis` at `research/crypto-trading-analysis/` (541 uses, most heavily used)
Both were nested in subdirectories and silently deleted because `hermes skills list` couldn't see them at those depths.

**Prevention:** Always verify custom skills are at `~/.hermes/skills/<name>/` top level. If migrating from a nested location, move them before the next agent session runs cleanup operations. Run `hermes skills list` after any agent session that touched the skills directory to catch deletions early.

### Installing custom/local skills

- `hermes skills install` does NOT accept local file paths — it only accepts hub names, HTTP(S) URLs, or GitHub identifiers
- Local SKILL.md files placed in `~/.hermes/skills/<name>/` are **auto-detected by Hermes** — no install command needed
- To make a local skill persistently accessible via hub, host it on GitHub and install from the raw URL
- If GitHub API rate-limited, set `GITHUB_TOKEN` env var before installing

### Important: `hermes skills install` does NOT accept local file paths

`hermes skills install /path/to/SKILL.md` will fail. The install command only accepts:
1. Hub skill names: `hermes skills install <name> --yes`
2. HTTP(S) URLs: `hermes skills install https://... --yes`
3. GitHub identifiers: `hermes skills install owner/repo/skill-name --yes`

Local SKILL.md files placed in `~/.hermes/skills/<name>/` are **auto-detected by Hermes** — no install command needed. Just write the file and it becomes available.

## Renaming Skills

To rename a skill without breaking references:

### Safety checklist (before renaming)
1. **Check cron jobs**: `hermes cronjob list` — any cron `skills: [old-name]` entries?
2. **Check cross-references**: `grep -rn 'old-name' ~/.hermes/skills/` — any SKILL.md references to old name?
3. **Check system vs user**: Chinese description = user-created (safe to rename). English description = system/plugin (DO NOT rename — breakage risk).
4. **Check auto-sync scripts**: `auto_push_github.sh` and similar — do they copy to old-name directories?
5. **Check mirror repo**: `~/.hermes/skills-host-mirror/` — old English directories to rename + commit

### Rename steps (in order)
1. **Directory name**: `mv ~/.hermes/skills/<category>/<old-name>/ ~/.hermes/skills/<category>/<new-name>/`
2. **SKILL.md frontmatter**: update `name:` field to match new name
3. **SKILL.md description**: update any `description:` with old name references
4. **Cross-references**: search-replace old name in all SKILL.md text body and `related_skills` tables
5. **Mirror repo**: `git rm -r old-name/`, create `新名称/`, copy all files, `git add -A`, commit, push
6. **Root README.md**: update skill links + directory structure tree
7. **Auto-sync scripts**: update destination paths in `auto_push_github.sh`

### Verify
- `skills_list` — new name appears, old name gone
- `skill_view('新名称')` — loads correctly
- `grep -rn 'old-name' ~/.hermes/skills/` — zero results
- Mirror: `cd ~/.hermes/skills-host-mirror && git status` — clean

Python scripts are NOT affected — they use `import module_name`, not skill names.

## Installing Skills

### From official hub:
```bash
hermes skills install <skill-name> --yes
```

### From GitHub URL:
```bash
hermes skills install https://raw.githubusercontent.com/<user>/<repo>/main/<path>/SKILL.md --yes
```

### From skills.sh:
```bash
npx skills@latest add <owner>/<repo>
```

## Listing All Skills with Descriptions

When the user asks to list all skills (especially for reviewing which ones to auto-load), use this reliable approach:

```bash
for d in ~/.hermes/skills/*/; do
  name=$(basename "$d")
  md="$d/SKILL.md"
  if [ ! -f "$md" ]; then
    echo "📁 $name (无SKILL.md)"
    continue
  fi
  desc=$(sed -n '/^description:/p' "$md" | head -1 | sed 's/description:[[:space:]]*//' | tr -d '"'"' | cut -c1-120)
  if [ -z "$desc" ]; then
    desc=$(grep -m1 '^[-*]' "$md" | sed 's/^[*-][[:space:]]*//')
  fi
  if [ -z "$desc" ]; then
    desc=$(grep -m1 '^## ' "$md" | sed 's/^## //')
  fi
  echo "📁 $name"
  [ -n "$desc" ] && echo "   $desc"
  echo
done
```

This handles three fallback levels for description extraction: YAML frontmatter → first bullet → first ## heading. Also flags directories without SKILL.md.

**Note:** Empty directories (no SKILL.md) are common from old hub-installed skills or cleanup remnants. They can be safely ignored or cleaned up on user request.

## Verifying Installation

After any install/delete operation, **always verify**:
```bash
hermes skills list
```

Check that:
- Expected skills appear in the list
- No unexpected skills disappeared
- Source type is correct (hub vs local)
