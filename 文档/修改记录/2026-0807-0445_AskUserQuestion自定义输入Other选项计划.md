> ⚠️ **归档声明**：本文档为历史快照，记录了特定时间点的决策与实施状态。
> 当前代码状态以对应 `.py` 源文件为准，本文档内容可能已过时，仅供参考。

# AskUserQuestion 自定义输入（Other 选项）计划

> **状态**：已实施（方案 B 引导提示，2026-08-07；原计划 T1/T2 的「Other 输入经 selected 通道回传」被 T0 spike 证伪，见 §8）
> **范围**：`gui/panels/chat/permission_dialog.py`（QuestionDialog）、`gui/panels/chat/cards.py`（QuestionCard 按钮组）、`llm/permission_policy.py`（如需识别自定义回传）、`.temp/`（spike 脚本）、`scripts/`（验证）
> **时间**：2026-08-07 04:45（设计，UTC+8）
> **优先级**：中（体验性缺陷：用户只能二选一/多选一，无法自由作答）
> **前序**：`文档/修改记录/2026-0807-0148_AskUserQuestion自动选答修复计划.md`（已实施：交互回环建成，本计划在其两个交互载体上补自定义输入项）

---

## 1. 背景与症状

kimi agent 侧实证说明（2026-08-07 会话截图）：按 AskUserQuestion 工具接口规范，**每次提问时系统应自动附加一个 "Other" 选项供用户自定义输入**——agent 不传该项，由终端/客户端 UI 渲染附加。agent 原话：

> 按照工具的接口规范，每次提问时系统应该自动附一个 "Other" 选项供自定义输入，不需要我手动添加——它是终端界面渲染出来的。……我只能保证我这边不传 Other 选项（由系统附加），但界面长什么样我控制不了。

我方现状（0148 计划实施后的两个交互载体）：

| 载体 | 现状 | 缺口 |
|:---|:---|:---|
| QuestionDialog（弹窗兜底） | 只渲染 agent 提供的固定选项 + Skip | 无自定义输入入口 |
| QuestionCard 按钮组（卡片内交互） | 同上 | 同上 |

真实帧佐证（`.temp/frame_archive/askuser_*.json`）：`request_permission` 的 options 恒为 `[q0_opt_0…N（allow_once）, q0_skip（reject_once）]`，**确无 Other 项**——与 agent 说法互证：Other 是客户端附加义务，不是 agent 发送内容。

---

## 2. 核心未知项（决定方案形态，必须 spike 先行）

**自由文本的回传协议形态未知**。ACP `session/request_permission` 响应模型是：

```json
{"outcome": {"outcome": "selected", "optionId": "<agent 提供的 optionId 原值>"}}
```

或 `{"outcome": {"outcome": "cancelled"}}`。自由文本如何编码进这个模型，候选形态（按可能性排序，**严禁臆选，必须实证**）：

| 候选 | 形态 | 疑点 |
|:---:|:---|:---|
| H1 | optionId 直接填用户文本原文（如 `"紫色渐变"`） | agent 是否接受非列表 optionId？answers 如何呈现？ |
| H2 | 约定前缀 optionId（如 `"custom:紫色渐变"` / `"other:…"`） | 约定是否存在？需查 kimi ACP 文档/源码或行为取证 |
| H3 | 响应带 `_meta` 扩展字段携带文本 | ACP 是否允许 outcome 挂 `_meta`？agent 是否消费？ |
| H4 | 回 cancelled + 下一轮用户正文输入代偿 | 体验降级，非本规范语义 |

调研入口（按序）：
1. kimi CLI 本机安装目录（`~/.kimi-code/`）查 AskUserQuestion / request_permission 相关源码或文档，找 Other/custom 回传约定的直接证据；
2. kilocode/opencode 参考代码（`参考代码/kilocode-main` 等）同类交互的自由文本回传实现（kilocode ask 类工具有 text 输入先例）；
3. 行为 spike（T0）：改造 `.temp/capture_askuser_frames.py`，handler 返回 H1/H2 候选 optionId，观察 agent 是否报错、completed 帧 answers 如何呈现。

---

## 3. 修复方案（以 spike 结论校准后定稿）

总原则：**Other 项是客户端附加义务，两个交互载体统一附加；回传编码严格按 spike 实证形态，不臆造 optionId**。

### T0：回传协议 spike（前置，阻塞 T1/T2 设计定稿）

