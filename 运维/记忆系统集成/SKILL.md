---
name: 记忆系统集成
description: 比较和集成主流 Agent Memory 系统（Mnemosyne, Hindsight, Mem0, Honcho 等）与 Hermes 的对接方式。覆盖 MemoryProvider 接口、MCP 协议接入、数据导入导出。
---

# Agent Memory Systems

比较和集成主流 Agent Memory 系统与 Hermes 的对接方式。

## 系统概览

| 系统 | 集成方式 | 存储 | 需要 | 自动注入 |
|------|---------|------|------|---------|
| **Mnemosyne** | MemoryProvider 插件 | SQLite | 零配置 | ✅ 每轮自动 |
| **Hindsight** | MCP Server | PostgreSQL | PG + LLM API + Worker | ❌ 需手动 |
| **Honcho** | MemoryProvider 插件 | 云端 | API Key | ✅ 每轮自动 |

## Mnemosyne 集成（推荐）

Mnemosyne 是 Hermes 的 **原生 MemoryProvider 插件**，通过标准接口深度集成。

### 启用方式

在 `~/.hermes/config.yaml` 中配置：

```yaml
memory:
  provider: mnemosyne
```

### 生命周期钩子

Mnemosyne 实现了 `MemoryProvider` 抽象类的核心方法：

- **`prefetch()`** — 每轮对话前自动语义搜索相关记忆，注入 system prompt
- **`sync_turn()`** — 每轮结束后自动保存对话内容到 episodic memory
- **`on_session_end()`** — 会话结束时自动 consolidation（压缩旧记忆）
- **`get_tool_schemas()`** — 暴露 `mnemosyne_remember` 等工具供 AI 主动调用
- **`on_memory_write()`** — 镜像 Hermes 内置 memory 工具的写入
- **`on_pre_compress()`** — 上下文压缩前提取洞察

### 暴露的工具

| 工具 | 用途 |
|------|------|
| `mnemosyne_remember` | 存记忆（content + importance 0-1 + scope global/session） |
| `mnemosyne_recall` | 语义搜索记忆 |
| `mnemosyne_shared_remember` | 跨 profile 共享记忆 |
| `mnemosyne_stats` | 查看记忆统计 |
| `mnemosyne_sleep` | 手动触发 consolidation |
| `mnemosyne_remember_canonical` | 存身份/偏好等固定信息 |
| `mnemosyne_import` | 从其他系统导入（支持 hindsight, mem0） |

### 注意事项

- import 名是 `hermes_memory_provider`，不是 `mnemosyne_memory`
- importance 参数用 0.0-1.0，CLI 命令 `mnemosyne store` 用 1-10
- 依赖 `fastembed` + `sqlite-vec`，安装 `pip install mnemosyne-memory[embeddings] sqlite-vec`

## Hindsight 集成

Hindsight **不是** Hermes 的 MemoryProvider，它是一个独立的 agent memory 系统。

### 接入方式：MCP Server

Hindsight 通过 **MCP（Model Context Protocol）** 与 Hermes 通信：

1. **启动 Hindsight 服务**：
   ```bash
   export HINDSIGHT_API_LLM_API_KEY="***"
   hindsight-api  # 监听 0.0.0.0:8888
   ```

2. **在 Hermes 配置中注册 MCP server**：
   ```yaml
   mcp_servers:
     hindsight:
       url: "http://localhost:8888/mcp/"
   ```

### Hindsight MCP 工具（33个）

核心工具：`recall`, `retain`, `reflect`, `list_banks`, `create_bank`, `list_memories`, `get_memory`, `update_memory`, `invalidate_memory`

### 前置依赖

- PostgreSQL（可用 pg0-embedded 内嵌运行）
- LLM API Key（默认 OpenAI）
- 独立进程（端口 8888）

### 数据迁移

Mnemosyne 的 `mnemosyne_import` 支持从 Hindsight 导入数据：
```
mnemosyne_import provider=hindsight api_key=*** base_url=http://localhost:8888
```

## 选择建议

- **大多数场景**：用 Mnemosyne，零配置、自动注入、自动保存
- **需要复杂知识图谱/银行管理**：Hindsight 更强大，但需要额外基础设施
- **两者互补**：Mnemosyne 做日常记忆，Hindsight 做深度知识管理，定期迁移
