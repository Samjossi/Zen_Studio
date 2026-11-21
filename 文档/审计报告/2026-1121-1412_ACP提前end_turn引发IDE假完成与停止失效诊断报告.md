# ACP 提前 end_turn 引发 IDE 假完成、停止失效与会话不可续诊断报告

> **状态**：客户端缺陷已定位并复现（Zen Studio 1.8.0.571 冻结态 + Kimi Code CLI 2.7.2）；修复未实施
> **范围**：Zen Studio 的 Kimi ACP 连接层、轮次收尾状态机、排队发送、停止/关闭路径、跨轮帧清理；不涉及模型与网络
> **时间**：2026-11-21 14:15（UTC+8，编写）
> **优先级**：高（界面假完成、孤儿轮次持续执行与计费、停止按钮失效、会话无法继续对话）
> **事故会话**：`session_42ee0f58-6798-4617-af9b-e0a5767ad0ac`（13:53 重跑）、`session_393f99fd-a1f4-42e6-92a0-cbb399190439`（13:41 首跑）
> **关联文档**：
> - `文档/审计报告/2026-1120-0926_ACP假完成与后台通知轮次状态脱节诊断报告.md`（同一致命链的历史诊断；其 P0 曾落地，后于 85cf8aa 被移除）
> - `文档/审计报告/2026-1121-1225_KimiCLI_ACP提前end_turn与隐藏通知轮次审计报告.md`（上游契约缺陷与 `session/close` 缺口）
> - `work plans/2026-1121-1336_Kimi后台通知轮次修复UI复验提示词.md`（本次复验所用 Prompt A/B）
> - `文档/审计报告/2026-1121-1409_Kimi后台通知UI复验卡死与假完成复发诊断报告.md`（同一事故的并行记录，由孤儿轮次起草 14:09、落盘 14:11:07；两份报告的关系见 §2.3）
> **脱敏声明**：本文不含凭证、密钥、Token；只保留与故障相关的 prompt 原文、工具命令摘要与事件时间戳。

---

## 1. 结论摘要

本次不是 CLI 卡死，不是 ACP 管道断裂，也不是网络断线。Kimi 进程从头到尾都活着（turn 2 直到 14:11:25 才结束，比界面宣布“完成”晚了 11 分 51 秒）。

直接故障是**客户端把 ACP 的 `session/prompt` 响应当成了“本轮真正结束”的唯一依据**：

1. Kimi CLI 在后台通知轮/忙态下提前返回 `end_turn`。13:53 这次重跑出现了两次：`requestId=6`（13:55:31，真实通知轮 13:55:54 才结束）和 `requestId=7`（13:59:31，wire 中该轮**根本没有** `turn.ended`）。
2. 我们收到 `end_turn` 就 `return`，`_on_finished` 随即 `_set_busy(False)`、播完成音、把界面置闲。
3. 置闲瞬间，排队发送逻辑把输入框里的待发文本（本报告推断为 Prompt B，14:09 报告记为单字符 `s`，见 §6.2）**自动发进一个仍然忙的 agent**；agent 回一个空 `end_turn`，界面再“完成”一次、再响一次铃。
4. 这次假完成已经把 `_turn_id` 清空。此后 `session/cancel` 直接 no-op，而我们又没有实现官方 `session/close`，**停止按钮彻底失效**，孤儿轮次再也停不下来。
5. 用户看到的就是：按钮变灰、一按就弹完成音、AI 再也不回答、会话好像“断了”。

一句话根因：**客户端用“收到 response”代替“轮次真的结束”，且假完成后既不能续、也不能停。**

---

## 2. 前因：防护曾经落地，昨夜被主动移除

本次不是偶发，也不是 2.7.2 新引入，而是在**客户端防护被移除后的真空期**复发。按时间顺序：

| 时间 | 事件 | 结论与处置 |
|:---|:---|:---|
| 2026-11-20 09:26 | `2026-1120-0926_ACP假完成与后台通知轮次状态脱节诊断报告.md` | 指出“IDE 将 ACP response 等同于用户任务真实完成”，提出 P0：轮次一致性核对、失配时禁止自动待发、不播报成功。 |
| 2026-11-20 白天 | 客户端防护落地 | `kimi_turn_consistency.py`（276 行，`KimiTurnTracker` / `TurnDesynchronized` / wire 快照）、`kimi_acp.py` 的 drain/turn_sync/失配恢复、面板的 turn_sync 提示与挂起看门狗。 |
| **2026-11-20 22:42** | **提交 `85cf8aa`「移除私有轮次生命周期并恢复官方单轮次契约」** | **删除上述全部防护，并让 Kimi 不再启动挂起看门狗**；依据 `work plans/2026-1120-2134_...落地计划.md`。 |
| 2026-11-21 12:25 | `2026-1121-1225_KimiCLI_ACP提前end_turn与隐藏通知轮次审计报告.md` | 在 2.7.1 上确认上游提前 `end_turn`、追问延迟执行、隐藏轮不可取消；提出补 `session/close`。 |
| 2026-11-21 13:36 | `work plans/2026-1121-1336_Kimi后台通知轮次修复UI复验提示词.md` | 编写 Prompt A/B，准备在 2.7.2 上复验。 |
| 2026-11-21 13:41 / 13:53 | 首跑与重跑（本次事故） | 上游再次违约 → 无防护 → 假完成、追问被吞、停止失效、孤儿轮次。 |

