"""Local update mapper：标准 ACP 帧、显式 kind 与独立词表。

T0 基线按厂商归属改写：只使用 LocalUpdateMapper 与 Local 卡片工厂，不经过
Kimi/DSH 规则，并锁定 Local 不借用 DSH 的工具名兜底。
"""
import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors.local.cards import BashCard
from ide.gui.panels.chat.vendors.local.factory import make_tool_card
from ide.llm.base import UsageStats
from ide.llm.providers.local.updates import LocalUpdateMapper

SESSION_ID = "local-mapper-session"

_TID = iter(f"local-{index}" for index in range(1, 100))


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
    return LocalUpdateMapper()


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


def test_local_standard_frames_are_stable(qapp, colors, mapper):
    assert mapper.map_session_update(text_frame("本地正文")).text == "本地正文"
    assert mapper.map_session_update(
        text_frame("本地思考", thought=True)).kind == "reasoning"
    usage = mapper.map_session_update(usage_frame(7, 70))
    assert usage.usage == UsageStats(used=7, size=70, cost=None)

    tid = _tid()
    first = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "Bash", "kind": "execute",
        "status": "pending", "rawInput": {"command": "pwd"},
    }))
    assert first.payload["command"] == "pwd"
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, BashCard)
    update = mapper.map_session_update(tool_update({
        "toolCallId": tid, "kind": "execute", "status": "completed",
        "rawOutput": {"output": "/workspace\n"},
    }))
    assert update.payload["output"] == "/workspace"
    card.apply_update(update.payload)
    assert "/workspace" in card._output.toPlainText()


def test_local_does_not_apply_dsh_tool_name_fallback(mapper):
    tid = _tid()
    chunk = mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "bash", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert chunk.payload["tool_kind"] == "other"
