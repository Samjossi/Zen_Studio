# ACP 轮次中断止血与 stopReason 修复落地计划

> **状态**：已实施
> **范围**：Kimi provider 的 `stopReason` 语义、`cancelled` 的收尾归类、面板停止原因枚举；**不含**连接层、恢复层、Worker 完成信号与 provider 状态机重构
> **时间**：2026-11-20 09:54（修订，UTC+8）
> **优先级**：高（用户当前无法连续工作）
> **依据**：
> - 用户 2026-11-20 指示：先修复频繁中断，不要让复杂度继续膨胀；三点收尾语义按建议执行
> - `文档/审计报告/2026-1120-0804_K3.1长任务后ACP无响应与断管问题审计报告.md`
> - `文档/审计报告/2026-1120-0926_ACP假完成与后台通知轮次状态脱节诊断报告.md`
> - ACP v1 规范：<https://agentclientprotocol.com/protocol/v1/prompt-turn>
> - Kimi Code CLI `kimi acp` 官方文档：<https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-acp.html>

---

## 1. 背景与目标

### 1.1 用户当前最痛的问题

用户反馈 Kimi 后台"经常断掉"，导致无法继续工作。本计划的目标不是把架构一次做漂亮，而是**用最小改动让中断不再被误报为成功、中断后能继续**。结构收敛全部推后。

### 1.2 现有日志告诉我们什么

对 `~/.config/zen-studio/logs/zen-studio.log`（约 9 MB，截至 09:53）做了只读统计，结果如下：

- 全日志 **只有 1 次**非预期 `connection_dead`：`2026-11-20 08:35:06`，`reason=eof`、`exitCode=-15`，是 0804 计划冻结态验收时**定点结束测试进程**造成的；
- 全日志 **零** `recovery_failed`；`session/resume` 只发生 1 次，也是同一次验收；
- 09:10–09:33 的事故会话（0926 报告主体）期间**没有任何连接死亡**，只有"轮末上下文用量不可用"；旧 agent 在 IDE 已播报完成后继续改文件。

因此，现有日志不支持"ACP 连接频繁断开"这个描述；更一致的形态是：**轮次终态判断错误，IDE 提前收尾或把未完成当完成，用户随后无法继续原会话。**

这直接决定本计划的优先级：先修 `stopReason` 语义，不碰连接层。

T1 仍会先与用户确认一次中断体感（整轮无响应 / 播报完成后仍在跑 / 真的连接报错），避免把"体感中断"错修成"连接断开"。无论确认结果如何，§4 的两项修复都成立。

### 1.3 P0 只做两件事

1. **`stopReason` 非 `end_turn` 不再当成功**：Kimi provider 目前完全忽略该字段，`cancelled` / `max_tokens` / `max_turn_requests` / `refusal` 全部走成功播报。
2. **`cancelled` 与手动停止同级收尾**：保留屏幕输出、回滚 history、取消待发、不播报成功、使用独立文案。

### 1.4 P1 暂缓（本计划明确不做）

以下内容在上一版设计中存在，现全部推后，理由见 §10：

- `TurnOutcome` 单一终止通道与 Worker `finished(object)` 信号改造；
- provider 会话恢复状态三态收敛；
- 轮末 3 秒窗口的迟到帧归属重做；
- `SessionRecord` 持久化 `sessionId`、跨重启重挂接；
- 任何读 `wire.jsonl` 判定完成/失败的逻辑；
- 连接层、`session/resume`、stderr、退出清理的任何改动。

---

## 2. 协议事实

### 2.1 一轮的终态只由 `session/prompt` 响应的 `StopReason` 表示

规范（<https://agentclientprotocol.com/protocol/v1/prompt-turn>）：

- "If there are no pending tool calls, the turn ends and the Agent **MUST** respond to the original `session/prompt` request with a `StopReason`."
- 取值：`end_turn`（模型完成且不再请求工具）、`max_tokens`、`max_turn_requests`、`refusal`、`cancelled`。
- 客户端发 `session/cancel` 后，agent **MUST** 在终止操作后用 `cancelled` 回应原请求。
- `session/update` 没有任何"轮次开始/结束"事件；且规范允许轮次外继续发 update。

### 2.2 Kimi 官方能力矩阵

官方文档（<https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-acp.html>）：

