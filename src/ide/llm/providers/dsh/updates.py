"""DSH session/update → Chunk 映射。

DSH 自有规则：kind 缺失或敷衍上报 `other` 时按 DSH 工具名兜底推导、
rawInput snake_case 词表、`read_image`、`todo_write`、DSH 图片输入与现有
输出形态。公共 ACP 纯函数只提供 content 块、ANSI 净化、截断与标准 diff。

`_derived_tool_kinds` 只登记本 mapper 依据 DSH 工具名推导出的 kind，供
后续缺 kind 的 update 帧回填；显式上报的 kind 永不改写。账本以
`(sessionId, toolCallId)` 键控，跨 provider 实例、跨会话均不串联。
"""
import json
import re
from pathlib import Path

from ide.llm.base import Chunk
from ide.llm.providers.acp.content import (
    _BODY_SOFT_LIMIT_LINES,
    _OUTPUT_LINE_MAX,
    _clean_terminal_text,
    _collect_content_block,
    _dispatch_output_string,
    _extract_diff,
    _truncate_line,
    _truncate_lines,
)
from ide.llm.providers.acp.protocol import map_usage_update
from ide.llm.providers.dsh.models import (
    QuestionItem,
    TodoEntry,
    TodoPayload,
    ToolCallPayload,
    ToolUpdatePayload,
)

_TAIL_WINDOW_LINES = 5

#: DSH 的 todo 回执；不含 Kimi 的 `Current todo list:` 块模板。
_RECEIPT_PATTERNS = [
    re.compile(r"(?ms)^Todo list updated\.\s*$"),
    re.compile(r"(?ms)^Ensure that you continue to use the todo list.*$"),
]

_TASK_RESULT_RE = re.compile(r"<task_result>(.*?)</task_result>", re.DOTALL)

#: DSH rawInput 为 snake_case；path 作为少数工具的位置字段保留兜底，避免
#: 已实证的通用位置参数被误判为未知工具而整体落 JSON。
_INPUT_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "execute": ("command",),
    "edit": ("file_path", "path", "old_string", "new_string", "replace_all"),
    "read": ("file_path", "path", "offset", "limit"),
    "search": ("pattern", "query", "path", "include"),
    "fetch": ("url",),
    "think": ("description", "prompt", "subagent_type"),
    "other": ("file_path", "path", "url", "query", "command",
              "prompt", "description", "questions", "todos", "name"),
}
_INPUT_DETAIL_VALUE_MAX = 2000
_INPUT_DETAIL_BRIEF_VALUE_MAX = 200
_INPUT_DETAIL_BRIEF_KEYS = frozenset({"prompt", "questions", "description"})

_MEDIA_IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})

#: DSH 工具名 → kind 兜底表；只补缺失/other，不覆盖显式上报。键为归一化
#: 小写名，`-` 统一成 `_` 以兼容连字符工具名。todo_write 显式登记 other：
#: 清单渲染靠卡片侧工具名分派，不走 kind。
_FALLBACK_TOOL_KINDS: dict[str, str] = {
    "bash": "execute", "pwsh": "execute", "shell": "execute",
    "terminal": "execute",
    "write": "edit", "edit": "edit", "patch": "edit",
    "read": "read", "read_image": "read",
    "grep": "search", "glob": "search", "fs_search": "search",
    "web": "fetch", "web_search": "fetch", "web_fetch": "fetch",
    "fetch": "fetch",
    "todo_write": "other",
    "subagent": "think", "task": "think",
}


def _filter_tool_receipt(text: str) -> str:
    for pattern in _RECEIPT_PATTERNS:
        text = pattern.sub("", text)
    return text


