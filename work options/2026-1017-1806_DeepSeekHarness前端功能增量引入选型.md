> **状态**：已确认（2026-10-17 需求方勾选拍板：A2 + A4 + A5，落地见 `work plans/2026-1017-1840_聊天状态动画与token用量统计增量落地计划.md`）；2026-10-17 19:24 完成二次筛选（按 Kimi 后台适配性，详见 §1.5 与 §七）
> **范围**：参考 `参考代码/deepseek-harness/` 的 Web/桌面前端，为本项目前端挑选可增量引入的功能
> **时间**：2026-10-17 18:06（创建）/ 2026-10-17 19:24（二次筛选）(UTC+8)
> **优先级**：中

# DeepSeek Harness 前端功能 增量引入选型

## 一、背景与约束

### 1.1 目的

`参考代码/deepseek-harness/` 是一套成熟的 Agent 前端（Web 版 `参考代码/deepseek-harness/apps/web/` + 桌面版 `参考代码/deepseek-harness/apps/desktop/`，真实界面全部由 `参考代码/deepseek-harness/packages/client/` 下约 60 个 UI 插件包组成）。本文档将其**用户可见、可操作**的界面功能逐项列出并附勾选框，供需求方圈选；圈选结果将作为后续 `work plans/` 落地计划的输入。

### 1.2 引入原则：增量、正交、可回退

需求方明确要求：**只做增量，不正交耦合，不替换现有界面**。据此，所有入选功能落地时统一遵守：

1. **新增不改动**：每个功能以独立面板/独立卡片类型/独立菜单项加入，不修改现有组件的既有行为与交互路径；
2. **可独立开关**：每个功能有独立启用入口（设置项或菜单），关掉后与引入前行为一致；
3. **可单独移除**：不喜欢可整块拆除，不留残桩；
4. **复用优先**：与本项目已有能力重叠的，优先增强现有实现而非另起炉灶（条目中标注"增强"）；
5. **逐项落地**：每个勾选项单独一个落地计划，单独验收，互不阻塞。

### 1.3 本项目现有家底（用于判断"新增/增强"）

- 技术栈：Python 3.12 + PySide6（QWidget 体系），强制使用项目 `.venv`
- 左栏：会话面板 + 文件浏览器（`src/gui/panels/file_explorer/`）
- 中栏：聊天面板（卡片、权限确认、提问桥、思考标题、时间线、附件、模型栏，`src/gui/panels/chat/`）+ 底部终端面板（`src/gui/panels/terminal/`）
- 变更面板（diff，`src/gui/panels/changes/`）、查看器（代码/Markdown/图片/PDF/媒体，`src/gui/panels/viewer/`）
- 菜单体系：文件/编辑/视图/设置/终端/帮助（`src/gui/menus/`）

### 1.4 证据来源说明

每条功能标注 harness 侧证据路径。最权威的"用户可见功能名册"是 `参考代码/deepseek-harness/apps/web/tests/`（约 180 个 e2e 测试，测试名即功能名）与各 UI 包的 i18n 文案文件 `参考代码/deepseek-harness/packages/client/<包名>/src/client/locales.ts`。

### 1.5 Kimi 后台适配基线（二次筛选依据）

本项目主力后台为 **Kimi Code CLI**，经 `src/llm/providers/acp.py` + `src/llm/providers/kimi_acp.py` 实测确认其数据通道如下。二次筛选据此将原清单分为"保留区"（§三，通道可提供数据或纯前端通用）与"暂缓区"（§七，依赖 Kimi 未暴露的通道）。

**通道构成**

| 通道 | 说明 |
|:---|:---|
| ACP 主通道 | 长驻 `kimi acp` 子进程，stdio ndjson JSON-RPC；每个聊天标签一个独立进程（上限 4）；`session/new` / `session/prompt` / `session/cancel` |
| wire 旁路 | 读 `~/.kimi-code/session_index.jsonl` 定位会话落盘 `agents/*/wire.jsonl`，取用量真值与子代理事件（`kimi_acp.py` 的 `_WireSidecar`） |

**Kimi 后台已能提供数据的能力（✅ 可直接做）**

