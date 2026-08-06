# AskUserQuestion 自动选答修复计划

> **状态**：草稿
> **范围**：`llm/permission_policy.py`（决策层）、`llm/providers/acp.py`（协议层）、`llm/base.py`（载荷契约）、`gui/panels/chat/panel.py`（审批路由）、`gui/panels/chat/permission_queue.py`（交互串行化）、`gui/panels/chat/permission_dialog.py`（弹窗面）、`gui/panels/chat/cards.py`（QuestionCard）、`scripts/`（验证脚本）
> **时间**：2026-08-07 01:48（设计，UTC+8）
> **优先级**：高（功能性缺陷：用户选择权被静默剥夺）
> **前序**：`work plans/2026-0806-1712_工具调用卡片渲染修复计划.md`（T5 已建 QuestionCard 渲染骨架，本计划在其上补交互回环）

---

## 1. 背景与症状

AskUserQuestion 工具的设计语义是"agent 提问 → 用户勾选 → 答案回传"。实测症状：

| # | 症状 | 证据 |
|:---:|:---|:---|
| S1 | **用户从未看到选择界面**，问题一闪而过即"完成" | 截图①：卡片直接显示 `✅ 蓝色佛像`（第一个选项） |
| S2 | **被自动选中的恒为第一个选项** | 截图②：四个食物选项自动勾了第一个"牛肉面" |
| S3 | **completed 帧出参 `{"answers": {...}}` 正常到达**，QuestionCard 问答对渲染本身无缺陷——问题出在"答案根本不是用户给的" | 截图①②的 ✅ 行 |

本质：AskUserQuestion 的交互请求被当成普通工具审批，走"自动放行"通道秒回，用户选择权被静默剥夺。

---

## 2. 根因分析

### 2.1 数据链

```
kimi agent 调用 AskUserQuestion
  → 发出 session/request_permission 反向请求（options = 答案选项）
  → llm/providers/acp.py _handle_reverse
  → panel.py _ask_permission（GUI 注入的 permission_handler）
  → llm/permission_policy.py decide_permission 前置决策
  → 判定 allow → select_option_id 自动挑 optionId 回传
  → agent 收到"用户已选第一个选项"，completed 帧带 answers 下传
  → QuestionCard 渲染问答对（此时木已成舟）
```

### 2.2 根因定位

| # | 根因 | 位置 | 对应症状 |
|:---:|:---|:---|:---:|
| R1 | **question 类请求无特判，混入工具审批决策链**：`decide_permission` 按 `toolCall.kind` 判定，AskUserQuestion 的 ACP kind 为 `"other"`，四态模式下（confirm_all 除外）均命中"非 execute 放行"分支 → `DECISION_ALLOW` | `llm/permission_policy.py:118` | S1 |
| R2 | **自动放行的选答策略是"按 kind 优先序挑第一个"**：`select_option_id` 按 `ALLOW_KIND_PREFERENCE = ("allow_always", "allow_once")` 取首个匹配 kind 的 optionId——kimi 把答案选项以 allow 类 kind 编码（实证：第一个选项被选中），于是第一个答案被秒选 | `llm/permission_policy.py:136-145` | S2 |
| R3 | **兜底路径同病**：决策 allow 但无 allow 类选项时降级弹窗（`panel.py:698`）；而当前路径在降级之前就已命中 R2 返回，用户连弹窗都见不到。另：handler 未注入时 `_handle_reverse` 的 C2 语义兜底（`_pick_option(options, "allow_once")`）同样自动选第一个 | `llm/providers/acp.py:959-960` | S1 S2 |
| R4 | **交互载体缺位**：现有交互面只有 `PermissionDialog`（工具审批语义：允许一次/始终允许/拒绝），没有"提问-选项"语义的交互载体；QuestionCard 是纯展示组件，无回传通道 | `gui/panels/chat/` | S1 |

**关键实证依据**：截图中两次提问均自动选中**第一个**选项且无任何弹窗出现，与 R1+R2 链路的预测完全吻合（`select_option_id` 按 kind 优先序取首个匹配项）。

**待证实的抓帧项**：kimi 的 AskUserQuestion `session/request_permission` 帧中 options 的确切 kind 编码（各选项 kind 是 `allow_once` 还是其他值、optionId 与选项文本的映射形态），实施前需抓一份真实帧存档作为改造蓝本（纪律同 0806 计划 §7"mock 与真实数据偏差"）。

---

## 3. 修复方案

总原则：**question 类请求从工具审批决策链中剥离，永不自动放行，强制走用户交互**；交互载体分两步走——先复用审批队列弹专用对话框（快速止血），再升级为卡片内交互（终态体验）。

### T1：决策层 question 特判（修 R1）—— 协议纯逻辑层

**改动点 — `llm/permission_policy.py`**：

