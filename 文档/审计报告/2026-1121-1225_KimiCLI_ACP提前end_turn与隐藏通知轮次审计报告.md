# Kimi Code CLI ACP 轮次提前 end_turn、追问延迟执行与隐藏通知轮审计报告

> **状态**：上游 ACP 轮次契约缺陷已定位；客户端未使用官方 `session/close` 的关闭缺口已确认。2026-11-21 裸 ACP 探针已实测：2.7.1 声明 `sessionCapabilities.close`，close 能停通知轮并回收后台子进程；`session/cancel` 不覆盖通知轮。待双方分别修复
> **范围**：Kimi Code CLI 2.7.1（ACP 模式）、Zen Studio 1.8.0.565（ACP 客户端）、后台任务完成通知、用户追问排队、轮次收尾、关闭/取消
> **时间**：2026-11-21 12:25（UTC+8，编写）
> **优先级**：高（界面假完成、后台持续写文件、用户无法继续、新任务与旧任务并发）
> **事故会话**：`session_ea2c9889-2849-49a3-bd3f-ba2010051304`
> **报告目的**：提交 Kimi Code CLI 官方定位并修复；附裸 ACP 最小复现、完整证据时序与期望契约
> **关联计划**：`work plans/2026-1121-1236_KimiACP关闭能力与通知轮次最小探针验证计划.md`
> **脱敏声明**：本文不含凭证、密钥、Token、完整文件内容；本机路径以 `~` 或 `<WORKSPACE>` 表示；仅保留与故障相关的用户 prompt 原文。报告可直接转交上游。

---

## 1. 结论摘要

本次不是网络断线，不是 ACP 管道断裂，也不是 IDE 杀掉了 Kimi 进程。直接故障是 **Kimi Code CLI 2.7.1 在 ACP 模式下把“后台任务完成通知”拆成了一个不暴露给客户端的内部轮次，并在原用户轮仍处于 `tool_use` 时提前向 ACP 返回 `end_turn`**。

由此产生三段可复现的异常：

1. **提前完成**：用户轮最后一步 `step.end` 为 `finishReason=tool_use`，模型刚发出 8 个工具调用并拿到结果，CLI 却在 `step.end` 后 1 毫秒写入 `turn.ended reason=completed`，3 毫秒内向 ACP 返回 `stopReason='end_turn'`。随后 CLI 以 `origin.kind=task`、`trigger=notify` 启动内部轮次继续执行工具。
2. **响应先于执行**：用户在 12:07:34 发送追问“为什么停止了呀？”，CLI 在 8 毫秒内返回空 `end_turn`；该追问实际在 **16 分 11 秒后**（12:23:45）才作为 `turnId=2` 真正进入 wire 执行，12:23:56 才产生回答。客户端早已把该轮按完成收尾，无法把迟到的执行与回答关联回原请求。
3. **隐藏轮缺少轮次级句柄**：通知轮不是客户端发起的 `session/prompt`，没有 ACP 请求 ID；客户端在界面上已经“完成”，无法按轮取消。官方提供了会话级的 `session/close`（中断进行中的 turn），但 Zen Studio 当前没有实现它；这是客户端侧的关闭缺口。该轮在 UI 完成之后继续运行 **19 分 47 秒**，期间修改源码、跑测试、截图，并在 12:23:05 弹出“工作区被外部重置，是否重新应用”的确认。用户 12:23:19 选择「暂停实施」后，轮次直到 12:23:45 才结束；随后排队的追问仍然被执行。

用户所描述的“我关了 Kimi，它没有真正停止，还在后台；我新做任务时它还在跑”，与上述第三条对应：上游隐藏轮没有轮次级句柄，客户端又没有使用官方 `session/close`，所以关闭没有真正终止它。用户在看到界面“完成”后开始新任务，是合理反应；根因是界面完成信号与 CLI 真实执行状态不一致，而不是用户误操作。

### 1.1 官方文档核对（2026-11-21 补查）

补查官方文档后，需要修正一个责任判断：

- 官方 `kimi acp` 参考页明确支持 `session/cancel`（中断当前 turn）与 `session/close`（尽力拆除：中断进行中的 turn、释放会话级资源并关闭存活会话；未知 id 不算错误）。**2026-11-21 裸 ACP 探针已确认**：2.7.1 的 `initialize` 声明 `sessionCapabilities.close={}`；`session/close` 能停住 `origin.kind=task/trigger=notify` 的通知轮并回收后台子进程；`session/cancel` 不覆盖该通知轮。事故当时客户端没有读取/记录该能力位，本次已补测（见 9.5）。
- 官方 2.7.1 changelog 明确写着“Keep queued prompts pending until background notification turns finish”，说明追问排队到通知轮结束是官方有意设计，不是随机丢消息。
- 但 ACP 规范对 `session/prompt` 响应的定义是：v1 的响应就是 turn 的结束；v2 的响应只确认消息已插入，且明确写着“仅仅接收、排队或分配 ID 不足以返回成功响应”。本次 12:07:34 的 `end_turn` 发生在追问尚未插入 wire（12:23:45 才出现 `turn.prompt`）之前，因此即使按 v2 语义也不成立。
- Zen Studio 当前没有实现 `session/close`：`KimiProvider.close()` 走 `AcpConnection.terminate()`（SIGTERM），停止按钮走 `session/cancel`；而 `cancel_turn()` 在 `_turn_id=None` 时直接返回 False。12:03:57 的 `end_turn` 已经把 `_turn_id` 置空，所以之后的停止/关闭没有可取消的轮次。

因此责任分成两层：**上游负责提前 `end_turn` 与“未插入却回成功”的轮次契约；客户端负责“没有使用官方 `session/close`、停止按钮在 `_turn_id=None` 时 no-op”的关闭缺口。** “关了还在跑”的直接原因在客户端这一层；不能把它写成官方低级错误。2026-11-21 裸 ACP 探针已实测：`session/close` 能中断通知轮并回收后台子进程（9.5），因此“补上 close 后仍停不住”不再作为待验证的上游关闭缺陷；上游仍需修提前 `end_turn` 与响应先于执行。

需要明确区分责任边界：上述 1–3 中，1、2 是上游轮次契约问题；第 3 条“隐藏轮缺少轮次级句柄”在协议层确实没有轮次级请求 ID，但官方提供了 `session/close` 作为会话级终止入口，而 Zen Studio 没有实现它。因此“关了还在跑”不能全部归因于 Kimi：客户端缺少官方关闭路径是直接原因之一。报告将两者分开，既不把客户端缺口算作上游缺陷，也不让上游用“你们没调 session/close”掩盖提前 `end_turn` 与响应先于执行的问题。

---

## 2. 环境与证据

### 2.1 运行环境

