# Kimi 工具与图标

> **状态**：现行（2026-11-21）
> **后台**：Kimi Code（`kimi acp`）
> **数据源**：`src/ide/gui/panels/chat/vendors/kimi/`、`src/ide/llm/providers/kimi/updates.py`、`kimi/subagent.py`、公共 `card_base.py`；工具名清单来自 `维护手册/工具箱说明.md`（2026-08-01）
> **一句话**：Kimi 有 26 个后端工具名，IDE 只对 7 个名字做专用卡分派；标题图标完全由 ACP `kind` 决定，没有卡级覆盖。

## 1. 总览

| 项 | 数量 | 说明 |
|:---|:---:|:---|
| 后端工具名 | **26** | 来自 `工具箱说明.md`；随 Kimi CLI 版本变化 |
| 工厂具名分派 | **7** | AskUserQuestion / ask / Agent / Task / ReadMediaFile / TodoList / todowrite |
| `kind` 通道 | **7** | execute / edit / read / search / fetch / think / other |
| 工具卡类 | **8** | Bash / Diff / TextOutput / Subagent / Question / MediaRead / TodoList / Mcp |
| 工具卡图标 | **7** | 💻 📝 📄 🔍 🌐 🤖 🧩 |
| 非工具卡 | 2 | 计划清单 📋、思维链 🧠 |

> Kimi 的 `QuestionCard` / `MediaReadCard` / `TodoListCard` 只决定**卡内结构**，
> 标题图标仍取 `_KIND_ICONS[kind]`。测试帧里这三个工具的 `kind=other`，所以都显示 🧩；
> 图片卡和未知工具在标题行无法区分，这是与 DSH 最大的差异。

## 2. 26 个后端工具名

「常见 `kind`」带 `†` 的是按工具语义推断，最终以 ACP 帧为准；不带 `†` 的有本仓库测试或子代理 wire 映射实证。

### 2.1 文件与代码

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `Read` | 读取文本，支持分页/行号 | 按 kind | `read` | 📄 |
| `Write` | 创建/覆盖/追加文件 | 按 kind | `edit` | 📝 |
| `Edit` | 精确字符串替换 | 按 kind | `edit` | 📝 |
| `Glob` | 按文件名模式查找 | 按 kind | `read` / `search` | 📄 / 🔍 |
| `Grep` | 正则搜索文件内容 | 按 kind | `read` / `search` | 📄 / 🔍 |
| `ReadMediaFile` | 读取图片等媒体 | **具名 → MediaReadCard** | `other`（测试帧）；`read` 也兼容 | 🧩（`read` → 📄） |

### 2.2 执行与进程

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `Bash` | 执行 shell 命令 | 按 kind | `execute` | 💻 |
| `TaskList` | 列出后台任务 | 按 kind | `other†` | 🧩 |
| `TaskOutput` | 查看后台任务输出 | 按 kind | `other†` | 🧩 |
| `TaskStop` | 停止后台任务 | 按 kind | `other†` | 🧩 |

### 2.3 子 Agent 与规划

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `Agent` | 启动单个子 agent | **具名 → SubagentCard** | `think` | 🤖 |
| `AgentSwarm` | 并行多个子 agent | 按 kind | `think†`（`other` 则落 McpCard） | 🤖 / 🧩 |
| `EnterPlanMode` | 进入规划模式 | 按 kind | `other†` | 🧩 |
| `ExitPlanMode` | 退出规划模式 | 按 kind | `other†` | 🧩 |
| `TodoList` | 维护结构化待办 | **具名 → TodoListCard** | `other`（测试帧） | 🧩 |

### 2.4 目标模式

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `CreateGoal` | 创建长期目标 | 按 kind | `other†` | 🧩 |
| `GetGoal` | 查看目标状态 | 按 kind | `other†` | 🧩 |
| `UpdateGoal` | 继续/完成/受阻 | 按 kind | `other†` | 🧩 |
| `SetGoalBudget` | 设置目标预算 | 按 kind | `other†` | 🧩 |

### 2.5 定时任务

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `CronCreate` | 创建定时任务 | 按 kind | `other†` | 🧩 |
| `CronList` | 列出定时任务 | 按 kind | `other†` | 🧩 |
| `CronDelete` | 删除定时任务 | 按 kind | `other†` | 🧩 |

### 2.6 网络与信息

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `WebSearch` | 联网搜索 | 按 kind | `fetch` | 🌐 |
| `FetchURL` | 抓取网页正文 | 按 kind | `fetch` | 🌐 |