- `sessionCapabilities.resume = {}`，`session/resume` 重挂接但不回放历史；
- `session/update` 只有 `agent_message_chunk` / `tool_call*` / `plan` / `config_option_update` / `available_commands_update`，**不含轮次边界事件**；
- 日志写 stderr 与 `~/.kimi-code/logs/`。

### 2.3 责任边界

Kimi CLI 的内部通知轮次（`origin.kind=task`、`trigger=notify`）不映射到 ACP，IDE 在协议内不可见。若 Kimi 在用户 prompt 尚未执行时返回 `end_turn`，属上游违反 ACP 语义；IDE 无法在协议内识别，只能不误报、留证据。

因此本计划只修 IDE 自己确定做错的部分：**把非 `end_turn` 的终态当成成功**。不试图用 wire 猜上游。

---

## 3. 现状锚点

### 3.1 Kimi 完全忽略 `stopReason`（核心缺陷）

`src/ide/llm/providers/kimi_acp.py:1027`：

```python
@staticmethod
def _raise_on_turn_error(response: dict) -> None:
    """prompt 响应含 error 时抛错（stopReason 正常结束为静默返回）。"""
    if "error" in response:
        err = response["error"]
        raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
```

`_iter_turn_chunks()` 收到 `kind == "response"` 后直接进入 `_collect_turn_end_usage()` 并 `return`。后果：

- `cancelled` 被当正常完成：播报成功、写入 assistant 历史、自动发送排队待发；
- `max_tokens` / `max_turn_requests` / `refusal` 同样被当成功，用户以为任务做完了；
- 注释"stopReason 正常结束为静默返回"是错误认知的化石。

对照：`dsh_acp.py:511`、`dream_acp.py:352`、`local_acp.py:351`、`reasonix_acp.py:448` 至少识别 `stopReason == "error"`；`kilocode_acp.py` / `opencode_acp.py` 与 Kimi 一样没有判定。本期只改 Kimi（当前主力后端、事故主体），不扩大回归面。

### 3.2 面板的停止收尾靠布尔区分

`panel.py:358` 的 `_stopped_for_stall: bool` 与 `_on_stopped()` 无参数，只能区分"手动停止"与"看门狗停止"。要新增 `cancelled` 第三态，必须把信号与收尾入口改成带 reason 的形式，否则只能再加一个并列布尔——正是要避免的复杂度。

### 3.3 断管恢复与看门狗已可用，本期不动

- 0804 计划已实现：连接死亡保留 `session_id`、下一次手动发送 `session/resume`、失败 fail closed、stderr 有界诊断；真实 CLI 与 AppImage 均验收过。
- 1118 计划已实现：`abort_turn()` 本地中断、面板看门狗、停滞与普通停止分流。

本计划不重复建设，只在验收中回归它们，确认 `stopReason` 改动没有打断既有路径。

---

## 4. 设计决策

### D1 `stopReason` 分类表（Kimi）

| `stopReason` | 处理 | 用户可见结果 |
|:---|:---|:---|
| `end_turn` | 正常返回 | 现有成功路径，不变 |
| `cancelled` | 抛 `TurnCancelled` | 走停止收尾，文案"本轮已被取消"；不播报成功、不发待发、回滚 history、保留屏幕输出 |
| `max_tokens` | 抛 `RuntimeError`（含原因） | 现有失败路径：保留屏幕输出、回滚 history、取消待发、显示"输出达到模型长度上限，本轮未正常完成" |
| `max_turn_requests` | 抛 `RuntimeError`（含原因） | 失败路径，文案"单轮模型请求次数达到上限，本轮未正常完成" |
| `refusal` | 抛 `RuntimeError`（含原因） | 失败路径，文案"agent 拒绝继续本轮" |
| 缺失 / 未知 | 正常返回 + 警告日志 | 现有成功路径（fail-open，兼容旧 CLI 与未来枚举） |

未知值 fail-open 的理由：`StopReason` 可能扩展；fail-closed 会让旧 IDE 遇到新值永久卡 busy。日志必须记录原始值。

### D2 `cancelled` 走停止通道，而不是失败通道

新增 `TurnCancelled` 异常（放 `acp.py`，与 `TurnAborted` 同模块）：

- `TurnCancelled` 表示 **agent 明确回了 cancelled**；
- `TurnAborted` 表示 **IDE 本地中断**（用户停止或看门狗），语义不变；
- 两者都不属于"请求失败"，不显示 `[请求失败]`。

