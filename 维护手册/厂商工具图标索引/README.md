# 厂商工具图标索引

> **状态**：现行（2026-11-21）
> **范围**：Kimi / DSH / Local 三家后台的「工具名 → 卡片 → 标题图标」
> **数据源**：`src/ide/gui/panels/chat/vendors/<vendor>/`、`src/ide/llm/providers/<vendor>/updates.py`、公共 `src/ide/gui/panels/chat/card_base.py`、`维护手册/工具箱说明.md`
> **用途**：查某个后台有哪些工具、各自落什么卡、标题行图标是什么；后续加工具时知道改哪张表

## 1. 先看口径

工具卡片不是只有一张表，实际分四层，按优先级从高到低：

| 层 | 位置 | 作用 |
|:---|:---|:---|
| 1. 工厂具名分派 | 各厂商 `factory.py` 的 `_TOOL_NAME_CARDS` | 工具名直接命中专用卡（如 `read_image`、`TodoList`） |
| 2. mapper 工具名兜底 | 仅 DSH：`dsh/updates.py` 的 `_FALLBACK_TOOL_KINDS` | 后端没给 `kind`/给了 `other` 时，按工具名反推 `kind` |
| 3. `kind` 分派 | 各厂商 `factory.py` | ACP 帧的 `kind` → 卡类；Kimi/Local 主要靠这一层 |
| 4. 兜底 | 各厂商 `McpCard` | 未命中任何名字/`kind` 的工具 |

图标优先级：

```
DSH：header_icon 参数（工厂传） > _header_icon 类属性 > 公共 _KIND_ICONS[kind]
Kimi/Local：公共 _KIND_ICONS[kind]（无卡级覆盖）
```

- `_KIND_ICONS` 是公共表，三家共用：execute 💻 / edit 📝 / read 📄 / search 🔍 / fetch 🌐 / think 🤖 / other 🧩。
- 只有 DSH 在厂商包内做了卡级图标覆盖，所以能做到「同一 `kind`、不同卡型、不同图标」。
- Kimi/Local 的 `QuestionCard`、`MediaReadCard`、`TodoListCard` 没有卡级图标；当 `kind=other` 时，标题图标和未知工具一样是 🧩，只能靠卡片内容区分。
- `plan`（待办快照）与 `agent_thought_chunk`（思维链）不是工具调用，分别固定 📋 / 🧠。

## 2. 三家总览

| 后台 | IDE 可识别工具名 | 后端工具清单 | `kind` 通道 | 工具卡类 | 工具卡图标种数 | 读图 / 清单 / MCP 是否独立图标 |
|:---|:---:|:---|:---:|:---:|:---:|:---|
| **DSH** | 19 | 19（代码内完整） | 7 | 7 | 10 | **是**：👁️ / 🎯 / 🔌 |
| **Kimi** | 7（工厂具名） | 26（`工具箱说明.md`） | 7 | 8 | 7 | 否：通常都是 🧩 |
| **Local** | 7（兼容词表） | 未固化，由 `local-cli` 运行时上报 | 7 | 8 | 7 | 否：通常都是 🧩 |

> 「IDE 可识别工具名」= IDE 里有专门分派或工具名兜底的名称，不等于后端权限系统里的工具总数。
> 工具卡类不含 `TodoCard`（📋）与 `ThinkingCard`（🧠）；这两类不是工具调用。

## 3. 同一功能在三家的图标对照