| 项 | 值 |
|:---|:---|
| 客户端 | Zen Studio 1.8.0.565（冻结态，日志落在 `~/.config/zen-studio/logs/`） |
| CLI | Kimi Code CLI 2.7.1 |
| 模型 | `kimi-code/k3.1-pro` |
| 平台 | Linux x86_64 |
| ACP 连接 | `generation=3`，日志记录子进程 `pid=43922`，启动于 12:02:56.554 |
| 会话 | `session_ea2c9889-2849-49a3-bd3f-ba2010051304` |
| 工作区 | `<WORKSPACE>`（本地 Git 仓库） |

### 2.2 证据文件

| 证据 | 路径 | 用途 |
|:---|:---|:---|
| IDE 日志 | `~/.config/zen-studio/logs/zen-studio.log` | ACP 连接、`end_turn` 请求 ID、完成提示音/桌面通知、关闭记录 |
| Kimi wire | `~/.kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_ea2c9889-2849-49a3-bd3f-ba2010051304/agents/main/wire.jsonl` | 真实轮次、通知 steering、工具调用、`turn.ended`、追问入队/执行 |
| Kimi 会话状态 | 同目录 `state.json` | `lastPrompt`、`updatedAt`、会话标题 |
| Kimi CLI 日志 | 同目录 `logs/kimi-code.log` | 模型请求/响应连续性、有无 ERROR/HTTP 500 |
| 文件改动 | `<WORKSPACE>` 的 `git status` / `git diff --stat` / mtime | 隐藏轮是否真实修改了源码与测试 |

### 2.3 审计限制

- IDE 日志只记录 `requestId`、`responseId`、`stopReason`，没有落盘完整 ACP stdout 帧；因此无法逐帧证明“客户端是否收到通知轮的 `session/update`”。本文只使用 wire 与 CLI 日志能直接证明的事实。
- 用户“关闭 Kimi”的具体时刻没有被 IDE 日志记录；日志能确认的是 12:23:19 用户选择了「暂停实施」，以及之后隐藏轮仍运行到 12:23:45。
- 沙箱内看不到宿主 PID 命名空间，未能抓取进程树；本文不声称存在“脱离父进程的 daemon”，只说“会话写手在客户端完成后仍持续写 wire/改文件”。
- 本次只读取日志、会话数据与源码，未读取 Kimi 凭证，未修改事故会话；报告中的文件内容均已省略。

### 2.4 官方文档核对（2026-11-21 补查）