Worker 捕获 `TurnCancelled` 后发 `stopped_by_user("cancelled")`，面板走 `_on_stopped("cancelled")`。用户按停止导致的 `TurnCancelled` 优先显示为"已手动停止"：`_is_stop_requested` 为真时一律按用户停止收尾。

### D3 停止信号带 reason，删除并列布尔

`stopped_by_user: Signal()` → `Signal(str)`，取值 `"user" | "stall" | "cancelled"`：

- 停止钮 → `request_stop("user")`；
- 看门狗 → `request_stop("stall")`（仍调用 `provider.mark_stalled()`）；
- `cancelled` 终态 → Worker 直接 `emit("cancelled")`；
- 面板 `_stop_reason` 单一字段替代 `_stopped_for_stall`，`_on_stopped(reason)` 决定文案与 `_last_turn["outcome"]`。

`request_stop` 的 reason 只写面板字段，不改变 Worker/Provider 的协议取消行为。

### D4 不改 Worker 完成信号与 `TurnOutcome`

`finished_with_error(str)` 保持不变；本计划不引入 `TurnOutcome`、不改面板成功/失败分派、不改 provider 会话状态。理由：当前问题可以在现有失败/停止两条通道内解决，先止血。

### D5 抛错时机必须在用量收尾之后

`max_tokens` 等终态同样需要轮末用量与消耗统计。`_iter_turn_chunks()` 收到 response 后：

1. 先分类 `stopReason`，把待抛异常暂存；
2. 执行 `_collect_turn_end_usage()`（最多 3 秒，保持现状）；
3. 收尾完成后再抛异常。

否则会丢掉本轮的 `usage` / `speed` / `consumption`，让统计与徽章出现无谓缺口。

### D6 不改连接层、恢复层、退出层

见 §1.4。任何"顺手加固"都不在本期。

---

## 5. 实施步骤

### T1 确认中断形态（先于改码）

1. 向用户确认最近一次"断掉"的具体表现，三选一（可多选）：
   - 整轮无响应、界面一直转；
   - 界面提示完成/响铃，但任务其实没做完或文件还在变；
   - IDE 明确报连接错误/需要重新发送。
2. 取该时间点前后 200 行 `~/.config/zen-studio/logs/zen-studio.log`，核对是否存在 `connection_dead`、`recovery_failed`、`轮次取消`、`轮次本地中断`、`turn_response`（若已加）。
3. 若对应用户描述的是第 3 类且日志有非预期 EOF/exitCode，转入 0804 恢复链路的独立排查；本计划其余步骤仍按协议修复执行，不混为一谈。
4. 不改代码。

**检查点**：明确本次要修的是"终态误判"还是"连接断开"；若两者都有，分别记录，不合并结论。

### T2 `stopReason` 分类（Kimi）

1. `acp.py` 新增 `class TurnCancelled(Exception)`，加入 `__all__`；`ide/llm/__init__.py` 导出。
2. `kimi_acp.py` 新增 `_classify_stop_reason(response) -> Exception | None`，按 D1 表返回待抛异常或 `None`：
   - `cancelled` → `TurnCancelled("本轮已被取消")`；
   - `max_tokens` / `max_turn_requests` / `refusal` → `RuntimeError`（带中文原因与原始 `stopReason`）；
   - `end_turn` / 缺失 → `None`；
   - 未知 → `None` + `logger.warning`。
3. `_iter_turn_chunks()` 的 `kind == "response"` 分支改为：
   - 先 `pending_error = self._classify_stop_reason(obj)`；
   - `yield from self._collect_turn_end_usage(...)`；
   - 收尾结束后 `if pending_error is not None: raise pending_error`；
   - `end_turn` 路径行为完全不变。
4. 删除 `_raise_on_turn_error` 中"stopReason 正常结束为静默返回"的错误注释；保留原有 `"error" in response` 判断，且其优先级高于 `stopReason`（error 帧直接抛）。
5. 不修改 `_collect_turn_end_usage()` 的窗口长度与控制流。

**检查点**：`cancelled` 不再进入成功分支；`max_tokens` 仍保留本轮用量；`end_turn` 行为逐字节不变。

### T3 `cancelled` 收尾通道

1. `worker.py`：
   - `stopped_by_user = Signal()` → `Signal(str)`；
   - `except Exception` 分支顺序：`_is_stop_requested` → `emit("user")`；`isinstance(e, TurnCancelled)` → `emit("cancelled")`；否则 `finished_with_error.emit(str(e))`；
   - 其余三处 `emit()` 补 `"user"`（未启动即停止、正常停止、残余流停止）。
