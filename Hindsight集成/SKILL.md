---
name: hindsight-integration
description: Integrate Hindsight agent memory system with Hermes Agent via MCP protocol. Covers installation, permissions, startup, troubleshooting.
---

# Hindsight + Hermes Integration

Integrate Hindsight (agent memory system) with Hermes Agent via MCP protocol.

## Prerequisites

- Hindsight API installed (`hindsight-api` package)
- `pg0-embedded` package (for embedded PostgreSQL)
- `sentence-transformers` + `torch` (for local embeddings)
- LLM API key (OpenAI-compatible endpoint)
- Non-root user for Hindsight (pg0-embedded requires non-root)

## Steps

### 1. Create Hindsight User

```bash
useradd -m hindsight
```

### 2. Fix Permissions

Hindsight needs to access the venv Python binary. Two issues:

- **venv ownership**: chown venv dir to hindsight
  ```bash
  chown -R hindsight:hindsight /usr/local/lib/hermes-agent/venv
  ```
- **/root directory**: pg0-embedded symlinks python to `/root/.local/...`. Make /root traversable:
  ```bash
  chmod o+x /root
  ```

### 3. Create Systemd Service (Recommended)

Use systemd for auto-restart on crash and boot persistence.

**3a. Create env file** (`/home/hindsight/.hindsight.env`):

```
HINDSIGHT_API_LLM_API_KEY=<key>
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_MODEL=<model>
HINDSIGHT_API_BASE_URL=<base_url>
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
HINDSIGHT_API_DATABASE_URL=pg0
```

Set ownership: `sudo chown hindsight:hindsight /home/hindsight/.hindsight.env && sudo chmod 600 /home/hindsight/.hindsight.env`

> ⚠️ **Use EnvironmentFile, NOT Environment= in service file.** Direct env vars with API keys containing special characters may get shell-mangled.

**3b. Service file** (`/etc/systemd/system/hindsight-api.service`):

See `templates/hindsight-api.service`.

**3c. Enable and start**:
```bash
sudo systemctl daemon-reload
sudo systemctl enable hindsight-api
sudo systemctl start hindsight-api
```

### 3. Start Hindsight Service (systemd recommended)

Create `/etc/systemd/system/hindsight-api.service`:

```ini
[Unit]
Description=Hindsight API Service (Agent Memory)
After=network.target

[Service]
Type=simple
User=hindsight
Group=hindsight
WorkingDirectory=/home/hindsight
EnvironmentFile=/home/hindsight/.hindsight.env
ExecStart=/usr/local/lib/hermes-agent/venv/bin/hindsight-api --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hindsight

[Install]
WantedBy=multi-user.target
```

Create env file `/home/hindsight/.hindsight.env`:
```
HINDSIGHT_API_LLM_API_KEY=<key>
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_MODEL=<model>
HINDSIGHT_API_BASE_URL=<base_url>
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
HINDSIGHT_API_DATABASE_URL=pg0
```

Set permissions: `chown hindsight:hindsight /home/hindsight/.hindsight.env && chmod 600 /home/hindsight/.hindsight.env`

Activate:
```bash
sudo cp /tmp/hindsight-api.service /etc/systemd/system/hindsight-api.service
sudo systemctl daemon-reload
sudo systemctl enable hindsight-api
sudo systemctl start hindsight-api
```

**Why systemd over manual start?** 守护进程自动重启崩溃进程、开机自启、日志集中管理。Hindsight 无守护进程时 crash 后 MCP 断连需手动 SSH 重启。

**Manual start fallback** (for testing only):
```bash
su -s /bin/sh hindsight -c '
HINDSIGHT_API_LLM_API_KEY=*** \
HINDSIGHT_API_LLM_PROVIDER=openai \
HINDSIGHT_API_LLM_MODEL="<model>" \
HINDSIGHT_API_BASE_URL="<base_url>" \
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local \
HINDSIGHT_API_DATABASE_URL=pg0 \
HOME=/home/hindsight \
/usr/local/lib/hermes-agent/venv/bin/hindsight-api --log-level info
'
```