| 能力 | 通道与口径 |
|:---|:---|
| 思维链流 | ACP `agent_thought_chunk`，已接入 `reasoning_heading.py` |
| 工具调用事件 | ACP `tool_call` / `tool_call_update`，含 in_progress 状态、父子层级（toolCallId `父/子` → `parent_tool_call_id`） |
| 子代理事件 | kimi 走 wire 旁路合成帧，渲染进 SubagentCard 内嵌区 |
| todo 列表 | ACP `rawInput.todos` 提取 + 跨调用 diff + changed 高亮，已有 TodoListCard |
| 权限审批 | ACP `session/request_permission` 反向请求 → PermissionQueue 串行弹窗 + 危险命令黑名单 |
| 提问桥 | 双通道识别（`permission_policy.py`），卡内按钮交互 + 弹窗兜底；自由文本不可回传 |
| 上下文占用 | `UsageStats.used/size`：kimi 取 wire `llm.request.maxTokens`，状态行百分比徽章已在 |
| 忙碌排队 | 前端本地已实现（busy 期间登记待发，轮末自动发送，当前仅单条） |

**部分可用（🟡 可做，但口径/交互有缺口，条目内注明）**

| 能力 | 缺口 |
|:---|:---|
| token 用量 | kimi 无 ACP `usage_update` 推送，轮末读 wire `usage.record`；口径仅 `inputOther + inputCacheRead`，**无 output / 缓存写分项** |
| 计划模式 | ACP `plan` update 已映射为 todo 清单展示；**无计划批准/拒绝交互通道** |
| 回合终止原因 | ACP `session/prompt` 响应含 stopReason，是否含 `max_tokens` 落地时验证 |

**未暴露（❌ 对应条目移入 §七暂缓区）**

运行阶段文案（"正在思考/正在读文件"）、上下文压缩事件、生成中转向（steer）、后台任务事件、定时任务（cron）事件。

---

## 二、名词说明

| 术语 | 含义 |
|:---|:---|
| **正交增量** | 新功能与现有功能互不依赖、互不改写，可独立加/减 |
| **Composer** | harness 对"输入框"的称呼，含草稿编辑、附件、命令芯片等 |
| **Trajectory（轨迹）** | 开发者视角的事件时间线视图，展示每次 LLM 请求/工具调用的原始数据 |
| **Dock（停靠）** | 面板可拖拽分栏、合并、浮动的布局机制 |
| **接合点** | 该功能在本项目代码中的落点（新增挂接位置或增强对象） |
| **通道** | Kimi 后台能否提供该功能所需数据：✅ 已具备 / 🟡 部分可用 / ⚪ 纯前端不依赖后台 |
| **复杂度** | 移植到 PySide6 的预估工作量：小（1 个计划内）/ 中 / 大（需拆分多计划） |

---

## 三、功能清单（勾选区·保留区）

> 用法：在想引入的功能前把 `- [ ]` 改成 `- [x]`。"关系"列：**新增**=本项目完全没有；**增强**=在现有组件上扩展。
> 每条含四要素：**功能**（用户可见什么）/ **接合点**（落在本项目哪里）/ **通道**（Kimi 后台数据可得性）/ **复杂度**。

### A. 会话主区（聊天流）