关键事实：**当前代码里没有防线，不是因为它从未实现，而是因为它在 11-20 22:42 被 `85cf8aa` 主动移除了。** 移除决策本身有合理理由（私有 wire 不是公共契约，不应长期扩散），但计划 `2026-1120-2134` §8 已经写明这是“移除本地掩盖后必须面对的真实风险”，并接受“无自动看门狗意味着永久沉默需要手动停止”。今天两次实测证明：这个风险已经兑现，而且“手动停止”也没有可用路径（见 §7）。

### 2.1 `85cf8aa` 具体移除了什么

| 被移除项 | 规模/位置 | 作用 |
|:---|:---|:---|
| `src/ide/llm/providers/kimi_turn_consistency.py` | 整文件删除 276 行 | `KimiTurnTracker` 轮次一致性核对、`TurnDesynchronized` 失配异常、wire 快照 |
| `src/ide/llm/providers/kimi_acp.py` | -271 行 | 轮前/轮后 drain、turn_sync、失配恢复、`mark_stalled` |
| `src/ide/gui/panels/chat/panel.py` | -118 行 | turn_sync 临时提示面板、失配提示、看门狗无条件启动 |
| `settings` | -1 键 | `temporary_notice_panel_enabled` |
| Kimi 看门狗 | 现 `src/ide/llm/providers/kimi/provider.py:208` `stall_watchdog=False` | Kimi 轮次无任何超时兜底，busy 可无限持续 |

### 2.2 本次相对 12:25 审计新增的证据

| # | 新增事实 | 意义 |
|:---|:---|:---|
| 1 | 2.7.2 仍然复现提前 `end_turn` | 12:25 的证据来自 2.7.1；升级版本没有消除该行为 |
| 2 | 追问不再只是“延迟执行”，这次**根本没进 wire** | Prompt B 全文命中 0 次，比 16 分钟延迟更严重：消息被吞 |
| 3 | 首次抓到“假完成 → 自动续发 → 空完成 → 完成音”的完整闭环 | 用户描述的“一按就完成音”有了代码级解释 |
| 4 | 假完成后 `_turn_id=None`，停止 no-op，且无 `session/close` | 会话不可恢复的直接原因 |
| 5 | 孤儿轮在界面“完成”后继续跑 **11 分 51 秒**（整轮 15 分 31 秒）、6.6 万输出 token，并向 UI 提问 | 界面状态与 agent 状态完全脱钩，不只是提示音误报 |
| 6 | 该孤儿轮起草 14:09、14:11:07 落盘了 `2026-1121-1409_...诊断报告.md`，并修改 README | “已完成”的轮次在写自己的事故报告，是最直接的活证据 |

### 2.3 与 14:09 并行报告的关系

`2026-1121-1409_Kimi后台通知UI复验卡死与假完成复发诊断报告.md` 是**孤儿 Kimi 轮次（turn 2）自己起草、14:11:07 落盘的**（wire 中 turn 2 的 `Write` 工具调用目标即该文件，随后 `Edit` 修改了 README）。两份报告的核心结论一致：上游提前 `end_turn` + 客户端无防护。差异与取舍：

| 项 | 14:09 报告 | 本报告 | 处理 |
|:---|:---|:---|:---|
| 13:54:40 的排队草稿 | 记为单个字符 `s` | 推断为 Prompt B（`requestId=6`） | 两边都没有原始帧直接证据；`state.json` 当前的 `lastPrompt:"s"` 对应 `updatedAt=14:11:34` 的 turn 3，不能直接证明 13:54:40 的内容。机制与后果不因内容而变 |
| 13:41 首轮“IDE 彻底卡死” | 列为工作假设（孤儿帧死锁等） | 未展开，只记录 UI 假完成与 105 秒通知轮 | 卡死机理仍待复现，保留 14:09 报告的假设清单 |
| 修复方向 | 恢复防护并适配 2.7.2（待选型） | 给出 P0/P1 代码级方案与验收 | 以本报告作为修复依据，14:09 报告保留为上游与卡死假设的独立记录 |

---

## 3. 环境与证据

### 3.1 运行环境

