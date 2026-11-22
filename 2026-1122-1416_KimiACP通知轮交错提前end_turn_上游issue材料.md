# Kimi ACP 通知轮交错提前 end_turn 上游 issue 材料

> **状态**：草稿（未提交；按跟踪任务 §5，代提交须用户当次明确授权）
> **范围**：把全裸基线实验（2.5.0 / 2.7.2 各 3 次交错对照）整理成可直接提交给 MoonshotAI/kimi-code 的 issue 正文与脱敏证据
> **时间**：2026-11-22 14:16（整理）(UTC+8)
> **优先级**：高（上游仍无进展，且 2.5.0 也被证明不免疫）
> **依据**：
> - `2026-1122-1250_KimiACP全裸基线通知轮交错提前end_turn复现审计报告.md`
> - `2026-1122-1144_KimiACP连接层全裸回退与原始症状复现验证计划.md` §13
> - `Kimi上游ACP缺陷跟踪任务.md` §2、§3.2、§10

---

## 1. 用途与边界

- 本文件是**提交用材料**，不是审计结论的替代；结论以 `2026-1122-1250_…审计报告.md` 为准。
- **未向上游提交任何内容**。提交 issue / 评论必须由用户当次明确授权，且只使用本文件与 `.temp/b0-run/issue-package/` 中已脱敏的附件。
- 不修改上游仓库、不代用户回复评论；提交后只做只读跟踪。
- 材料只含合成测试提示词（`sleep`/读写 marker），不含真实项目路径、对话正文、工具参数、工具输出或凭证。

## 2. 提交字段建议

| 字段 | 建议值 |
|:---|:---|
| 仓库 | `MoonshotAI/kimi-code` |
| 类型 | 新建 issue（Bug），并在 #5095 留一条带链接的补充评论 |
| 标题 | `ACP: prompt arriving during a self-initiated notification turn is settled with an empty end_turn, then executed without session/update` |
| Labels | 若可写：`bug`、`acp`（仓库无 acp label 时只留 bug） |
| 版本 | 2.5.0 与 2.7.2 同报；强调「不是 2.6 回归」 |

为什么新建而不是只评论 #5095：#5095 的核心表述是「prompt 被吞/丢失」，而本次证据显示 **prompt 没有被丢弃，而是在结算之后被不可见地执行**（有文件副作用）。这是同一族但可区分的签名，独立 issue 更利于定位结算边界。

## 3. 可直接粘贴的 issue 正文（英文）

> 以下与 `.temp/b0-run/issue-package/issue-body.md` 同文；标题层级已下调以适配本文档。

### ACP: prompt arriving during a self-initiated notification turn is settled with an empty `end_turn`, then executed without `session/update`

#### Summary

When a `session/prompt` request arrives while the CLI is executing a self-initiated notification turn (in this case, a background Bash task completion), the ACP server answers the request in **~10 ms** with `stopReason: "end_turn"`, **zero** `agent_message_chunk`, and **zero** tool-call updates.

The prompt is not dropped. The CLI queues it, starts executing it exactly when the notification turn ends, performs the requested tool calls for **4.3–9.6 s**, and produces the expected file output — but emits **no `session/update` frames at all** for that turn. When a permission frame is emitted, it is the only late frame: `session/request_permission` arriving **187 ms** (this repro) to **6.2 s** (cancel probe) after the prompt response.

A client that trusts `end_turn` believes the turn is complete: it shows an empty reply, and it can no longer cancel the work, because `session/cancel` is a no-op once the client-side turn is closed.

#### Environment

- `kimi-code` CLI **2.5.0** (sha256 `289a8d33…fd6c`) and **2.7.2** (sha256 `7f82148d…a596`), Linux x86_64
- ACP v1 over stdio (ndjson JSON-RPC); client capabilities: `fs` read/write `false`, `terminal` `false`
- Model `kimi-for-coding`, thinking effort `max`
- Trigger: a background Bash task (`sleep 30`) started through the agent's own Bash tool

#### Reproduction (client-agnostic)