- [ ] **A1 思考过程折叠行** — 功能：AI 推理内容以可折叠行展示，折叠态显示一行概括标题（现有四级正则提取），展开看完整思维链；可叠加分步耗时。证据：`packages/client/ui-chat/src/client/ReasoningRow.tsx`。接合点：增强 `src/gui/panels/chat/reasoning_heading.py` 与卡片轨 ThinkingCard。通道：✅（`agent_thought_chunk` 已接入）。关系：增强。复杂度：小
- [ ] **A2 运行中状态动画** — 功能：生成中显示动态状态（微光/动画指示 + 当前阶段文案），明确区分"AI 工作中"与"卡死"。证据：`packages/client/ui-chat/src/client/RunningStatus.tsx`。接合点：新增独立状态组件，挂 `panel.py` 状态行；阶段文案由本地工具卡 in_progress 事件近似驱动。通道：🟡（无后台阶段文案接口，动画纯前端，文案取本地工具事件）。关系：新增。复杂度：小
- [ ] **A3 回合导航器** — 功能：长会话内按"回合"快速跳转（上一回合/下一回合按钮，可选快捷键），避免长列表反复滚屏。证据：`packages/client/ui-chat/src/client/TurnNavigator.tsx`。接合点：新增，作用于 `transcript.py` 卡片轨的滚动定位（用户气泡即回合锚点）。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **A4 单回合 token 用量面板** — 功能：每回合末尾展示本回合 token 用量与缓存命中情况。证据：`packages/client/ui-chat/src/client/TurnUsagePanel.tsx`。接合点：新增回合末卡片，数据源为 `kimi_acp.py` 轮末读取的 wire `usage.record`（2s QTimer 轮内轮询 + 轮末真值）。通道：🟡（口径仅 input + 缓存读，无 output/缓存写分项，面板按可得口径设计）。关系：新增。复杂度：中
- [ ] **A5 会话统计胶囊** — 功能：会话头部显示回合数/步数/缓存命中率胶囊，点开展开明细（LLM 耗时、工具耗时等）。证据：`packages/client/ui-chat/src/client/StatsPills.tsx`。接合点：新增会话头部组件，数据源同 A4（wire 旁路 + 前端本地计时）。通道：🟡（TTFT/TPS 需 wire.jsonl 中有时间戳可算，落地时验证字段）。关系：新增。复杂度：中
- [ ] **A7 输出达上限提示 + 一键继续** — 功能：输出 token 到顶、回复被截断时，会话流中提示并可点"继续"让 AI 接着写。证据：`packages/client/ui-chat/src/client/locales.ts`。接合点：新增截断提示卡 + "继续"按钮（发一条约定的续写 prompt）。通道：🟡（依赖 ACP `session/prompt` 响应 stopReason 含 max_tokens，落地先验证 kimi 是否上报；若无则不可用，届时转入暂缓区）。关系：新增。复杂度：小
- [ ] **A8 消息操作条** — 功能：每条消息悬停浮现操作条：复制全文、复制为 Markdown 等。证据：`packages/client/ui-chat/src/client/MessageIconActions.tsx`。接合点：新增，挂 `transcript.py` 各 Block 的 hover 事件。通道：⚪ 纯前端（"分支为新对话"在 ACP 无 fork 接口，操作条内不含此项）。关系：新增。复杂度：小
- [ ] **A9 消息点赞/点踩 + 反馈对话框** — 功能：对 AI 回复打好评/差评，差评弹出分类对话框（任务结果/指令理解/交互/稳定性等），反馈存本地日志供回溯。证据：`packages/client/ui-message-feedback/src/client/`。接合点：新增，操作条（A8）扩展项 + 本地 JSON 落盘。通道：⚪ 纯前端（harness 侧"附会话日志提交"为上传遥测，本项目改为本地留存）。关系：新增。复杂度：中
- [ ] **A10 密钥自动打码** — 功能：用户已发送消息中识别出的凭据（API key、token、密码形态字符串）以遮罩显示，点击切换显隐，防止截图/旁观者泄露。证据：`packages/client/ui-secret-mask/src/client/`。接合点：新增，作用于 `transcript.py` UserBubbleBlock 渲染前的正则脱敏层。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **A11 工作细节详略档位** — 功能：会话流渲染密度四档（紧凑/标准/详细/详尽），控制工具卡默认开合、思维链显隐、时间线密度。证据：`packages/client/ui-chat/` 的 transcript-view 设置项。接合点：新增全局设置项，作用于 `cards.py` OpenStateMap 默认值与 `transcript.py` 渲染开关。通道：⚪ 纯前端。关系：新增。复杂度：中

### B. 输入框与命令交互