| 项 | 值 |
|:---|:---|
| 客户端 | Zen Studio **1.8.0.571（冻结态 / AppImage）**，PID 84030 |
| CLI | Kimi Code CLI **2.7.2**（`kimi-code/kimi-for-coding`） |
| ACP 连接 | `generation=1`，子进程 `pid=84143`，启动于 13:53:57.688 |
| 模型 | `kimi-code/kimi-for-coding`，thinkingEffort=high |
| 工作区 | `<WORKSPACE>`（本地 Git 仓库） |
| 触发 prompt | Prompt A（13:53 修正版，第 2 步已改为“不要输出文本、不要结束本轮”） |

### 3.2 证据文件

| 证据 | 路径 | 用途 |
|:---|:---|:---|
| IDE 日志（冻结态） | `~/.config/zen-studio/logs/zen-studio.log` | ACP 连接代次、`end_turn` 请求 ID/时间、完成音、提问通知 |
| Kimi wire（本次） | `~/.kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_42ee0f58-6798-4617-af9b-e0a5767ad0ac/agents/main/wire.jsonl` | 真实轮次、`turn.prompt`/`turn.ended`、工具调用、通知 steering、孤儿轮次 |
| Kimi wire（首跑） | 同目录 `session_393f99fd-a1f4-42e6-92a0-cbb399190439/agents/main/wire.jsonl` | 13:41 首跑对照 |
| CLI 会话日志 | 同目录 `logs/kimi-code.log` | LLM 请求时序、`turnStep` 步进 |
| 客户端代码 | `src/ide/llm/providers/kimi/provider.py`、`src/ide/llm/providers/acp/connection.py`、`src/ide/gui/panels/chat/panel.py` | 缺陷定位 |

### 3.3 审计限制

- 客户端只记录 `stopReason` 与请求 ID，不记录 ACP 原始帧；`requestId=6` 的 prompt 正文不在任何日志中，本文对该 ID 的归属为**高置信推断**（见 6.2），不是直接观测。
- wire 由 CLI 写入，时间戳可能比 ACP 帧早/晚数毫秒；所有“提前/滞后”结论均以同一日志源的相对顺序为准。
- 14:11:25 出现 turn 3（prompt 为单个字符 `s`），14:11:34 完成；IDE 日志中没有对应的 `requestId=8` 响应记录。该 prompt 由哪个客户端发出尚未确认，本文只把它作为活证据记录，不并入主链。
- Prompt B 是否最终补执行仍保持开放：截至 14:11:34，wire 中全文命中 0 次。

---

## 4. 完整时间线（UTC+8）

### 4.1 13:41 首跑：界面完成 105 秒前就“完成”了

| 时刻 | 来源 | 事件 |
|:---|:---|:---|
| 13:41:46.507 | IDE | `[kimi acp] 连接启动 generation=3 pid=81861` |
| 13:41:46.860 | IDE | 初始化 Kimi CLI 2.7.2，`resume=True` |
| 13:41:47.294 | wire | turn 0 用户 prompt 进入 |
| 13:41:53.184 | wire | `turn.ended turn=0 reason=completed`，耗时 **5,889 ms** |
| 13:41:53.187 | IDE | 收到 `end_turn requestId=5`，界面收尾 |
| 13:41:53.604 | IDE | 播完成音 + 桌面通知 |
| 13:42:16.954 | wire | **隐藏通知轮 turn 1 启动**（`trigger=notify`，后台任务完成通知） |
| 13:44:02.373 | wire | `turn.ended turn=1`，耗时 **105,418 ms** |

界面在 13:41:53 就已判完成；CLI 在 13:42:16 才启动通知轮，并继续跑了 105 秒。期间用户看到的一直是“完成”。

### 4.2 13:53 重跑：两条日志逐毫秒对齐

