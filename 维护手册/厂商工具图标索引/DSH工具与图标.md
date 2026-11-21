# DSH 工具与图标

> **状态**：现行（2026-11-21）
> **后台**：DeepSeek Harness（`dsh acp`）
> **数据源**：`src/ide/gui/panels/chat/vendors/dsh/factory.py`、`cards.py`；`src/ide/llm/providers/dsh/updates.py`；公共 `card_base.py`
> **一句话**：DSH 是三家唯一按「卡片类型」拆图标的厂商——读图 👁️、清单 🎯、MCP 🔌 都有独立图案。

## 1. 总览

| 项 | 数量 | 说明 |
|:---|:---:|:---|
| IDE 可识别工具名 | **19** | `read_image` / `todo_write` / `subagent` 有工厂具名；其余按 mapper 兜底 `kind` |
| `kind` 通道 | **7** | execute / edit / read / search / fetch / think / other |
| 工具卡类 | **7** | Bash / Diff / TextOutput / Subagent / MediaRead / TodoList / Mcp |
| 工具卡图标 | **10** | 💻 📝 📄 🔍 🌐 🤖 👁️ 🎯 🔌 🧩 |
| 非工具卡 | 2 | 计划清单 📋、思维链 🧠 |
| 预留未接 | 1 | `QuestionCard` ❓（工厂未分派） |

## 2. 19 个工具名

| 工具名 | 用途 | 入口 | 卡片 | 图标 |
|:---|:---|:---|:---|:---:|
| `bash` | 执行命令 | mapper kind=execute | `BashCard` | 💻 |
| `pwsh` | PowerShell 命令 | mapper kind=execute | `BashCard` | 💻 |
| `shell` | Shell 命令 | mapper kind=execute | `BashCard` | 💻 |
| `terminal` | 终端命令 | mapper kind=execute | `BashCard` | 💻 |
| `write` | 写入文件 | mapper kind=edit | `DiffCard` | 📝 |
| `edit` | 编辑文件 | mapper kind=edit | `DiffCard` | 📝 |
| `patch` | 打补丁 | mapper kind=edit | `DiffCard` | 📝 |
| `read` | 读取文本 | mapper kind=read | `TextOutputCard` | 📄 |
| `read_image` | 读取图片 | **工厂具名** | `MediaReadCard` | 👁️ |
| `grep` | 内容搜索 | mapper kind=search | `TextOutputCard` | 🔍 |
| `glob` | 文件名查找 | mapper kind=search | `TextOutputCard` | 🔍 |
| `fs_search` | 文件系统搜索 | mapper kind=search | `TextOutputCard` | 🔍 |
| `web` | 网页访问 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `web_search` | 联网搜索 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `web_fetch` | 抓取网页 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `fetch` | 抓取 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `todo_write` | 待办清单 | **工厂具名** | `TodoListCard` | 🎯 |
| `subagent` | 子代理 | **工厂具名**（kind=think） | `SubagentCard` | 🤖 |
| `task` | 子任务 | mapper kind=think | `SubagentCard` | 🤖 |

> `read_image` 的 mapper 兜底 kind 是 `read`，但工厂具名分派优先，所以不是 📄；
> `todo_write` 的 mapper 兜底 kind 是 `other`，同样由工厂具名分派成 🎯。

## 3. 分派优先级

```
1. 工厂具名 _TOOL_NAME_CARDS：read_image → MediaReadCard
                              todo_write → TodoListCard
                              subagent  → SubagentCard
2. kind 分派：execute → BashCard；edit → DiffCard；
              read/search/fetch → TextOutputCard；think → SubagentCard
3. MCP 前缀 mcp__… → McpCard(header_icon="🔌")
4. 兜底 → McpCard(header_icon="🧩")
```

图标覆盖在 DSH `ToolCard` 内：

```
header_icon 参数（工厂传） > _header_icon 类属性 > 公共 _KIND_ICONS[kind]
```

## 4. `kind` 通道

| `kind` | 卡片 | 图标 |
|:---|:---|:---:|
| `execute` | `BashCard` | 💻 |
| `edit` | `DiffCard` | 📝 |
| `read` | `TextOutputCard` | 📄 |
| `search` | `TextOutputCard` | 🔍 |
| `fetch` | `TextOutputCard` | 🌐 |
| `think` | `SubagentCard` | 🤖 |
| `other` | `McpCard` | 🧩 |

## 5. MCP、未知与提问

| 场景 | 工具名示例 | 卡片 | 图标 |
|:---|:---|:---|:---:|
| MCP 服务器工具 | `mcp__demo__tool` | `McpCard` | 🔌 |
| MCP 工具恰好叫 read_image | `mcp__demo__read_image` | `MediaReadCard` | 👁️ |
| MCP 工具恰好叫 todo_write | `mcp__demo__todo_write` | `TodoListCard` | 🎯 |
| 未知工具 | `未收录工具` | `McpCard` | 🧩 |
| AskUserQuestion | `AskUserQuestion` | `McpCard` | 🧩 |
| QuestionCard（预留） | — | `QuestionCard` | ❓ |

- `mcp__` 前缀大小写不敏感；MCP 判定在工具名分派之后，所以归一化后的末段若是 `read_image`/`todo_write`/`subagent`，仍走专用卡。
- `QuestionCard` 有 ❓ 类属性，但工厂没有分派入口；DSH 的 `AskUserQuestion` 实际落 `McpCard` 并显示 🧩。
- 未列名的 MCP 工具都显示 🔌；未列名的普通工具显示 🧩。

## 6. 非工具卡

| 事件 | 卡片 | 图标 |
|:---|:---|:---:|
| `sessionUpdate=plan` | `TodoCard` | 📋 |
| `agent_thought_chunk` | `ThinkingCard` | 🧠 |

## 7. 维护锚点

| 改动 | 文件 |
|:---|:---|
| 工具名 → `kind` 兜底 | `src/ide/llm/providers/dsh/updates.py` 的 `_FALLBACK_TOOL_KINDS` |
| 工具名 → 专用卡 | `src/ide/gui/panels/chat/vendors/dsh/factory.py` 的 `_TOOL_NAME_CARDS` |
| 卡级图标 | `src/ide/gui/panels/chat/vendors/dsh/cards.py` 的 `_header_icon` |
| MCP 图标 | `factory.py` 的 `_is_mcp_tool()` 与 `header_icon="🔌"` |
| `kind` 图标 | 公共 `src/ide/gui/panels/chat/card_base.py` 的 `_KIND_ICONS` |

改完跑：

```bash
UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/shot_vendor_cards.py --vendor dsh
```

`tests/test_dsh_card_icons.py` 会锁住本文件里的图标矩阵；`read_image` 的断言是两码点 `U+1F441 U+FE0F`。

---

*创建于 2026-11-21 | 图标分型依据 `work plans/2026-1121-0200_...计划.md` §10*
