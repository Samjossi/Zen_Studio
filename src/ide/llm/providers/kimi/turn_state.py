"""Kimi 轮次元数据跟踪与 ACP 响应终态确认。

Kimi CLI 的 ACP 适配层会在内部轮次仍在执行时提前回 `end_turn`（2026-11-21
13:53 事故：假完成 → 排队文本注入忙态 → `_turn_id` 清空后停止失效）。
本模块只从 wire.jsonl 提取轮次元数据，在 ACP response 到达后核对真实
终态，是客户端侧的 fail-closed 防线；官方 ACP 单轮次契约仍是主链路。

口径约束：

- 只解析元数据（promptId/turnId/trigger/reason/finishReason/taskId），
  不保存 prompt 正文、工具参数、工具输出与 diff；
- 无风险证据时 wire 不可读一律 fail-open（维持旧行为）；有证据但
  wire 受损才 fail-closed（unknown），不让私有格式拖垮正常对话；
- 核心判定用 `turn.ended` + `prompt.completed` + 活跃通知/后台任务；
  `step.end` 的 finishReason 只作增强证据——`tool_use` 直接判失配，
  缺失时不单独判 unknown、也不据此误判 normal（保持排空等落盘）。
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ide.llm.providers.kimi import wire

logger = logging.getLogger(__name__)

#: 风险轮次「用户 prompt 未落盘」的有界确认窗口：上游写盘通常滞后 ACP
#: 回执数百毫秒，窗口内继续等 wire，超时判失配。v1 不暴露用户设置。
PROMPT_GRACE_MS = 1500

TurnConsistencyState = Literal["normal", "draining", "desynchronized", "unknown"]


class TurnDesynchronized(RuntimeError):
    """ACP 宣称正常完成，但 Kimi 私有轮次记录与之矛盾。

    走失败通道收尾：保留已显示正文与工具卡，回滚可重发的用户消息，
    不播完成音。区别于「请求失败」——这不是传输错误，是执行生命周期
    脱钩，已发生的工具副作用需要用户核对。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"轮次未确认完成：{reason}；已发生的工具副作用请核对。"
            "本轮不会自动重发；下一次手动发送将尝试恢复原会话")


@dataclass
class TurnMetadata:
    prompt_id: str
    turn_id: str
    origin_kind: str
    trigger: str
    notification_id: str | None = None
    last_finish_reason: str | None = None
    turn_ended: bool = False
    prompt_completed: bool = False
    turn_end_reason: str | None = None
    completion_reason: str | None = None
    #: wire 记录自带的毫秒时间戳：轮末日志据此重建「response 时间 vs
    #: turn.ended 落盘时间」时序（A10 诊断口径），不引入额外时钟
    turn_ended_at: int | None = None

    @property
    def completed(self) -> bool:
        return self.turn_ended and self.prompt_completed


@dataclass(frozen=True)
class TurnConsistencySnapshot:
    state: TurnConsistencyState
    reason: str
    agent_active: bool
    user_prompt_id: str | None
    user_turn_id: str | None
    last_finish_reason: str | None
    notification_ids: tuple[str, ...]
    active_task_ids: tuple[str, ...]
    #: 用户轮 turn.ended 的 wire 毫秒时间戳（缺=None）：轮末日志据此与
    #: ACP response 日志行互证「提前回执」时序
    user_ended_at_ms: int | None = None