| 时刻 | 来源 | 事件 | 说明 |
|:---|:---|:---|:---|
| 13:53:57.688 | IDE | `[kimi acp] 连接启动 generation=1 pid=84143` | 新会话 |
| 13:53:58.052 | IDE | 初始化 2.7.2，`resume=True` | |
| 13:53:58.512 | wire | `turn 0` = Prompt A（修正版） | |
| 13:54:03.192 | wire | Bash：`mkdir -p .temp/kimi-ui-fix-check/` | |
| 13:54:06.649 | wire | Bash：`rm -f .../bg.txt` | 清残留 |
| 13:54:08.557 | wire | Bash：`sleep 25; printf done > .../bg.txt`（后台任务 `bash-bbz8r17i`） | 后台启动 |
| 13:54:08.574 | wire | `task.started status=running detached=true` | |
| 13:54:10.352 | wire | Bash：前台 `sleep 30` | |
| 13:54:33.578 | wire | `task.terminated status=completed` | 后台任务 25 秒完成 |
| 13:54:33.579 | wire | **`prompt.steered`**（`task:bash-bbz8r17i:completed`） | 通知插入当前 prompt |
| 13:54:40.368 | wire | `sleep 30` 返回；`step.end finish=tool_use` | 模型仍想继续 |
| **13:54:40.369** | wire | **`turn.ended turn=0 reason=completed`，41,855 ms** | 最后一步是 `tool_use` |
| **13:54:40.372** | wire | **`turn.prompt turn=1 trigger=notify`** | 隐藏通知轮启动 |
| **13:54:40.374** | IDE | **`end_turn requestId=5`** | 界面假完成 |
| 13:54:40.37x | 推断 | 客户端把排队待发文本（推断为 Prompt B）作为 `requestId=6` 发出 | 见 6.2 |
| 13:54:40.789 | IDE | 完成音 + 桌面通知 | 第一次“任务完成” |
| 13:54:43.695 | wire | 通知轮 Read `bg.txt` → `done` | |
| 13:54:47.992 | wire | 通知轮 Bash：`sleep 60` | |
| **13:55:31.437** | IDE | **`end_turn requestId=6`** | `sleep 60` 仍在跑，空回执 |
| 13:55:34.440 | IDE | `kimi 轮末上下文用量不可用` | 无本轮有效用量 |
| 13:55:34.443 | IDE | 完成音 + 桌面通知 | 第二次“任务完成” |
| 13:55:48.006 | wire | `sleep 60` 返回 | |
| 13:55:49.444 | wire | 通知轮 Read `bg.txt` → `done` | |
| 13:55:54.116 | wire | **`turn.ended turn=1`，73,743 ms** | 通知轮真正结束 |
| 13:55:54.118 | wire | `turn.prompt turn=2` = 用户“这里测试出现了卡死，断掉了ACP的对话。” | |
| **13:59:31.090** | IDE | **`end_turn requestId=7`** | wire 无对应 `turn.ended` |
| 13:59:34.096 | IDE | 完成音 + 桌面通知 | 第三次“任务完成” |
| 13:59:51.194 | wire | turn 2 仍在 `step 9 tool_use` → `step 10 begin` | 证明 13:59:31 是提前回执 |
| 14:08:02.575 | IDE | `AI 发起了提问，请在对话卡片中作答` | 孤儿轮次弹出 AskUserQuestion |
| 14:09:17.xxx | wire | turn 2 仍在追加 `step.begin` | 距界面“完成”已 9 分 43 秒 |
| **14:11:25.814** | wire | **`turn.ended turn=2`**（turn 2 总时长 15 分 31 秒） | 界面早已完成，孤儿轮才真正结束 |
| 14:11:25.819 | wire | `turn.prompt turn=3` = 单个字符 `s` | 来源见 §3.3 |
| 14:11:34.980 | wire | `turn 3` 完成 | IDE 日志无对应 `requestId=8` 响应记录 |

### 4.3 孤儿轮次的规模（13:55:54 – 14:11:25）

| 指标 | 值 |
|:---|:---|
| turn 2 执行步数 | **28 个 `step.end`** |
| 累计输出 token | **66,416** |
| 工具调用 | Bash 23、Grep 6、Read 6、AskUserQuestion 1、Write 1、Edit 1 |
| turn 2 时长 | 13:55:54.118 → 14:11:25.814，**15 分 31 秒** |
| 界面“完成”后的继续运行 | 13:59:34.096 → 14:11:25.814，**11 分 51 秒** |
| 截至 13:59 的 `turn.ended` | **0 条**（14:11:25 才补上） |
| FOLLOWUP_OK 命中 | 0 次（Prompt B 从未进入 wire） |

也就是说：界面早在 13:59:34 就宣布“完成”并播了铃，而 agent 在此后又跑了 11 分 51 秒、6.6 万输出 token，14:08 向界面发起提问，最后在 14:11 用 `Write`/`Edit` 自己写了一份事故诊断报告并改了 README。

---

## 5. 缺陷一（核心）：把 ACP response 当轮次终点

### 5.1 代码事实

`src/ide/llm/providers/kimi/provider.py:402-410`：

```python
if kind == "response":
    self._raise_on_turn_error(obj)
    pending_error = self._classify_stop_reason(obj, request_id)
    yield from self._collect_turn_end_usage(
        conn, turn_usage, sidecar, sidecar_queue,
        turn_push_stats)
    if pending_error is not None:
        raise pending_error
    return
```

只要收到 `kind == "response"`，无论 `stopReason` 是 `end_turn` 还是别的，消费循环就结束。它没有核对：

- CLI 的真实轮次是否已经 `turn.ended`；
- 是否存在仍在运行的 `trigger=notify` 通知轮；
- 是否存在 `task.started` 未 `task.terminated` 的后台任务；
- 本次 response 之前，用户 prompt 是否真正进入过 wire。

### 5.2 为什么 `end_turn` 不等于轮次结束

两次提前回执的证据：