2. `panel.py`：
   - `_stopped_for_stall: bool` 改为 `_stop_reason: Literal["user", "stall"] | None`；
   - `request_stop(reason: str = "user")` 记录 reason；看门狗调 `request_stop("stall")`；
   - `_on_stopped(reason: str = "user")`，文案三分支：
     - `user`：`⏹ 已手动停止`
     - `stall`：`⏹ 已因无响应中断；下一次手动发送将尝试恢复原会话`
     - `cancelled`：`⏹ 本轮已被 agent 取消；原会话保留，可直接发送下一条`
   - `_last_turn["outcome"]` 分别记 `手动停止` / `看门狗停止` / `已取消`；
   - `turn_completed.emit(False)` 与待发取消语义保持不变。
3. `_on_finished` 不动；`recovery_pending` 与字符串前缀判断本期保留，留待 P1 统一清理。

**检查点**：三种停止原因在界面与轮次记账中可区分；无新增并列布尔。

### T4 回归与真实/冻结态验收

1. 新增测试（`tests/test_acp_recovery.py`）：
   - `stopReason` 参数化：`end_turn` / 缺失 / 未知 → 正常完成；
   - `cancelled` → `TurnCancelled`，且 `_collect_turn_end_usage` 已执行（用量不丢）；
   - `max_tokens` / `max_turn_requests` / `refusal` → `RuntimeError`，消息含原始 `stopReason`；
   - `"error" in response` 优先于 `stopReason`。
2. 面板/Worker 测试（`tests/test_turn_abort.py`、`tests/test_notification.py`）：
   - `stopped_by_user("cancelled")` → `_on_stopped("cancelled")`：成功通知 0 次、待发取消、history 回滚、文案正确；
   - `stopped_by_user("stall")` 仍触发 `mark_stalled()` 与恢复提示；
   - 用户按停止时即使 agent 回 `cancelled`，显示"已手动停止"；
   - 直接调用 `_on_stopped()` 的旧测试改为传参。
3. 既有回归：`test_acp_recovery.py` 14 项、`test_turn_abort.py` 11 项、`test_notification.py` 全绿；
4. 门禁：`bash scripts/check.sh` 全绿（基线 511 passed）。
5. 真实 Kimi：
   - 正常轮次日志出现 `stopReason=end_turn`；
   - 工具执行中按停止：面板"已手动停止"，不播报成功；
   - 若能让 agent 回 `cancelled`，面板显示"本轮已被取消"；
   - 断管恢复链路回归一次（确认未被本期改动破坏）。
6. 冻结态 AppImage：至少验证正常完成、手动停止两条；`cancelled` 若无稳定构造手段，如实标注未验证。

### T终 实施回写（固定收尾，不可省略）

- 将「与计划的偏差 / 验证结果 / 经验沉淀」三部分回写到本文档末尾，新增「实施结果回写」一节。
- 本文档状态由「草稿」更新为「已实施」。
- `文档/修改记录/` 新增对应记录，与本节互相引用（计划写过程与经验，修改记录写改动清单与验证）。
- 回写要求以 `work plans/README.md`《实施回写规范》为准；即使本计划其他步骤与此冲突，回写不可省略。

---

## 6. 涉及文件清单

| 文件 | 动作 |
|:---|:---|
| `src/ide/llm/providers/acp.py` | 新增 `TurnCancelled`，加入 `__all__` |
| `src/ide/llm/providers/kimi_acp.py` | `stopReason` 分类；收尾后抛错；删除错误注释 |
| `src/ide/llm/__init__.py` | 导出 `TurnCancelled` |
| `src/ide/gui/panels/chat/worker.py` | `stopped_by_user(str)`；捕获 `TurnCancelled` |
| `src/ide/gui/panels/chat/panel.py` | `_stop_reason` 枚举；`request_stop(reason)`；`_on_stopped(reason)` 三分支 |
| `tests/test_acp_recovery.py` | `stopReason` 映射与用量保留用例 |
| `tests/test_turn_abort.py` | 停止原因、看门狗、cancelled 收尾用例 |
| `tests/test_notification.py` | `_on_stopped` 调用适配 |
| `src/ide/llm/README.md`、`src/ide/gui/README.md` | 同步 `stopReason` 与停止原因口径 |
| `文档/修改记录/` | 实施后新增对应记录 |

