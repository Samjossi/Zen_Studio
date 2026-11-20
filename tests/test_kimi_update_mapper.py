"""Kimi update mapper：帧 → Kimi 载荷 → 卡片。

T0 基线按厂商归属改写：本文件只使用 KimiUpdateMapper 与 Kimi 卡片工厂，
不经过公共映射入口或其他厂商卡片包。
"""
import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors.kimi.cards import (
    DiffCard,
    MediaReadCard,
    QuestionCard,
    SubagentCard,
    TodoListCard,
)
from ide.gui.panels.chat.vendors.kimi.factory import make_tool_card
from ide.llm.base import UsageStats
from ide.llm.providers.kimi import subagent as kimi_subagent
from ide.llm.providers.kimi.updates import KimiUpdateMapper

SESSION_ID = "kimi-mapper-session"

_TID = iter(f"kimi-{index}" for index in range(1, 100))


def _tid() -> str:
    return next(_TID)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def colors():
    return CardColors(
        reasoning_fg="#8899aa",
        tool_fg="#aabbcc",
        tool_error_fg="#ff5555",
        user_bubble_bg="#223344",
        tool_output_bg="#112233",
        link_fg="#66ccff",
        diff_add_fg="#55cc55",
        diff_del_fg="#cc5555",
    )


@pytest.fixture
def mapper():
    return KimiUpdateMapper()


def frame(update: dict, session_id: str = SESSION_ID) -> dict:
    return {
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


def text_frame(text: str, *, thought: bool = False) -> dict:
    return frame({
        "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
        "content": {"type": "text", "text": text},
    })


def usage_frame(used: int = 10, size: int = 100) -> dict:
    return frame({"sessionUpdate": "usage_update", "used": used, "size": size})


def tool_call(update: dict) -> dict:
    return frame({"sessionUpdate": "tool_call", **update})


def tool_update(update: dict) -> dict:
    return frame({"sessionUpdate": "tool_call_update", **update})


def test_kimi_text_reasoning_usage_are_stable_chunks(mapper):
    assert mapper.map_session_update(text_frame("正文")).kind == "text"
    assert mapper.map_session_update(text_frame("正文")).text == "正文"
    assert mapper.map_session_update(
        text_frame("思考", thought=True)).kind == "reasoning"
    usage = mapper.map_session_update(usage_frame(42, 200))
    assert usage.kind == "usage"
    assert usage.usage == UsageStats(used=42, size=200, cost=None)


def test_kimi_edit_frames_reach_diff_card(qapp, colors, mapper):
    tid = _tid()
    first = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "Edit", "kind": "edit",
        "status": "pending", "content": [],
    }))
    assert first.kind == "tool_call"
    payload = first.payload
    assert payload["tool_kind"] == "edit"
    card = make_tool_card(colors, OpenStateMap(), payload)
    assert isinstance(card, DiffCard)

    update = mapper.map_session_update(tool_update({
        "toolCallId": tid, "title": "Editing src/a.py", "kind": "edit",
        "status": "in_progress",
        "rawInput": {"path": "src/a.py", "old_string": "旧", "new_string": "新"},
        "locations": [{"path": "src/a.py"}],
        "content": [{
            "type": "diff", "path": "src/a.py",
            "oldText": "旧", "newText": "新",
        }],
    }))
    assert update.kind == "tool_call_update"
    assert update.payload["diff_stat"] == "+1 −1"
    assert "old_string: 旧" in update.payload["input_detail"]
    assert update.payload["summary"] == "src/a.py"

    card.apply_update(update.payload)
    badge = card._diff_badge.text()
    assert "+1" in badge and "−1" in badge
    subtitle = card._subtitle_label.text()
    assert "a.py" in subtitle and "src/" in subtitle
    assert "旧" in card._copy_source and "新" in card._copy_source


def test_kimi_write_without_diff_item_synthesizes_diff(qapp, colors, mapper):
    tid = _tid()
    first = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "Write", "kind": "edit",
        "status": "pending", "content": [],
    }))
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, DiffCard)

    update = mapper.map_session_update(tool_update({
        "toolCallId": tid, "title": "Writing .temp/a.txt", "kind": "edit",
        "status": "in_progress",
        "rawInput": {"path": ".temp/a.txt", "content": "第一行\n第二行\n第三行\n"},
        "content": [{
            "type": "content",
            "content": {"type": "text", "text": '{"path":".temp/a.txt"}'},
        }],
    }))
    assert update.payload["diff_stat"] == "+3 −0"
    assert len(update.payload["diff_hunks"]) == 1
    card.apply_update(update.payload)
    assert "第一行" in card._copy_source


