"""KAP 事件 → 现有 Kimi Chunk 载荷的映射。

KAP 的 `item.*` 与 ACP 的 `session/update` 不是同一套字段，但 Kimi 的
工具卡格式（`ToolCallPayload` / `ToolUpdatePayload`）已经在 ACP 链路定型。
本模块把 KAP item 先适配成 `session/update` 形状，再交给同厂商的
`KimiUpdateMapper`：卡片、diff、入参摘要、问题选项、TODO 语义全部复用，
KAP 不另造一套 GUI 载荷。

文本/思维链走 `item.delta` 增量；`item.ended` 只在增量有缺口时补发尾部，
避免「开始帧带全文 + delta + 结束帧全文」三路重复。工具输出不逐 delta
上屏，等 `item.updated`/`item.ended` 的完整快照，防止每来一个字符就
重绘一张卡。
"""
from __future__ import annotations

import json
from typing import Any

from ide.llm.base import Chunk, TurnConsumption, UsageStats
from ide.llm.providers.kimi.models import TodoEntry, TodoPayload
from ide.llm.providers.kimi.updates import KimiUpdateMapper

#: KAP 工具名 → ACP tool_kind；显式 display.kind 优先于本表。
_TOOL_NAME_KINDS: dict[str, str] = {
    "bash": "execute",
    "shell": "execute",
    "exec": "execute",
    "execute": "execute",
    "edit": "edit",
    "write": "edit",
    "str_replace": "edit",
    "strreplace": "edit",
    "apply_patch": "edit",
    "multiedit": "edit",
    "multi_edit": "edit",
    "read": "read",
    "readfile": "read",
    "read_file": "read",
    "readmediafile": "read",
    "grep": "search",
    "glob": "search",
    "search": "search",
    "fetchurl": "fetch",
    "fetch_url": "fetch",
    "websearch": "fetch",
    "web_search": "fetch",
    "fetch": "fetch",
    "agent": "think",
    "task": "think",
    "subagent": "think",
    "think": "think",
}

_DISPLAY_KINDS: dict[str, str] = {
    "command": "execute",
    "execute": "execute",
    "diff": "edit",
    "edit": "edit",
    "write": "edit",
    "patch": "edit",
    "read": "read",
    "search": "search",
    "fetch": "fetch",
}


def _normalize_tool_name(name: object) -> str:
    text = str(name or "").strip().lower()
    if "__" in text:
        text = text.split("__")[-1]
    return text


def _tool_kind(item: dict) -> str:
    display = item.get("display")
    if isinstance(display, dict):
        display_kind = display.get("kind")
        if isinstance(display_kind, str):
            mapped = _DISPLAY_KINDS.get(display_kind.strip().lower())
            if mapped:
                return mapped
    return _TOOL_NAME_KINDS.get(_normalize_tool_name(item.get("name")), "other")


def _raw_input(item: dict) -> dict:
    raw = item.get("input")
    if isinstance(raw, dict):
        return dict(raw)
    text = item.get("input_text")
    if isinstance(text, str) and text.strip().startswith("{"):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    display = item.get("display")
    if isinstance(display, dict):
        # display 是展示层摘要，只在 rawInput 缺失时补命令等最小字段，
        # 不能整块塞进 rawInput（会把展示字段误当协议参数）。
        for key in ("command", "path", "url", "query", "pattern"):
            value = display.get(key)
            if isinstance(value, str) and value:
                return {key: value}
    return {}


def _content(item: dict) -> list[dict] | None:
    """display 的 diff 形态 → `_extract_diff` 能吃的 content 数组。"""
    display = item.get("display")
    if isinstance(display, list):
        return display
    if not isinstance(display, dict):
        return None
    old_text = display.get("oldText")
    new_text = display.get("newText")
    if isinstance(old_text, str) or isinstance(new_text, str):
        row = {"type": "diff"}
        if isinstance(old_text, str):
            row["oldText"] = old_text
        if isinstance(new_text, str):
            row["newText"] = new_text
        return [row]
    content = display.get("content")
    return content if isinstance(content, list) else None


def _output_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _error_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        message = value.get("message") or value.get("msg")
        return str(message) if message else json.dumps(value, ensure_ascii=False)
    return str(value)


def _acp_tool_start(item: dict, kind: str) -> dict:
    update: dict[str, Any] = {
        "sessionUpdate": "tool_call",
        "toolCallId": str(item.get("item_id") or ""),
        "title": str(item.get("name") or "?"),
        "kind": kind,
        "rawInput": _raw_input(item),
    }
    content = _content(item)
    if content:
        update["content"] = content
    return update