- **13:55:31（requestId=6）**：wire 中 turn 1 的 `sleep 60` 从 13:54:48 跑到 13:55:48，`turn.ended` 在 13:55:54。13:55:31 时该轮仍在执行，ACP 却已回 `end_turn`。
- **13:59:31（requestId=7）**：wire 中 turn 2 没有 `turn.ended`，13:59:51 还在 `step 9 → step 10`。ACP 同样回了 `end_turn`。

这说明 CLI 的 ACP 适配层存在“忙态提前回执”：收到 prompt 后先在 ACP 层应答，真实执行留在内部轮次继续。对客户端而言，`stopReason=end_turn` 只证明“这个 JSON-RPC 请求有响应了”，不证明“用户任务做完了”。

### 5.3 后果

- `ChatWorker` 正常收尾 → `_on_finished("")` → `_set_busy(False)`；
- 历史里追加一条**空 assistant 消息**；
- `_start_usage_grace()`、`_stall_timer.stop()`、`turn_completed.emit(True)`；
- 主窗口 `_on_turn_completed` 播完成音、发桌面通知。

于是“界面状态”和“CLI 真实状态”从这一刻起彻底分叉，且没有任何后续机制能把它们重新对齐。

---

## 6. 缺陷二（放大器）：假完成瞬间自动续发排队文本

### 6.1 代码路径

`src/ide/gui/panels/chat/panel.py:1349-1362`（`_on_finished` 内）：

```python
# 三态收尾：正常完成自动发输入框待发内容（接续轮 busy 不间断）；
# 失败解除待发态——草稿本在输入框零回滚成本 + 系统提示（防连环
# 失败烧 token），下次正常发送成功后经本条成功路径自动恢复接续
if error:
    ...
elif not self._fire_pending_send():
    self._set_busy(False)
    self.turn_completed.emit(True)
```

`_fire_pending_send()`（`panel.py:1121`）在“正常完成”分支无条件执行：只要有待发文本，就 `_send_turn()` 立刻发出。它不知道这个“完成”是不是真的。

### 6.2 13:54:40 的 3 毫秒竞态

时间线：`turn.ended turn=0` 在 13:54:40.369，ACP 回执在 .374，而隐藏通知轮 `turn.prompt turn=1` 在 .372 启动。客户端在同一个瞬间把输入框里的待发文本作为 `requestId=6` 发进了一个刚刚进入通知轮的 agent。这段文本的内容有两种说法：本报告推断为按计划在 T+40 粘贴的 Prompt B；14:09 报告记为单字符 `s`。原始帧无法直接区分，但**无论内容是哪一种，机制与后果完全相同**。

高置信推断依据：

1. `begin_turn()` 只被 `KimiProvider.chat()` 调用，而 `chat()` 只由 `_send_turn()` 触发；
2. Prompt A 是 `requestId=5`，第二个用户发送只能是排队补发；时间点与文档要求的 T+40 吻合（若为 `s`，则说明输入框里当时只有一个误触字符）；
3. 客户端在轮末自动补发的行为与 `_fire_pending_send()` 完全一致，延迟仅 3 毫秒。

结果：这次补发的 prompt 在 13:55:31 收到空 `end_turn`，但**从未进入 wire**；`FOLLOWUP_OK` 全文命中 0 次，说明它被 CLI 的 ACP 层吞掉，既没执行也没回答。

### 6.3 闭环

```
用户输入 → 点发送/排队 → 假完成 → 自动补发 → CLI 忙 → 立刻空 end_turn
   ↑                                                        ↓
   └──────────── 完成音 + 置闲 + 按钮回“发送” ←────────────┘
```

这就是用户描述的“只要接着发送东西，它就立刻弹出任务结束的提示音，AI 也不会再次回答”。它不是单次故障，而是一个自激循环：每一次“发送”都被当成一次“完成”。

---

## 7. 缺陷三：假完成之后，停止按钮失效

### 7.1 `_turn_id` 已被清空

`src/ide/llm/providers/acp/connection.py:349-356`：

```python
def cancel_turn(self, session_id: str) -> bool:
    if self._turn_id is None or not self.is_alive:
        return False
```

`_iter_turn_chunks` 返回后，`chat()` 的 `finally` 调用 `conn.end_turn()`，`_turn_id` 被置空。此时用户按“■ 停止”：

`KimiProvider.cancel()`（`kimi/provider.py:221-233`）→ `conn.cancel_turn()` 返回 False（no-op）→ `abort_turn()` 只置了一个没有消费者的事件。**停止动作等于什么也没发生。**

### 7.2 没有 `session/close`

全仓库搜索 `session/close` 只在审计报告里出现，代码中没有实现。而 12:25 报告的探针已经实测：Kimi 2.7.1 的 `initialize` 声明了 `sessionCapabilities.close`，`session/close` 能停住 `origin.kind=task/trigger=notify` 的通知轮并回收后台子进程；`session/cancel` 不覆盖通知轮。