def _extract_raw_output(update: dict) -> tuple[str, list[str]] | None:
    raw = update.get("rawOutput")
    if isinstance(raw, dict):
        output = raw.get("output")
    elif isinstance(raw, str):
        output = raw
    else:
        output = None
    texts: list[str] = []
    images: list[str] = []
    json_parsed = False
    if isinstance(output, str):
        output = _filter_tool_receipt(_clean_terminal_text(output))
        json_parsed = _dispatch_output_string(output, texts, images)
    else:
        for item in update.get("content") or []:
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, dict):
                    _collect_content_block(content, texts, images)
    text = _filter_tool_receipt("\n".join(texts)).strip("\n")
    if not text.strip() and not images:
        return None
    if not json_parsed:
        text = "\n".join(
            line if len(line) <= _OUTPUT_LINE_MAX
            else line[: _OUTPUT_LINE_MAX - 1] + "…"
            for line in text.split("\n"))
    return text, images


def _extract_tool_output(
    update: dict,
    keep_lines: int = _BODY_SOFT_LIMIT_LINES,
    keep_tail: bool = True,
) -> tuple[str, int, list[str]] | None:
    if (extracted := _extract_raw_output(update)) is None:
        return None
    raw_text, images = extracted
    if raw_text:
        body, total = _truncate_lines(raw_text, keep_lines, keep_tail)
    else:
        body, total = "", 0
    return body, total, images


def _extract_write_diff(update: dict) -> tuple[str, list[dict], bool] | None:
    """DSH write 无 diff 项时由 rawInput.content 合成 `+N −0`。"""
    if update.get("kind") != "edit":
        return None
    raw = update.get("rawInput")
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if not isinstance(content, str) or not content:
        return None
    if any(key in raw for key in ("old_string", "new_string")):
        return None
    return _extract_diff([{"oldText": "", "newText": content}])


def _extract_result_summary(text: str) -> str:
    match = _TASK_RESULT_RE.search(text)
    return (match.group(1) if match else text).strip("\n")


def _format_input_detail(update: dict) -> str | None:
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not raw:
        return None
    kind = update.get("kind")
    keys = _INPUT_DETAIL_KEYS.get(kind if isinstance(kind, str) else "")
    if keys is None:
        keys = tuple(dict.fromkeys(
            key for group in _INPUT_DETAIL_KEYS.values() for key in group))
    lines = []
    for key in keys:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        value = str(value)
        value_max = (_INPUT_DETAIL_BRIEF_VALUE_MAX
                     if key in _INPUT_DETAIL_BRIEF_KEYS
                     else _INPUT_DETAIL_VALUE_MAX)
        if len(value) > value_max:
            value = value[: value_max - 1] + "…"
        lines.append(f"{key}: {value}")
    if lines:
        return "\n".join(lines)
    pretty = json.dumps(raw, ensure_ascii=False, indent=2)
    text, _ = _truncate_lines(pretty, _BODY_SOFT_LIMIT_LINES, keep_tail=False)
    return text


def _extract_media_path(update: dict) -> str | None:
    raw = update.get("rawInput")
    if not isinstance(raw, dict):
        return None
    path = raw.get("file_path") or raw.get("path")
    if not isinstance(path, str) or not path:
        return None
    ext = Path(path).suffix.lower()
    if ext in _MEDIA_IMAGE_EXTS:
        return path
    if ext:
        return None
    title = update.get("title")
    if isinstance(title, str) and \
            title.strip().split("__")[-1].lower() == "read_image":
        return path
    return None


def _extract_questions(update: dict) -> list[str] | None:
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        return None
    questions = [q["question"] for q in raw["questions"]
                 if isinstance(q, dict) and isinstance(q.get("question"), str)]
    return questions or None


def _extract_question_options(update: dict) -> list[QuestionItem] | None:
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        return None
    items: list[QuestionItem] = []
    for q in raw["questions"]:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        item: dict = {
            "question": q["question"],
            "multi_select": bool(q.get("multi_select") or q.get("multiSelect")),
        }
        if isinstance(q.get("header"), str):
            item["header"] = q["header"]
        options: list[dict] = []
        for opt in q.get("options") or []:
            if not isinstance(opt, dict) or not isinstance(opt.get("label"), str):
                continue
            entry: dict = {"label": opt["label"]}
            if isinstance(opt.get("description"), str):
                entry["description"] = opt["description"]
            options.append(entry)
        item["options"] = options
        items.append(item)
    return items or None