- [ ] **B1 斜杠命令弹窗** — 功能：输入 `/` 弹出可搜索命令菜单，选中即补全。证据：`packages/client/ui-commands/src/client/`。接合点：新增弹窗组件，挂 `input.py`。通道：🟡（ACP 有 `available_commands_update` 机制，落地先验证 kimi acp 是否广播可用命令；若不广播则改为前端内置命令清单）。关系：新增。复杂度：中
- [ ] **B2 @ 引用菜单** — 功能：输入 `@` 弹出文件/文件夹浏览菜单，选中以芯片形式插入输入框，随消息提交为上下文引用。证据：`packages/client/ui-input-trigger/`、`packages/client/ui-reference/`。接合点：增强 `input.py`（已有 @路径 引用）与 `attachments.py` 芯片行。通道：⚪ 纯前端（引用内容随 prompt 文本下发，后台无感）。关系：增强。复杂度：中
- [ ] **B3 忙碌排队坞** — 功能：AI 忙碌时发出的消息进入可视化队列坞，可查看/编辑/移除/调整顺序，轮末按序自动发出。证据：`packages/client/ui-conversation/src/client/QueueDock.tsx`。接合点：增强 `panel.py` 现有本地排队（当前仅单条待发、不可视），扩展为多条 + 队列 UI。通道：⚪ 纯前端（排队本就在前端实现）。关系：增强。复杂度：中
- [ ] **B5 上下文用量仪表** — 功能：输入框附近实时显示上下文占用（已用/总量、百分比，悬停看构成明细），接近上限变色预警。证据：`packages/client/ui-conversation/src/client/ContextMeter.tsx`。接合点：增强 `panel.py` 状态行现有百分比徽章（数据源 `UsageStats.used/size` 已在）。通道：✅（kimi 取 wire `llm.request.maxTokens`）。关系：增强。复杂度：小
- [ ] **B6 待办列表面板** — 功能：AI 的 todo 列表以常驻面板呈现：完成/进行/待定计数、跨调用变更高亮，不淹没在会话流里。证据：`packages/client/ui-conversation/src/client/TodoPanel.tsx`。接合点：增强——现有 `rawInput.todos` diff 与 TodoListCard 已在，新增独立侧栏面板复用同一数据源。通道：✅。关系：增强。复杂度：中
- [ ] **B7 拖拽悬浮层** — 功能：拖文件进窗口时全屏悬浮提示（将附加 N 个文件、大小/数量限制说明），松手落入附件区。证据：`packages/client/ui-attachment/src/client/DropOverlay.tsx`。接合点：增强 `attachments.py`（现有图片附件 ≤5 张 ≤10MB），补全窗口级 dragEnter 悬浮层。通道：⚪ 纯前端。关系：增强。复杂度：小
- [ ] **B8 图片灯箱** — 功能：点击消息内图片弹出灯箱看原图、缩放、切换多张，而非跳系统查看器。证据：`packages/client/ui-primitives/` 的 ImageLightbox。接合点：增强 viewer 图片查看能力，会话内缩略图（`attachments.py`、MediaReadCard）点击接入。通道：⚪ 纯前端。关系：增强。复杂度：小
- [ ] **B9 语音输入（实验）** — 功能：麦克风录音转文字填入输入框，首次使用引导式设置。证据：`apps/web/tests/voice-input.e2e.ts`。接合点：新增输入区麦克风按钮 + STT 引擎接入（需新增依赖，选型待定）。通道：⚪ 纯前端，但 harness 标注 experimental 且为独立子系统。关系：新增。复杂度：大

### C. 工具调用与审批卡片

- [ ] **C1 通用工具卡片族** — 功能：每个工具调用一张卡：工具名 + IN/OUT 参数、命令行、退出码徽章、运行状态、展开折叠。证据：`packages/client/ui-tool/src/client/GenericToolCard.tsx`。接合点：增强 `cards.py`（CollapsibleCard 外壳与 ToolCard/BashCard 等已在，对齐信息密度与徽章体系）。通道：✅（ACP tool_call 事件流已接入）。关系：增强。复杂度：中
- [ ] **C2 按工具定制视图** — 功能：bash/read/写文件/搜索/web 各有专属展示（read 显示"x/y 行"窗口、搜索显示匹配数与文件分组、web 显示查询词与结果摘要）。证据：`packages/client/ui-tool/src/client/toolviews/`。接合点：增强 `cards.py` 的 `make_tool_card` 分派，逐工具加定制渲染。通道：✅（rawInput/rawOutput 载荷已在事件帧中）。关系：增强。复杂度：中
- [ ] **C3 工具调用树** — 功能：多步调用以树形缩进展示父子关系，子代理内部的调用嵌套在父节点下。证据：`packages/client/ui-tool/src/client/ToolCallTree.tsx`。接合点：新增视图模式；层级数据已在（toolCallId `父/子` → `parent_tool_call_id`，SubagentCard 内嵌区为雏形）。通道：✅。关系：新增。复杂度：中
- [ ] **C4 审批卡片增强** — 功能：审批呈现分级：等待审批态倒计时、"工具请求越权执行"升级高亮、"允许一次/始终允许/拒绝"分级选项。证据：`packages/client/ui-approval/src/client/`。接合点：增强 `permission_dialog.py` + `permission_queue.py`（串行队列、180s 超时兜底、危险命令黑名单已在）。通道：✅（`session/request_permission`）。关系：增强。复杂度：小
- [ ] **C5 提问卡片增强** — 功能：提问卡支持选项+自定义回答、推荐选项标记、多题导航、倒计时自动继续（可暂停）。证据：`packages/client/ui-user-questions/src/client/QuestionComposer.tsx`。接合点：增强 `question_bridge.py` 与 QuestionCard（卡内按钮串行桥已在；自由文本回传 ACP 不支持，"自定义回答"走"Skip 后正文作答"引导）。通道：🟡（选项交互 ✅，自由文本 ❌ 以引导替代）。关系：增强。复杂度：中
- [ ] **C7 权限预设三档** — 功能：只读 / 工作区写入 / 完全访问三档快捷切换，切到全开需风险确认弹窗。证据：`packages/client/ui-permission-presets/`。接合点：增强——在 `llm/permission_policy.py` 之上加预设档（决定哪些审批自动放行、哪些仍弹窗）。通道：🟡（档位逻辑在前端策略层；ACP `session/set_mode` 类机制落地时验证 kimi 是否支持，不支持则纯前端策略实现）。关系：增强。复杂度：中