```
# llm/permission_policy.py

_QUESTION_TOOL_NAMES = frozenset({"askuserquestion"})  # 归一化后小写名

def is_question_request(params: PermissionParams) -> bool:
    """question 类交互请求识别（title 归一化：去 mcp__ 前缀取末段、小写，
    与 cards.py _normalize_tool_name 同纪律）。"""
    tool_call = params.get("toolCall") or {}
    title = (tool_call.get("title") or "").strip().lower().split("__")[-1]
    return title in _QUESTION_TOOL_NAMES

def decide_permission(params, mode) -> Decision:
    if is_question_request(params):
        return DECISION_ASK, None   # 四态一律不自动放行：答案是用户的，不是策略的
    ...
```

- 特判置于四态分支**之前**——即使 `auto_all`（护栏关闭档）也不自动选答：权限模式管的是"危险操作放不放行"，管不到"替用户回答问题"。这是语义边界，不是配置项。
- 模块零 Qt 纯逻辑定位不变，单测直接覆盖。

### T2：交互载体第一步——QuestionDialog 专用对话框（修 R4，止血）

**改动点 1 — `gui/panels/chat/permission_dialog.py` 新增 `QuestionDialog`**：

| 元素 | 内容 |
|:---|:---|
| 标题 | "AI 提问" |
| 问题区 | 从 `toolCall.rawInput.questions` 提取问题文本逐条展示（缺省时退化为 `params.options` 的 name 列表上方一行通用提示） |
| 选项区 | `params.options` 每个选项一个按钮，文案用 agent 提供的 `name`（**不走 `KIND_LABELS` 审批语义映射**——选项是答案不是审批动作） |
| multi_select | rawInput.questions 带 `multiSelect: true` 时选项区改复选框 + "确认"按钮，回传约定见风险表 ⚠️4 |
| 取消 | 关闭/ESC 返回 None → 上层按拒绝兜底（agent 收到 reject/cancelled，自行处理用户未答场景） |

**改动点 2 — `gui/panels/chat/permission_queue.py`**：

- `_ask_one` 按 `is_question_request(entry[0])` 分派：`QuestionDialog` / `PermissionDialog` 二选一，队列串行化机制（防多模态框互冻、180s 超时兜底、stale 标记）原样复用，零改动。

**改动点 3 — `gui/panels/chat/panel.py`**：

- `_ask_permission` 决策为 ask 且 `is_question_request(params)` 时走 `PERMISSION_QUEUE.ask`（队列内部分派），R2 的 `select_option_id` 自动选答路径对 question 请求**不可达**（T1 已在前置决策拦截）。
- 防线纵深：`select_option_id` 调用点保持不动（非 question 请求语义不变）。

### T3：协议层结构化载荷补全（为 T4 铺路）

当前 `_extract_questions`（`llm/providers/acp.py:560`）只提取问题文本列表，丢弃了选项结构。补全：

| 载荷 | 字段 | 内容 |
|:---|:---|:---|
| `ToolCallPayload` / `ToolUpdatePayload` 新增 | `question_options: list[dict]` | 每问 `{question, options: [{label, description}], multi_select}`，rawInput.questions 原样提取（协议层单点格式化纪律：GUI 不碰 rawInput） |
| `llm/base.py` | 契约补充 | 两 TypedDict 增补可选字段 + docstring |

- 与既有 `questions: list[str]` 并存（QuestionCard 现行渲染不破坏），T4 完成后 `questions` 字段评估退役。

### T4：交互载体终态——QuestionCard 卡片内交互（体验升级，修 R4 终态）

**设计**：pending 态的 QuestionCard body 渲染选项按钮组（单选：按钮；多选：复选框+确认钮），用户点击即回传 optionId，reader 线程的审批等待由按钮回调唤醒。

**打通方式（复用既有串行化骨架）**：

```
panel._ask_permission
  → is_question_request → QUESTION_BRIDGE.ask(params, tool_call_id)
  → GUI 线程：定位 tool_call_id 对应的 QuestionCard，激活选项按钮组
  → 用户点击 → 回调置结果 → Event.set() → reader 线程拿到 optionId 回传
  → completed 帧到达 → 卡片按既有 _fill_answers 渲染问答对
```

- `QUESTION_BRIDGE` 仿 `PERMISSION_QUEUE` 模式：reader 线程阻塞等 `threading.Event`，GUI 线程经 `QTimer.singleShot` 激活卡片交互；超时/卡片缺失（旧会话重放、双轨旧轨）降级 `QuestionDialog` 兜底，再降级拒绝兜底。
- 并发约束：同窗口多个 question 请求串行激活（与审批队列同因：防互冻）。
- 双轨边界：卡片内交互仅新轨可用；旧轨（`output.py` 行文本渲染）走 QuestionDialog 兜底（风险表 ⚠️1 同 0806 计划）。

### T5：抓帧存档与 mock 蓝本（贯穿性验证基建）