def test_kimi_read_media_carries_path_and_output_image(qapp, colors, mapper):
    tid = _tid()
    first = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "ReadMediaFile", "kind": "other",
        "status": "pending", "content": [],
    }))
    # Kimi 信任显式 other，不做工具名兜底；卡片按工具名分派。
    assert first.payload["tool_kind"] == "other"
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, MediaReadCard)

    update = mapper.map_session_update(tool_update({
        "toolCallId": tid, "kind": "other", "status": "in_progress",
        "rawInput": {"path": ".temp/pic.png"},
        "content": [{
            "type": "content",
            "content": {"type": "text", "text": '<image path=".temp/pic.png">'},
        }],
    }))
    assert update.payload["media_path"] == ".temp/pic.png"
    assert update.payload["images"] == ["file://.temp/pic.png"]
    card.apply_update(update.payload)
    assert ".temp/pic.png" in card._input.toPlainText()


def test_kimi_todo_and_question_payloads_reach_cards(qapp, colors, mapper):
    todo_tid = _tid()
    todo_first = mapper.map_session_update(tool_call({
        "toolCallId": todo_tid, "title": "TodoList", "kind": "other",
        "status": "pending", "content": [],
    }))
    todo_card = make_tool_card(colors, OpenStateMap(), todo_first.payload)
    assert isinstance(todo_card, TodoListCard)

    todo_update = mapper.map_session_update(tool_update({
        "toolCallId": todo_tid, "kind": "other", "status": "in_progress",
        "rawInput": {"todos": [
            {"status": "done", "title": "整理取证脚本"},
            {"status": "in_progress", "title": "运行真机会话"},
            {"status": "pending", "title": "核对帧产物"},
        ]},
    }))
    entries = todo_update.payload["todos"]
    assert [entry["content"] for entry in entries] == [
        "整理取证脚本", "运行真机会话", "核对帧产物"]
    assert [entry["status"] for entry in entries] == [
        "completed", "in_progress", "pending"]
    assert all(entry["changed"] for entry in entries)
    todo_card.apply_update(todo_update.payload)
    assert todo_card._todo_layout.count() == 3
    assert "1/3 项完成" in todo_card._subtitle_label.text()

    ask_tid = _tid()
    ask_first = mapper.map_session_update(tool_call({
        "toolCallId": ask_tid, "title": "AskUserQuestion", "kind": "other",
        "status": "pending", "content": [],
    }))
    ask_card = make_tool_card(colors, OpenStateMap(), ask_first.payload)
    assert isinstance(ask_card, QuestionCard)

    ask_update = mapper.map_session_update(tool_update({
        "toolCallId": ask_tid, "kind": "other", "status": "in_progress",
        "rawInput": {"questions": [{
            "header": "颜色",
            "question": "你希望问候语是什么颜色？",
            "options": [{"label": "红色"}, {"label": "蓝色"}],
            "multi_select": False,
        }]},
    }))
    assert ask_update.payload["questions"] == ["你希望问候语是什么颜色？"]
    assert ask_update.payload["question_options"][0]["options"][0]["label"] == "红色"
    ask_card.apply_update(ask_update.payload)
    assert ask_card._qa_rows[0][0] == "你希望问候语是什么颜色？"


def test_kimi_agent_sidecar_nests_child_card(qapp, colors, mapper):
    parent_tid = _tid()
    parent = mapper.map_session_update(tool_call({
        "toolCallId": parent_tid, "title": "Agent", "kind": "think",
        "status": "pending", "content": [],
    }))
    parent_card = make_tool_card(colors, OpenStateMap(), parent.payload)
    assert isinstance(parent_card, SubagentCard)
    assert parent.payload["is_subagent"] is True

    child = kimi_subagent.synthesize_wire_call(
        {"name": "Bash", "toolCallId": "wire-1",
         "args": {"command": "ls -la"}},
        parent_tid,
    )
    child_chunk = mapper.map_session_update(frame(child))
    assert child_chunk.payload["parent_tool_call_id"] == parent_tid
    assert child_chunk.payload["title"] == "Bash"
    assert child_chunk.payload["tool_kind"] == "execute"
    assert child_chunk.payload["command"] == "ls -la"
    parent_card.append_child_call(child_chunk.payload)
    assert parent_tid + "/wire-1" in parent_card._tool_cards

    result = kimi_subagent.synthesize_wire_result(
        {"toolCallId": "wire-1", "result": {"output": "README.md"}},
        parent_tid,
        "execute",
    )
    result_chunk = mapper.map_session_update(frame(result))
    assert result_chunk.payload["status"] == "completed"
    assert result_chunk.payload["parent_tool_call_id"] == parent_tid
    parent_card.append_child_update(result_chunk.payload)