---

## 7. 验收条件

### 7.1 功能验收

- `stopReason=cancelled`：成功通知 0 次、待发 0 次、assistant 历史写入 0 次，界面显示"已被取消"；
- `max_tokens` / `max_turn_requests` / `refusal`：不走成功播报，显示对应原因，屏幕已输出内容保留，用户消息不被静默吞掉；
- `end_turn`：行为与现状完全一致（含排队待发）；
- 缺失 / 未知 `stopReason`：正常收尾、不卡 busy、日志保留原始值；
- 用户按停止且 agent 回 `cancelled`：显示"已手动停止"，不显示"被取消"；
- 断管恢复、看门狗、排队发送不回归。

### 7.2 结构验收

| 项 | 现状 | 目标 |
|:---|:---|:---|
| 面板停滞标志 | `_stopped_for_stall` 布尔 | `_stop_reason` 三值枚举（含 cancelled） |
| `stopReason` 判定 | 无 | Kimi provider 单点分类 |
| 新增并列布尔 | — | 0 个 |
| 新增常驻状态机 | — | 0 个 |

### 7.3 工程验收

- `bash scripts/check.sh` 全绿；
- 新增用例覆盖 §5 T4.1/T4.2 全部条目；
- 真实 Kimi 正常/停止/断管恢复完成；未做项明确标注；
- 日志不出现 prompt 正文、工具输出、凭证。

---

## 8. 风险与注意点

### 8.1 抛错时机错了会丢用量

必须在 `_collect_turn_end_usage()` 之后抛（D5）。实现时先写一条断言式测试：`cancelled` 轮仍有 `usage` chunk。

### 8.2 用户停止与 agent cancelled 的优先级

`_is_stop_requested` 必须先判。否则用户按停止后 agent 回 `cancelled`，界面会错误显示"被 agent 取消"，让用户以为不是自己操作的。

### 8.3 未知 `stopReason` 不能 fail-closed

未来枚举扩展时，旧 IDE 必须能继续收尾。未知值放行 + 警告；测试覆盖。

### 8.4 不要把"修复假完成"写成"根治上游"

若 `end_turn` 提前返回真实存在，那是 Kimi CLI 违约。本期只保证 IDE 不把其它终态当成功；回写不得宣称上游问题已解决。

### 8.5 不扩大后端范围

只改 Kimi。其他 5 家已处理 `stopReason == "error"`，未被本次事故指向；统一映射留待 P1。

---

## 9. 已确认决策

### 9.1 用户已拍板（2026-11-20）

1. **`cancelled` 收尾**：按"与手动停止同级的非成功终态"处理——保留屏幕输出、回滚 history、取消待发、不播报成功、独立文案。
2. **`max_tokens`**：保留屏幕已输出内容；回滚 history；提示截断。
3. **里程碑**：先只做 P0 止血；结构收敛（原 M2）延后，不混在本期提交。

### 9.2 由用户指示确定的边界

- 不在本期引入 `TurnOutcome` / Worker 完成信号改造 / provider 三态 / 迟到帧归属 / 会话身份持久化。
- 先让用户能连续工作，再谈结构简化。

---

## 10. P1 暂缓清单（登记，不实施）

以下项在上一版设计中存在，现明确推后。触发条件：P0 上线后仍出现同类事故，或用户明确要求做结构简化。

| 项 | 原目标 | 暂缓理由 |
|:---|:---|:---|
| `TurnOutcome` 单通道 | 删除面板字符串前缀/鸭子属性，统一终态 | 需要改 Worker 信号与面板分派，回归面大于本期收益 |
| provider 三态 | 5 处置位收敛为 `ok/needs_recovery/failed` | 纯结构重构，不解决当前中断 |
| 轮末迟到帧归属 | response 后 3 秒内工作帧不冒充本轮 | 需要维护已展示工具集，且只覆盖 3 秒窗口 |
| 会话身份持久化 | 新标签/重启后重挂接原上下文 | 需产品决策，且涉及存档格式与关闭语义 |
| 读 wire 判完成 | — | 明确否决：Kimi 私有格式不能成为通用完成判据 |

---

*设计于 2026-11-20 09:54 (UTC+8)*

---

## 实施结果回写（2026-11-20 10:00 UTC+8）

