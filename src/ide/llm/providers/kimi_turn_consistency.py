"""Kimi 主 agent wire 的轮次元数据跟踪与 ACP 响应一致性判定。"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TurnConsistencyState = Literal["normal", "draining", "desynchronized", "unknown"]


class TurnDesynchronized(RuntimeError):
    """ACP 宣称正常完成，但 Kimi 私有轮次记录与之矛盾。"""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Kimi 轮次状态失配：{reason}；原请求未被确认完成，"
            "请核对已发生的工具副作用。下一次手动发送将恢复原会话，"
            "不会自动重发原请求")


@dataclass
class _TurnMetadata:
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


class KimiTurnTracker:
    """只跟踪当前 IDE 轮次之后新增的轮次元数据，不保留正文或工具载荷。"""

    def __init__(self, session_dir: Path, *, start_at_end: bool = True) -> None:
        self.path = session_dir / "agents" / "main" / "wire.jsonl"
        self.offset = 0
        self.buffer = b""
        self.available = False
        self.missing = False
        self.broken = False
        self._turns: list[_TurnMetadata] = []
        self._by_prompt: dict[str, _TurnMetadata] = {}
        self._by_turn: dict[str, _TurnMetadata] = {}
        self._finish_reasons: dict[str, str] = {}
        self._ended: dict[str, str | None] = {}
        self._completed: dict[str, str | None] = {}
        self._notifications: dict[str, _TurnMetadata | None] = {}
        self._missing_prompt_expired = False
        if start_at_end:
            try:
                self.offset = self.path.stat().st_size
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
        """增量读取完整 JSONL 行；残行等待下次补齐，格式损坏则 fail closed。"""
        try:
            size = self.path.stat().st_size
            self.available = True
            self.missing = False
            if size < self.offset:
                self.offset = size
                self.buffer = b""
                self.broken = True
                return
            if size == self.offset:
                return
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
                self.offset = stream.tell()
            self.available = True
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
            if turn := self._by_turn.get(turn_id):
                turn.turn_ended = True
                turn.turn_end_reason = self._ended[turn_id]
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
        elif record_type == "context.append_loop_event":
            event = record.get("event")
            if not isinstance(event, dict) or event.get("type") != "step.end":
                return
            turn_id = self._turn_key(event.get("turnId"))
            finish_reason = event.get("finishReason")
            if turn_id is None or not isinstance(finish_reason, str):
                self.broken = True
                return
            self._finish_reasons[turn_id] = finish_reason
            if turn := self._by_turn.get(turn_id):
                turn.last_finish_reason = finish_reason

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
        turn = _TurnMetadata(
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
            self.broken = True
            return
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

    def snapshot(
        self,
        *,
        response_received: bool,
        missing_prompt_expired: bool = False,
    ) -> TurnConsistencySnapshot:
        self._missing_prompt_expired |= missing_prompt_expired
        user = next((turn for turn in reversed(self._turns)
                     if turn.origin_kind == "user"), None)
        active_notifications = self._active_notification_ids()
        user_active = response_received and user is not None and not user.completed
        active = bool(active_notifications) or user_active
        notification_ids = tuple(self._notifications)
        prompt_id = user.prompt_id if user else None
        turn_id = user.turn_id if user else None
        finish_reason = user.last_finish_reason if user else None

        def result(state: TurnConsistencyState, reason: str) -> TurnConsistencySnapshot:
            return TurnConsistencySnapshot(
                state=state,
                reason=reason,
                agent_active=active,
                user_prompt_id=prompt_id,
                user_turn_id=turn_id,
                last_finish_reason=finish_reason,
                notification_ids=notification_ids,
            )

        if not response_received:
            if active_notifications:
                return result("draining", "内部通知轮次仍活跃")
            return result("normal", "无待排空内部通知")
        if self.broken:
            return result("unknown", "wire 元数据不可用或格式损坏")
        if self.missing and (self.available or self._notifications or user is not None):
            return result("unknown", "风险核对期间 wire 暂不可读")
        if user is None:
            if not self.available and not self._notifications:
                return result("normal", "wire 暂缺且无通知风险")
            if active_notifications or not self._missing_prompt_expired:
                return result("draining", "等待用户 prompt 落盘")
            return result("desynchronized", "ACP 已响应但未出现用户 turn.prompt")
        if not user.completed:
            return result("draining", "用户轮次尚未完整落盘")
        if user.completion_reason != "completed" or user.turn_end_reason != "completed":
            return result("desynchronized", "用户轮次终态不是 completed")
        if user.last_finish_reason == "tool_use":
            return result("desynchronized", "用户轮次在 tool_use 后提前完成")
        if user.last_finish_reason != "end_turn":
            return result("unknown", "用户轮次缺少可识别的最后 finishReason")
        if active_notifications:
            return result("draining", "内部通知轮次仍活跃")
        return result("normal", "用户轮次完整且 agent 已空闲")
