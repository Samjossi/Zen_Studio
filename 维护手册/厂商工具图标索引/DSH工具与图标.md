# DSH 工具与图标

> **状态**：现行（2026-11-21）
> **后台**：DeepSeek Harness（`dsh acp`）
> **数据源**：`src/ide/gui/panels/chat/vendors/dsh/factory.py`、`cards.py`；`src/ide/llm/providers/dsh/updates.py`；公共 `card_base.py`
> **一句话**：DSH 是三家唯一按「卡片类型」拆图标的厂商——读图 👁️、清单 🎯、MCP 🔌 都有独立图案。
> **口径**：IDE 已登记 **19** / 官方 standard 预设 **31**；未登记的 20 个当前全部兜底 `McpCard` 🧩。

## 1. 总览

| 项 | 数量 | 说明 |
|:---|:---:|:---|
| 官方 standard 预设工具 | **31** | 含 `glob` / `grep`；来源 `参考代码/deepseek-harness/apps/cli/tests/web-agent-presets.e2e.ts` |
| IDE 已登记工具名 | **19** | 11 个与官方交集 + 8 个兼容别名 |
| 未登记工具 | **20** | 当前全部落 `McpCard` 🧩，见 §3 |
| `kind` 通道 | **7** | execute / edit / read / search / fetch / think / other |
| 工具卡类 | **7** | Bash / Diff / TextOutput / Subagent / MediaRead / TodoList / Mcp |
| 工具卡图标 | **10** | 💻 📝 📄 🔍 🌐 🤖 👁️ 🎯 🔌 🧩 |
| 非工具卡 | 2 | 计划清单 📋、思维链 🧠 |
| 预留未接 | 1 | `QuestionCard` ❓（工厂未分派） |

> 19 = 已登记名，不等于 DSH 后端全部工具；完整对齐计划见
> `work plans/2026-1121-1153_DSH官方31工具全量对齐计划.md`（草稿）。

## 2. IDE 已登记的 19 个工具名

| 工具名 | 用途 | 官方预设 | 入口 | 卡片 | 图标 |
|:---|:---|:---:|:---|:---|:---:|
| `bash` | 执行命令 | 交集 | mapper kind=execute | `BashCard` | 💻 |
| `pwsh` | PowerShell 命令 | 仅 IDE 别名 | mapper kind=execute | `BashCard` | 💻 |
| `shell` | Shell 命令 | 仅 IDE 别名 | mapper kind=execute | `BashCard` | 💻 |
| `terminal` | 终端命令 | 仅 IDE 别名 | mapper kind=execute | `BashCard` | 💻 |
| `write` | 写入文件 | 交集 | mapper kind=edit | `DiffCard` | 📝 |
| `edit` | 编辑文件 | 交集 | mapper kind=edit | `DiffCard` | 📝 |
| `patch` | 打补丁 | 仅 IDE 别名 | mapper kind=edit | `DiffCard` | 📝 |
| `read` | 读取文本 | 交集 | mapper kind=read | `TextOutputCard` | 📄 |
| `read_image` | 读取图片 | 交集 | **工厂具名** | `MediaReadCard` | 👁️ |
| `grep` | 内容搜索 | 交集 | mapper kind=search | `TextOutputCard` | 🔍 |
| `glob` | 文件名查找 | 交集 | mapper kind=search | `TextOutputCard` | 🔍 |
| `fs_search` | 文件系统搜索 | 仅 IDE 别名 | mapper kind=search | `TextOutputCard` | 🔍 |
| `web` | 网页访问 | 仅 IDE 别名 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `web_search` | 联网搜索 | 交集 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `web_fetch` | 抓取网页 | 交集 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `fetch` | 抓取 | 仅 IDE 别名 | mapper kind=fetch | `TextOutputCard` | 🌐 |
| `todo_write` | 待办清单 | 交集 | **工厂具名** | `TodoListCard` | 🎯 |
| `subagent` | 子代理 | 交集 | **工厂具名**（kind=think） | `SubagentCard` | 🤖 |
| `task` | 子任务 | 仅 IDE 别名 | mapper kind=think | `SubagentCard` | 🤖 |

> `read_image` 的 mapper 兜底 kind 是 `read`，但工厂具名分派优先，所以不是 📄；
> `todo_write` 的 mapper 兜底 kind 是 `other`，同样由工厂具名分派成 🎯。
> 官方预设 31 与这 19 个的交集是 11 个，另有 8 个仅 IDE 兼容别名。

## 3. 后端存在但 IDE 未登记（20 个）

这 20 个名字不在 `_FALLBACK_TOOL_KINDS`，也不在 `_TOOL_NAME_CARDS`；`kind=other` 时走兜底
`McpCard`，图标统一 🧩，body 是 MCP 的「入参/出参」样式（`get_goal` / `update_goal` 截图即此形态）。

