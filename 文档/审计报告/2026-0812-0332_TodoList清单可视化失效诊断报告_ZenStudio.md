# Zen Studio TodoList 清单可视化失效诊断报告

> **⚠️ 归档状态**：本文档撰写于 2026-0812-0332，记录当时的实施状态。
> 文档中提及的"已知问题"和"待办项"可能已在后续修复，请勿以本文档推断当前代码。

> **状态**：已确认（根因定位完成，修复待审阅）
> **范围**：`llm/providers/acp.py`（协议层 todos 提取）、`llm/base.py`（载荷契约）、`gui/panels/chat/cards.py`（TodoListCard 渲染）、`gui/panels/chat/panel.py`（新轨放行）、`scripts/shot_tool_cards.py`（mock 验证）
> **时间**：2026-08-12 03:32（UTC+8，编写）
> **优先级**：中（可读性缺陷，不阻塞功能）
> **关联**：`2026-0808-0627_TodoList清单可视化渲染计划.md`（被诊断对象）；`2026-0811-0841_集成终端环境变量污染问题报告_ZenStudio.md`（同格式前例）

---

## 1. 问题现象

用户在 kimi 后端真机会话中发现：TodoList 工具调用卡片**仍然**渲染为普通 McpCard——

- 入参区显示 `todos: [{"status": "in_progress", "title": "阶段1：…"}]` JSON 原文；
- 无结构化清单（☑/▶/☐ 状态图标、完成项删除线、x/y 进度副标题、变更高亮）；
- 出参区 "Current todo list" 回执文本未过滤。

即 `2026-0808-0627_TodoList清单可视化渲染计划.md`（下称"0808 计划"）的验收项 A1 在真机上完全不成立，与计划落地前观感无异。

---

## 2. 结论摘要（TL;DR）

0808 计划的代码**全部落地且逻辑自洽**，mock 验证 19 场景全过；失效根因是**一处字段名失配**：

> kimi 系后端的 TodoList 载荷条目使用 `title` 字段承载条目文本，而协议层提取函数 `_extract_todo_entries` 只收录 `content` 字段（kilocode/opencode 系 `todowrite` 的字段名）为字符串的条目。kimi 的每条 todo 在提取时被**静默跳过**，提取结果恒为空列表，下游"清单载荷 → 专用卡渲染 → 入参 JSON 抑制"全链路从源头断流，按设计的"空清单静默降级"路径退化为普通 McpCard。

这是一例典型的"mock 绿、真机红"：mock 场景按 kilocode 形态（`content` 字段）构造，与提取口径自洽所以全过；0808 计划 §7 风险表第一行明确预警过"kimi update 帧 rawInput.todos 实证"须以帧存档复核字段路径，该复核在实施时未执行，真机验收 A1/A2/A3 亦按计划遗留、从未进行。

**排除项**：运行环境（AppImage 新旧、进程版本）已逐项排除，不是"旧包未更新"问题——证据见 §5.2。

---

## 3. 失效链路全图

真机一帧 `session/update`（`tool_call_update`，kimi 首帧空壳、载荷随 in_progress 帧迟到）在代码中的实际旅程：

```
# llm/providers/acp.py
_map_tool_call_update
  ├─ rawInput.todos 检出：isinstance(raw_input.get("todos"), list)   ✅ 命中（list）
  ├─ _extract_todo_entries(raw_input["todos"])
  │     └─ 逐条目判收：isinstance(item.get("content"), str)          ❌ 全体跳过
  │        （kimi 条目为 {"status": ..., "title": ...}，无 content 键）
  │     └─ 返回 []                                                    ← 断流点
  ├─ if entries := ... : payload["todos"] = entries                  ❌ 空列表，不置载荷
  └─ （同帧）_format_input_detail 走 "other" 白名单含 "todos" 键
        └─ json.dumps 原文装入 input_detail                          ✅ 入参区 JSON 照常上屏
```

渲染层随之连锁退化：

```
# gui/panels/chat/cards.py
make_tool_card → _TOOL_NAME_CARDS 分派 "todolist" → TodoListCard     ✅ 分派本身正常
TodoListCard.__init__
  ├─ 载荷无 "todos" → 清单区不建                                     ❌ 无清单
  └─ 空清单防御条款：无可视化则入参 JSON 不抑制（保留原文兜底）         ❌ JSON 原文残留
```

最终呈现 = 普通 McpCard + 入参 JSON 原文，与截图逐项吻合。**链路中没有任何一环报错**——每一环都在按"结构不符静默跳过 / 空清单静默降级"的防御设计运行，失效完全无声。

---

## 4. 根因详述

### 4.1 字段名失配（直接根因）

提取函数的收录判据（`llm/providers/acp.py`）：

```python
# llm/providers/acp.py
def _extract_todo_entries(items: object) -> list[TodoEntry]:
    """plan.entries / rawInput.todos → TodoEntry 列表（两通道同构，F1）。

    仅收录 content 为字符串的条目；status/priority 为字符串才保留
    （渲染层按缺省 pending 容错），结构不符的条目静默跳过。
    """
    ...
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        entry = TodoEntry(content=item["content"])
```