于是形成一个死结：**通知轮在跑 → 客户端认为完成 → `_turn_id=None` → cancel no-op → 没有 close 可用 → 通知轮永远停不下来。**

---

## 8. 缺陷四：跨轮帧未清理

`src/ide/llm/providers/kimi/provider.py:366` 的 `begin_turn()` 之前没有清队列；对照：

- `src/ide/llm/providers/local/provider.py:342`：`conn.purge_updates()`
- `src/ide/llm/providers/dsh/provider.py:360`：`conn.purge_updates()`

Kimi 是三个 ACP 后端里唯一不清理的。假完成之后，隐藏轮仍在往 `_updates` 队列写 `session/update`；这些迟到帧会被下一轮 `next_update()` 原样取出并映射成新轮次的内容/工具卡，造成串轮。`usage.poll()` 的空闲排空（`usage.py:_drain_idle_updates`）只保留 usage，其余帧被丢弃，所以这个缺陷在“空闲期”被部分掩盖，一旦用户立刻再发就会显形。

---

## 9. 上游触发边界（不等于把责任全推给上游）

必须承认，提前回执是上游行为，两次实测都在 2.7.2 上：

| 编号 | 上游行为 | 证据 |
|:---|:---|:---|
| U1 | 原用户轮最后一步是 `tool_use` 时写 `turn.ended reason=completed`，并向 ACP 回 `end_turn` | 13:54:40.368 `step.end finish=tool_use` → .369 `turn.ended` → .374 `end_turn` |
| U2 | 忙态收到新 prompt 时先回 `stopReason=end_turn`，真实执行留在内部轮或直接丢弃 | 13:55:31（requestId=6）、13:59:31（requestId=7）均无对应 `turn.ended` |

但这两条都**不足以让会话变成死局**。客户端本可以用已有的 wire 旁路自证：

- `wire.jsonl` 就在会话目录里，`WireSidecar` 已经在读；
- 判定规则很简单：**最后一个 `turn.prompt` 之后没有 `turn.ended`，就说明 agent 仍在跑**；或者存在 `task.started` 未 `task.terminated`；
- 三个提前回执全部能被这条规则拦住（见 4.2 的时间顺序）。

所以责任边界是：**上游负责“提前回执”，客户端负责“把提前回执当成终态，并让界面/停止/排队全部跟着走错”。** 后者才是让会话不可恢复的直接原因。

---

## 10. 症状映射

| 用户看到的现象 | 代码路径 | 触发条件 |
|:---|:---|:---|
| 一按发送就弹“任务完成”音，AI 不回答 | `provider.py:402-410` → `panel.py:1361` → `main_window._on_turn_completed` | CLI 忙态空 `end_turn` |
| 发送钮变成“排队/等待发送…”灰态，迟迟不恢复 | `panel.py:778-803`（`_refresh_send_button`）；`model_bar.py:342-353`（`set_busy` 禁四钮） | `_busy=True` 且 `_pending_send=True`；Kimi 不启动看门狗（`kimi/provider.py:208` `stall_watchdog=False`），busy 可无限持续 |
| 停止按钮按了没反应 | `connection.py:349-356` + `kimi/provider.py:221-233` | 假完成后 `_turn_id is None` |
| 界面显示“完成”，后台还在改文件/跑命令 | `provider.py:402-410`；无 `session/close` | 通知轮/孤儿轮 |
| 新消息上屏了但永远没有回答 | `_fire_pending_send()` 把 prompt 发进忙态 agent；Prompt B 未入 wire | 见 6.2 |
| 过几分钟突然弹出一个提问卡片 | 孤儿轮次 `AskUserQuestion`（14:08:02，`question_ec2bc4f9...`） | 孤儿轮仍在驱动 UI 交互 |

---

## 11. 为什么“CLI 里验证通过”不能证明 ACP 正常

Kimi Code CLI 有两条执行路径：

1. **交互模式（TUI）**：用户输入直接进 CLI 自己的循环，`end_turn` 由 CLI 内部消费，不经过 ACP `session/prompt` 响应；
2. **ACP 模式**：CLI 作为 agent 子进程，由 IDE 发 `session/prompt`，CLI 的 ACP 适配层负责“何时回 response”。

本次缺陷发生在第 2 条路径的适配层。CLI 交互模式下“通知轮正常接续”只能证明 CLI 内部轮次引擎没问题，不能证明 ACP 适配层会在正确的时机回 `end_turn`。这也解释了为什么用户侧验证“CLI 通过”与 IDE 侧“必然假完成”可以同时成立。

---

## 12. 修复方案

### P0-1 恢复轮末一致性核对（阻断假完成）