def _tool_call_fallback(payload: ToolCallPayload) -> str:
    icon = "⧉" if payload.get("is_subagent") else "▸"
    line = f"◐ {icon} {payload.get('title') or '?'}"
    if summary := payload.get("summary"):
        line += f" — {summary}"
    if diff_stat := payload.get("diff_stat"):
        line += f"  {diff_stat}"
    return f"\n{line}\n"


def _tool_update_fallback(payload: ToolUpdatePayload) -> str:
    name = payload.get("title") or payload.get("tool_call_id") or "?"
    if payload.get("status") == "failed":
        line = f"✖ ▸ {name}"
        if error := payload.get("error"):
            line += f"（{error}）"
    else:
        line = f"✔ ▸ {name}"
        if diff_stat := payload.get("diff_stat"):
            line += f"  {diff_stat}"
    return f"{line}\n"


def _todo_fallback_text(entries: list[TodoEntry]) -> str:
    marks = {"pending": "[ ]", "in_progress": "[>]"}
    lines = []
    for entry in entries:
        mark = marks.get(entry.get("status") or "", "[x]")
        lines.append(f"- {mark} {entry.get('content') or ''}")
    return "\n".join(lines) + "\n" if lines else ""


def _extract_todo_entries(items: object) -> list[TodoEntry]:
    entries: list[TodoEntry] = []
    if not isinstance(items, list):
        return entries
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("content")
        if not isinstance(text, str):
            continue
        entry = TodoEntry(content=text)
        if isinstance(item.get("status"), str):
            entry["status"] = item["status"]
        if isinstance(item.get("priority"), str):
            entry["priority"] = item["priority"]
        entries.append(entry)
    return entries


def _tool_call_summary(update: dict) -> str | None:
    kind = update.get("kind")
    raw = update.get("rawInput") or {}
    if kind == "execute":
        command = _truncate_line(raw.get("command"))
        if command and command != _truncate_line(update.get("title")):
            return command
        return None
    if kind in ("edit", "read"):
        locations = update.get("locations") or []
        if locations and isinstance(locations[0], dict):
            if path := _truncate_line(locations[0].get("path")):
                return path
        return _truncate_line(raw.get("file_path") or raw.get("path"))
    if kind == "search":
        return _truncate_line(raw.get("pattern") or raw.get("query"))
    if kind == "fetch":
        return _truncate_line(raw.get("url"))
    if kind == "think":
        return _truncate_line(raw.get("description") or raw.get("prompt"))
    return None


def _fallback_tool_kind(title: object) -> str | None:
    """DSH 工具名 → 兜底 kind；未收录/非字符串返回 None（不猜测）。"""
    if not isinstance(title, str):
        return None
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return _FALLBACK_TOOL_KINDS.get(name.lower().replace("-", "_"))