- 改造 `.temp/capture_askuser_frames.py`：handler 分别按 H1（文本原文）/H2（约定前缀）回传，抓 completed 帧 answers 与 agent 后续反应（报错/追问/正常采纳）；
- 存档 `.temp/frame_archive/askuser_other_*.json`，作为 T1/T2 的编码蓝本；
- 若 H1-H3 全部不work（agent 报错或吞掉文本），降级 H4 并在 GUI 引导用户改用正文输入（本计划范围外，另立项）。

### T1：QuestionDialog 附加 Other 输入区

- 选项按钮组下方附加一行：「其他…」按钮（或常驻输入行，形态按视觉验证拍板）→ 展开 `QLineEdit` + 「确认」按钮；
- 确认后按 T0 实证编码回传（同 `selected_option_id` 通道，不改动 `_handle_reverse` 响应结构——除非 H3 实证需要 `_meta`，那时协议层同步改）；
- 空文本确认视为未答（等同关闭，返回 None 走拒绝兜底）。

### T2：QuestionCard 按钮组附加 Other 项

- `activate_options` 渲染的按钮组末尾附加「其他…」按钮 → 点击替换为行内输入框 + 确认钮（与选项按钮同组禁点语义：确认后全组定格、✅ 即时反馈显示用户文本前 N 字符）；
- 桥（QUESTION_BRIDGE）回调通道不变——自定义文本经同一 `on_chosen(option_id)` 回传，编码由卡片按 T0 蓝本执行（编码逻辑下沉到协议层纯函数，GUI 不拼协议字符串，纪律同 0148 计划 ⚠️「不臆造 optionId」）。

### T3：completed 渲染兼容核查

- 自定义文本答案的 completed 帧 answers 形态（T0 抓帧确认）若为纯文本值，QuestionCard `_fill_answers` 现行渲染已兼容（非选项文本原样 ✅ 显示）；若形态特殊（如嵌套结构），补渲染分支。

---

## 4. 实施顺序

| 序 | 任务 | 收益 | 依赖 |
|:---:|:---|:---|:---|
| 1 | **T0 spike** | 回传编码实证（方案定稿前提） | 无，可立即实施 |
| 2 | **T1** | 弹窗载体可自由作答 | T0 |
| 3 | **T2** | 卡片载体可自由作答（终态体验） | T0+T1 |
| 4 | **T3** | completed 渲染核查 | T0 |

---

## 5. 验证方案

### 5.1 spike 验证（T0）

| 用例 | 断言 |
|:---|:---|
| H1 文本原文回传 | agent 不报错；completed answers 值即用户文本（或明确拒绝证据） |
| H2 约定前缀回传 | 同上对照 |
| 空文本/特殊字符（引号、换行、emoji） | JSON 帧不破损，agent 行为合理 |

### 5.2 mock 截图（视觉验证闭环，纪律同《视觉验证闭环开发指南》）

`scripts/shot_tool_cards.py` 增补场景：
- `askuser_dialog_other输入`：QuestionDialog 展开 Other 输入行形态；
- `askuser_卡片_other输入`：pending 卡按钮组末尾 Other 项 + 点击后行内输入框；
- `askuser_other_已答`：自定义文本答案的 completed 问答对渲染。

### 5.3 真实 E2E（验收标准）

改造 `.temp/e2e_askuser_bridge.py`：模拟用户走 Other 通道输入「紫色渐变」，断言：
- A1：agent 不报错、轮次正常结束；
- A2：completed answers 值为用户自由文本；
- A3：固定选项路径回归（既有 E2E 不变）；
- A4：QuestionDialog 路径同样可自由作答（降级链路不残缺）。

---

## 6. 风险与注意事项

| 符号 | 项 | 说明 |
|:---:|:---|:---|
| 🔴 | **回传编码臆造风险** | 本计划最大风险点。spike 未实证前**严禁动工 T1/T2**——自由文本编码错配会让用户答案静默丢失（比 0148 计划的自动选答更隐蔽：用户以为自己答了，agent 收到的却是空/乱码） |
| ⚠️ | **kimi 规范的出处核实** | agent 自述的"接口规范"需在本机 kimi 安装目录找到实证（源码/文档），防 agent 幻觉误导设计；找不到则以行为 spike 为准 |
| ⚠️ | **其他后端兼容性** | Other 附加是 kimi 侧规范；kilocode/opencode 等后端若无此约定，客户端统一附加 Other 是否安全（agent 收到非列表 optionId 的反应）需逐后端 spike 或按后端能力位开关 |
| 🟡 | **Other 项位置与文案** | 附加在固定选项之后、Skip 之前/之后（视觉验证拍板）；文案用「其他…」还是 agent 规范的 "Other"（中文化一致性 vs 规范对齐，拍板项） |
| 🟡 | **multi_select 交互** | 0148 计划实证多选经 ACP 降级单选；Other 输入与多选无交集，不引入复选框 |
| 🟡 | **协议文档同步** | 若回传编码涉及协议层改动（H3 或新约定函数字段），`dream-acp/protocol/dream-acp-v1.md` 同步补充 |