1. `initialize` → `session/new`.
2. **Prompt A:** "Start `sleep 30; echo bg-done > c2_bg_result.txt` in background mode; reply `C2-STARTED` immediately." The turn ends in ~4 s.
3. When the task completes, the CLI starts a notification turn. In its own wire log this appears as `turn.prompt` with `origin.kind="task"` (2.5.0) / `trigger="notify"` (2.7.2).
4. **Prompt B, sent while that turn is active** (in our runs, 31 s after Prompt A): "Read `c2_bg_result.txt`, write its content into `c2_marker.txt`, reply `C2-OK`."
5. Observe the `session/prompt` response for Prompt B: it arrives in **0.009–0.010 s** with `end_turn`, no content chunks, no tool frames.
6. Wait ~10 s: `c2_marker.txt` appears with content `bg-done`. The CLI's own wire shows the user turn ran **4344–9572 ms** and made **2 tool calls** (Read + Write).
7. Optional: send `session/cancel` after the empty `end_turn`; in our client the turn is already closed, so no `session/cancel` frame is produced and the hidden work continues to completion.

> Timing hint: the notification turn lasts roughly 2–8 s, so a fixed 31 s can miss it. The deterministic way is to watch the CLI's own `wire.jsonl` and send Prompt B the moment a `turn.prompt` with `origin.kind="task"` appears. In our six runs, every Prompt B that arrived within ±1 ms of the notification turn's `turn.ended` reproduced; the one that arrived 352 ms later did not.

#### Observed matrix

Six interleaving runs, same prompts, same client, two CLI versions:

| CLI | Prompt lands inside notification turn | `session/prompt` response | ACP text | wire: user-turn duration | wire: tool calls | marker written |
|:--|:--:|--:|--:|--:|--:|:--|
| 2.5.0 | yes (−1 ms) | **0.01 s**, `end_turn` | 0 | 4503 ms | 2 | yes |
| 2.5.0 | yes (−1 ms) | **0.01 s**, `end_turn` | 0 | 6585 ms | 2 | yes |
| 2.5.0 | yes (−1 ms) | **0.01 s**, `end_turn` | 0 | 9572 ms | 2 | yes |
| 2.7.2 | yes (+1 ms) | **0.01 s**, `end_turn` | 0 | 4344 ms | 2 | yes |
| 2.7.2 | yes (+1 ms) | **0.01 s**, `end_turn` | 0 | 4457 ms | 2 | yes |
| 2.7.2 | no (+352 ms) | 5.04 s, `end_turn` | 5 | 5032 ms | 3 | yes |

The correlation is the point: **every** follow-up that arrived within ±1 ms of the notification turn's end was settled empty; the only follow-up that arrived 352 ms after the boundary behaved normally. In other words, the prompt is queued and starts exactly at the notification-turn boundary, but the ACP response is sent on receipt.

This also reproduces on 2.5.0 (3/3), so it is **not** a 2.6 regression; the trigger is the interleaving, not the version.

#### Frame excerpt (2.5.0, metadata only)

```
c2s  session/prompt            id=6
s2c  {id:6, result:{stopReason:"end_turn"}}        +9 ms
s2c  session/request_permission id=1               +187 ms
c2s  {id:1, result:{optionId:"allow_always"}}      +187 ms
     (no session/update for this turn, ever)
```

#### Wire excerpt (2.5.0, metadata only)

```
turn.prompt  origin.kind=user   turnId=0  (Prompt A)
turn.ended   turnId=0  reason=completed  durationMs=3800
turn.prompt  origin.kind=task   turnId=1            <-- notification turn
turn.ended   turnId=1  reason=completed  durationMs=4195
turn.prompt  origin.kind=user   turnId=2            <-- starts at the same ms the notification turn ends
turn.ended   turnId=2  reason=completed  durationMs=4503
  event tool.call  event.turnId=2   (x2: Read + Write)
```

#### Expected behavior

- The `session/prompt` response should be tied to the execution of that prompt: either delay settlement until the queued turn actually runs and completes, or reject it with a retryable error. It must not be answered with `end_turn` on receipt.
- Self-initiated turns should emit `session/update` (or an explicit turn-start notification) so clients can see work they did not initiate.
- Such turns should be cancellable via `session/cancel`; today the client has no handle to cancel them.

#### Notes / relationship to existing issues

- Same signature as #5095 ("answered with empty end_turn and lost"), but with a crucial difference: **the prompt is not lost** — it is executed invisibly after settlement. The file side effect is observable.
- Also related: #4944 (settle queued prompts early, ignore cancellation), #4109 (self-started turns send no `session/update`), #1485, #4027.
- #5166 added `loop.drained` / `loop.idle` for the TUI; ACP does not consume these boundaries. They look like the right place to gate settlement.
- Suggested repro instrumentation: the CLI's own `wire.jsonl` under the session directory already contains `turn.prompt` / `turn.ended` / `tool.call` with `turnId`; no client-side patching needed.