class DshUpdateMapper:
    """DSH provider 实例私有的 update 映射器。"""

    def __init__(self) -> None:
        #: 已提取入参的调用键；终态帧清理。
        self._input_detail_seen: set[tuple[str, str]] = set()
        #: 工具名兜底推导出的 kind，供缺 kind 的 update 帧回填；只收推导
        #: 命中帧，显式上报的后端永不入账。
        self._derived_tool_kinds: dict[tuple[str, str], str] = {}
        #: 会话级 todo 快照：跨调用 diff 的 before；跨轮保留。
        self._last_todo_snapshots: dict[str, list[TodoEntry]] = {}

    def reset(self) -> None:
        self._input_detail_seen.clear()
        self._derived_tool_kinds.clear()
        self._last_todo_snapshots.clear()

    def _record_input_detail(self, payload: dict, detail: str | None,
                             session_id: str) -> None:
        if detail:
            payload["input_detail"] = detail
            if tid := payload.get("tool_call_id"):
                self._input_detail_seen.add((session_id, tid))

    def _apply_todo_diff(self, session_id: str,
                         entries: list[TodoEntry]) -> list[TodoEntry]:
        before = self._last_todo_snapshots.get(session_id) or []
        result: list[TodoEntry] = []
        for index, entry in enumerate(entries):
            prev = before[index] if index < len(before) else None
            if prev is None or any(
                    entry.get(key) != prev.get(key)
                    for key in ("content", "status", "priority")):
                entry = {**entry, "changed": True}
            result.append(entry)
        self._last_todo_snapshots[session_id] = result
        return result

    def _map_tool_call(self, update: dict, session_id: str = "") -> Chunk:
        raw = update.get("rawInput") or {}
        payload = ToolCallPayload()
        if isinstance(update.get("toolCallId"), str):
            payload["tool_call_id"] = update["toolCallId"]
        payload["title"] = update.get("title") or "?"
        tool_kind = update.get("kind") if isinstance(update.get("kind"), str) \
            else "other"
        if tool_kind == "other":
            # DSH 投影层 kind 恒为 other；按本厂商工具名推导，命中即登记
            # 供缺 kind 的 update 帧回填，并让本帧下游提取按推导 kind 走。
            if (derived := _fallback_tool_kind(update.get("title"))) \
                    and derived != "other":
                tool_kind = derived
                update = {**update, "kind": derived}
                if tid := payload.get("tool_call_id"):
                    self._derived_tool_kinds[(session_id, tid)] = derived
        payload["tool_kind"] = tool_kind
        if tool_kind == "think":
            payload["is_subagent"] = True
        if tool_kind == "execute":
            if isinstance(raw.get("command"), str):
                if command := _clean_terminal_text(raw["command"]).strip():
                    payload["command"] = command
        if tool_kind == "edit":
            if diff := _extract_diff(update.get("content")):
                payload["diff_stat"], payload["diff_hunks"], truncated = diff
                if truncated:
                    payload["diff_truncated"] = True
            elif write_diff := _extract_write_diff(update):
                payload["diff_stat"], payload["diff_hunks"], truncated = write_diff
                if truncated:
                    payload["diff_truncated"] = True
        if summary := _tool_call_summary(update):
            payload["summary"] = summary
        self._record_input_detail(
            payload, _format_input_detail(update), session_id)
        if questions := _extract_questions(update):
            payload["questions"] = questions
        if question_options := _extract_question_options(update):
            payload["question_options"] = question_options
        if media_path := _extract_media_path(update):
            payload["media_path"] = media_path
        if isinstance(raw.get("todos"), list):
            if entries := self._apply_todo_diff(
                    session_id, _extract_todo_entries(raw["todos"])):
                payload["todos"] = entries
        return Chunk("tool_call", _tool_call_fallback(payload), payload=payload)

    def _map_tool_call_update(self, update: dict,
                              session_id: str = "") -> Chunk | None:
        payload = ToolUpdatePayload()
        tid = None
        if isinstance(update.get("toolCallId"), str):
            tid = update["toolCallId"]
            payload["tool_call_id"] = tid
        status = update.get("status")
        if not isinstance(status, str) or not status:
            return None
        payload["status"] = status
        if isinstance(update.get("title"), str):
            payload["title"] = update["title"]
        kind = update.get("kind")
        if (not isinstance(kind, str) or not kind or kind == "other") \
                and tid and (derived := self._derived_tool_kinds.get(
                    (session_id, tid))):
            kind = derived
            update = {**update, "kind": derived}
        if tid and (session_id, tid) not in self._input_detail_seen:
            self._record_input_detail(
                payload, _format_input_detail(update), session_id)
            if media_path := _extract_media_path(update):
                payload["media_path"] = media_path
            if kind == "execute":
                raw = update.get("rawInput") or {}
                if isinstance(raw.get("command"), str):
                    if command := _clean_terminal_text(raw["command"]).strip():
                        payload["command"] = command
        if questions := _extract_questions(update):
            payload["questions"] = questions
        if question_options := _extract_question_options(update):
            payload["question_options"] = question_options
        raw_input = update.get("rawInput")
        if isinstance(raw_input, dict) and isinstance(raw_input.get("todos"), list):
            if entries := self._apply_todo_diff(
                    session_id, _extract_todo_entries(raw_input["todos"])):
                payload["todos"] = entries
        if status == "in_progress":
            extracted = _extract_tool_output(
                update, keep_lines=_TAIL_WINDOW_LINES, keep_tail=True)
        else:
            extracted = _extract_tool_output(update, keep_tail=(kind == "execute"))
        if extracted:
            output, total_lines, images = extracted
            if output:
                payload["output"] = output
            payload["output_total_lines"] = total_lines
            if images:
                payload["images"] = images
        if status in ("completed", "failed") and tid:
            self._input_detail_seen.discard((session_id, tid))
            self._derived_tool_kinds.pop((session_id, tid), None)
        if diff := _extract_diff(update.get("content")):
            payload["diff_stat"], payload["diff_hunks"], truncated = diff
            if truncated:
                payload["diff_truncated"] = True
        elif write_diff := _extract_write_diff(update):
            payload["diff_stat"], payload["diff_hunks"], truncated = write_diff
            if truncated:
                payload["diff_truncated"] = True
        if kind in ("edit", "read"):
            if summary := _tool_call_summary(update):
                payload["summary"] = summary
        if status == "completed" and kind == "think":
            if extracted_raw := _extract_raw_output(update):
                summary, _ = _truncate_lines(
                    _extract_result_summary(extracted_raw[0]),
                    _BODY_SOFT_LIMIT_LINES, keep_tail=False)
                if summary.strip():
                    payload["result_summary"] = summary
        if status == "failed":
            raw = update.get("rawOutput")
            error = None
            if isinstance(raw, dict):
                error = raw.get("error")
                if isinstance(error, dict):
                    error = error.get("message")
            elif isinstance(raw, str):
                error = raw
            if not (first := _truncate_line(error)):
                for item in update.get("content") or []:
                    text = (item.get("content") or {}) \
                        if isinstance(item, dict) else {}
                    if first := _truncate_line(text.get("text")):
                        break
            if first:
                payload["error"] = first
            detail_source = None
            if isinstance(error, str) and error.strip():
                detail_source = error
            elif extracted_raw := _extract_raw_output(update):
                detail_source = extracted_raw[0]
            if detail_source:
                detail, _ = _truncate_lines(
                    _clean_terminal_text(detail_source).strip("\n"),
                    _BODY_SOFT_LIMIT_LINES, keep_tail=True)
                if detail.strip():
                    payload["error_detail"] = detail
        return Chunk("tool_call_update", _tool_update_fallback(payload),
                     payload=payload)

    def map_session_update(self, obj: dict) -> Chunk | None:
        update = (obj.get("params") or {}).get("update") or {}
        session_id = (obj.get("params") or {}).get("sessionId") or ""
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (update.get("content") or {}).get("text")
            return Chunk("text", text) if text else None
        if kind == "agent_thought_chunk":
            text = (update.get("content") or {}).get("text")
            return Chunk("reasoning", text) if text else None
        if kind == "tool_call":
            return self._map_tool_call(update, session_id)
        if kind == "tool_call_update":
            return self._map_tool_call_update(update, session_id)
        if kind == "plan":
            entries = _extract_todo_entries(update.get("entries"))
            return Chunk("todo", _todo_fallback_text(entries),
                         payload=TodoPayload(entries=entries))
        if kind == "usage_update":
            stats = map_usage_update(update)
            return Chunk("usage", "", usage=stats) if stats else None
        return None
