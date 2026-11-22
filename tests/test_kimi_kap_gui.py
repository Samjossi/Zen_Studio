"""KAP 事件载荷复用 Kimi 卡片：vendor=kimi 的工厂分派与降级。"""
import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors import registry as card_registry
from ide.gui.panels.chat.vendors.kimi.cards import BashCard, McpCard
from ide.gui.panels.chat.vendors.kimi.presenter import KimiCardPresenter
from ide.llm.providers.kimi.kap.events import KapEventMapper


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def colors():
    return CardColors(
        reasoning_fg="#8899aa", tool_fg="#aabbcc", tool_error_fg="#ff5555",
        user_bubble_bg="#223344", tool_output_bg="#112233",
        link_fg="#66ccff", diff_add_fg="#55cc55", diff_del_fg="#cc5555")


def test_kap_bash_payload_routes_to_kimi_bash_card(qapp, colors):
    mapper = KapEventMapper()
    payload = mapper.map_item_started({
        "type": "tool_call", "item_id": "tool-1", "status": "running",
        "name": "Bash",
        "input": {"command": "echo hi"},
        "display": {"kind": "command", "command": "echo hi"},
    })[0].payload
    card = card_registry.make_tool_card(
        "kimi", colors, OpenStateMap(), payload)
    assert isinstance(card, BashCard)
    assert payload["tool_kind"] == "execute"


def test_kap_unknown_tool_falls_back_to_generic_kimi_card(qapp, colors):
    mapper = KapEventMapper()
    payload = mapper.map_item_started({
        "type": "tool_call", "item_id": "tool-2", "status": "running",
        "name": "MysteryTool", "input": {},
    })[0].payload
    card = card_registry.make_tool_card(
        "kimi", colors, OpenStateMap(), payload)
    assert isinstance(card, McpCard)


def test_kap_uses_kimi_presenter():
    assert isinstance(card_registry.make_presenter("kimi"), KimiCardPresenter)