Key env vars:
- `HINDSIGHT_API_LLM_API_KEY` — LLM API key
- `HINDSIGHT_API_LLM_PROVIDER` — `openai` (for OpenAI-compatible endpoints)
- `HINDSIGHT_API_LLM_MODEL` — model name
- `HINDSIGHT_API_LLM_BASE_URL` — ⚠️ **CRITICAL**: This is the env var used by LLM calls (including entity extraction/retain). NOT `HINDSIGHT_API_BASE_URL`. The latter is a generic config key but Hindsight's LLM provider reads `HINDSIGHT_API_LLM_BASE_URL`. Mismatch causes 401 AuthenticationError on every retain.
- `HINDSIGHT_API_EMBEDDINGS_PROVIDER` — `local` (uses sentence-transformers, no API key needed)
- `HINDSIGHT_API_DATABASE_URL` — `pg0` (embedded PostgreSQL)

### 4. Verify Health

```bash
curl http://localhost:8888/health
```

### 5. Configure Hermes MCP

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hindsight:
    url: http://localhost:8888/mcp/
    connect_timeout: 60
    timeout: 180
```

### 6. Restart Hermes Gateway

Changes take effect after gateway restart.

### 7. Recreating Hindsight Container (Docker)

When you need to update the image, change ports, or fix env vars:

```bash
# Data is in bind mount — safe to rm container
docker stop hindsight && docker rm hindsight

# Recreate with same env-file + mounts
bash /root/.hermes/skills/hindsight-integration/templates/hindsight-env-example.sh
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Permission denied` on hindsight-api | venv owned by root | `chown -R hindsight:hindsight /usr/local/lib/hermes-agent/venv` |
| `cannot be run as root` (pg0) | Running as root | Use `sudo -u hindsight` or `su` |
| `Permission denied` on python binary | /root is 700 | `chmod o+x /root` |
| `ModuleNotFoundError: pydantic_core._pydantic_core` | System python vs venv ABI mismatch | Use venv python, not system python |
| `model_not_found` / `无效的令牌` | Agnes API key invalid or model unsupported | Verify key, check Agnes supports the model |
| Embedding 401/503 | Agnes doesn't support embedding models | Use `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local` |
| `could not open shared memory segment` | PG0 shared memory lost (container/restart) | Force-kill stale PG processes, restart service: `kill -9 <pid>; systemctl start hindsight-api` |
| Health returns `healthy` but no data | PG0 crashed, API still running | Check journalctl logs, restart service |
| `psql: connection refused` | PG0 not accepting connections | Shared memory issue — restart PG0 via service restart |
| Embedding model `FileNotFoundError` in Docker | Container cannot see host path | Bind-mount model dir to `SENTENCE_TRANSFORMERS_HOME` inside container |
| `UnsupportedProtocol` httpx error in container | Hindsight's httpx mishandles hf-mirror relative URLs | Set `HF_ENDPOINT=` (empty) to disable hf-mirror env var |
| PG0 startup `Permission denied` in Docker | Host pg0 dir is 755, container hindsight user needs write | `chmod -R 777 /root/.hindsight-docker` before starting container |
| Reranker model fails to download | Same httpx bug as above | Set `HINDSIGHT_API_RERANKER_PROVIDER=rrf` to disable reranker |
| API key returns 401 | Shell truncates key in `docker run -e` | Use `--env-file` with .env file containing full key |
| Health check fails immediately after start | PG0 needs ~10s to initialize | Sleep 30s after container start before curl |
| Chinese embedding model too slow to download | hf-mirror throttles to ~1KB/s | Clone + git-lfs pull on host, bind-mount into container (see references/docker-embeddings.md) |
| `retain` returns `accepted` but `list_memories` is empty | Fact extraction `AuthenticationError` — Hindsight container's Agnes API key is invalid/expired or truncated in docker env | 1. Check key length: `docker exec hindsight printenv HINDSIGHT_API_LLM_API_KEY | wc -c`. Should be 50+ chars. 2. Test key inside container: `docker exec hindsight curl -s https://apihub.agnes-ai.com/v1/models -H 'Authorization: Bearer $HINDSIGHT_API_LLM_API_KEY'`. 3. If key is truncated (< 20 chars), it was mangled during docker run — recreate container with `--env-file` instead of `-e`. 4. Fix: update env file with FULL key, restart container. |
| `hermes chat` Connection error (provider=custom) | config.yaml has `providers: {}` empty — `hermes chat` defaults to `provider=custom` which needs its own API key entry. Gateway works because it loads providers differently. | 1. Check: `grep -A20 'providers:' ~/.hermes/config.yaml`. If empty `{}`, that's the cause. 2. Add a `custom` provider entry with the same API key as `agnes`: `api_key: sk-xxx`, `base_url: https://apihub.agnes-ai.com/v1`, `models: [agnes-2.0-flash]`, `name: custom`. 3. Or use `--provider agnes` flag to bypass custom. 4. ⚠️ When writing API key via Python regex, verify key length after write — keys can get truncated (e.g., 51 chars → 48 chars). Always compare against source (Docker env var). |
| `hermes chat` API key truncated in config.yaml | When writing API keys into config.yaml via string substitution/regex, keys can get truncated (e.g., 51-char key becomes 48 chars). Always verify key length after write by comparing against source (Docker env var, backup file). If truncated, re-extract from original source (e.g., `docker exec hindsight printenv HINDSIGHT_API_LLM_API_KEY`). |
| `AuthenticationError` on ALL retains but key length is correct | Env var name mismatch: using `HINDSIGHT_API_BASE_URL` instead of `HINDSIGHT_API_LLM_BASE_URL`. Hindsight's LLM provider reads `HINDSIGHT_API_LLM_BASE_URL` specifically. Generic `HINDSIGHT_API_BASE_URL` is NOT picked up for LLM calls. | 1. Check: `docker exec hindsight printenv HINDSIGHT_API_LLM_BASE_URL`. Should match your API endpoint. 2. If empty/wrong, fix `.env` file to use `HINDSIGHT_API_LLM_BASE_URL` (note the `_LLM_` part). 3. Restart container. |