而 kimi 真机载荷（用户截图入参区实证）：

```json
todos: [{"status": "in_progress", "title": "阶段1：主题系统（tokens+stylesheet+菜单切换+持久化）"}, ...]
```

条目文本键为 `title`，无 `content` 键 → `isinstance(item.get("content"), str)` 恒为 False → 逐条 `continue` → 返回空列表。

对照两系字段命名：

| 后端系 | 工具名 | 条目文本字段 | 状态字段 | 优先级字段 |
|:---|:---|:---|:---|:---|
| kilocode / opencode | `todowrite` | `content` | `status` | `priority` |
| kimi | `TodoList` | **`title`** | `status` | 无 |

`_extract_todo_entries` 的判据只为 kilocode 形态而写，函数 docstring 中"两通道同构（F1）"指的是 plan 通道与 rawInput 通道同构，并不覆盖后端间的字段名差异。

### 4.2 为什么失效无声（设计放大因素）

链路上三处防御性设计各自合理，叠加后把"字段名失配"完全掩盖：

| # | 设计 | 位置 | 单独看 | 叠加效果 |
|:---:|:---|:---|:---|:---|
| 1 | 结构不符的条目**静默跳过** | `_extract_todo_entries` | 防脏数据破卡 | 失配无任何日志/异常 |
| 2 | 空提取结果**不置载荷** | `_map_tool_call_update` 的 `if entries :=` | 防空载荷刷屏 | 渲染层无从区分"无 todos"与"todos 提取失败" |
| 3 | 空清单**静默降级**为普通卡、入参 JSON 不抑制 | TodoListCard 空清单防御条款 | 保留原文兜底不丢信息 | 视觉上与"功能不存在"完全一致 |

三层"静默"叠加 = 零报错、零日志、零视觉异常痕迹，只能靠真机对照验收发现。

### 4.3 为什么 mock 全过（验证盲区）

0808 计划 T3 的全部 mock 场景（`scripts/shot_tool_cards.py` 场景 04 改写 + 16/17/18 新增）按 kilocode 形态构造 `rawInput.todos`（`content` 字段）——与提取口径自洽，必然全过。kimi 真实形态（`title` 字段）从未进入任何 mock 场景。

更关键的是：0808 计划 §7 风险表第一行原文预警——

> ⚠️ **kimi update 帧 rawInput.todos 实证**：截图入参区已有 todos JSON……间接实证 update 帧 rawInput 携带 todos；**但 T1 实施时仍须以 `.temp/frame_archive/` 帧存档复核字段路径（防嵌套层级差异）**

该预警精准命中了本次事故的类别（字段路径差异），但实施时未执行复核。旁证：`.temp/frame_archive/` 内最新帧存档停留在 2026-08-07（AskUserQuestion 专项），0808 计划实施期间（08-08）没有留下任何真机帧存档——"间接实证"被当成了"已实证"。

同时，计划 R2 修订记录自述"遗留：真机验收 A1/A2/A3 待做"——**真机验收从未进行**，本次用户截图即是迟到的 A1，直接不通过。

---

## 5. 排查过程与排除项

### 5.1 计划落地状态核对

0808 计划 R2 修订记录声称的落地内容逐项核对，**全部在库**：

| 计划项 | 核验结果 |
|:---|:---|
| `_apply_todo_diff` 跨调用 diff（same 口径、sessionId 键控 `_last_todo_snapshots`） | ✅ 在库 |
| 首帧特判撤销 + 首帧/update 帧双通道 todos 提取 | ✅ 在库 |
| `_RECEIPT_PATTERNS` 增补 kimi「Current todo list」回执过滤 | ✅ 在库（因载荷断流无机会触发） |
| `panel.py` `_allow_progress_frame` 增补 todos 放行 | ✅ 在库 |
| `cards.py` TodoListCard + `_TOOL_NAME_CARDS` 双名注册（todolist/todowrite） | ✅ 在库 |
| `_strip_todos_line` 入参 JSON 抑制 + 空清单静默降级 | ✅ 在库（降级路径正是截图所见） |
| 工具名归一化 `_normalize_tool_name`（小写化、命名空间前缀剥离） | ✅ 在库，`"TodoList" → "todolist"` 分派无失配 |

即：**分派链、放行链、diff 链全部完好，唯一断点是提取判据**。

### 5.2 运行环境排除（AppImage 新旧嫌疑）

用户运行的是 AppImage 打包版，首要嫌疑本是"包内代码旧于修复"。逐项排除：