| 分组 | 工具名 | 用途 | 当前落卡 | 图标 |
|:---|:---|:---|:---|:---:|
| 目标 | `create_goal` | 创建长期目标，可设轮数上限 | `McpCard` | 🧩 |
| 目标 | `get_goal` | 查看当前目标状态与修订号 | `McpCard` | 🧩 |
| 目标 | `update_goal` | 更新目标（编辑/暂停/继续/完成/受阻） | `McpCard` | 🧩 |
| 计划 | `exit_plan_mode` | 提交计划供用户审核，退出规划模式 | `McpCard` | 🧩 |
| 提问 | `ask_user_question` | 结构化提问，等待用户选择 | `McpCard` | 🧩 |
| 子代理 | `list_agents` | 列出当前子代理 | `McpCard` | 🧩 |
| 子代理 | `send_message` | 给子代理发送消息 | `McpCard` | 🧩 |
| 子代理 | `interrupt_agent` | 中断指定子代理 | `McpCard` | 🧩 |
| 子代理 | `subagent_fork` | 派生子代理（继承上下文分叉） | `McpCard` | 🧩 |
| 后台任务 | `job_list` | 列出后台任务 | `McpCard` | 🧩 |
| 后台任务 | `job_output` | 读取后台任务输出 | `McpCard` | 🧩 |
| 后台任务 | `job_kill` | 终止后台任务 | `McpCard` | 🧩 |
| 定时 | `schedule_create` | 创建定时任务 | `McpCard` | 🧩 |
| 定时 | `schedule_list` | 列出定时任务 | `McpCard` | 🧩 |
| 定时 | `schedule_update` | 更新定时任务 | `McpCard` | 🧩 |
| 定时 | `schedule_delete` | 删除定时任务 | `McpCard` | 🧩 |
| 其他 | `present` | 呈报交付文件 | `McpCard` | 🧩 |
| 其他 | `skill` | 加载技能完整说明 | `McpCard` | 🧩 |
| 其他 | `workflow` | 运行工作流脚本 | `McpCard` | 🧩 |
| 其他 | `working_directory` | 读取/切换工作目录 | `McpCard` | 🧩 |

> 官方 expected 帧里 `get_goal` / `update_goal` 的 `kind` 是 `other`；这 20 个都不带
> `mcp__` 前缀，所以 `_is_mcp_tool()` 也不命中，不会显示 🔌。
> 全量对齐（31 个工具各有明确卡片与图标）见 `work plans/2026-1121-1153_DSH官方31工具全量对齐计划.md`。

## 4. 分派优先级

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

## 5. `kind` 通道

| `kind` | 卡片 | 图标 |
|:---|:---|:---:|
| `execute` | `BashCard` | 💻 |
| `edit` | `DiffCard` | 📝 |
| `read` | `TextOutputCard` | 📄 |
| `search` | `TextOutputCard` | 🔍 |
| `fetch` | `TextOutputCard` | 🌐 |
| `think` | `SubagentCard` | 🤖 |
| `other` | `McpCard` | 🧩 |

## 6. MCP、未知与提问

| 场景 | 工具名示例 | 卡片 | 图标 |
|:---|:---|:---|:---:|
| MCP 服务器工具 | `mcp__demo__tool` | `McpCard` | 🔌 |
| MCP 工具恰好叫 read_image | `mcp__demo__read_image` | `MediaReadCard` | 👁️ |
| MCP 工具恰好叫 todo_write | `mcp__demo__todo_write` | `TodoListCard` | 🎯 |
| 未知工具 | `未收录工具` | `McpCard` | 🧩 |
| 提问（官方名） | `ask_user_question` | `McpCard` | 🧩 |
| 提问（驼峰兼容） | `AskUserQuestion` | `McpCard` | 🧩 |
| QuestionCard（预留） | — | `QuestionCard` | ❓ |

- `mcp__` 前缀大小写不敏感；MCP 判定在工具名分派之后，所以归一化后的末段若是 `read_image`/`todo_write`/`subagent`，仍走专用卡。
- `QuestionCard` 有 ❓ 类属性，但工厂没有分派入口；DSH 的 `ask_user_question` / `AskUserQuestion` 实际落 `McpCard` 并显示 🧩。
- 未列名的 MCP 工具都显示 🔌；未列名的普通工具显示 🧩。

## 7. 非工具卡

| 事件 | 卡片 | 图标 |
|:---|:---|:---:|
| `sessionUpdate=plan` | `TodoCard` | 📋 |
| `agent_thought_chunk` | `ThinkingCard` | 🧠 |

## 8. 维护锚点

| 改动 | 文件 |
|:---|:---|
| 官方 31 工具清单 | `参考代码/deepseek-harness/apps/cli/tests/web-agent-presets.e2e.ts` 的 `standard` 预设 |
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

*创建于 2026-11-21 | 图标分型依据 `work plans/2026-1121-0200_...计划.md` §10 | 补官方 31 口径与 20 个未登记工具（2026-11-21）*