### D. 变更、diff 与交付

- [ ] **D1 本轮变更文件卡** — 功能：每回合末尾列出本回合改动文件清单（+n/-n 行数、二进制/过大标记），点击直接跳 diff。证据：`packages/client/ui-deliverables/src/client/ChangedFiles.tsx`。接合点：增强 `src/gui/panels/changes/panel.py`，新增"按回合分组"视图与回合末摘要卡。通道：⚪ 纯前端（变更数据来自本地 git/文件监听，与后台无关）。关系：增强。复杂度：小
- [ ] **D2 diff 评审页增强** — 功能：分屏/统一视图切换、自动换行开关、"本轮新建/已删除"文件标记。证据：`packages/client/ui-deliverables/src/client/ReviewTab.tsx`。接合点：增强 `src/gui/panels/changes/` 与 diff 查看组件。通道：⚪ 纯前端。关系：增强。复杂度：中
- [ ] **D3 交付文件卡** — 功能：AI 产出的文件以卡片呈现在会话流中：侧边栏预览、打开所在文件夹、在文件管理器中显示。证据：`packages/client/ui-deliverables/src/client/PresentedFileCard.tsx`。接合点：新增卡片类型（`cards.py` 分派 + viewer 预览联动）。通道：⚪ 纯前端（从工具事件与回复文本中识别产出文件）。关系：新增。复杂度：小
- [ ] **D4 回复中文件引用可点击** — 功能：AI 回复正文里提到的文件路径渲染为可点击链接，点击在查看器打开。证据：`apps/web/tests/produced-file-mentions.e2e.ts`。接合点：增强 `file_links.py`（反引号/@路径 链接化已在，补齐裸路径识别与悬停预览）。通道：⚪ 纯前端。关系：增强。复杂度：小

### E. 侧边栏、面板与布局

- [ ] **E1 可拖拽停靠分栏** — 功能：面板可拖放拆分/合并/浮动/全屏，布局可保存恢复（Dock 体系）。证据：`packages/client/ui-dockkit/`、`packages/client/ui-sidebar-right/`。接合点：增强——现有 QSplitter 固定布局替换为 QDockWidget 体系或自研 dock，属布局层大改，需独立立项。通道：⚪ 纯前端。关系：增强。复杂度：大
- [ ] **E2 内嵌浏览器面板** — 功能：侧栏内嵌浏览器：地址栏/前进/后退/刷新，供查文档、看 AI 给出的链接。证据：`packages/client/ui-sidebar-browser/`。接合点：新增独立面板（PySide6 QWebEngineView，需确认打包体积影响）。通道：⚪ 纯前端。关系：新增。复杂度：中
- [ ] **E3 文档预览面板扩展** — 功能：在现有 viewer 基础上补 Excel/Office 文档预览与缩放控件。证据：`packages/client/ui-sidebar-documentpreview/`。接合点：增强 `src/gui/panels/viewer/`（现有代码/Markdown/图片/PDF/媒体）。通道：⚪ 纯前端（可能需新增解析依赖）。关系：增强。复杂度：中
- [ ] **E6 会话列表增强** — 功能：会话列表显示状态点（运行中/待审批/待回答）、归档/置顶、相对时间。证据：`packages/client/ui-workspace/`。接合点：增强会话面板 + `tabs.py`/`session_store.py`（状态点数据前端本地可得：worker 运行态、PermissionQueue 待审批、提问桥待回答）。通道：⚪ 纯前端。关系：增强。复杂度：中
- [ ] **E7 空会话引导页** — 功能：空会话时显示品牌标语 + 快捷入口（新建会话、打开项目、最近会话），替代空白页。证据：`packages/client/ui-conversation/src/client/EmptyHero.tsx`。接合点：增强 `welcome.py`。通道：⚪ 纯前端。关系：增强。复杂度：小
- [ ] **E8 会话宽度调节** — 功能：聊天区最大宽度可调（窄/中/宽三档），宽屏用户减少长行阅读疲劳。证据：`packages/client/ui-conversation/src/client/ConversationWidthControls.tsx`。接合点：新增视图菜单项，作用于 `transcript.py` 卡片轨最大宽度。通道：⚪ 纯前端。关系：新增。复杂度：小

