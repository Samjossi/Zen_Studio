"""DSH update mapper：kind 兜底、snake_case、DSH 专有工具与图片。

T0 基线按厂商归属改写：只使用 DshUpdateMapper；卡片仍用 T3 前的公共
工厂，验证载荷字段语义不变。
"""
import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.cards import (
    BashCard,
    CardColors,
    DiffCard,
    MediaReadCard,
    OpenStateMap,
    SubagentCard,
    TextOutputCard,
    TodoListCard,
    make_tool_card,
)
from ide.llm.base import UsageStats
from ide.llm.providers.dsh.updates import DshUpdateMapper

SESSION_ID = "dsh-mapper-session"

_TID = iter(f"dsh-{index}" for index in range(1, 100))


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
    return DshUpdateMapper()


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


def test_dsh_text_reasoning_usage_are_stable_chunks(mapper):
    assert mapper.map_session_update(text_frame("正文")).kind == "text"
    assert mapper.map_session_update(
        text_frame("思考", thought=True)).kind == "reasoning"
    usage = mapper.map_session_update(usage_frame(5, 50))
    assert usage.usage == UsageStats(used=5, size=50, cost=None)


def test_dsh_bash_read_write_and_kind_fallback(qapp, colors, mapper):
    bash_tid = _tid()
    bash_first = mapper.map_session_update(tool_call({
        "toolCallId": bash_tid, "title": "bash", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert bash_first.payload["tool_kind"] == "execute"
    bash_update = mapper.map_session_update(tool_update({
        "toolCallId": bash_tid, "status": "in_progress",
        "rawInput": {"command": "ls -la"},
    }))
    assert bash_update.payload["command"] == "ls -la"
    bash_card = make_tool_card(colors, OpenStateMap(), bash_first.payload)
    assert isinstance(bash_card, BashCard)
    bash_card.apply_update(bash_update.payload)
    assert bash_card._command == "ls -la"
    assert "$ ls -la" in bash_card._copy_text()

    read_tid = _tid()
    read_first = mapper.map_session_update(tool_call({
        "toolCallId": read_tid, "title": "read", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert read_first.payload["tool_kind"] == "read"
    read_update = mapper.map_session_update(tool_update({
        "toolCallId": read_tid, "status": "completed",
        "rawInput": {"file_path": "src/a.py"},
        "rawOutput": {"output": "第一行\n第二行\n"},
    }))
    assert "file_path: src/a.py" in read_update.payload["input_detail"]
    assert read_update.payload["output"] == "第一行\n第二行"
    read_card = make_tool_card(colors, OpenStateMap(), read_first.payload)
    assert isinstance(read_card, TextOutputCard)
    read_card.apply_update(read_update.payload)
    assert "第一行" in read_card._output.toPlainText()

    write_tid = _tid()
    write_first = mapper.map_session_update(tool_call({
        "toolCallId": write_tid, "title": "write", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert write_first.payload["tool_kind"] == "edit"
    write_update = mapper.map_session_update(tool_update({
        "toolCallId": write_tid, "status": "in_progress",
        "rawInput": {"file_path": "src/new.py", "content": "a\nb\n"},
    }))
    assert write_update.payload["diff_stat"] == "+2 −0"
    assert "file_path: src/new.py" in write_update.payload["input_detail"]
    write_card = make_tool_card(colors, OpenStateMap(), write_first.payload)
    assert isinstance(write_card, DiffCard)
    write_card.apply_update(write_update.payload)
    assert "a" in write_card._copy_source and "b" in write_card._copy_source


def test_dsh_read_image_todo_and_subagent_fallbacks(qapp, colors, mapper):
    image_tid = _tid()
    image_first = mapper.map_session_update(tool_call({
        "toolCallId": image_tid, "title": "read_image", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert image_first.payload["tool_kind"] == "read"
    image_update = mapper.map_session_update(tool_update({
        "toolCallId": image_tid, "status": "in_progress",
        "rawInput": {"file_path": ".temp/dsh.png"},
    }))
    assert image_update.payload["media_path"] == ".temp/dsh.png"
    image_card = make_tool_card(colors, OpenStateMap(), image_first.payload)
    assert isinstance(image_card, MediaReadCard)
    image_card.apply_update(image_update.payload)

    todo_tid = _tid()
    todo_first = mapper.map_session_update(tool_call({
        "toolCallId": todo_tid, "title": "todo_write", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert todo_first.payload["tool_kind"] == "other"
    todo_update = mapper.map_session_update(tool_update({
        "toolCallId": todo_tid, "status": "in_progress",
        "rawInput": {"todos": [
            {"content": "实现", "status": "in_progress"},
            {"content": "验证", "status": "pending"},
        ]},
    }))
    assert [entry["content"] for entry in todo_update.payload["todos"]] == [
        "实现", "验证"]
    todo_card = make_tool_card(colors, OpenStateMap(), todo_first.payload)
    assert isinstance(todo_card, TodoListCard)
    todo_card.apply_update(todo_update.payload)
    assert todo_card._todo_layout.count() == 2

    agent_tid = _tid()
    agent_first = mapper.map_session_update(tool_call({
        "toolCallId": agent_tid, "title": "subagent", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert agent_first.payload["tool_kind"] == "think"
    assert agent_first.payload["is_subagent"] is True
    agent_card = make_tool_card(colors, OpenStateMap(), agent_first.payload)
    assert isinstance(agent_card, SubagentCard)


def test_dsh_unknown_tool_falls_back_to_mcp_card(qapp, colors, mapper):
    tid = _tid()
    chunk = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "未收录工具", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert chunk.payload["tool_kind"] == "other"
    card = make_tool_card(colors, OpenStateMap(), chunk.payload)
    assert not isinstance(card, (BashCard, DiffCard, TextOutputCard))


def test_dsh_explicit_kind_is_not_overridden(mapper):
    tid = _tid()
    chunk = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "bash", "kind": "read",
        "status": "pending", "content": [],
    }))
    assert chunk.payload["tool_kind"] == "read"
