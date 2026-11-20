> ⚠️ **归档声明**：本文档为历史快照，记录了特定时间点的决策与实施状态。
> 当前代码状态以对应源文件为准，本文档内容可能已过时，仅供参考。

# ACP 轮次中断止血与 stopReason 修复实施记录

> **状态**：已实施
> **范围**：Kimi provider 的 `stopReason` 语义、`cancelled` 收尾归类、面板/Worker 停止原因枚举
> **时间**：2026-11-20 10:00（UTC+8）
> **关联计划**：`work plans/2026-1120-0946_ACP轮次中断止血与stopReason修复落地计划.md`
> **关联审计**：`文档/审计报告/2026-1120-0926_ACP假完成与后台通知轮次状态脱节诊断报告.md`

## 1. 改动清单

| 文件 | 改动 |
|:---|:---|
| `src/ide/llm/providers/acp.py` | 新增 `TurnCancelled`（agent 明确回 `stopReason=cancelled`），与本地中断 `TurnAborted` 分列，加入 `__all__` |
| `src/ide/llm/__init__.py` | re-export `TurnCancelled` |
| `src/ide/llm/providers/kimi_acp.py` | 新增 `_classify_stop_reason()`：`cancelled` → `TurnCancelled`；`max_tokens` / `max_turn_requests` / `refusal` → 带原始值的 `RuntimeError`；`end_turn` / 缺失 → 正常；未知 → warning + fail-open。`_iter_turn_chunks()` 先分类、再执行轮末用量收尾、收尾完成后才抛。每条响应记录 `stopReason` 与 session ID/generation。`_raise_on_turn_error()` 保留 error 帧优先并删除错误注释 |
| `src/ide/gui/panels/chat/worker.py` | `stopped_by_user` 改为 `Signal(str)`，载荷 `user` / `stall` / `cancelled`；`request_stop(reason)` 保留首次来源；捕获 `TurnCancelled` 走 `emit("cancelled")`，`_is_stop_requested` 仍优先 |
| `src/ide/gui/panels/chat/panel.py` | 删除 `_stopped_for_stall` 布尔，改单一 `_stop_reason`；`request_stop(reason)` 记录并透传来源；看门狗改调 `request_stop("stall")`；`_on_stopped(reason)` 按 `手动停止` / `看门狗停止` / `已取消` 三分支收尾，本地已请求停止时优先本地来源 |
| `tests/test_acp_recovery.py` | 新增 8 项 `stopReason` 用例：`end_turn` / 缺失 / 未知正常完成、`cancelled` 抛 `TurnCancelled`、三类失败终态、error 帧优先，均断言轮末用量收尾已执行 |
| `tests/test_turn_abort.py` | 新增 6 项用例：agent `cancelled` → `stopped_by_user("cancelled")`、取消轮回滚与零成功通知、用户停止优先于 agent cancelled、看门狗文案、看门狗 `stall` 载荷回传、无恢复能力后端不承诺恢复；适配信号载荷与桩签名 |
| `tests/test_notification.py` | `_on_stopped()` 调用改为显式传 `"user"` |
| `src/ide/llm/README.md`、`src/ide/gui/README.md` | 同步 `stopReason` 分类与三种停止来源口径 |

## 2. 验证

- **静态检查**：`.venv/bin/python -m ruff check` 通过；`scripts/privacy_scan.sh` 与 `check_label_selectable_guard.py` 通过。
- **全量门禁**：`UV_CACHE_DIR=$PWD/.temp/uv-cache bash scripts/check.sh` → **525 passed**（基线 511 + 新增 14）；27 条 PySide6 teardown warning 与本次改动无关，既有用例同样出现。
- **定向回归**：`tests/test_acp_recovery.py`、`tests/test_turn_abort.py`、`tests/test_notification.py` 共 72 passed。
- **真实 Kimi**：本机 CLI 2.6.3 在 `session/new` 返回 `-32603 Internal error`，prompt 尚未发出，无法完成正常轮次/手动停止/断管恢复的真实验收；判定为当前环境（沙箱无外网或 CLI 凭证/服务不可用）限制，未改代码绕过。日志留存 `.temp/real_stopreason_check.log`。
- **冻结态 AppImage**：未构建、未验证，如实标注为未做项。
- **未做项**：`stopReason=cancelled` 的真实 agent 构造、断管恢复真实回归、AppImage 正常完成/手动停止两条均未执行；由内存假后端用例覆盖协议分支。

## 3. 与计划的偏差

- 计划 D3 写「`request_stop` 的 reason 只写面板字段」；实际同时把 reason 透传给 `ChatWorker.request_stop()`，使 `stopped_by_user` 载荷天然携带 `stall`。面板仍保留 `_stop_reason` 作为本地来源优先判据，二者互补；未改变 `cancel()` 协议行为。
- `_classify_stop_reason()` 实现为实例方法而非 `@staticmethod`，因为日志需要 `sessionId` 与连接代次，便于上游契约变化时取证。
- 新增 `stopReason` info 日志（计划 T4.5 要求正常轮次日志可见 `end_turn`），属计划未逐字列出的隐藏改动。
- 看门狗 `stall` 的收尾文案按 provider 能力分流：仅 `mark_stalled` 可调用时提示「下一次手动发送将尝试恢复原会话」，其他后端改为「可重新发送，反复出现请新建会话」。计划只按 Kimi 写死恢复文案，照搬会向 DSH/其他后端用户承诺不存在的恢复能力；`_last_turn` 仍统一记「看门狗停止」。
- 真实 Kimi 与 AppImage 验收未完成，原因见 §2；未用「单元测试通过」替代真实验收结论。
- T1 未向用户逐项确认中断体感（用户已指示直接执行）；以 0926 审计报告的现有日志统计为证据基线，未读取项目外日志。

## 4. 经验沉淀

- ACP 轮次终态只能由 `session/prompt` 响应的 `StopReason` 判定；`session/update` 没有轮次边界，不能靠帧流猜测完成。
- 非 `end_turn` 终态同样需要轮末用量：分类必须早于收尾、抛错必须晚于收尾，否则 usage/速度/消耗出现无谓缺口。
- 本地停止来源要在「请求停止」时固化并保留首次值；等收尾信号再区分，用户停止与看门狗都会被 agent 的 `cancelled` 覆盖。
- 未知 `StopReason` 必须 fail-open 并记录原始值；fail-closed 会让旧 IDE 在新枚举出现时永久卡 busy。
- 真实上游不可用时，应保留可复现的失败证据并明确标注未验证，不用假后端结果冒充真实验收。