## Hindsight MCP Tools (33 total)

Core tools available via MCP:
- `recall` — semantic search
- `retain` — store memory
- `reflect` — reflection/insight extraction
- `list_banks`, `create_bank` — memory bank management
- `list_memories`, `get_memory`, `update_memory`, `invalidate_memory`
- `list_documents`, `get_document`, `delete_document`
- `list_mental_models`, `create_mental_model`, `update_mental_model`, `delete_mental_model`, `refresh_mental_model`, `clear_mental_model`
- `list_directives`, `create_directive`, `delete_directive`
- `list_operations`, `get_operation`, `cancel_operation`
- `list_tags`, `get_bank`, `get_bank_stats`, `update_bank`, `delete_bank`
- `clear_memories`

## Docker Deployment (Alternative to systemd)

For environments where systemd is unavailable or when container isolation is preferred:

**Prerequisites**: Docker installed, Hindsight image pulled.

**Step 1 — Pull image**:
```bash
docker pull ghcr.io/vectorize-io/hindsight:latest
```
If behind firewall/GFW, use offline tar: `docker load -i hindsight.tar`

**Step 2 — Run container**:
```bash
# Linux/Mac:
docker run -d --name hindsight \
  -p 8888:8888 -p 9999:9999 \
  -e HINDSIGHT_API_LLM_PROVIDER=deepseek \
  -e HINDSIGHT_API_LLM_API_KEY="your-key" \
  -e HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -e HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=shibing624/text2vec-base-chinese \
  -v $HOME/.hindsight-docker:/home/hindsight/.pg0 \
  ghcr.io/vectorize-io/hindsight:latest

# Windows PowerShell:
docker run -d --name hindsight `
  -p 8888:8888 -p 9999:9999 `
  -e HINDSIGHT_API_LLM_PROVIDER=deepseek `
  -e HINDSIGHT_API_LLM_API_KEY="your-key" `
  -e HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash `
  -e HF_ENDPOINT=https://hf-mirror.com `
  -e HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=shibing624/text2vec-base-chinese `
  -v "$HOME\.hindsight-docker:/home/hindsight/.pg0" `
  ghcr.io/vectorize-io/hindsight:latest