- 抓一份真实 kimi AskUserQuestion 全序列帧（tool_call 首帧 → request_permission 反向请求 → 回应 → update 帧 → completed 帧）存档 `docs/` 或 `.temp/` 约定的帧存档位置，作为 T1-T4 的 mock 蓝本与回归基准。
- `scripts/shot_tool_cards.py` 增补场景：
  - `askuser_pending_选项按钮`：pending 态卡片显示选项按钮组（T4 后）；
  - `askuser_multiselect`：多选形态渲染；
  - 既有 `askuserquestion_问答对` 场景保留（completed 渲染回归）。

---

## 4. 实施顺序

| 序 | 任务 | 收益 | 依赖 |
|:---:|:---|:---|:---|
| 1 | **T1 + T2** | **止血**：用户即刻能自己选答案（弹窗形态） | 无，可立即实施 |
| 2 | **T5 抓帧** | 校准 T1 识别条件与 T2 渲染数据源 | 与 1 并行，改造前必做 |
| 3 | **T3** | 载荷补全 | 无 |
| 4 | **T4** | 终态体验：卡片内直接作答 | T1+T2+T3 |

每个任务完成即跑验证闭环（§5），全部完成后真机冒烟。

---

## 5. 验证方案

### 5.1 单元测试（纯逻辑层）

| 用例 | 断言 |
|:---|:---|
| `is_question_request` title 形态 | `"AskUserQuestion"` / `"mcp__xxx__AskUserQuestion"` / 小写混写均命中；`"Agent"` 不命中 |
| `decide_permission` 四态 × question | confirm_all / confirm_execute / auto_guarded / **auto_all** 四态下 question 请求一律 `DECISION_ASK` |
| 非 question 请求回归 | execute/黑名单/other 原四态行为不变 |

### 5.2 mock 截图（渲染层，纪律同 0806 计划 §5）

`.venv/bin/python scripts/shot_tool_cards.py` → 查看 `.temp/card_shots/*.png` → 对照预期打勾。

### 5.3 真机冒烟（验收标准）

| # | 验收项 | 预期 |
|:---:|:---|:---|
| A1 | kimi 后端触发 AskUserQuestion | **弹出选择界面（对话框或卡片按钮），不做任何操作时不自动选答**，agent 阻塞等待 |
| A2 | 选择非第一个选项（如第三个） | 回传的答案是用户所选项，卡片 ✅ 行显示所选答案 |
| A3 | 直接关闭对话框/不回答 | 按拒绝兜底回传，agent 侧表现合理（不崩溃、能继续） |
| A4 | multi_select 问题 | 可勾选多项，确认后全部回传 |
| A5 | 四态权限模式各跑一轮 | 包括 auto_all 在内，question 均不自动选答 |
| A6 | 180s 不操作 | 超时按拒绝兜底（既有 PERMISSION_TIMEOUT_S 语义），agent 不永久挂死 |
| A7 | 普通工具审批回归 | Bash 黑名单命中弹窗、非危险自动放行等行为不变 |

---

## 6. 风险与注意事项

| 符号 | 项 | 说明 |
|:---:|:---|:---|
| ⚠️ | **optionId 与答案的映射** | 回传的是 agent 提供的 optionId 原值（纪律：不臆造）。kimi 的 optionId 是否即答案文本需抓帧证实（T5）；若为 opaque id，agent 自行映射回选项，客户端无需关心 |
| ⚠️ | **multi_select 的回传协议形态** | ACP request_permission 响应模型是"选一个 optionId"。多选如何编码（agent 是否发多个请求/是否有约定 optionId 组合语法）必须抓帧证实，未证实前 multi_select 选项按单选逐个提问处理并留 TODO |
| ⚠️ | **双轨渲染** | 卡片内交互（T4）仅新轨可用；旧轨走 QuestionDialog 兜底。T2 的弹窗方案双轨通用，故止血先行 |
| ⚠️ | **决策层纯逻辑纪律** | `permission_policy.py` 零 Qt 零 IO 定位不变；`is_question_request` 只做 title 判定，不碰载荷深层结构 |
| 🟡 | **QuestionDialog 与 PermissionDialog 的文案分野** | question 选项文案用 agent 提供的 name 原文，禁用 KIND_LABELS 审批语义映射（"允许一次"贴在"牛肉面"上是语义错乱） |
| 🟡 | **旧会话重放** | 会话历史重放时 question 已完成，卡片只渲染问答对，不复活交互按钮（completed 态判定优先） |
| 🟡 | **其他后端的 question 工具** | kilocode/opencode 等后端的同类工具名（如有）后续补入 `_QUESTION_TOOL_NAMES`，集中维护 |
| 🟡 | **协议文档同步** | 载荷新增 `question_options` 后 `dream-acp/protocol/dream-acp-v1.md` 同步补充（同 0806 计划纪律） |

---

## 7. 与 0806 计划的关系

0806 计划 T5 已建成 QuestionCard 的**展示侧**（问答对渲染、questions 迟到回填、answers JSON 解析），本计划在其上补**交互侧**（用户真实作答的回环）。两计划合流后，AskUserQuestion 才形成完整闭环：agent 提问 → 用户作答 → 答案回传 → 卡片呈现。