> 这不是新设计：`85cf8aa` 删除的 `kimi_turn_consistency.py` + `kimi_acp.py::_drain_turn_consistency` 就是这套逻辑。恢复时按 2.7.2 的 wire 事件字段适配，并保留“wire 不可读则 fail-open”。

**位置**：`src/ide/llm/providers/kimi/provider.py::_iter_turn_chunks`

**做法**：

1. 收到 `kind == "response"` 且 `stopReason == "end_turn"` 后，先不要 `return`；
2. 等待一个 300–500 ms 的确认窗口（让 wire 落盘）；
3. 读取 `wire.jsonl`，判定 agent 是否仍忙：
   - 最后一个 `turn.prompt` 之后没有对应的 `turn.ended` → 忙；
   - 存在 `task.started` 且无 `task.terminated` → 忙；
   - 出现 `trigger=notify` 的轮次且未结束 → 忙；
4. 仍忙 → 继续消费 `next_update()`，保持 `_busy=True`，不 `return`、不播完成音；可选发一条系统提示“CLI 仍在执行后台任务/通知轮，等待落定”；
5. 直到 wire 出现真实 `turn.ended`，或看门狗超时，才收尾；
6. **fail-open**：wire 不可读、格式漂移、旁路关闭时，退回当前行为，绝不因此阻塞正常后端。

**为什么可行**：三次提前回执全部能被“最后 turn.prompt 无 turn.ended”识别；`WireSidecar` 已经在读同一个文件，复用即可，不需要新增协议能力。

### P0-2 禁止向忙态 agent 自动续发

**位置**：`src/ide/gui/panels/chat/panel.py::_fire_pending_send`

**做法**：在自动补发前查询 P0-1 的同一个 `agent_idle` 信号；忙则保持“等待发送…”状态，不注入、不清空输入框、不播完成音。只有真实空闲才补发。

### P0-3 补 `session/close`，让停止真正生效

**位置**：`kimi/provider.py::cancel`、`acp/connection.py`、标签关闭/切后端路径

**做法**：

1. `initialize` 时记录 `sessionCapabilities.close`；
2. `cancel()` 在 `_turn_id is None` 时回落 `session/close`（会话级终止），不再 no-op；
3. 标签销毁、切换后端、应用退出统一走 `session/close`，再 `terminate()` 兜底；
4. 保留现有 `session/cancel` 作为正常轮次级取消。

### P1-1 补 `purge_updates()`

`kimi/provider.py::chat()` 在 `begin_turn()` 前调用 `conn.purge_updates()`，与 local/dsh 对齐。

### P1-2 完成提醒只在真完成时触发

`turn_completed.emit(True)` 必须挂在 P0-1 的“真实结束”分支上；假完成分支不发声、不通知，改为系统提示。

### P1-3 诊断补齐

轮末记录（不记正文）：ACP request id、response id、`stopReason`、sessionId、generation、wire 中该 prompt 的 `promptId/turnId`、最后 `turn.ended`、是否存在活跃 notify 轮、活跃 `taskId`。这样下一次同类问题无需再靠推断。

### 验收标准

| # | 验收项 | 通过条件 |
|:---|:---|:---|
| 1 | 假完成 | `end_turn` 后 wire 仍有活跃轮时，完成音次数 = 0，`turn_completed=True` 次数 = 0 |
| 2 | 自动续发 | 通知轮活跃期间，`_fire_pending_send` 注入次数 = 0 |
| 3 | 停止 | 假完成后按停止，`session/close` 实际发出且 CLI 进程内轮次停止（wire 停止增长） |
| 4 | 串轮 | 新轮首个 chunk 之前队列中不存在上一轮残留帧 |
| 5 | 可续 | 真完成后可正常继续对话，`requestId` 单调、无重复完成音 |
| 6 | 回归 | 正常对话、正常停止、普通断管恢复、排队发送四条路径不回归 |
| 7 | 真实复验 | 用 `2026-1121-1336` 的 Prompt A/B 在冻结态 AppImage 上跑通：`NOTIFY_DONE → PHASE1_OK → FOLLOWUP_OK` 按序出现，全程只有一次完成音 |

---

## 13. 复现与取证步骤

1. 冻结态启动，新会话切到 Kimi（CLI 2.7.2）。
2. 粘贴 Prompt A（13:53 修正版）。
3. 观察：`sleep 30` 返回后界面是否立刻“完成”（应为否）。
4. 在后台任务完成通知后粘贴 Prompt B。
5. 观察：是否出现“1 秒内空完成 + 完成音”；`FOLLOWUP_OK` 是否出现。
6. 取证命令：

