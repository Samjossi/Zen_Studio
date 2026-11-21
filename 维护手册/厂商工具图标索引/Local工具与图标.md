# Local 工具与图标

> **状态**：现行（2026-11-21）
> **后台**：Local（`local-cli acp`，本地 GGUF 模型）
> **数据源**：`src/ide/gui/panels/chat/vendors/local/`、`src/ide/llm/providers/local/updates.py`、公共 `card_base.py`
> **一句话**：Local 的工具全量由 `local-cli` 运行时上报，IDE 只固化 7 个兼容具名 + 7 个 `kind` 通道；标题图标完全由 `kind` 决定。

## 1. 总览

| 项 | 数量 | 说明 |
|:---|:---:|:---|
| IDE 具名工具 | **7** | 兼容词表，不是 Local 后端工具全量 |
| 后端工具全量 | **未固化** | 仓库不静态枚举；以 `session/update` 帧为准 |
| `kind` 通道 | **7** | execute / edit / read / search / fetch / think / other |
| 工具卡类 | **8** | Bash / Diff / TextOutput / Subagent / Question / MediaRead / TodoList / Mcp |
| 工具卡图标 | **7** | 💻 📝 📄 🔍 🌐 🤖 🧩 |
| 非工具卡 | 2 | 计划清单 📋、思维链 🧠 |

> 与 Kimi 一样，Local 的 `QuestionCard` / `MediaReadCard` / `TodoListCard` 没有卡级图标；
> `kind=other` 时统一显示 🧩，标题行无法与未知工具区分。

## 2. 7 个 IDE 具名工具

`local/factory.py` 的 `_TOOL_NAME_CARDS` 是兼容词表：保留旧共享表里 Local 可能收到的通用工具名，但**不收** DSH 的 `read_image` / `todo_write`。

| 名称（归一化后） | 性质 | 卡片 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `askuserquestion` | 兼容具名 | `QuestionCard` | `other` | 🧩 |
| `ask` | 兼容具名 | `QuestionCard` | `other` | 🧩 |
| `agent` | 兼容具名 | `SubagentCard` | `think` | 🤖 |
| `task` | 兼容具名 | `SubagentCard` | `think` | 🤖 |
| `readmediafile` | 兼容具名 | `MediaReadCard` | `other` | 🧩 |
| `todolist` | 兼容具名 | `TodoListCard` | `other` | 🧩 |
| `todowrite` | 兼容具名 | `TodoListCard` | `other` | 🧩 |

- 工具名归一化：去 `mcp__server__` 前缀取末段、转小写。
- 这 7 个名字只保证「若后端这样命名，就落对应专用卡」；不保证 Local 后端一定会发这些名字。
- 图片卡在测试帧里 `kind=other` → 🧩；若后端上报 `kind=read`，图标会变成 📄，但卡片仍是 `MediaReadCard`。

## 3. `kind` 通道

| `kind` | 卡片 | 图标 |
|:---|:---|:---:|
| `execute` | `BashCard` | 💻 |
| `edit` | `DiffCard` | 📝 |
| `read` | `TextOutputCard` | 📄 |
| `search` | `TextOutputCard` | 🔍 |
| `fetch` | `TextOutputCard` | 🌐 |
| `think` | `SubagentCard` | 🤖 |
| `other` | `McpCard` | 🧩 |

> `local/updates.py` 信任显式 `kind`，使用标准 ACP content 与自身 rawInput 词表；不借用 DSH 工具名兜底，也不解析 Kimi 私有 `<image path>` 标记。

## 4. MCP、未知与提问

| 场景 | 工具名示例 | 卡片 | 图标 |
|:---|:---|:---|:---:|
| MCP 工具 | `mcp__demo__tool` | `McpCard` | 🧩 |
| MCP 工具末段命中具名 | `mcp__demo__TodoList` | `TodoListCard` | 🧩 |
| 未知工具 | `未收录工具` | `McpCard` | 🧩 |
| AskUserQuestion | `AskUserQuestion` | `QuestionCard` | 🧩 |
| QuestionCard 直接实例 | — | `QuestionCard` | 🧩 |

Local 没有 DSH 的 `_is_mcp_tool()` 拆分：`mcp__server__tool` 归一化成 `tool` 后按 `kind` 落卡，不会显示 🔌。

## 5. 非工具卡

| 事件 | 卡片 | 图标 |
|:---|:---|:---:|
| `sessionUpdate=plan` | `TodoCard` | 📋 |
| `agent_thought_chunk` | `ThinkingCard` | 🧠 |

## 6. 为什么没有完整工具清单

1. `local/factory.py` 只有 7 个兼容具名 + 7 个 `kind` 通道，没有 Kimi `工具箱说明.md` 那样的后端工具全量表。
2. `local-cli models` 只枚举 GGUF 模型，不列工具；当前记录环境也未安装 `local-cli`，无法离线核对。
3. 工具名与 `kind` 只存在于真机 `session/update` 帧（`tool_call.title` / `tool_call.kind`），仓库不静态枚举，符合厂商隔离设计。

**补全方法**：跑一次真实 Local 会话，按下方模板补行；若某工具需要专用卡，先在 `local/factory.py` 的 `_TOOL_NAME_CARDS` 加名，再同步本文件。

```markdown
| `工具名` | 用途 | 按 kind / 具名 → 卡类 | `kind` | 图标 |
```

## 7. 维护锚点

| 改动 | 文件 |
|:---|:---|
| 工具名 → 专用卡 | `src/ide/gui/panels/chat/vendors/local/factory.py` 的 `_TOOL_NAME_CARDS` |
| `kind` 图标 | 公共 `src/ide/gui/panels/chat/card_base.py` 的 `_KIND_ICONS` |
| 后端工具名/`kind` 实证 | 真机 `session/update` 帧或 `scripts/shot_vendor_cards.py` 的 Local 场景 |

改完跑：

```bash
UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/shot_vendor_cards.py --vendor local
```

---

*创建于 2026-11-21 | 工具全量待真机帧补全*