class TurnStateTracker:
    """只跟踪当前 IDE 轮次之后新增的轮次元数据，不保留正文或工具载荷。

    增量游标 + 残行缓冲的读取纪律与 `wire.py` 一致：半行等下次补齐，
    损坏/截断/缺失不抛异常，落到 broken/missing 标记由 snapshot 降级判定。
    """

    def __init__(self, session_dir: Path, *, start_at_end: bool = True) -> None:
        self.path = wire.wire_path(session_dir)
        self.offset = 0
        self.buffer = b""
        self.available = False  # stat 成功过（文件存在）
        self.missing = False    # 最近一次读取 OSError
        self.broken = False     # 出现过坏行或截断
        self._turns: list[TurnMetadata] = []
        self._by_prompt: dict[str, TurnMetadata] = {}
        self._by_turn: dict[str, TurnMetadata] = {}
        self._finish_reasons: dict[str, str] = {}
        self._ended: dict[str, str | None] = {}
        self._completed: dict[str, str | None] = {}
        self._notifications: dict[str, TurnMetadata | None] = {}
        self._tasks_started: set[str] = set()
        self._tasks_terminated: set[str] = set()
        if start_at_end:
            try:
                self.offset = self.path.stat().st_size
                self.available = True
            except OSError:
                pass

    @staticmethod
    def _turn_key(value: object) -> str | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str) and value:
            return value
        return None

    def poll(self) -> None:
        """增量读取完整 JSONL 行；残行等待下次补齐，损坏行累计 broken。"""
        try:
            size = self.path.stat().st_size
            self.available = True
            self.missing = False
            if size < self.offset:
                self.offset = size
                self.buffer = b""
                self.broken = True  # 文件被截断/轮换：已读数据不可信
                return
            if size == self.offset:
                return
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
                self.offset = stream.tell()
        except OSError:
            self.missing = True
            return
        self.buffer += data
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for raw in lines:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.broken = True
                continue
            if isinstance(record, dict):
                self._consume(record)
            else:
                self.broken = True

    def _consume(self, record: dict) -> None:
        record_type = record.get("type")
        if record_type == "turn.prompt":
            self._consume_prompt(record)
        elif record_type == "prompt.steered":
            self._consume_steered(record)
        elif record_type == "turn.ended":
            turn_id = self._turn_key(record.get("turnId"))
            if turn_id is None:
                self.broken = True
                return
            reason = record.get("reason")
            self._ended[turn_id] = reason if isinstance(reason, str) else None
            ended_at = record.get("time")
            if turn := self._by_turn.get(turn_id):
                turn.turn_ended = True
                turn.turn_end_reason = self._ended[turn_id]
                if isinstance(ended_at, int) and not isinstance(ended_at, bool):
                    turn.turn_ended_at = ended_at
        elif record_type == "prompt.completed":
            prompt_id = record.get("promptId")
            if not isinstance(prompt_id, str) or not prompt_id:
                self.broken = True
                return
            reason = record.get("reason")
            self._completed[prompt_id] = reason if isinstance(reason, str) else None
            if turn := self._by_prompt.get(prompt_id):
                turn.prompt_completed = True
                turn.completion_reason = self._completed[prompt_id]
        elif record_type == "task.started":
            task_id = self._task_key(record)
            if task_id is not None:
                self._tasks_started.add(task_id)
        elif record_type == "task.terminated":
            task_id = self._task_key(record)
            if task_id is not None:
                self._tasks_terminated.add(task_id)
        elif record_type == "context.append_loop_event":
            event = record.get("event")
            if not isinstance(event, dict) or event.get("type") != "step.end":
                return
            turn_id = self._turn_key(event.get("turnId"))
            finish_reason = event.get("finishReason")
            if turn_id is None or not isinstance(finish_reason, str):
                return  # 任务跟踪同款容错：增强证据缺失不致命
            self._finish_reasons[turn_id] = finish_reason
            if turn := self._by_turn.get(turn_id):
                turn.last_finish_reason = finish_reason
        # 未知事件类型：wire 格式漂移不应让判定失效，静默忽略

    @staticmethod
    def _task_key(record: dict) -> str | None:
        info = record.get("info")
        task_id = info.get("taskId") if isinstance(info, dict) else None
        if not isinstance(task_id, str) or not task_id:
            task_id = record.get("taskId")
        return task_id if isinstance(task_id, str) and task_id else None

    def _consume_prompt(self, record: dict) -> None:
        prompt_id = record.get("promptId")
        turn_id = self._turn_key(record.get("turnId"))
        origin = record.get("origin")
        trigger = record.get("trigger")
        if (not isinstance(prompt_id, str) or not prompt_id or turn_id is None
                or not isinstance(origin, dict) or not isinstance(trigger, str)):
            self.broken = True
            return
        origin_kind = origin.get("kind")
        if not isinstance(origin_kind, str):
            self.broken = True
            return
        notification_id = origin.get("notificationId")
        if notification_id is not None and not isinstance(notification_id, str):
            self.broken = True
            return
        internal = origin_kind == "task" or trigger == "notify"
        if not internal and origin_kind != "user":
            self.broken = True
            return
        turn = TurnMetadata(
            prompt_id=prompt_id,
            turn_id=turn_id,
            origin_kind=origin_kind,
            trigger=trigger,
            notification_id=notification_id,
            last_finish_reason=self._finish_reasons.get(turn_id),
        )
        if turn_id in self._ended:
            turn.turn_ended = True
            turn.turn_end_reason = self._ended[turn_id]
        if prompt_id in self._completed:
            turn.prompt_completed = True
            turn.completion_reason = self._completed[prompt_id]
        self._turns.append(turn)
        self._by_prompt[prompt_id] = turn
        self._by_turn[turn_id] = turn
        if internal:
            self._notifications[prompt_id] = turn

    def _consume_steered(self, record: dict) -> None:
        prompt_ids = record.get("promptIds")
        if not isinstance(prompt_ids, list):
            prompt_ids = []
        notification_ids = [item for item in prompt_ids
                            if isinstance(item, str) and item]
        message_id = record.get("messageId")
        if not notification_ids and isinstance(message_id, str) and message_id:
            notification_ids = [message_id]
        if not notification_ids:
            return  # steering 载荷漂移不致命
        for notification_id in notification_ids:
            self._notifications.setdefault(notification_id, None)

    def _active_notification_ids(self) -> tuple[str, ...]:
        active = []
        for prompt_id, turn in self._notifications.items():
            if turn is None:
                if prompt_id not in self._completed:
                    active.append(prompt_id)
            elif not turn.completed:
                active.append(prompt_id)
        return tuple(active)

    def _active_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks_started - self._tasks_terminated))

    def snapshot(
        self,
        *,
        response_received: bool,
        prompt_grace_expired: bool = False,
    ) -> TurnConsistencySnapshot:
        """四态判定；`response_received=False` 时只回答「是否有待排空」。"""
        user = next((turn for turn in reversed(self._turns)
                     if turn.origin_kind == "user"), None)
        active_notifications = self._active_notification_ids()
        active_tasks = self._active_task_ids()
        agent_active = (bool(active_notifications) or bool(active_tasks)
                        or (user is not None and not user.completed))
        notification_ids = tuple(self._notifications)
        prompt_id = user.prompt_id if user else None
        turn_id = user.turn_id if user else None
        finish_reason = user.last_finish_reason if user else None
        ended_at = user.turn_ended_at if user else None

        def result(state: TurnConsistencyState, reason: str) -> TurnConsistencySnapshot:
            return TurnConsistencySnapshot(
                state=state,
                reason=reason,
                agent_active=agent_active,
                user_prompt_id=prompt_id,
                user_turn_id=turn_id,
                last_finish_reason=finish_reason,
                notification_ids=notification_ids,
                active_task_ids=active_tasks,
                user_ended_at_ms=ended_at,
            )

        if not response_received:
            if agent_active:
                return result("draining", "内部通知/后台任务仍活跃")
            return result("normal", "无待排空内部通知")
        if not self.available and not self._turns and not self._notifications:
            # D12：wire 从未可读且无任何证据——fail-open，私有格式不拖垮正常对话
            return result("normal", "wire 不可用且无风险证据")
        if self.broken or self.missing:
            # 已有风险证据但 wire 受损：fail-closed。唯一例外是已缓存证据
            # 足够完整（核心 + 增强证据都在内存里），与正常判定同口径。
            if (user is not None and user.completed
                    and user.completion_reason == "completed"
                    and user.turn_end_reason == "completed"
                    and user.last_finish_reason == "end_turn"
                    and not active_notifications and not active_tasks):
                return result("normal", "wire 受损但用户轮证据完整")
            return result("unknown", "wire 元数据不可用或格式损坏")
        if user is None:
            if agent_active or not prompt_grace_expired:
                return result("draining", "等待用户 prompt 落盘")
            return result("desynchronized", "ACP 已响应但未出现用户 turn.prompt")
        if not user.completed:
            return result("draining", "用户轮次尚未完整落盘")
        if (user.completion_reason != "completed"
                or user.turn_end_reason != "completed"):
            return result("desynchronized", "用户轮次终态不是 completed")
        if active_notifications or active_tasks:
            return result("draining", "内部通知/后台任务仍活跃")
        if user.last_finish_reason == "tool_use":
            return result("desynchronized", "用户轮次在 tool_use 后提前完成")
        if user.last_finish_reason != "end_turn":
            # 增强证据缺失或漂移：不单独判 unknown、也不误判 normal，
            # 保持排空等落盘；用户停止随时可用
            return result("draining", "等待最后 finishReason 落盘")
        return result("normal", "用户轮次完整且 agent 已空闲")