```bash
# IDE 侧轮次与完成音
grep -nE "轮次响应|任务完成|需要你的回答" ~/.config/zen-studio/logs/zen-studio.log | tail -20

# CLI 真实轮次
.venv/bin/python - <<'PY'
import json, datetime, pathlib
p = pathlib.Path.home()/".kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_42ee0f58-6798-4617-af9b-e0a5767ad0ac/agents/main/wire.jsonl"
rows = [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]
for r in rows:
    if r["type"] in ("turn.prompt", "turn.ended", "task.started", "task.terminated"):
        t = datetime.datetime.fromtimestamp(r["time"]/1000).strftime("%H:%M:%S.%f")[:-3] if r.get("time") else "?"
        print(t, r["type"], r.get("turnId"), r.get("trigger") or r.get("reason") or "")
PY
```

判定：若 `turn.ended` 晚于或不存在于 ACP `end_turn` 的时间点，即为客户端假完成。

---

## 14. 附录

### 14.1 IDE 日志摘录（13:53 重跑，已脱敏）

```text
13:53:57,688 [kimi acp] 连接启动 generation=1 pid=84143
13:53:58,052 [kimi acp] 初始化 agent=Kimi Code CLI version=2.7.2 generation=1 resume=True
13:54:40,374 [kimi acp] 轮次响应 stopReason='end_turn' requestId=5 responseId=5 sessionId=session_42ee0f58-... generation=1
13:54:40,789 系统播放器播放：yup-03.wav
13:55:31,437 [kimi acp] 轮次响应 stopReason='end_turn' requestId=6 responseId=6 sessionId=session_42ee0f58-... generation=1
13:55:34,440 kimi 轮末上下文用量不可用（ACP 未送达、wire 无本轮有效用量）
13:55:34,443 系统播放器播放：yup-03.wav
13:59:31,090 [kimi acp] 轮次响应 stopReason='end_turn' requestId=7 responseId=7 sessionId=session_42ee0f58-... generation=1
13:59:34,096 系统播放器播放：yup-03.wav
14:08:02,575 桌面通知已发送：需要你的回答 / AI 发起了提问，请在对话卡片中作答
```

### 14.2 wire 事件表（turn 级）

| 时刻 | 事件 | turnId | trigger/reason | 备注 |
|:---|:---|:---|:---|:---|
| 13:53:58.512 | turn.prompt | 0 | normal | Prompt A |
| 13:54:33.579 | prompt.steered | — | — | 后台完成通知 |
| 13:54:40.369 | turn.ended | 0 | completed / 41,855 ms | 最后一步 tool_use |
| 13:54:40.372 | turn.prompt | 1 | notify | 隐藏通知轮 |
| 13:55:54.116 | turn.ended | 1 | completed / 73,743 ms | |
| 13:55:54.118 | turn.prompt | 2 | normal | 用户“卡死”消息 |
| 14:08:02 | AskUserQuestion | 2 | — | 孤儿轮向 UI 提问 |
| 14:11:25.814 | turn.ended | 2 | completed / 15 分 31 秒 | 界面完成后 11 分 51 秒才结束 |
| 14:11:25.819 | turn.prompt | 3 | normal | 单字符 `s` |
| 14:11:34.980 | prompt.completed | 3 | completed | IDE 无对应响应 |

### 14.3 代码位置索引

| 缺陷 | 文件:行 | 说明 |
|:---|:---|:---|
| D1 响应即收尾 | `src/ide/llm/providers/kimi/provider.py:402-410` | `kind == "response"` 直接 return |
| D2 假完成即自动续发 | `src/ide/gui/panels/chat/panel.py:1349-1362`、`1121` | `_on_finished` → `_fire_pending_send` |
| D3 停止失效 | `src/ide/llm/providers/acp/connection.py:349-356`、`src/ide/llm/providers/kimi/provider.py:221-233` | `_turn_id is None` no-op |
| D4 跨轮残留 | `src/ide/llm/providers/kimi/provider.py:366` | 缺 `purge_updates()`（local:342 / dsh:360 有） |
| D5 灰态 | `src/ide/gui/panels/chat/panel.py:778-803`、`model_bar.py:342-353` | busy/待发三态与四钮禁用 |

### 14.4 证据文件清单

- `~/.config/zen-studio/logs/zen-studio.log`
- `~/.kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_42ee0f58-6798-4617-af9b-e0a5767ad0ac/agents/main/wire.jsonl`
- `~/.kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_393f99fd-a1f4-42e6-92a0-cbb399190439/agents/main/wire.jsonl`
- `~/.kimi-code/sessions/wd_zen_studio_b126c4fffdd5/session_42ee0f58-6798-4617-af9b-e0a5767ad0ac/state.json`

---

## 15. 一句话总结

**Kimi 的 ACP 适配层会在轮次仍在执行时提前回 `end_turn`；我们把这条回执当成了任务完成，于是界面假完成、排队文本自动注入忙态 agent、完成音反复播放，而假完成又清掉了唯一的轮次句柄，让停止彻底失效——会话不是断了，是我们自己把它判死了。更准确地说：防线本来有，昨夜被主动撤走，今天上游一违约，故障就原样回来了。**