### F. 设置与偏好

- [ ] **F1 设置中心骨架** — 功能：模态设置窗 + 左侧分类导航（账号/通用/模型/外观等），替代散落的菜单项。证据：`packages/client/ui-settings-general/src/client/SettingsRoot.tsx`。接合点：增强——新增 SettingsDialog，收纳现有设置菜单各项与 `config/settings.json` 持久化。通道：⚪ 纯前端。关系：增强。复杂度：中
- [ ] **F2 外观设置** — 功能：浅色/深色/跟随系统切换 + 正文/代码/终端三组字体字号。证据：`packages/client/ui-theme/`。接合点：增强——现有主题 QSS 体系（`assets/themes/`）加设置界面与运行时切换。通道：⚪ 纯前端。关系：增强。复杂度：中
- [ ] **F3 快捷键编辑器** — 功能：可搜索的快捷键列表、按键录制修改、冲突检测、恢复默认。证据：`packages/client/ui-shortcuts/`。接合点：新增设置页；前提 I1 快捷键体系先建立（当前菜单全部不绑快捷键，仅少量局部绑定）。通道：⚪ 纯前端。关系：新增。复杂度：中
- [ ] **F4 模型选择器增强** — 功能：模型 + 推理强度双列菜单、搜索、分组（按供应商/能力）。证据：`packages/client/ui-model-selection/`。接合点：增强 `model_bar.py`（后台/接口/模型/推理强度四级选择已在，补搜索与分组）。通道：✅（模型清单来自各 provider 注册表 `src/llm/registry.py`）。关系：增强。复杂度：中
- [ ] **F5 链接打开方式偏好** — 功能：http 链接默认在应用内查看器打开还是系统浏览器打开，用户可选。证据：`packages/client/ui-settings-general/` link-opening 项。接合点：新增设置项，作用于 `file_links.py` 与会话流链接点击分派。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **F6 会话导出** — 功能：导出会话为 Markdown/ZIP（含工具卡摘要与附件清单），供分享与归档。证据：`packages/client/ui-commands/` 的 /export。接合点：新增菜单项；`panel.py` 的 `export_session` 已有全量快照数据源（现为内部 JSON 存档），补 Markdown 渲染导出。通道：⚪ 纯前端。关系：新增。复杂度：小

### G. 开发者向可视化

- [ ] **G1 轨迹时间线视图** — 功能：开发者视角事件时间线：可拖拽聚焦、搜索、请求级 token 详情与耗时分解。证据：`packages/client/ui-trajectory/`。接合点：增强 `timeline.py`（现有 ActivityTimeline 活动色块条）；数据源为 wire 旁路 `agents/*/wire.jsonl` 原始帧（kimi 侧真值已在读）。通道：🟡（kimi 靠 wire.jsonl 旁路，字段以落盘为准，落地时核对可用事件类型）。关系：增强。复杂度：大
- [ ] **G2 事件详情多页签** — 功能：单个事件查看 Summary/Raw/Payload/Schema/Timing/Diff 多页签。证据：同 G1。接合点：新增事件详情对话框，配合 G1 或现有 timeline tooltip 升级。通道：🟡 同 G1。关系：新增。复杂度：中
- [ ] **G3 JSON 树查看器** — 功能：可折叠 JSON 树，支持复制值/复制路径/美化，用于查看工具参数与事件载荷。证据：`packages/client/ui-primitives/` 的 JsonTree。接合点：新增通用组件，供 G2、工具卡 IN/OUT 参数区复用。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **G4 连接状态指示与恢复提示** — 功能：后台进程异常退出时给出断线指示与"重启后台"按钮，恢复失败按原因提示而非静默无响应。证据：`packages/client/ui-primitives/` ConnectionIndicator；`apps/web/tests/connection-recovery.e2e.ts`。接合点：新增状态行指示器，监听 `acp.py` 的 Popen 子进程退出码与心跳超时。通道：⚪ 纯前端（进程健康前端可自查）。关系：新增。复杂度：小