---

## 7. 与 0148 计划的关系

0148 计划归还了用户的**选项选择权**（不自动选答）；本计划归还用户的**自由作答权**（不被固定选项禁锢）。两者合流后 AskUserQuestion 交互才完整覆盖 kimi 侧接口规范：固定选项 + Skip + Other 自定义输入。

---

## 8. 实施记录（2026-08-07，T0 spike → 方案 B 降级）

### 8.1 T0 spike 结论：回传通道证伪（H1-H3 全灭）

spike 脚本 `.temp/spike_askuser_other.py`，对真实 `kimi acp` 逐一实证四种编码
（抓帧存档 `.temp/frame_archive/askuser_other_{h1,h2,h3a,h3b}_*.json`）：

| 候选 | 编码 | 实测结果 |
|:---:|:---|:---|
| H1 | optionId = 文本原文（`"紫色渐变"`） | ❌ completed `answers:{}`，note "User dismissed"——未知 optionId 被**静默视为 dismiss** |
| H2 | optionId = `custom:紫色渐变` | ❌ 同上 |
| H3a | 无效 optionId + outcome/result 双位 `_meta.customText` | ❌ 同上 |
| H3b | 合法 optionId（q0_opt_2）+ `_meta.customText` | ❌ answers 落选项名「绿色渐变」，`_meta` 被完全忽略 |

补证链：
- kimi 官方文档（tools.html）确证「系统自动附加"其他"选项」——但那是 kimi **自家 TUI/web 客户端**的内部通道，ACP 适配层不外露；
- changelog 0.23.0：「AskUserQuestion 的回答以问题文本与选项标签回传给模型……现有客户端仍以选项 id 作答」——ACP 侧只有 optionId 一个槽位，适配层校验 optionId ∈ 请求选项集，表外值静默丢弃；
- kilocode 先例（`question.shared.ts`：custom 文本原文直接进 answers 槽位）成立，但那是 kilocode 自家 question 协议，非 ACP request_permission；
- 本机 kimi 0.29.1 → 升级 0.34.0（当日最新）复测 H1，结论不变；
- kimi acp 文档：不稳定面 19 个方法仅接入 `session/set_model`，elicitation 未接——协议层确无第二通道。

**结论**：kimi ACP（≤0.34.0）的 request_permission 通道无法回传自由文本。原计划 T1/T2 的「Other 输入经 selected_option_id 通道回传」不成立；若强行附加输入框，用户答案将静默丢失（比 0148 自动选答更隐蔽）。🔴 风险实证兑现。

### 8.2 降级方案 B（用户拍板）：仅加引导提示

- `permission_dialog.py`：新增模块级常量 `OTHER_HINT_TEXT`（单一文案来源）；QuestionDialog 按钮组上方加弱化色引导行（`CHAT_PACK["tool_fg"]`）；
- `cards.py`：QuestionCard `activate_options` 按钮组末尾加同款引导行（`self._colors.tool_fg`），文案复用 `OTHER_HINT_TEXT`；
- 引导语义与既有机制对齐：「Skip」（reject_once 选项，弹窗/卡片均有）→ agent 收到 skip/dismiss 后正文追问（spike 实测行为）→ 用户聊天输入框正文作答；
- T3 顺带修复：`QuestionCard._on_completed` 对 dismissed 终态（`answers:{}` + `note`）渲染 `⏭ + note` 原文，替代裸 JSON——方案 B 引导后 dismissed 成为常态终态，此渲染缺口必修。

### 8.3 验证结果

| 验证 | 结果 |
|:---|:---|
| `scripts/test_question_permission.py` | 全部断言通过（回归无损） |
| mock 截图 07（pending 卡）/ 10（弹窗） | 引导行形态视觉确认 ✅（`.temp/card_shots/`） |
| mock 截图 11（新增 dismissed 场景） | `⏭ User dismissed…` 渲染确认 ✅ |
| 真实 E2E `.temp/e2e_askuser_bridge.py` | PASS：固定选项路径回归无损 |

### 8.4 遗留（另立项候选）

- 方案 A（取消 + 自动把自定义文本补发为下一条用户消息）功能上可实现答案必达，但改变轮次语义、completed 卡仍显示 dismissed，本次未采纳；若未来 kimi ACP 接入 elicitation 不稳定面或开放自定义回传编码，应重启 T1/T2 原始设计（本计划 §3 蓝本仍有效）。