| 来源 | 原文/语义 | 对本案的意义 |
|:---|:---|:---|
| [kimi acp 子命令](https://www.kimi.com/code/docs/kimi-code-cli/reference/kimi-acp.html) | `session/cancel`：中断当前 turn；`session/close`：尽力拆除——中断进行中的 turn、释放会话级资源并关闭存活会话；未知 id 不算错误 | 官方有专门的会话级关闭入口；客户端未使用。该页为当前文档，2.7.1 实际能力位待握手确认 |
| [Kimi Code CLI Changelog 2.7.1](https://www.kimi.com/code/docs/en/kimi-code-cli/changelog) | “Keep queued prompts pending until background notification turns finish.” | 追问排队到通知轮结束是官方有意设计；不是丢消息 |
| [ACP v2 Migration：Prompt lifecycle](https://agentclientprotocol.com/protocol/v2/migration.md) | v1 的 `session/prompt` 响应就是 turn 的结束；v2 的响应只确认消息已插入，且“仅仅接收、排队或分配 ID 不足以返回成功响应”；后台工作应在 `state_update.background` 中报告 | 12:07:34 的 `end_turn` 发生在追问插入之前，按 v1/v2 都不成立；隐藏轮应有 background 表达 |

**核对结论**：官方并非没有终止定义，也不是“低级错误”——他们有 `session/cancel`/`session/close` 与 ACP v2 的 background 语义。问题在于 **客户端只用了 `session/cancel`，没有用 `session/close`**，而 `session/cancel` 在 `_turn_id=None` 时是 no-op；同时 **CLI 在 v1 语义下提前回 `end_turn`、在 v2 语义下于消息未插入时回成功**，这仍是上游契约问题。两者叠加，才是完整事故。

---

## 3. 完整时间线（UTC+8）

### 3.1 第一段：正常启动与后台门禁

| 时间 | 证据 | 含义 |
|:---|:---|:---|
| 12:02:56.554 | IDE `[kimi acp] 连接启动 generation=3 pid=43922` | ACP 子进程启动 |
| 12:02:56.925 | IDE `初始化 agent=Kimi Code CLI version=2.7.1 generation=3 resume=True` | 握手成功 |
| 12:02:57.400 | wire `turn.prompt`，`origin.kind=user`，`promptId=msg_01M7Y4B1QPMT7XBVXF3Q4CZ2EN`，`turnId=0` | 用户发出 `@work plans/2026-1121-1153_DSH官方31工具全量对齐计划.md 我们做吧。` |
| 12:03:50.371 | wire `tool.call Bash`，`run_in_background=true`，`taskId=bash-3rfx702q` | 启动后台门禁基线 `scripts/check.sh` |
| 12:03:50.416 | wire `tool.result`：`automatic_notification: true`，`next_step: The completion arrives automatically in a later turn — do NOT wait...` | CLI 自己声明“完成通知会在**later turn**到达” |
| 12:03:53.068 | wire `task.terminated status=completed` + `prompt.steered`，`messageId=task:bash-3rfx702q:completed` | 后台任务完成通知插入当前用户轮 |

### 3.2 第二段：提前 end_turn 与隐藏通知轮

| 时间 | 证据 | 含义 |
|:---|:---|:---|
| 12:03:57.428 | wire 8 个 `tool.call`（7×Glob + 1×Read，`turnId=0 step=6`） | 模型仍在读取实现文件 |
| 12:03:57.485 | wire `step.end turnId=0 step=6 finishReason=tool_use`，`providerFinishReason=tool_calls` | 模型明确要求继续工具调用，不是自然收尾 |
| 12:03:57.486 | wire `turn.ended turnId=0 reason=completed durationMs=60085` | CLI 把用户轮标成正常完成 |
| 12:03:57.487 | wire `prompt.completed`，`promptId=msg_01M7Y4B1QPMT7XBVXF3Q4CZ2EN`，`reason=completed` | 用户 prompt 被标为完成 |
| 12:03:57.489 | IDE `[kimi acp] 轮次响应 stopReason='end_turn' requestId=5 responseId=5` | ACP 客户端收到正常终态 |
| 12:03:57.489 | wire 新 `turn.prompt`：`origin.kind=task`、`taskId=bash-3rfx702q`、`trigger=notify`、`promptId=task:bash-3rfx702q:completed`、`turnId=1` | CLI 立即开启内部通知轮 |
| 12:03:57.497 | wire `step.begin turnId=1 step=1` | 隐藏轮开始执行 |
| 12:03:57.910 | IDE 播放完成提示音并发送桌面通知 | 界面正式宣告“AI 已完成本轮任务” |

从 `turn.ended` 到 ACP `end_turn` 只差 **3 毫秒**，说明客户端是即时按协议收尾，不存在等待、超时或重连。

### 3.3 第三段：UI 完成后隐藏轮继续运行

| 时间 | 证据 | 含义 |
|:---|:---|:---|
| 12:04:09–12:13:57 | wire `turnId=1` step 1–26；多次 `Edit`/`Write` | 修改 `src/ide/llm/providers/dsh/updates.py`、`src/ide/gui/panels/chat/vendors/dsh/cards.py`、`factory.py`、测试文件 |
| 12:07:34.026 | `state.json` 的 `updatedAt` 更新，`lastPrompt="为什么停止了呀？"` | 追问被记录为“最后输入” |
| 12:07:34.034 | IDE `[kimi acp] 轮次响应 stopReason='end_turn' requestId=6 responseId=6` | 追问收到空 `end_turn`，距状态更新仅 **8 毫秒** |
| 12:07:37.042 | IDE `WARNING kimi 轮末上下文用量不可用（ACP 未送达、wire 无本轮有效用量）` | 该“轮”没有正文、没有工具、没有用量，符合空回 |
| 12:07:37.045 | IDE 再次播放完成提示音/桌面通知 | 界面第二次宣告完成；用户实际没有得到回答 |
| 12:15:51.037 | wire `turnId=1 step=33` | 隐藏轮仍在运行 |
| 12:15:29–12:22:39 | 多次审批：ruff、pytest、截图、`git diff --stat`、`ps`、`date` | 隐藏轮继续做测试、视觉验收与自检 |
| 12:20:17 | 另一并行任务（DSH 会话）用户输入“我们不要保留。所有的变更。” | 用户侧开始回滚新任务的改动 |
| 12:20:22 | 并行任务执行 `git restore --worktree`，回滚 10 个跟踪文件并删除 1 个未跟踪测试 | 工作区被外部整体重置（用户侧行为，不是 Kimi 缺陷） |
| 12:23:05.471 | wire `interaction.request kind=question`：`检测到我刚完成的改动被外部整体重置，是否按刚才已通过 63 项测试的版本重新应用并继续？` | 隐藏轮发现工作区被重置，弹出确认 |
| 12:23:19.730 | wire `interaction.resolved`：选择 `暂停实施` | 用户明确要求暂停 |
| 12:23:45.322 | wire `turn.ended turnId=1 reason=completed durationMs=1187831` | 隐藏轮直到此刻才结束（19 分 47.831 秒） |
| 12:23:45.324 | wire 新 `turn.prompt`：`origin.kind=user`、`promptId=msg_01M7Y4KFWHVJWFS9VDTW6B4N8B`、`turnId=2`，文本=`为什么停止了呀？` | 12:07:34 的追问这时才真正进入执行 |
| 12:23:45.337 | wire `step.begin turnId=2 step=1` | 追问开始执行 |
| 12:23:56.719 | wire `content.part` 正文 | CLI 回答：“不是我主动停下的……检测到工作区被外部操作整体重置……当时收到的选择是「暂停实施」……” |
| 12:23:56.724 | wire `turn.ended turnId=2 reason=completed durationMs=11399` | 追问执行完毕 |
| 12:23:56 | wire 最后一条 `prompt.completed`，此后无新事件 | 会话进入空闲 |

**关键时间差**：

- 第一次 `end_turn`（requestId=5）到内部通知轮开始：**0 毫秒**（同一毫秒级）。
- 追问 `end_turn`（requestId=6）到追问真正执行：**16 分 11.290 秒**。
- 追问 `end_turn` 到追问回答：**16 分 22.690 秒**。
- 隐藏轮总时长：**19 分 47.831 秒**；期间 UI 一直认为任务已完成。

### 3.4 断线排除

同一时段内：

- IDE 日志没有 `[kimi acp] 连接关闭 generation=3`；最近的 Kimi 关闭记录是 11-20 的旧记录。
- IDE 日志没有该会话的 `connection_dead`、`recovery_failed`、`BrokenPipe`、`轮次取消`、`轮次本地中断`。
- Kimi `logs/kimi-code.log` 没有 ERROR、HTTP 500、panic、abort；12:03–12:23 持续出现 `llm request/response`。
- `wire.jsonl` 从 turn 0 连续写到 turn 2，中间没有断档；最后一条在 12:23:56。
- 12:03:57 之后 Kimi 继续修改文件、跑测试、截图，说明进程和会话写手都活着。

因此可以排除“官方 CLI 断线”。本次是 **轮次语义错误**，不是连接错误。

---

## 4. 缺陷一：通知 steering 使原用户轮在 `tool_use` 后提前 `end_turn`

### 4.1 事实

12:03:53.068，后台任务 `bash-3rfx702q` 完成，CLI 写入 `prompt.steered`，通知 ID 为 `task:bash-3rfx702q:completed`，目标是当前活跃 prompt `msg_01M7Y4B1QPMT7XBVXF3Q4CZ2EN`。

12:03:57.428，turn 0 step 6 的模型响应产生 8 个工具调用：7 个 `Glob`（goal/jobs/schedule/subagent/deliverables/workflow/session-working-directory）和 1 个 `Read`（`AGENTS.md`）。工具结果在 12:03:57.453–57.485 返回。

12:03:57.485，`step.end` 明确为 `finishReason=tool_use`、`providerFinishReason=tool_calls`。这表示模型要求继续执行工具，轮次没有自然结束。

12:03:57.486，CLI 却写入 `turn.ended turnId=0 reason=completed`；12:03:57.489 向 ACP 返回 `end_turn`；同一毫秒又以 `origin.kind=task`、`trigger=notify` 开启 turn 1。

### 4.2 为什么这是协议错误

ACP 的 `session/prompt` 响应 `stopReason='end_turn'` 表示该 prompt 已正常完成。客户端据此解除 busy、写成功历史、播放完成通知，是完全正确的协议行为。

但此刻：

- 最后一步仍是 `tool_use`，模型还有未消费完的工具结果和后续工作；
- 通知轮 turn 1 尚未开始，客户端没有任何请求 ID 可以关联它；
- CLI 自己此前已在 Bash 工具结果中写明“完成通知会在 later turn 到达”，说明它预期后续还有执行。

因此，`end_turn` 与真实执行状态矛盾。这不是“客户端误判”，而是上游在错误的边界发出了终态。

### 4.3 影响

- 界面显示完成，用户开始新任务；
- 旧 agent 仍在后台改同一批文件；
- 用户的新任务回滚/重置工作区后，旧 agent 才发现并询问；
- 用户此时无论选“继续”还是“暂停”，都已经在和一个不可见的执行体竞争。

---

## 5. 缺陷二：追问先收到 `end_turn`，16 分钟后才真正执行

### 5.1 事实

12:07:34.026，`state.json` 的 `updatedAt` 更新，`lastPrompt` 变为“为什么停止了呀？”。

12:07:34.034，IDE 收到 `[kimi acp] 轮次响应 stopReason='end_turn' requestId=6 responseId=6`。此时：

- `wire.jsonl` 中没有该追问对应的 `turn.prompt`；
- 没有正文、没有工具调用、没有有效 usage；
- 距 `state.json` 更新仅 8 毫秒。

12:23:45.324，隐藏轮 turn 1 结束后，`wire.jsonl` 才出现该追问的 `turn.prompt`：`origin.kind=user`、`promptId=msg_01M7Y4KFWHVJWFS9VDTW6B4N8B`、`turnId=2`。

12:23:56.724，turn 2 完成，产生回答。

### 5.2 为什么这是协议错误

客户端在 12:07:34 收到的是**终态响应**，不是“已排队”回执。按 ACP 语义，它只能认为该 prompt 已结束。16 分钟后 CLI 又执行同一 prompt，但此时：

- 客户端没有活跃的 `session/prompt` 请求；
- 没有请求 ID 可以把 turn 2 的回答关联回 requestId=6；
- 回答只能作为无主更新落入客户端队列，或被丢弃/错挂到下一轮。

这违反了最基本的请求-响应顺序：**响应不得早于对应执行完成**。如果 CLI 想排队，正确做法是：

1. 立即返回明确的“已排队/稍后执行”状态（或 JSON-RPC error），不要返回 `end_turn`；或
2. 阻塞到该 prompt 真正执行完，再返回 `end_turn`；并且只形成一个 `turn.prompt`。

当前实现选择了最危险的第三种：先回 `end_turn`，再在内部轮结束后执行，且不通知客户端。

### 5.3 CLI 自己的回答也印证了时间错位

turn 2 的正文（12:23:56）说：

> “不是我主动停下的。实施过程中检测到工作区被外部操作整体重置，刚完成的源码、63 项测试和文档改动都被清除了，`git status` 回到干净状态。为避免覆盖你或其他流程的操作，我弹出确认询问；当时收到的选择是「暂停实施」，所以我按这个选择停止了。如果这是误选，你说一声“继续”，我就重新应用改动并接着完成截图、文档和全量验收。”

这段回答本身是合理的，但它出现在用户提问 **16 分 22 秒后**，且客户端早已把提问按完成收尾。客户端没有活跃请求可关联这条回答；是否上屏取决于客户端对迟到帧的处理，本文不把它写成已确认的 UI 事实。问题不在回答内容，而在“回答产生时，请求早已被上游自己结束”。

---

## 6. 缺陷三：隐藏通知轮不可见、不可取消；UI 完成后仍后台运行

### 6.1 通知轮没有客户端可用的轮次级句柄

turn 1 的来源是：

```json
{
  "type": "turn.prompt",
  "origin": {"kind": "task", "taskId": "bash-3rfx702q", "status": "completed",
             "notificationId": "task:bash-3rfx702q:completed"},
  "promptId": "task:bash-3rfx702q:completed",
  "turnId": 1,
  "trigger": "notify"
}
```

它不是客户端通过 `session/prompt` 发起的请求，因此：

- 客户端没有 requestId；
- 客户端无法用 `session/cancel` 精确取消它（`session/cancel` 只按 session 取消当前轮，而当前轮在客户端侧已经是“完成”状态；官方另有会话级 `session/close`，但客户端未实现）；
- 客户端无法知道它何时开始、何时结束、是否还在写文件；
- 客户端无法把它的工具卡片、正文、用量正确挂到某个用户轮。

### 6.2 “关闭/暂停”没有立即终止隐藏轮

用户口述在无法继续后关闭/暂停了 Kimi。日志能确认的客观事实是：

- 12:23:05 隐藏轮仍在向用户提问；
- 12:23:19 用户选择「暂停实施」；
- 12:23:45 turn 1 才结束；
- 12:23:45–56 排队的追问仍然执行。

需要区分两种“停”：

- **语义暂停**：12:23:19 的「暂停实施」是对 Kimi 提问的 `interaction.resolved` 回答，不是 ACP `session/cancel`。它只把“暂停”作为一条消息交给模型，不触发传输层取消。模型仍把当前步走完，才在 12:23:45 结束 turn 1；随后排队的追问仍然执行。
- **传输取消**：如果用户另外按了停止按钮或关闭标签，才会走 `provider.cancel()` / `connection.terminate()`。本次日志没有看到这类动作到达 ACP。

也就是说，用户的“暂停”并没有撤销隐藏轮，也没有取消排队中的追问；隐藏轮要走到自己的结束边界才停。对于一个已经在 UI 上显示“完成”的轮次，用户在当前客户端没有可用的即时停止手段——语义暂停不够，传输取消又没有生效/没有到达。官方提供了 `session/close`，但客户端未实现。

### 6.3 客户端关闭语义的独立缺口

IDE 代码里存在一条关闭链路（但不含官方 `session/close`）：

- `ChatTabs._close_tab()` → `ChatPanel.close()`；
- `ChatPanel.close()` → `worker.request_stop()` + `_cleanup_blocking()`；
- `_cleanup_blocking()` → `_close_providers()`；
- `KimiProvider.close()` → `AcpConnection.terminate()` → SIGTERM/等待/必要时 kill。

注意：这条链路**没有使用官方 `session/close`**。官方 `kimi acp` 文档明确支持 `session/close`，语义是“中断进行中的 turn、释放会话级资源并关闭存活会话”；而我们的 `KimiProvider.close()` 只对子进程发 SIGTERM，`cancel()` 只发 `session/cancel`。`AcpConnection.cancel_turn()` 在 `_turn_id=None` 时直接返回 False（`connection.py` 第 355 行），而 12:03:57 的 `end_turn` 已经把 `_turn_id` 清空。因此停止按钮没有可取消的轮次，关闭也没有走官方优雅关闭。这解释了为什么用户“关了还在跑”。

但本次 IDE 日志中没有 `[kimi acp] 连接关闭 generation=3`，也没有 `轮次取消`/`轮次本地中断`。如果用户执行了关闭标签/停止按钮，该动作没有到达 ACP 连接层；也可能是用户只回答了「暂停实施」，而该回答本就不触发传输取消。可能原因包括：

- 客户端没有实现 `session/close`，关闭只走 SIGTERM，未走官方优雅关闭；
- 用户关闭的是会话视图/任务，而不是持有 provider 的标签页；
- 标签页关闭时 worker 已结束，但 provider 仍被其他引用保留；
- 关闭动作与隐藏轮的内部状态发生竞态，`cancel_turn()` 因 `_turn_id=None` 成为 no-op；
- 用户只做了语义暂停（question 回答），没有触发传输取消；
- 其他客户端侧生命周期缺口。

**这一点属于 Zen Studio 的责任，本文不把它归因于 Kimi。** 但它与上游缺陷叠加，造成了用户看到的“关了还在跑”。客户端侧需要单独修复并复验：实现官方 `session/close` 并在关闭标签时调用；停止按钮在 `_turn_id=None` 时应走 `session/close` 会话级兜底（并明确告知会关闭当前会话），不能继续用 `session/cancel` 的 no-op 冒充停止；关闭后必须确认进程退出。2026-11-21 裸 ACP 探针已实测：`session/close` 能中断通知轮、回收后台子进程并返回成功（9.5），因此“close 停不住通知轮”不再是待验证的上游关闭缺陷；上游仍需修提前 `end_turn` 与响应先于插入。

### 6.4 对上游的合理要求

即使客户端关闭链路修好，上游仍需定义并实现：

- 内部通知轮在 ACP 上是否可见？如果可见，用什么请求 ID/事件表达？
- 客户端发送 `session/cancel`、`session/close` 或关闭 stdin 时，是否取消所有内部轮和后台任务？
- 是否存在脱离 ACP 前端的会话写手？如果有，如何停止？
- 客户端断开后，CLI 是否保证在有限时间内退出或进入空闲，而不是继续写 wire/改文件？

这些不是“客户端自己的事”，而是 ACP 会话生命周期契约的一部分。

---

## 7. 影响链：为什么用户会走到“两个任务打架”

把责任放回用户视角，事件顺序是：

1. 用户让 Kimi 执行 31 工具对齐计划；
2. 12:03:57，界面显示“AI 已完成本轮任务”，播放完成音；
3. 用户认为本轮结束，开始新的 DSH 任务（12:08）；
4. Kimi 的隐藏轮仍在后台修改同一批文件；
5. 新任务在 12:20:17 决定“不保留所有变更”，执行 `git restore`，把 Kimi 的改动整体回滚；
6. Kimi 隐藏轮在 12:23:05 才发现工作区被重置，询问是否重新应用；
7. 用户选择「暂停实施」；
8. 隐藏轮到 12:23:45 才结束，排队的“为什么停止了呀？”到 12:23:56 才回答。

用户说“两个 agent 互相争，这是我的问题”，这句话在“同时开两个任务”这个动作上成立；但**用户之所以会开第二个任务，是因为第一个任务在界面上已经完成**。如果 12:03:57 的 `end_turn` 没有提前发出，或者界面能显示“Kimi 仍在后台同步通知轮”，用户不会在新任务里回滚一个仍在运行的 agent 的改动。

因此：

- **直接根因**：上游提前 `end_turn` + 隐藏通知轮不可见/不可取消。
- **放大因素**：客户端关闭/停止没有到达 ACP 连接层；界面没有“后台仍在运行”的状态。
- **用户侧行为**：新任务与回滚是合理反应，不是本次缺陷的根因。

---

## 8. 责任边界与待验证项

### 8.1 已确认属于上游

| 编号 | 事实 | 证据 |
|:---|:---|:---|
| U1 | 原用户轮最后一步 `tool_use`，CLI 仍返回 `end_turn` | wire 12:03:57.485–489；IDE requestId=5 |
| U2 | 追问在 8ms 内收到空 `end_turn`，16 分 11 秒后才执行 | IDE requestId=6；wire turn 2 的 `turn.prompt` 12:23:45.324 |
| U3 | 通知轮 `origin=task/trigger=notify` 不暴露给 ACP，客户端无轮次级请求 ID；官方仅有 `session/close` 会话级关闭 | wire turn 1；IDE 无对应 `session/prompt`；官方 kimi acp 文档 |
| U4 | 隐藏轮在客户端完成后继续运行 19 分 47 秒，期间改文件、跑测试、提问 | wire turn 1 全程；文件 mtime/ git 记录 |

### 8.2 属于客户端、需 Zen Studio 单独修复

| 编号 | 事实 | 说明 |
|:---|:---|:---|
| C1 | 未实现官方 `session/close` | 官方文档声明 `session/close` 会中断进行中的 turn；`KimiProvider.close()` 只发 SIGTERM，未调用 `session/close` |
| C2 | `cancel_turn()` 在 `_turn_id=None` 时 no-op | `session/cancel` 只取消当前 turn；12:03:57 的 `end_turn` 已把 `_turn_id` 置空，停止按钮无轮可取消；通知轮没有轮次级请求 ID |
| C3 | 界面在 `end_turn` 后立即播报完成 | 客户端按 v1 协议行为正确；但缺少 `session/close` 与后台状态展示 |

### 8.3 待与官方共同验证

2026-11-21 裸 ACP 探针已答前两项（见 9.5）：

- ~~客户端发送 `session/cancel` 时，CLI 是否取消内部通知轮？~~ **已答（2026-11-21 探针）：不取消；通知轮无 `turn.ended`，后台子进程继续存活。**
- ~~客户端发送 `session/close` 时，CLI 是否中断 `origin=task/trigger=notify` 的通知轮？~~ **已答（2026-11-21 探针）：会中断通知轮并回收后台子进程。**
- 客户端关闭 stdin / 退出 ACP 进程时，CLI 是否终止所有后台任务与会话写手？
- CLI 是否存在独立于 ACP 前端的 daemon/写手？如果有，如何优雅停止？
- 追问排队期间，CLI 是否能在 ACP 上返回“已排队”而非 `end_turn`？

仍未验证 stdin EOF/SIGTERM、daemon/写手、显式“已排队”响应。探针结果不声称“CLI 忽略关闭信号”：`session/close` 有效，`session/cancel` 有明确边界；也不声称存在脱离父进程的 daemon。

---

## 9. 裸 ACP 最小复现

> 目的：排除 Zen Studio 的 UI/worker/关闭逻辑，只验证 Kimi CLI 的 ACP 轮次行为。建议官方用自带的 ACP 测试客户端复现。

### 9.1 前置

- 临时工作区（不要用真实项目），仅放几个可读文件；
- 启动 `kimi acp`，完成 `initialize`、`session/new`；
- 记录完整 stdout 帧、`wire.jsonl`、`state.json`、`logs/kimi-code.log`；
- 不开任何 IDE。

### 9.2 步骤

1. 发送第一个 `session/prompt`，内容大意：
   > 在后台运行 `bash -c 'sleep 2; echo done'`，不要等待它；同时读取工作区里的 5 个文件，然后继续实现。
2. 记录第一个 `session/prompt` 的响应时刻、`stopReason`，以及响应前最后一个 `step.end.finishReason`。
3. 后台任务完成时，观察：
   - `wire.jsonl` 是否出现 `task.terminated` + `prompt.steered`；
   - 当前用户轮是否在 `tool_use` 后出现 `turn.ended reason=completed`；
   - 是否出现新的 `turn.prompt`（`origin.kind=task`、`trigger=notify`）；
   - ACP 客户端是否收到对应这个新轮次的任何请求/事件。
4. 在通知轮仍活跃时，发送第二个 `session/prompt`：
   > 为什么停止了呀？
5. 记录第二个响应的耗时、`stopReason`；同时检查：
   - `state.json.lastPrompt` 是否已更新；
   - `wire.jsonl` 是否出现第二个 `turn.prompt`；
   - 如果出现，距响应过了多久。
6. 在通知轮仍活跃时，分别测试三个终止路径：
   - 发送 `session/cancel`（当前 turn）；
   - 发送 `session/close`（会话级拆除，官方文档声明会中断进行中的 turn）；
   - 关闭 stdin / 终止 ACP 进程。
   每个路径都检查：CLI 是否在有限时间内停止写 `wire.jsonl`？是否还修改工作区文件？是否还有后台 bash/子进程存活？
7. 观察通知轮结束后的行为：
   - 排队的第二个 prompt 是否仍被执行？
   - 执行结果是否还能关联回第二个 `session/prompt` 的请求 ID？

### 9.3 期望 vs 实际

| 检查点 | 期望 | 本次实际 |
|:---|:---|:---|
| 第一轮响应前最后一步的 `finishReason` | `end_turn`，且没有待执行的 `tool_use` | `tool_use`，8 个工具刚返回 |
| 第一轮 `end_turn` 后的状态 | 客户端可认为完成，CLI 空闲 | CLI 立即开 turn 1，继续运行 19m47s |
| 通知轮可见性 | 作为新 ACP 轮次暴露，或等客户端下一次 prompt | ACP 无请求 ID，客户端不可见 |
| 第二个 prompt 响应 | 执行完成后才 `end_turn`，或明确“已排队” | 8ms 空 `end_turn` |
| 第二个 prompt 执行 | 恰好一次，且在响应前 | 16m11s 后才执行，响应早已发出 |
| `session/cancel` | 取消当前 turn；通知轮是否随之取消需官方确认 | 探针实测（2026-11-21）：不覆盖通知轮，30s 内无 `turn.ended`，后台 `sleep` 子进程存活（9.5） |
| `session/close` | 中断进行中的 turn、释放会话资源 | 探针实测（2026-11-21）：能中断通知轮、回收后台子进程；同 session 再 prompt 返回 `-32602`（9.5） |
| 客户端关闭 | CLI 有限时间内停止/退出 | 本次客户端未发出可确认的关闭；CLI 继续写 wire |

### 9.4 建议抓取的证据

- `session/prompt` 请求/响应原文（含 `id`、`stopReason`）；
- 每条 `session/update` 的时间与 `sessionId`；
- `wire.jsonl` 的 `turn.prompt` / `turn.ended` / `prompt.completed` / `step.end`；
- `state.json` 的 `lastPrompt` / `updatedAt`；
- `logs/kimi-code.log` 的请求/响应时间；
- 进程树与文件 mtime（证明客户端关闭后是否仍写文件）。

### 9.5 探针实测结果（2026-11-21）

> 方法：直接 spawn `kimi acp`，不经过 Zen Studio provider/UI；每个场景独立进程、独立 session、独立临时 workspace。只记录帧/wire/文件/进程元数据，不记录 prompt 正文、工具参数/输出、diff 或凭证。
> 环境：Kimi Code CLI 2.7.1，`kimi-code/k3.1-pro`，Linux x86_64。
> 证据：`.temp/kimi_acp_close_probe/REPORT.md` 与同目录下各运行目录；机器汇总 `.temp/kimi_acp_close_probe/evidence.json`。

| 问题 | 实测结论 | 关键证据 |
|:---|:---|:---|
| Q1 close 能力 | **有**：`agentCapabilities.sessionCapabilities.close={}`；另含 `list/resume/delete/fork/additionalDirectories`；`agentInfo.version=2.7.1` | `s0-v1-20261121-131542/initialize.json` |
| Q2 提前 `end_turn` | **复现**：首轮响应前最后一个 `step.end` 为 `finishReason=tool_use`，紧接着 `turn.ended`、ACP `stopReason=end_turn`，同一毫秒通知轮启动 | `s1-20261121-130445`、`s1-20261121-131545` |
| Q3 追问先回后执行 | **复现**：追问 6–10ms 返回空 `end_turn`（0 正文/0 工具），响应时 wire 无对应 `turn.prompt`；95.3–105.5 秒后才插入执行 | 同上两次 S1 |
| Q4 `session/cancel` | **不覆盖通知轮**：cancel 后 30s 内无 `turn.ended`，后台 `sleep 90` 子进程仍存活；`wire` 的 `turn.cancel` 只在清理阶段 `session/close` 时出现 | `s2-20261121-130730`、`s2-20261121-131153` |
| Q5 `session/close` | **覆盖通知轮**：close 返回 `{}`，约 4ms 内通知轮 `turn.ended reason=cancelled`；close 前存活的 `sleep 90` 在 close 后消失，wire 停止增长 | `s3-20261121-130905`、`s3-20261121-131320` |
| Q6 close 后会话 | 同 session 再 `session/prompt` 返回 **`-32602` Invalid params: Unknown sessionId**；CLI 进程不退出、空闲、无残留子进程 | `s3-20261121-131320/summary.json` |
| Q7 ACP v2 | 请求 `protocolVersion: 2` 仍返回 **`1`**；响应为 v1 形状，无 `state_update`/`background`，2.7.1 为 v1-only | `s0-v2-20261121-131543/initialize.json` |

关键时序（`s1-20261121-130445`）：

```text
t=10.128  step 1 end, finishReason=tool_use（后台任务）
t=35.133  prompt.steered（后台完成通知进入）
t=42.813  step 2 end, finishReason=tool_use（前台工具返回，没有最终答复步）
t=42.813  turn.ended turn 0 reason=completed
t=42.816  ACP response stopReason=end_turn
t=42.816  turn.prompt turn 1, origin.kind=task, trigger=notify
t=48.87   追问 session/prompt 发出（通知轮活跃）
t=49.33   追问响应 stopReason=end_turn（0.010s，空）
t=154.860 追问才作为 turn 2 插入执行（迟到 105,540ms）
```

`session/close` 的两次实测（`s3-20261121-130905` / `s3-20261121-131320`）：

- close 响应均为 `{}`（`result_keys: []`，无 error）；
- 通知轮 `turn.ended` 均为 `reason=cancelled`，分别在 close 后 4ms；
- 第二次 close 前 `sleep 90` pid 119 真实存活，close 后快照只剩 `kimi-code`；
- 30 秒窗口内 wire 大小不变（129285 字节）。

**对前文的修正**：9.3 表中 `session/cancel` / `session/close` 两行已按本次实测更新；8.3 的前两项已答。2.7.1 的 close 能力位与“close 能停通知轮”均已证实，客户端不再有“能力未声明”的前置阻塞。上游仍需修 Q2/Q3（提前 `end_turn`、响应先于插入）与 Q4 的 cancel 边界；Q6 的 `-32602` 应对齐 ACP 的 `-32002`。

---

## 10. 期望契约（给官方的具体修改要求）

### C1 不得在 `tool_use` 未完成时返回 `end_turn`

`session/prompt` 的 `end_turn` 必须表示该 prompt 对应的用户轮已经真正结束，包括：

- 没有待执行的工具调用；
- 没有因通知 steering 产生的后续工作；
- 没有排队中的用户 prompt 属于该轮。

如果最后一步是 `tool_use`，CLI 应继续执行，或返回明确的非终态/错误，不得发 `end_turn`。

### C2 通知轮必须可见，或不得在 ACP 响应后自行启动

二选一：

- **可见方案**：把 `origin=task/trigger=notify` 的内部轮作为新的 ACP 轮次暴露给客户端，带可关联的 ID，客户端可以展示、取消、等待；
- **不启动方案**：当前 `session/prompt` 完成前不启动通知轮；等客户端下一次 `session/prompt` 时再合并通知，避免“响应后仍有隐藏执行”。

不能继续保留“ACP 返回 `end_turn`，但 CLI 内部又开一轮”的第三种状态。

### C3 用户 prompt 在通知轮期间必须排队并恰好执行一次，且响应不得早于执行

- 如果选择排队：立即返回明确的“已排队/稍后执行”状态（或 JSON-RPC error），不要返回 `end_turn`；在真正执行完成后再返回终态；全程只产生一个 `turn.prompt`。
- 如果选择拒绝：立即返回明确错误，不要只更新 `lastPrompt`。
- 禁止：先 `end_turn`，再在 16 分钟后执行；客户端无法关联，用户会认为消息丢了。

### C4 `session/cancel` 与客户端断开必须覆盖内部轮和后台任务

- `session/cancel` 应取消当前 turn；若设计上它不覆盖通知轮，必须明确文档化，并让 `session/close` 成为会话级兜底；
- `session/close` 应中断所有进行中的 turn（包括通知轮）、停止后台任务并释放会话资源；这是客户端应主动调用的官方关闭路径；
- 客户端关闭 stdin/终止 ACP 进程时，CLI 应在有限时间内停止所有模型请求、工具执行、后台任务和会话写手；
- 不应存在客户端不可见、不可取消的“影子执行”；
- 如果存在独立 daemon/写手，必须提供停止协议并在文档中说明。

### C5 ACP 响应应携带足够的关联信息

至少让客户端能判断：

- 该响应对应哪个 `promptId`/`turnId`；
- 该轮是完成、排队、正在 drain 通知轮，还是被取消；
- 是否还有后续内部轮次；
- 后续轮次如何关联回原用户请求。

仅靠 `stopReason='end_turn'` 不足以表达“用户轮已完成”与“内部还有通知轮”的区别。

### C6 后台任务通知的“later turn”语义必须与 ACP 对齐

CLI 自己的 Bash 工具结果写着：

> `The completion arrives automatically in a later turn — do NOT wait, poll, or call TaskOutput on it; continue with your current work.`

这句话在 CLI 内部是成立的，但在 ACP 客户端看来，`later turn` 没有任何协议表达。请让“later turn”在 ACP 上可观察、可取消，或者不要让它在当前 `session/prompt` 响应之后悄悄发生。

---

## 11. 影响与优先级

### 11.1 用户影响

- 界面显示完成，实际仍在写文件；
- 用户无法继续原任务，也无法确认何时真正结束；
- 用户开始新任务后，旧任务继续改同一批文件，造成覆盖/回滚/丢失；
- 用户选择“暂停”后，隐藏轮仍可能继续到自己的边界，排队的追问仍会执行；
- 用户对“停止/关闭”失去信任。

### 11.2 复现频率

- 本次事故 1/1 命中；
- 内部 2026-11-20 的真实 Kimi 测试中，U1 场景 **3/3 失败**（后台通知期间用户轮仍需工作），U2 3/3 失败，U3/U4 均失败；裸 ACP 探针也复现了提前 `end_turn`。
- 不是低概率竞态，而是通知轮路径上的稳定行为。

### 11.3 优先级

高。建议官方优先修复 C1、C2、C3；C4 作为会话生命周期的基础能力同步补；C5/C6 用于长期协议清晰度。

---

## 12. 附录

### 12.1 IDE 日志摘录（已脱敏）

```text
2026-11-21 12:02:56,554 INFO ide.llm.providers.acp.connection: [kimi acp] 连接启动 generation=3 pid=43922
2026-11-21 12:02:56,925 INFO ide.llm.providers.kimi.provider: [kimi acp] 初始化 agent=Kimi Code CLI version=2.7.1 generation=3 resume=True
2026-11-21 12:03:57,489 INFO ide.llm.providers.kimi.provider: [kimi acp] 轮次响应 stopReason='end_turn' requestId=5 responseId=5 sessionId=session_ea2c9889-2849-49a3-bd3f-ba2010051304 generation=3
2026-11-21 12:03:57,910 INFO ide.gui.sound_player: 系统播放器播放：/usr/bin/aplay yup-03.wav
2026-11-21 12:03:57,910 INFO ide.gui.desktop_notifier: 桌面通知已发送：任务完成 / AI 已完成本轮任务
2026-11-21 12:07:34,034 INFO ide.llm.providers.kimi.provider: [kimi acp] 轮次响应 stopReason='end_turn' requestId=6 responseId=6 sessionId=session_ea2c9889-2849-49a3-bd3f-ba2010051304 generation=3
2026-11-21 12:07:37,042 WARNING ide.llm.providers.kimi.usage: kimi 轮末上下文用量不可用（ACP 未送达、wire 无本轮有效用量）
2026-11-21 12:07:37,045 INFO ide.gui.desktop_notifier: 桌面通知已发送：任务完成 / AI 已完成本轮任务
```

同一时段内没有：

```text
[kimi acp] 连接关闭 generation=3
[kimi acp] 轮次取消 ... generation=3
[kimi acp] 轮次本地中断 ... generation=3
```

### 12.2 关键事件表（wire + state）

| 时间 | 事件 | 关键字段 |
|:---|:---|:---|
| 12:02:57.400 | `turn.prompt` | `origin.kind=user`, `turnId=0`, `promptId=msg_01M7Y4B1QPMT7XBVXF3Q4CZ2EN` |
| 12:03:50.415 | `task.started` | `taskId=bash-3rfx702q`, `detached=true` |
| 12:03:53.068 | `task.terminated` + `prompt.steered` | `status=completed`, `messageId=task:bash-3rfx702q:completed` |
| 12:03:57.485 | `step.end` | `turnId=0`, `step=6`, `finishReason=tool_use` |
| 12:03:57.486 | `turn.ended` | `turnId=0`, `reason=completed`, `durationMs=60085` |
| 12:03:57.487 | `prompt.completed` | `promptId=msg_01M7Y4B1QPMT7XBVXF3Q4CZ2EN`, `reason=completed` |
| 12:03:57.489 | `turn.prompt` | `origin.kind=task`, `trigger=notify`, `turnId=1`, `promptId=task:bash-3rfx702q:completed` |
| 12:07:34.026 | `state.json.updatedAt` | `lastPrompt="为什么停止了呀？"` |
| 12:23:05.471 | `interaction.request` | `kind=question`，工作区重置确认 |
| 12:23:19.730 | `interaction.resolved` | 选择 `暂停实施` |
| 12:23:45.322 | `turn.ended` | `turnId=1`, `reason=completed`, `durationMs=1187831` |
| 12:23:45.324 | `turn.prompt` | `origin.kind=user`, `turnId=2`, `promptId=msg_01M7Y4KFWHVJWFS9VDTW6B4N8B`, 文本=`为什么停止了呀？` |
| 12:23:56.724 | `turn.ended` | `turnId=2`, `reason=completed`, `durationMs=11399` |

### 12.3 `state.json` 关键字段（摘录）

```json
{
  "id": "session_ea2c9889-2849-49a3-bd3f-ba2010051304",
  "version": 2,
  "cwd": "<WORKSPACE>",
  "archived": false,
  "title": "@work plans/2026-1121-1153_DSH官方31工具全量对齐计划.md 我们做吧。",
  "createdAt": 1795233777027,
  "updatedAt": 1795235036725,
  "lastPrompt": "为什么停止了呀？"
}
```

时间换算：`createdAt=12:02:57.027`；`updatedAt=12:23:56.725`（报告撰写时的最后一次更新；12:07:34.026 曾首次写入 `lastPrompt`）。

### 12.4 隐藏轮的文件改动证据（不含文件内容）

12:10–12:15，wire 连续出现以下编辑：

- `src/ide/llm/providers/dsh/updates.py`（多次 Edit）
- `src/ide/gui/panels/chat/vendors/dsh/cards.py`（多次 Edit）
- `src/ide/gui/panels/chat/vendors/dsh/factory.py`（Write）
- `tests/test_dsh_card_icons.py`
- `tests/test_vendor_card_isolation.py`

12:15 的 `git status` 显示 6 个已修改文件 + 1 个未跟踪测试，`git diff --stat` 约 `+994/−27`。12:20:22 并行 DSH 任务执行 `git restore --worktree` 后，工作区回到干净状态；Kimi 隐藏轮在 12:23:05 检测到该重置并询问是否重新应用。该重置是用户侧并行任务的行为，不是 Kimi 的缺陷；列出它仅为证明“隐藏轮确实在 UI 完成后继续写文件”。

### 12.5 内部对照（可按需删除）

以下为本团队内部记录，供官方需要时参考；不包含凭证或源码，可按需删除本节：

- `work plans/2026-1120-1528_ACP后台通知轮次失配与编辑卡片假完成修复落地计划.md`：§1.1 已描述“Kimi 对 ACP session/prompt 提前返回 end_turn；IDE 按正常完成收尾，但 agent 仍在内部通知轮次中继续调用工具；后续用户追问还可能只更新会话元数据而未进入真实执行轮”；§D8 列出了两项上游契约要求。
- `文档/修改记录/2026-1120-2240_Kimi原生ACP链路简化与长任务稳定性重建实施记录.md`：§3.2 记录 Kimi 2.7.1 的 U1–U4 场景上游全部失败；§7 记录“空 end_turn 可能发生在 3 秒内且没有任何 update 帧”。
- `文档/审计报告/2026-1120-0926_ACP假完成与后台通知轮次状态脱节诊断报告.md`：更早的同模式事故记录。
- `文档/修改记录/2026-0718-1555_KimiACP后端升级实施计划.md`：当时明确记录“缺 `session/close`、`logout`，不影响主流程”；本次事故证明这个判断在“关闭后隐藏轮继续跑”场景下不成立。

这些内部文档说明：本次不是孤例，而是同一上游缺陷在 2.7.1 上的再次稳定复现。

---

## 13. 请求官方采取的行动

1. **复现确认**：用第 9 节的裸 ACP 用例，确认第 10 节 C1–C3 的行为；如无法复现，请告知需要的额外环境信息。
2. **修复 C1**：通知 steering 不得让仍处于 `tool_use` 的用户轮提前 `end_turn`。
3. **修复 C2/C3**：通知轮必须在 ACP 上可见，或不得在响应后自行启动；追问必须排队并恰好执行一次，且响应不得早于执行。
4. **修复 C4**：明确 `session/cancel` 与 `session/close` 的覆盖边界，并保证 `session/close` 能覆盖内部轮、后台任务和会话写手，在有限时间内停止。
5. **补充文档**：说明 ACP 模式下 `end_turn`、通知轮、追问排队、取消/关闭的准确语义，便于客户端正确实现。
6. **回归验证**：修复版本请覆盖 U1–U4 四个场景，并附裸 ACP 帧序证据（请求 ID、`stopReason`、`turn.prompt`、`turn.ended`、`prompt.completed`）。
7. **保持 `session/close` 的通知轮语义**：2026-11-21 裸 ACP 探针已确认 2.7.1 的 `session/close` 能中断 `origin.kind=task/trigger=notify` 的通知轮并回收后台子进程（9.5）。请在修复版本保持该语义，并补充关闭前是否需等待 idle、返回码与超时的文档；同时把未知 session 的错误码从 `-32602` 对齐到 ACP 规范的 `-32002` + `data.sessionId`。

Zen Studio 侧将同步修复客户端关闭链路（C1–C3），包括实现官方 `session/close` 并在关闭标签时调用；随后在官方修复版本上用同一用例复验。在此之前，本报告不把“客户端没调 `session/close`”算作 Kimi 的缺陷，也不把“提前 `end_turn`”算作客户端的责任。

---

## 14. 一句话总结

**Kimi CLI 在通知轮路径上把“ACP 响应已完成”和“CLI 内部仍在执行”拆成了两个互不可见的状态：先回 `end_turn`，再开隐藏轮；用户追问又被延迟 16 分钟执行。** 这不是断线，而是轮次生命周期契约错误；它直接导致用户在“已完成”的界面上开始新任务，旧 agent 却在后台继续写文件，最终造成回滚、覆盖和无法继续。客户端未使用官方 `session/close` 是“关了还在跑”的直接原因；上游提前 `end_turn` 是“界面假完成”的直接原因。两者叠加，才是完整事故。