| 功能 | DSH | Kimi | Local | 图标差异 |
|:---|:---|:---|:---|:---|
| 执行命令 | `bash` / `pwsh` / `shell` / `terminal` 💻 | `Bash` 💻 | 按 `kind=execute` 💻 | 一致 |
| 编辑文件 | `write` / `edit` / `patch` 📝 | `Write` / `Edit` 📝 | 按 `kind=edit` 📝 | 一致 |
| 读文本 | `read` 📄 | `Read` 📄 | 按 `kind=read` 📄 | 一致 |
| 看图 | `read_image` **👁️** | `ReadMediaFile` **🧩** | 按 `kind`（通常 `other`）**🧩** | **DSH 独有 👁️** |
| 搜索 | `grep` / `glob` / `fs_search` 🔍 | `Glob` / `Grep` 🔍（子代理旁路 📄） | 按 `kind=search` 🔍 | Kimi 子代理旁路例外 |
| 抓网页 | `web` / `web_search` / `web_fetch` / `fetch` 🌐 | `WebSearch` / `FetchURL` 🌐 | 按 `kind=fetch` 🌐 | 一致 |
| 待办清单 | `todo_write` **🎯** | `TodoList` **🧩** | `TodoList`（兼容词表）**🧩** | **DSH 独有 🎯** |
| 子代理 | `subagent` / `task` 🤖 | `Agent` 🤖 | `Agent`（兼容词表）🤖 | 一致 |
| 提问 | `AskUserQuestion` 🧩 | `AskUserQuestion` 🧩 | `AskUserQuestion`（兼容词表）🧩 | 一致；DSH 的 ❓ 仅预留 |
| MCP 工具 | `mcp__server__tool` **🔌** | 按 `kind`（通常 `other`）🧩 | 按 `kind`（通常 `other`）🧩 | **DSH 独有 🔌** |

## 4. 本目录文件

| 文件 | 内容 |
|:---|:---|
| `DSH工具与图标.md` | 19 个工具名 + 7 个 `kind` + MCP/未知/计划/思维链；含工厂分派优先级 |
| `Kimi工具与图标.md` | 26 个后端工具名、7 个工厂具名、7 个 `kind`、子代理 wire 工具名映射 |
| `Local工具与图标.md` | 7 个兼容具名、7 个 `kind`；说明全量工具清单为何待补 |

## 5. 图标码点

| 图标 | 码点 | 出现处 |
|:---:|:---|:---|
| 💻 | U+1F4BB | `kind=execute` / Bash |
| 📝 | U+1F4DD | `kind=edit` / Diff |
| 📄 | U+1F4C4 | `kind=read` / 文本输出 |
| 🔍 | U+1F50D | `kind=search` / 搜索 |
| 🌐 | U+1F310 | `kind=fetch` / 抓取 |
| 🤖 | U+1F916 | `kind=think` / 子代理 |
| 👁️ | U+1F441 U+FE0F | DSH `read_image`；VS16 必需，否则退成灰黑文本眼 |
| 🎯 | U+1F3AF | DSH `todo_write` |
| 🔌 | U+1F50C | DSH MCP 服务器工具 |
| 🧩 | U+1F9E9 | `kind=other` / 未命中兜底 |
| 📋 | U+1F4CB | 计划清单 `TodoCard` |
| 🧠 | U+1F9E0 | 思维链 `ThinkingCard` |
| ❓ | U+2753 | DSH `QuestionCard` 预留（未接分派） |

完整字形范围见 `维护手册/Noto彩色Emoji图标目录.md`；卡片实际用 Noto Color Emoji 兜底渲染。

## 6. 改代码后怎么同步

| 改动 | 要同步的文档 |
|:---|:---|
| 公共 `_KIND_ICONS` 增删/换图 | 本 README 第 2 节 + 三家文件的 `kind` 表 |
| DSH `_header_icon` / 工厂 `header_icon` | `DSH工具与图标.md` 的具名表与优先级 |
| 任一厂商 `_TOOL_NAME_CARDS` | 对应厂商文件的「工厂具名」表 |
| DSH `_FALLBACK_TOOL_KINDS` | `DSH工具与图标.md` 的 19 个工具名表 |
| Kimi `WIRE_TOOL_KINDS` | `Kimi工具与图标.md` 的「子代理 wire」小节 |
| 新增后台厂商 | 本目录新增一份文件，并更新 README 总览 |

校验入口：

```bash
UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/shot_vendor_cards.py --vendor all
```

- `tests/test_dsh_card_icons.py` 锁定 DSH 每个卡型的图标文本（含 👁️ 两码点）。
- `tests/test_vendor_card_isolation.py` 锁定同名工具不跨厂商误入。
- DSH 图标字符串里 `U+1F441` 必须写 `\U0001F441\uFE0F`（带 VS16），否则会退成灰黑文本眼。

---

*创建于 2026-11-21 | 图标选择与分型依据 `work plans/2026-1121-0200_...计划.md` §10*