| 证据 | 数值 | 判读 |
|:---|:---|:---|
| 源代码落地时间 | `acp.py` / `panel.py` mtime 2026-08-08 07:17；`cards.py` mtime 2026-08-11 08:44 | 修复代码 08-08 已入库 |
| AppImage 构建时间 | `~/AppImages/zen_studio.appimage` mtime 2026-08-11 10:15 | 构建**晚于**代码落地 |
| 打包源路径 | `building/zen-studio.spec`：`PROJECT_ROOT` 推导 + `pathex=[PROJECT_ROOT]`，从本源码树收编 | 构建必然包含新代码 |
| 出问题进程启动时间 | PID 53261，2026-08-12 03:25（截图 03:26 前一分钟） | 进程加载的是 08-11 构建，含新代码 |
| 进程可执行路径 | `/tmp/.mount_zen_stkAlMjB/usr/bin/zen-studio`（AppImage 挂载） | 确认为打包版而非源码直跑 |

结论：**运行中的 AppImage 包含全部修复代码**，"旧包未更新"嫌疑排除，问题在代码逻辑本身。

### 5.3 旁证：入参区 JSON 的来源

截图入参区 `todos: [...]` JSON 来自 0806 计划 T1 的迟到入参回填机制（`_format_input_detail`，`"other"` kind 白名单含 `"todos"` 键，对 list 值 `json.dumps` 原文装入）。该机制与 todos 提取机制**读取同一个 `rawInput["todos"]`**：前者对值不做结构校验（ dumps 一切），后者做 `content` 判收。同一数据源、一处上屏一处断流——这正是"载荷已到达协议层、仅提取判据失配"的直接证据，排除了"ACP 帧路径层级差异（todos 嵌套在别的层级）"的可能。

---

## 6. 修复建议（待审阅，尚未实施）

### 6.1 最小修复（一行量级）

`_extract_todo_entries` 增补 `title` 回退：条目无 `content` 字符串时取 `title` 字符串，归一装入 `TodoEntry["content"]`。归一后内部统一 `content` 存储，下游（diff 比对口径 content+status+priority、`_make_todo_row` 渲染、`_todo_fallback_text` 兜底）**零改动**。

要点：

- 判据从 `isinstance(item.get("content"), str)` 改为"`content` 或 `title` 任一字符串即收录，`content` 优先"；
- 两键均缺时维持静默跳过（防御语义不变）；
- docstring 同步注明"kimi 系 `title` / kilocode 系 `content` 两形归一"。

### 6.2 mock 场景补盲（防"假绿"重演）

`scripts/shot_tool_cards.py` 场景 16/17/18 增补 kimi 真实形态（`title` 字段、无 `priority`）的构造变体——至少保证每个核心场景两系字段名各覆盖一次。没有这一步，6.1 的修复仍然只靠真机碰运气验证。

### 6.3 流程纠偏（纪律层）

- 0808 计划的 A1/A2/A3 真机验收在修复后**必须实际执行并留截图**，关闭"mock 全过即宣布落地"的口子；
- 今后凡协议层按"某家后端字段形态"写提取判据，计划的风险表预警项（如"以帧存档复核字段路径"）应列为**实施前置项**而非备注——本次事故该预警原文命中，却因无强制力被跳过；
- 可考虑在 `.temp/frame_archive/` 补存一份 kimi TodoList 真机帧（修复验证时顺手留档），为后续 kimi 系其他工具的字段形态提供实证基准。

### 6.4 不建议项

- **不建议**在提取失败时加告警上屏——破坏"静默降级"设计基调，且降级方向安全（信息无损，JSON 原文兜底仍在）；
- **不建议**为 `title`/`content` 差异引入"后端字段映射表"抽象——仅两处调用点、一处判据，一行回退足矣（KISS）。

---

## 7. 验收标准（修复后）

| # | 验收项 | 预期 | 验证方式 |
|:---:|:---|:---|:---|
| B1 | kimi 真机 TodoList 调用（本次截图场景复现） | 卡内结构化清单（☑/▶/☐ + 删除线 + x/y 副标题），入参区无 todos JSON 原文 | 真机截图 |
| B2 | kimi 真机一轮多次 TodoList 调用 | 每次一卡、最新快照在底部、变更项醒目 | 真机截图 |
| B3 | mock 两系字段形态 | `content` 形与 `title` 形场景各自全过 | `scripts/shot_tool_cards.py` 截图闭环 |
| B4 | kilocode/opencode todowrite 回归 | 原 `content` 形态行为零变化 | mock 回归 |
| B5 | plan 通道回归 | 会话级 TodoCard 行为零变化 | mock 场景 19 |

---

## 8. 附：本次诊断时间线

| 时刻（UTC+8） | 事件 |
|:---|:---|
| 2026-08-08 07:17 | 0808 计划 T1-T3 代码落地（`acp.py`/`panel.py`/`cards.py`），mock 19 场景全过 |
| 2026-08-08 07:30 | 计划 R2 修订记录标注"真机验收 A1/A2/A3 待做" |
| 2026-08-11 10:15 | AppImage 重新构建（含全部修复代码） |
| 2026-08-12 03:25 | 用户启动新实例，真机会话中 TodoList 仍以 JSON 原文呈现（截图 03:26） |
| 2026-08-12 03:32 | 本诊断完成：根因 = `_extract_todo_entries` 仅认 `content`，kimi 实际为 `title` |