#### Attachments

- `frames-c2-2.5.0.jsonl` — ACP frames for the failing run (metadata only: direction, method, id, update type, stopReason, byte count)
- `wire-c2-2.5.0-meta.jsonl` — CLI wire records for the same run (type / origin / trigger / turnId / reason / duration; prompts, tool args and tool output removed)
- `frames-c2-2.7.2.jsonl`, `wire-c2-2.7.2-meta.jsonl` — same for 2.7.2
- `frames-c2-2.7.2-control.jsonl` — the passing control run (prompt 352 ms after the boundary)
- `matrix.md` — the six-run matrix above

No prompt text, tool arguments, tool output, file contents, credentials or user paths are included.

---

## 4. 附件清单与脱敏核对

暂存目录：`.temp/b0-run/issue-package/`（不入库；提交时作为附件上传）。

| 文件 | 内容 | 脱敏 |
|:---|:---|:---|
| `issue-body.md` | 上述正文的独立副本 | 无路径/凭证 |
| `frames-c2-2.5.0.jsonl` | 2.5.0 失败轮的 ACP 帧元数据 | 仅方向/方法/请求号/update 类型/stopReason/字节数 |
| `wire-c2-2.5.0-meta.jsonl` | 同轮 CLI wire 元数据 | 仅 type/origin/trigger/turnId/reason/duration；正文与工具载荷已剔除 |
| `frames-c2-2.7.2.jsonl` | 2.7.2 失败轮帧 | 同上 |
| `wire-c2-2.7.2-meta.jsonl` | 2.7.2 失败轮 wire | 同上 |
| `frames-c2-2.7.2-control.jsonl` | 2.7.2 对照轮（+352ms，未复现） | 同上 |
| `matrix.md` | 六轮矩阵 | 无 |

已做扫描：`/home/`、`/tmp/`、`sk-`、`Bearer ` 命中数 **0**。

## 5. 与既有 issue 的定位差异

| 既有条目 | 它的主张 | 本次补充 |
|:---|:---|:---|
| #5095 | 忙态 prompt 被空 `end_turn` 回答并**丢失** | 本次证明 prompt **未被丢弃**：4.3–9.6s 后在不可见轮次里执行并产生文件副作用；唯一迟到帧是权限请求 |
| #4944 | 排队 prompt 提前结算、取消失效 | 补齐毫秒级相关性：追问与通知轮收尾相差 ±1ms 即 100% 中招，+352ms 即正常 |
| #4109 | 自发轮次不发 `session/update` | 本次把「不可见」与「提前结算」绑定为同一条因果链 |
| #1485 | 空 `agent_message_chunk` | 本次是空 `end_turn` + 隐藏执行，不是单纯空正文 |

版本意义：2.5.0 **3/3**、2.7.2 **2/3**，说明这不是 2.6 引入的回归；#4630 至多改变触发窗口大小，不是根因。

## 6. 提交前检查清单

- [ ] 用户当次明确授权提交（本文件默认不提交）。
- [ ] 再次跑脱敏扫描：附件中不得出现真实路径、用户名、项目名、对话正文、工具参数/输出、凭证。
- [ ] 核对 CLI 版本与 sha256：2.5.0 `289a8d33…fd6c`；2.7.2 `7f82148d…a596`。
- [ ] 确认引用编号存在且未被关闭：#5095 / #4944 / #4109 / #1485 / #4027；#5166。
- [ ] 提交后把 URL 回填到 `Kimi上游ACP缺陷跟踪任务.md` §10。

## 7. 提交后跟踪动作

1. 每次新正式版发布，用同一 C2 探针回归：后台 `sleep 30` → 通知轮活跃期发追问 → 看 response 是否仍在 10ms 内空回。
2. 判定「交错结算已修」的最小条件：追问的 `session/prompt` response 不再早于该 prompt 实际执行；且隐藏轮不再产生无 `session/update` 的工具副作用。
3. 在此之前，客户端防线按既有审计 P0-1/P0-2/P1-1 维持并补强，不因上游对话进展而撤除。