```

**Step 3 — Verify**:
```bash
curl http://localhost:8888/health
# Browser: http://localhost:9999
```

**Management**:
```bash
docker stop/start/restart hindsight
docker logs [-f] hindsight
docker rm -f hindsight  # data preserved in mounted volume
```

**Data persistence**: Stored in `$HOME/.hindsight-docker` (bind-mounted). Survives container recreation.

### Using Agnes API as Hindsight LLM Provider

Hindsight needs an LLM for entity extraction. Use Agnes via OpenAI-compatible mode:

```bash
-e HINDSIGHT_API_LLM_PROVIDER=openai \
-e HINDSIGHT_API_LLM_MODEL=agnes-2.0-flash \
-e HINDSIGHT_API_BASE_URL=https://apihub.agnes-ai.com/v1 \
-e HINDSIGHT_API_LLM_API_KEY=<full-key> \
```

> ⚠️ **API Key Retrieval**: The key in `~/.hermes/config.yaml` may be truncated (e.g., `sk-F6J...Q2r7`). If so, retrieve the full key from Hermes gateway runtime or login to apihub.agnes-ai.com. Keys in logs/gateway.log are also sanitized. Check backup config files (`config.yaml.bak.*`) — they may have the full key.

#### ⚠️ Docker API Key Truncation Pitfall (2026-06-19 confirmed)

**Symptom**: `docker run -e HINDSIGHT_API_LLM_API_KEY="sk-xxx..."` causes key to be truncated to ~13 chars inside container. Verified: `docker exec hindsight printenv HINDSIGHT_API_LLM_API_KEY | wc -c` returns < 20.

**Root cause**: Docker `-e` flag with shell quoting can mangle long strings containing special chars. YAML config.yaml also truncates keys during serialization.

**Fix — ALWAYS use `--env-file` for Docker, NEVER `-e` for API keys**:

1. Create env file on host:
   ```bash
   cat > /root/.hindsight.env << 'EOF'
   HINDSIGHT_API_LLM_API_KEY=<FULL_KEY_FROM_APIHUB>
   HINDSIGHT_API_LLM_PROVIDER=openai
   HINDSIGHT_API_LLM_MODEL=agnes-2.0-flash
   HINDSIGHT_API_BASE_URL=https://apihub.agnes-ai.com/v1
   HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
   HINDSIGHT_API_DATABASE_URL=pg0
   HINDSIGHT_API_RERANKER_PROVIDER=rrf
   echo "   HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=/home/hindsight/.cache/sentence-transformers/text2vec"
   EOF
   chmod 600 /root/.hindsight.env
   ```

2. Recreate container with `--env-file`:
   ```bash
   docker stop hindsight && docker rm hindsight
   docker run -d --name hindsight \
     --env-file /root/.hindsight.env \
     -p 8888:8888 -p 9999:9999 \
     -v /root/.hindsight-docker:/home/hindsight/.pg0 \
     -v /root/.hindsight-docker/hf-cache/text2vec:/home/hindsight/.cache/sentence-transformers/text2vec \
     ghcr.io/vectorize-io/hindsight:latest
   ```

3. Verify key length inside container:
   ```bash
   docker exec hindsight printenv HINDSIGHT_API_LLM_API_KEY | wc -c
   # Should be 50+ chars
   ```

> 🔗 See `templates/hindsight-env-example.sh` for a ready-to-use Docker start script with `--env-file`.
> 🔗 See `references/config-yaml-api-key-truncation.md` for config.yaml API key truncation pitfall and fix.

### Bank Management

For operations on Hindsight banks (create/delete/clear), see `references/hindsight-bank-management.md`.

### Embeddings

Agnes does NOT support embedding models. Use `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local` (requires `sentence-transformers` + `torch`).

**Chinese embedding model download (Docker)**: hf-mirror 限速约 1KB/s，无法在容器内下载。正确做法：
1. 宿主机用 `git clone` + `git lfs pull` 从 hf-mirror 下载模型到本地目录
2. 将模型目录 bind-mount 到容器内的 `SENTENCE_TRANSFORMERS_HOME` 路径
3. 容器启动时用 `HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL` 指向挂载路径

见 `references/docker-embeddings.md` 详细步骤。

See `references/docker-deploy.md` for full Docker deployment guide.

### Embedding Model Identity Note

`shibing624/text2vec-base-chinese` and `hfl/chinese-macbert-base` are the **same model**. The former is a wrapper repo that sets `_name_or_path` to the latter. Hindsight accepts either name. See `references/embedding-model-identity.md` for the full comparison.

## Hermes MemoryProvider Plugin (Automatic Bidirectional Sync)

A `MemoryProvider` plugin enables **fully automatic** Hindsight integration — no manual `hindsight_retain` calls needed.

**Plugin locations (TWO copies):**
1. **User-created**: `~/.hermes/plugins/hindsight/__init__.py` — the source you manage
2. **Official (bundled)**: `/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/hindsight_memory_provider/__init__.py` — shipped with Hindsight, auto-installed

**⚠️ Dual-location pitfall:** Deleting `~/.hermes/plugins/hindsight/` does NOT break auto-sync if the venv copy remains. The venv copy is the one actually loaded by Hermes. Always check BOTH locations before assuming the plugin is gone.

**⚠️ Silent failure: plugin not loaded by gateway** (2026-06-19 confirmed):
- Even when `config.yaml` has `memory.provider: hindsight` and the venv plugin file exists, the gateway may silently skip loading it.
- **Diagnosis**: grep gateway logs for `hindsight`/`memory provider`/`plugin load`/`provider.*hindsight`. Zero matches = plugin not loaded.
- **Root cause**: Plugin file exists in venv but was never symlinked from `~/.hermes/plugins/hindsight/`. Some Hermes versions require the plugin to exist in BOTH locations (the user-facing symlink is the loader trigger).
- **Fix**: Create the symlink: `ln -s /usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/hindsight_memory_provider ~/.hermes/plugins/hindsight`. Then restart gateway.
- **Verification**: After restart, gateway logs should contain `Hindsight MCP session initialized: xxx`.

**How it works:**
- `prefetch()` — Before each turn, semantically recalls relevant memories from Hindsight and injects into system prompt
- `sync_turn()` — After each turn, stores a summary of the conversation to Hindsight
- `on_session_end()` — On session close, extracts recent user messages as structured facts
- `on_memory_write()` — Mirrors Hermes built-in `memory` tool writes to Hindsight

**Setup:**
1. Ensure plugin exists in venv: `/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/hindsight_memory_provider/__init__.py`
2. Set in `~/.hermes/config.yaml`: `memory.provider: hindsight`
3. Gateway restart required

**Key benefit:** Conversations are automatically persisted and recalled without any LLM intervention. The plugin communicates with Hindsight via raw HTTP MCP calls (not the MCP tool protocol), maintaining its own session.

**⚠️ Config editing:** `sed -i` may silently fail on config.yaml. Use Python for reliable edits:
```python
python3 -c "
p='/root/.hermes/config.yaml'
with open(p,'r') as f: lines=f.readlines()
out=[l.replace('provider: hindsight','provider: builtin') if l.strip()=='provider: hindsight' else l for l in lines]
with open(p,'w') as f: f.writelines(out)
"
```

## Mnemosyne Alternative

Mnemosyne is Hermes-native (MemoryProvider plugin), zero-config, SQLite-based. Use it as primary memory layer. Hindsight can be imported into Mnemosyne via `mnemosyne_import` tool.

## Systemd Service Template

For production use, manage Hindsight via systemd for auto-start + crash recovery:

1. **Create env file** (avoids shell parsing issues with special chars in API keys):
   ```bash
   cat > /home/hindsight/.hindsight.env << 'EOF'
   HINDSIGHT_API_LLM_API_KEY=<your_key>
   HINDSIGHT_API_LLM_PROVIDER=openai
   HINDSIGHT_API_LLM_MODEL=<model>
   HINDSIGHT_API_BASE_URL=<base_url>
   HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
   HINDSIGHT_API_DATABASE_URL=pg0
   EOF
   chown hindsight:hindsight /home/hindsight/.hindsight.env
   chmod 600 /home/hindsight/.hindsight.env
   ```

2. **Create service file** (`/etc/systemd/system/hindsight-api.service`):
   ```ini
   [Unit]
   Description=Hindsight API Service (Agent Memory)
   After=network.target

   [Service]
   Type=simple
   User=hindsight
   Group=hindsight
   WorkingDirectory=/home/hindsight
   EnvironmentFile=/home/hindsight/.hindsight.env
   ExecStart=/usr/local/lib/hermes-agent/venv/bin/hindsight-api --log-level info
   Restart=always
   RestartSec=5
   StandardOutput=journal
   StandardError=journal
   SyslogIdentifier=hindsight

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hindsight-api
   sudo systemctl start hindsight-api
   ```

4. **Verify**:
   ```bash
   sudo systemctl status hindsight-api --no-pager
   curl http://localhost:8888/health
   ```

### Memory Management

Hindsight peak memory ~1.6GB (Python 1.3GB + PG shared_buffers 256MB). On 7.6GB RAM machines:
- Ensure swap ≥ 2GB: `sudo fallocate -l 2G /swapfile; sudo chmod 600 /swapfile; sudo mkswap /swapfile; sudo swapon /swapfile`
- Add to `/etc/fstab`: `/swapfile none swap sw 0 0`
- Monitor: `free -h` and `swapon --show`
- If OOM risk: reduce PG `shared_buffers` in service file