def _acp_tool_update(item: dict, kind: str, status: str) -> dict:
    update: dict[str, Any] = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": str(item.get("item_id") or ""),
        "title": str(item.get("name") or "?"),
        "kind": kind,
        "status": status,
        "rawInput": _raw_input(item),
    }
    content = _content(item)
    if content:
        update["content"] = content
    raw_output: dict[str, Any] = {}
    output = _output_text(item.get("output"))
    if output is not None:
        raw_output["output"] = output
    error = _error_text(item.get("error"))
    if error:
        raw_output["error"] = {"message": error}
    if raw_output:
        update["rawOutput"] = raw_output
    return update


def _todo_entry(item: object) -> TodoEntry | None:
    if not isinstance(item, dict):
        return None
    content = item.get("title")
    if not isinstance(content, str) or not content:
        content = item.get("content")
    if not isinstance(content, str) or not content:
        return None
    entry = TodoEntry(content=content)
    status = item.get("status")
    if isinstance(status, str):
        entry["status"] = "completed" if status == "done" else status
    return entry


class KapEventMapper:
    """单 provider 实例私有的 KAP 事件映射器。"""

    def __init__(self) -> None:
        self._acp = KimiUpdateMapper()
        #: item_id → item 类型（delta 只带 id，不带类型）
        self._item_types: dict[str, str] = {}
        #: item_id → 已发出文本长度（补尾差量用）
        self._emitted: dict[str, int] = {}
        #: tool item 快照（item.updated 只带增量字段）
        self._tool_items: dict[str, dict] = {}
        #: 会话级 todo 上次快照（changed 高亮）
        self._todo_snapshot: list[TodoEntry] = []

    def reset(self) -> None:
        self._acp.reset()
        self._item_types.clear()
        self._emitted.clear()
        self._tool_items.clear()
        self._todo_snapshot = []

    def item_type(self, item_id: str) -> str | None:
        """delta 只带 item_id；未知类型的 delta 不得猜成正文。"""
        return self._item_types.get(item_id)

    def end_turn(self) -> None:
        """轮次收尾清文本/工具账本；todo 快照跨轮保留（清单连续高亮）。"""
        self._item_types.clear()
        self._emitted.clear()
        self._tool_items.clear()

    # ------------------------------------------------------------------
    # item 事件
    # ------------------------------------------------------------------
    def map_item_started(self, item: dict, fallback_context: int | None = None) -> list[Chunk]:
        item_type = str(item.get("type") or "")
        item_id = str(item.get("item_id") or "")
        if item_id:
            self._item_types[item_id] = item_type
        if item_type in ("assistant_message", "reasoning"):
            return self._emit_text(item_type, item_id, str(item.get("text") or ""))
        if item_type == "tool_call":
            self._tool_items[item_id] = dict(item)
            kind = _tool_kind(item)
            chunks = [self._acp.map_session_update({
                "params": {"sessionId": "", "update": _acp_tool_start(item, kind)}})]
            # 开始帧已带完整输出/错误的非流式工具（如非交互工具），补一帧
            # 终态更新，否则卡片会停在 running。
            if item.get("status") == "ended" or item.get("output") is not None \
                    or item.get("error") is not None:
                status = _acp_status(item)
                chunks.append(self._acp.map_session_update({
                    "params": {"sessionId": "", "update": _acp_tool_update(item, kind, status)}}))
            return [chunk for chunk in chunks if chunk is not None]
        return []

    def map_item_ended(self, item: dict, fallback_context: int | None = None) -> list[Chunk]:
        item_type = str(item.get("type") or "")
        item_id = str(item.get("item_id") or "")
        if item_type in ("assistant_message", "reasoning"):
            return self._emit_text(item_type, item_id, str(item.get("text") or ""))
        if item_type == "tool_call":
            self._tool_items[item_id] = dict(item)
            kind = _tool_kind(item)
            status = _acp_status(item)
            chunk = self._acp.map_session_update({
                "params": {"sessionId": "", "update": _acp_tool_update(item, kind, status)}})
            return [chunk] if chunk is not None else []
        return []

    def map_item_updated(self, message: dict) -> list[Chunk]:
        item_id = str(message.get("item_id") or "")
        current = dict(self._tool_items.get(item_id) or {})
        if not current:
            current = {"item_id": item_id, "name": "?", "type": "tool_call"}
        for key, value in (message.get("set") or {}).items():
            current[key] = value
        self._tool_items[item_id] = current
        kind = _tool_kind(current)
        status = _acp_status(current)
        chunk = self._acp.map_session_update({
            "params": {"sessionId": "", "update": _acp_tool_update(current, kind, status)}})
        return [chunk] if chunk is not None else []

    def map_item_removed(self, message: dict) -> list[Chunk]:
        item_id = str(message.get("item_id") or "")
        current = dict(self._tool_items.pop(item_id, {}) or {})
        if not current:
            return []
        current.update({"status": "ended", "outcome": "failed",
                        "error": "上游撤回了该工具调用"})
        kind = _tool_kind(current)
        chunk = self._acp.map_session_update({
            "params": {"sessionId": "", "update": _acp_tool_update(current, kind, "failed")}})
        return [chunk] if chunk is not None else []

    def map_delta(self, message: dict) -> list[Chunk]:
        item_id = str(message.get("item_id") or "")
        item_type = self._item_types.get(item_id)
        field = message.get("field")
        delta = message.get("delta")
        if not isinstance(delta, str) or not delta:
            return []
        if field != "text":
            # 工具输出/入参的逐字符 delta 不上屏；完整快照由 item.updated /
            # item.ended 统一给卡片。
            if item_id in self._tool_items and field in ("output", "input_text"):
                current = self._tool_items[item_id]
                key = "output" if field == "output" else "input_text"
                current[key] = str(current.get(key) or "") + delta
            return []
        if item_type == "reasoning":
            self._emitted[item_id] = self._emitted.get(item_id, 0) + len(delta)
            return [Chunk("reasoning", delta)]
        if item_type == "assistant_message":
            self._emitted[item_id] = self._emitted.get(item_id, 0) + len(delta)
            return [Chunk("text", delta)]
        # 未知 item_id 不猜正文：结束帧会带全文，由 map_item_ended 补尾；
        # 把通知/旧轮的 delta 猜成正文会把后台内容注入用户答案。
        return []

    def _emit_text(self, item_type: str, item_id: str, full: str) -> list[Chunk]:
        """发全文增量：已发过 delta 则只补尾部；无缺口返回空。"""
        if not full:
            return []
        emitted = self._emitted.get(item_id, 0)
        if len(full) <= emitted:
            return []
        suffix = full[emitted:]
        self._emitted[item_id] = len(full)
        return [Chunk("reasoning" if item_type == "reasoning" else "text", suffix)]

    # ------------------------------------------------------------------
    # 会话级事件
    # ------------------------------------------------------------------
    def map_todo(self, event: dict) -> Chunk | None:
        entries: list[TodoEntry] = []
        for item in event.get("items") or []:
            entry = _todo_entry(item)
            if entry is None:
                continue
            previous = (self._todo_snapshot[len(entries)]
                        if len(entries) < len(self._todo_snapshot) else None)
            if previous is None or any(
                    entry.get(key) != previous.get(key)
                    for key in ("content", "status")):
                entry = {**entry, "changed": True}
            entries.append(entry)
        self._todo_snapshot = entries
        marks = {"pending": "[ ]", "in_progress": "[>]"}
        text = "\n".join(
            f"- {marks.get(entry.get('status') or '', '[x]')} {entry['content']}"
            for entry in entries) + ("\n" if entries else "")
        return Chunk("todo", text, payload=TodoPayload(entries=entries)) if entries else None

    def map_context(self, event: dict, fallback_size: int | None = None) -> Chunk | None:
        used = event.get("context_tokens")
        if not isinstance(used, int) or used < 0:
            return None
        size = event.get("max_context_tokens")
        if not isinstance(size, int) or size <= 0:
            size = fallback_size
        if not isinstance(size, int) or size <= 0:
            return None
        return Chunk("usage", "", usage=UsageStats(used, size, None, "push"))

    @staticmethod
    def turn_consumption(turn: dict, session_id: str) -> TurnConsumption | None:
        usage = turn.get("usage")
        if not isinstance(usage, dict):
            return None
        keys = ("input_other", "input_cache_read", "input_cache_creation", "output")
        values = []
        for key in keys:
            value = usage.get(key)
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if not isinstance(value, int) or value < 0:
                return None
            values.append(value)
        turn_key = turn.get("turn_id")
        if not isinstance(turn_key, str) or not turn_key:
            ordinal = turn.get("ordinal")
            turn_key = str(ordinal) if ordinal is not None else "?"
        return TurnConsumption(
            input_other=values[0],
            input_cache_read=values[1],
            input_cache_creation=values[2],
            output=values[3],
            turn_id=f"{session_id}:{turn_key}",
        )


def _acp_status(item: dict) -> str:
    """KAP item 状态 → ACP tool_call_update 状态。"""
    status = item.get("status")
    if status == "running":
        return "in_progress"
    outcome = item.get("outcome")
    if outcome in ("failed", "interrupted") or item.get("error"):
        return "failed"
    return "completed"