### H. 桌面端与系统集成

- [ ] **H1 系统托盘三件套** — 功能：托盘图标 + 关窗隐藏到后台（首次弹确认）+ 退出前若有运行中任务弹原生确认框。证据：`apps/desktop/src/tray.ts`、`background-notice.ts`、`quit-confirmation.ts`。接合点：新增 QSystemTrayIcon + 主窗口 closeEvent 拦截（运行中任务状态取自各标签 worker）。通道：⚪ 纯前端。关系：新增。复杂度：中
- [ ] **H2 检查更新与更新弹窗** — 功能：检查中/下载进度/安装并重启，有任务运行时中断警告。证据：`apps/desktop/src/update-dialog.ts`。接合点：新增菜单项 + 更新通道接入。通道：⚠️ 依赖发布渠道（当前 AppImage 分发，无更新服务器，需先立发布体系）。关系：新增。复杂度：大
- [ ] **H3 原生"打开方式"** — 功能：右键/菜单提供"在文件管理器中显示""用系统终端打开工作区""默认应用打开"。证据：`packages/client/ui-open-in-app/`。接合点：新增——文件浏览器与 viewer 右键菜单扩展（`tests/test_context_menu.py` 对应组件）。通道：⚪ 纯前端（QDesktopServices / xdg-open）。关系：新增。复杂度：小
- [ ] **H4 致命错误恢复对话框** — 功能：崩溃时弹窗（错误摘要 + 诊断报告路径），提供退出/重启选择，而非直接消失。证据：`apps/desktop/src/fatal-recovery.ts`。接合点：新增——`main.py` 层 sys.excepthook + 子进程看门狗。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **H5 启动加载页** — 功能：启动时显示进度页；某模块加载失败按模块名+原因显示，而非白屏等待。证据：`packages/client/web/src/boot-page.ts`。接合点：新增 QSplashScreen + 启动分阶段上报（`main.py` 初始化拆步）。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **H6 单实例运行** — 功能：重复启动时聚焦已有窗口，而非开第二个实例抢配置锁。证据：`apps/desktop/src/single-instance.ts`。接合点：新增——QLocalServer/QLocalSocket 单实例守卫（与 `config/settings.lock` 现有锁体系协同）。通道：⚪ 纯前端。关系：新增。复杂度：小

### I. 全局交互与无障碍

- [ ] **I1 快捷键体系扩展** — 功能：平台化绑定表：Ctrl+B 侧栏开关、Ctrl+T 新会话、Ctrl+/ 快捷键参考、Esc 停止生成等。证据：`packages/client/shortcuts/src/`。接合点：增强——当前菜单全部无快捷键（`file_menu.py` 选型 §4.4 保持简单），本项即推翻该旧选型，需需求方确认后再动。通道：⚪ 纯前端。关系：增强。复杂度：中
- [ ] **I2 快捷键参考覆盖层** — 功能：可搜索的快捷键一览弹窗（Ctrl+/ 唤起）。证据：`apps/web/tests/shortcuts.e2e.ts`。接合点：新增弹窗；依赖 I1 先有绑定表才有内容可列。通道：⚪ 纯前端。关系：新增。复杂度：小
- [ ] **I3 中西文自动间距** — 功能：正文 CJK/Latin 之间自动加间距，代码区保留原始对齐不动。证据：`packages/client/web/README.md`。接合点：增强 Markdown 渲染层（`src/markdown_it/` 渲染后处理或 QSS 层方案，代码块豁免）。通道：⚪ 纯前端。关系：增强。复杂度：小
- [ ] **I4 数学公式渲染（KaTeX）** — 功能：Markdown 中 LaTeX 公式（$...$、$$...$$）渲染为排版公式。证据：`apps/web/katex-fonts.ts`。接合点：增强 `src/markdown_it/` 渲染管线；Qt 侧需评估方案（QTextBrowser 能力有限，可能走 SVG 预渲染或 QWebEngine 混排）。通道：⚪ 纯前端。关系：增强。复杂度：中

---

## 四、综合速览表