### 2.7 交互与技能

| 工具名 | 用途 | IDE 分派 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `AskUserQuestion` | 结构化提问 | **具名 → QuestionCard** | `other`（测试帧） | 🧩 |
| `Skill` | 调用已注册技能 | 按 kind | `other†` | 🧩 |

> 26 个名字来自 2026-08-01 的 `工具箱说明.md`（Kimi CLI 2.7.1 时期）。CLI 升级后以实际 `session/update` 帧的 `title`/`kind` 为准；本表只记录 IDE 侧的落卡规则。

## 3. 7 个工厂具名

`kimi/factory.py` 的 `_TOOL_NAME_CARDS`：

| 名称（归一化后） | 性质 | 卡片 | 常见 `kind` | 图标 |
|:---|:---|:---|:---|:---:|
| `askuserquestion` | 现役 | `QuestionCard` | `other` | 🧩 |
| `ask` | 历史 reasonix 兼容帧 | `QuestionCard` | `other` | 🧩 |
| `agent` | 现役 | `SubagentCard` | `think` | 🤖 |
| `task` | 显式承接（26 清单未列，可能为旧名） | `SubagentCard` | `think` | 🤖 |
| `readmediafile` | 现役 | `MediaReadCard` | `other` | 🧩 |
| `todolist` | 现役 | `TodoListCard` | `other` | 🧩 |
| `todowrite` | 历史 Kimi 清单名兼容 | `TodoListCard` | `other` | 🧩 |

工具名归一化：去 `mcp__server__` 前缀取末段、转小写；所以 `mcp__demo__TodoList` 仍会命中 `TodoListCard`，但图标仍取该帧的 `kind`。

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

> Kimi mapper 信任后端显式上报的 `kind`，**不按工具名反推**（与 DSH 相反）。因此同一工具在不同 CLI 版本里若 `kind` 变了，卡类可能变、图标也会变。

## 5. 子代理 wire 旁路的工具名映射

`kimi/subagent.py` 的 `WIRE_TOOL_KINDS` 用于把子代理 wire 事件合成 ACP 帧，仅影响子代理旁路：

| wire 工具名 | 合成 `kind` | 图标 |
|:---|:---|:---:|
| `Bash` | `execute` | 💻 |
| `Edit` | `edit` | 📝 |
| `Write` | `edit` | 📝 |
| `Read` | `read` | 📄 |
| `Glob` | `read` | 📄 |
| `Grep` | `read` | 📄 |
| `FetchURL` | `fetch` | 🌐 |
| `WebFetch` | `fetch` | 🌐 |
| `WebSearch` | `fetch` | 🌐 |

> 注意：这里 `Glob`/`Grep` 合成的是 `read`（📄），不是 `search`（🔍）。主会话若后端直接上报 `kind=search`，则显示 🔍；两处口径不同，排障时不要混用。

## 6. 非工具卡与兜底

| 场景 | 卡片 | 图标 |
|:---|:---|:---:|
| `sessionUpdate=plan` | `TodoCard` | 📋 |
| `agent_thought_chunk` | `ThinkingCard` | 🧠 |
| 未知工具 / 未命中 `kind` | `McpCard` | 🧩 |
| MCP 工具（`mcp__…`） | 按末段名字/`kind`；通常 `McpCard` | 🧩 |

Kimi 没有 DSH 的 `_is_mcp_tool()` 拆分：`mcp__server__tool` 会归一化成 `tool`，未命中具名表就按 `kind` 落 `McpCard`，一般显示 🧩，不会变 🔌。

## 7. 维护锚点

| 改动 | 文件 |
|:---|:---|
| 工具名 → 专用卡 | `src/ide/gui/panels/chat/vendors/kimi/factory.py` 的 `_TOOL_NAME_CARDS` |
| 子代理 wire 工具名 → kind | `src/ide/llm/providers/kimi/subagent.py` 的 `WIRE_TOOL_KINDS` |
| `kind` 图标 | 公共 `src/ide/gui/panels/chat/card_base.py` 的 `_KIND_ICONS` |
| 工具清单（后端名字） | `维护手册/工具箱说明.md`（人工维护） |

改完跑：

```bash
UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/shot_vendor_cards.py --vendor kimi
```

---

*创建于 2026-11-21 | 工具名来源 `维护手册/工具箱说明.md`（2026-08-01）*