T1–T4 与 T终 已执行，修改记录见 `文档/修改记录/2026-1120-1000_ACP轮次中断止血与stopReason修复实施记录.md`。以下为实施过程中相对原计划的偏差与经验。

### 与计划的偏差

1. **Worker 停止信号载荷**：计划 D3 写「`request_stop` 的 reason 只写面板字段」；实际同时透传给 `ChatWorker.request_stop(reason)`，使 `stopped_by_user(str)` 的载荷在 worker 侧即为 `user` / `stall` / `cancelled`。面板仍以 `_stop_reason` 作为本地来源优先判据，二者互补；`cancel()` 协议行为未变。
2. **分类函数形态**：`_classify_stop_reason()` 实现为实例方法而非 `@staticmethod`，因为需要 `sessionId` 与连接代次写日志，便于上游 `StopReason` 契约变化时取证。
3. **新增日志**：补了每条 prompt 响应的 `stopReason` info 日志（满足 T4.5「正常轮次日志出现 end_turn」的验收口径），计划行文中未逐字列出。
4. **测试桩适配**：`tests/test_turn_abort.py` 的假后端新增 `cancelled` 场景；`_StopRecorder.request_stop` 增加 `reason` 参数并记录；`tests/test_notification.py` 的 `_on_stopped()` 改为显式传 `"user"`。这些是计划「旧测试改为传参」的落地细节。
5. **T1 确认方式**：用户已指示直接执行，未逐项确认中断体感；以 0926 审计报告的现有日志统计为证据基线，未读取项目外日志。
6. **看门狗文案分流**：计划 D3 的 `stall` 文案写死「下一次手动发送将尝试恢复原会话」，但 `mark_stalled()` 仅 Kimi 实现；照搬会让其他后端承诺不存在的恢复能力。实际在 `_on_stopped("stall")` 按 provider 能力分流，其余后端提示「可重新发送，反复出现请新建会话」，轮次记账仍为「看门狗停止」。
7. **未完成项**：真实 Kimi 与冻结态 AppImage 验收未完成，原因见下，未用假后端结果冒充真实结论。

### 验证结果

- **全量门禁**：`UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh` 全绿，**525 passed**（基线 511 + 新增 14）；隐私扫描、QLabel 守卫、ruff 均通过。27 条 PySide6 teardown warning 为既有噪音，相关旧用例同样出现。
- **定向回归**：`test_acp_recovery.py` + `test_turn_abort.py` + `test_notification.py` 共 72 passed。新增覆盖：
  - `stopReason` 参数化：`end_turn` / 缺失 / 未知 → 正常完成；
  - `cancelled` → `TurnCancelled`，且轮末用量收尾已执行（usage chunk 在异常前产出）；
  - `max_tokens` / `max_turn_requests` / `refusal` → `RuntimeError` 且消息含原始值；
  - error 帧优先于 `stopReason`；
  - agent `cancelled` → `stopped_by_user("cancelled")`；用户停止优先于 agent cancelled；看门狗 `stall` 文案与记账；无 `mark_stalled` 的后端不承诺恢复。
- **真实 Kimi**：本机 CLI 2.6.3 在 `session/new` 返回 `-32603 Internal error`，prompt 尚未发出，无法完成正常轮次/手动停止/断管恢复真实验收；日志留存 `.temp/real_stopreason_check.log`。判定为当前执行环境限制（无外网或 CLI 凭证/服务不可用），未改代码绕过。
- **冻结态 AppImage**：未构建、未验证，如实标注为未做项。
- **未改变结论**：若 Kimi 真实存在「用户 prompt 未执行却回 `end_turn`」，仍属上游违约，本期未宣称已根治。

### 经验沉淀

1. ACP 轮次终态只能由 `session/prompt` 响应的 `StopReason` 判定；`session/update` 没有轮次边界，不能靠帧流猜测完成。
2. 非 `end_turn` 终态同样需要轮末用量：**分类必须早于收尾、抛错必须晚于收尾**，否则 usage / 速度 / 消耗出现无谓缺口。
3. 本地停止来源要在「请求停止」时固化并保留首次值；等收尾信号再区分，用户停止与看门狗都会被 agent 的 `cancelled` 覆盖。
4. 未知 `StopReason` 必须 fail-open 并记录原始值；fail-closed 会让旧 IDE 在新枚举出现时永久卡 busy。
5. 真实上游不可用时，应保留可复现的失败证据并明确标注未验证，不用内存假后端结果冒充真实验收。