| 分组 | 条目数 | 主要性质 | 整体移植性价比 |
|:---|:---:|:---|:---|
| A 会话主区 | 10（含已拍板 3） | 聊天体验增强 | ★★★★★（小改动多、感知强） |
| B 输入交互 | 8 | 输入效率 | ★★★★（B2/B3/B5 通道全通） |
| C 工具与审批 | 6 | 现有卡片增强 | ★★★★★（多在现有组件上扩展） |
| D 变更与交付 | 4 | diff 体验 | ★★★★（全为纯前端小改动） |
| E 侧边栏布局 | 6 | 面板体系 | ★★★（E1 为大工程，其余可控） |
| F 设置偏好 | 6 | 设置体系 | ★★★★（全为纯前端） |
| G 开发者视图 | 4 | 调试可视化 | ★★★（G1 为大工程，数据源 wire 旁路已在） |
| H 桌面集成 | 6 | 桌面体验 | ★★★★（H1/H3/H4/H5/H6 性价比高） |
| I 全局交互 | 4 | 一致性 | ★★★★ |
| ~~暂缓区~~ | 5 | 后台通道未暴露 | 见 §七 |

---

## 五、关键选型因素

1. **通道缺口已定界**——Kimi 后台未暴露的通道只有五类（§1.5 未暴露清单），对应条目已全部移入 §七；保留区内 🟡 条目（A4/A5/A7/B1/C5/C7/G1/G2）落地计划的第一步均为"验证对应数据口径"，验证不过再降级或转暂缓。
2. **增强优于新建**——C/D 组及 B2/B3/B5 多为现有卡片、diff 面板、排队机制的增强，符合正交增量原则且风险最低，建议优先。
3. **大工程单独评估**——E1（Dock 停靠）、G1（轨迹时间线）、B9（语音）、H2（自动更新）各自相当于一个子项目，建议若非强烈需要则缓行。
4. **实验特性标注**——harness 中标注 experimental 的功能（B9 语音、思考链翻译等）稳定性无承诺，引入需自担维护成本。
5. **I1 与旧选型冲突**——I1 推翻 `file_menu.py` "菜单不绑快捷键"的旧选型（选型 §4.4），勾选 I1 即视为同意推翻，落地计划中需同步更新该注释。

---

## 六、后续流程

1. 需求方在第三节勾选（`- [ ]` → `- [x]`），可附注修改条目描述；
2. 勾选确认后，每个勾选项在 `work plans/` 单独立落地计划（含与现有代码的接合点、开关位置、验收方式）；
3. 落地顺序建议：先"增强类"小项（C/D/A 小组），再"新增类"中项，大工程最后单独立项。

---

## 七、暂缓区（Kimi 后台通道未暴露）

> 以下 5 项依赖 Kimi 后台尚未通过 ACP/wire 旁路暴露的数据通道，前端无数据可渲染，**暂缓引入**。各项注明恢复条件；条件达成后可移回 §三重新勾选。

| 原编号 | 功能 | 缺失通道 | 恢复条件 |
|:---|:---|:---|:---|
| A6 | 上下文压缩卡片（压缩时插入可展开摘要卡） | ACP 无 compaction 事件；wire.jsonl 是否有压缩记录未确认 | 核查 wire.jsonl 落盘是否含压缩事件帧，或 kimi acp 新增对应通知 |
| B4 | 生成中转向 Steer（不停止生成直接插话改走向，Ctrl+Enter） | ACP 仅有 `session/cancel` + 前端 `_turn_lock` 串行轮次，无 steer/续写协议调用 | kimi acp 暴露轮内插话接口；当前可用"排队（B3）+ 停后重发"近似替代 |
| C6 | 计划评审面板（计划卡 + 批准/拒绝/要求修改） | ACP `plan` update 仅映射为 todo 展示，无批准/拒绝回传通道 | 落地前验证 kimi 计划模式批准是否经 `session/request_permission` 到达；若是则可按 C4 同源实现并移回保留区 |
| E4 | 后台任务列表面板（运行中/已完成分组、实时输出、停止） | ACP 无后台任务事件；Kimi CLI 内部有后台任务能力但未对外暴露 | kimi acp 新增任务状态通知，或 wire.jsonl 出现可解析的任务帧 |
| E5 | 定时任务管理页（一次性/间隔/cron 提醒、投递记录） | ACP 无调度接口；Kimi CLI 内部有 cron 能力但未对外暴露 | 同上，待 kimi acp 暴露调度通道 |

---

*创建于 2026-10-17 18:06 (UTC+8)；二次筛选于 2026-10-17 19:24*
