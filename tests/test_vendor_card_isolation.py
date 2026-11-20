"""厂商卡片隔离：同名工具不跨厂商误入，presenter 账本与问题卡注册表互不串扰。"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.transcript import ChatTranscriptView
from ide.gui.panels.chat.vendors.dsh import cards as dsh_cards
from ide.gui.panels.chat.vendors.dsh import factory as dsh_factory
from ide.gui.panels.chat.vendors.dsh.presenter import DshCardPresenter
from ide.gui.panels.chat.vendors.kimi import cards as kimi_cards
from ide.gui.panels.chat.vendors.kimi import factory as kimi_factory
from ide.gui.panels.chat.vendors.kimi.presenter import KimiCardPresenter
from ide.gui.panels.chat.vendors.local import cards as local_cards
from ide.gui.panels.chat.vendors.local import factory as local_factory
from ide.gui.theme import DEFAULT_THEME, get_theme_palette


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


def _payload(tid: str, title: str, kind: str = "other", **extra) -> dict:
    return {"tool_call_id": tid, "title": title, "tool_kind": kind,
            "status": "pending", **extra}


def test_same_tool_name_does_not_cross_vendor(qapp, colors):
    """Kimi 私有名只进 Kimi 工厂，DSH 同名标题回落 DSH 通用卡，反之亦然。"""
    kimi = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k1", "ReadMediaFile"))
    dsh = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d1", "ReadMediaFile"))
    assert isinstance(kimi, kimi_cards.MediaReadCard)
    assert isinstance(dsh, dsh_cards.McpCard)
    assert not isinstance(dsh, kimi_cards.MediaReadCard)

    dsh_image = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d2", "read_image"))
    kimi_image = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k2", "read_image"))
    assert isinstance(dsh_image, dsh_cards.MediaReadCard)
    assert isinstance(kimi_image, kimi_cards.McpCard)
    # Local 不借用 DSH 的 read_image 词表
    local_image = local_factory.make_tool_card(
        colors, OpenStateMap(), _payload("l2", "read_image"))
    assert isinstance(local_image, local_cards.McpCard)

    ask = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k3", "AskUserQuestion"))
    legacy_ask = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k3b", "ask"))
    dsh_ask = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d3", "AskUserQuestion"))
    assert isinstance(ask, kimi_cards.QuestionCard)
    assert isinstance(legacy_ask, kimi_cards.QuestionCard)
    assert isinstance(dsh_ask, dsh_cards.McpCard)
    # Local 自己的提问卡类，绝不是 Kimi 的类实例
    local_ask = local_factory.make_tool_card(
        colors, OpenStateMap(), _payload("l3", "AskUserQuestion"))
    local_legacy_ask = local_factory.make_tool_card(
        colors, OpenStateMap(), _payload("l3b", "ask"))
    assert isinstance(local_ask, local_cards.QuestionCard)
    assert isinstance(local_legacy_ask, local_cards.QuestionCard)
    assert not isinstance(local_ask, kimi_cards.QuestionCard)

    kimi_agent = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k4", "agent"))
    dsh_agent = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d4", "agent", kind="other"))
    assert isinstance(kimi_agent, kimi_cards.SubagentCard)
    assert isinstance(dsh_agent, dsh_cards.McpCard)

    dsh_subagent = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d5", "subagent"))
    assert isinstance(dsh_subagent, dsh_cards.SubagentCard)

    dsh_todo = dsh_factory.make_tool_card(
        colors, OpenStateMap(), _payload("d6", "todo_write"))
    kimi_todo = kimi_factory.make_tool_card(
        colors, OpenStateMap(), _payload("k6", "todo_write"))
    assert isinstance(dsh_todo, dsh_cards.TodoListCard)
    assert isinstance(kimi_todo, kimi_cards.McpCard)


def test_question_card_registry_is_per_vendor(qapp, colors):
    card = kimi_factory.make_tool_card(
        colors, OpenStateMap(),
        _payload("q-isolated", "AskUserQuestion", questions=["问题"]))
    assert isinstance(card, kimi_cards.QuestionCard)
    assert kimi_cards.find_question_card("q-isolated") is card
    assert dsh_cards.find_question_card("q-isolated") is None
    assert dsh_cards.find_pending_question_card(["问题"]) is None


def test_presenter_ledgers_are_per_instance(qapp):
    kimi = KimiCardPresenter()
    dsh = DshCardPresenter()
    kimi.prepare_call({
        "tool_call_id": "same-tid", "title": "Bash", "command": "ls"})
    payload = dsh.prepare_update(
        {"tool_call_id": "same-tid", "status": "completed", "output": "out"})
    assert "command" not in payload
    assert payload["title"] == "same-tid"  # DSH 自己的账本为空 → id 短串兜底
    # Kimi 账本仍只属于 Kimi 实例
    assert kimi._tool_commands == {"same-tid": "ls"}


def test_presenter_gates_late_frames_and_throttles_tail(qapp):
    presenter = KimiCardPresenter()
    presenter.prepare_call({
        "tool_call_id": "tail", "title": "Bash", "command": "ls"})
    assert presenter.prepare_update(
        {"tool_call_id": "tail", "status": "in_progress",
         "output": "one"}) is not None
    assert presenter.prepare_update(
        {"tool_call_id": "tail", "status": "in_progress",
         "output": "two"}) is None
    # 迟到入参/图片/清单帧不受 200ms 尾滚节流限制
    late = presenter.prepare_update(
        {"tool_call_id": "tail", "status": "in_progress",
         "input_detail": "command: ls"})
    assert late is not None and late["input_detail"] == "command: ls"


def test_presenter_keeps_full_output_for_any_tool(qapp):
    """单轨后不再有旧轨裁剪：非 execute 输出也原样交给卡片 body。"""
    presenter = KimiCardPresenter()
    presenter.prepare_call({
        "tool_call_id": "read-only", "title": "Read", "command": "cat a"})
    output = "\n".join(str(i) for i in range(25))
    payload = presenter.prepare_update(
        {"tool_call_id": "read-only", "status": "completed", "output": output})
    assert payload["command"] == "cat a"
    assert payload["output"] == output


def test_presenter_fs_mutation_and_todo_dispatch(qapp):
    assert KimiCardPresenter.is_fs_mutation_terminal(
        {"status": "completed", "tool_kind": "edit"})
    assert not KimiCardPresenter.is_fs_mutation_terminal(
        {"status": "completed", "tool_kind": "read"})

    class Output:
        def __init__(self):
            self.entries = None

        def upsert_todo_block(self, entries):
            self.entries = entries

    output = Output()
    KimiCardPresenter.append_todo(
        output, {"entries": [{"content": "x", "status": "pending"}]})
    assert output.entries == [{"content": "x", "status": "pending"}]


def test_transcript_uses_vendor_registry(qapp):
    view = ChatTranscriptView(
        get_theme_palette(DEFAULT_THEME)["chat"], vendor="kimi")
    view.append_tool_call(_payload("tr-kimi", "ReadMediaFile"))
    assert isinstance(view._tool_cards["tr-kimi"], kimi_cards.MediaReadCard)

    view.set_vendor("dsh")
    view.append_tool_call(_payload("tr-dsh", "read_image"))
    assert isinstance(view._tool_cards["tr-dsh"], dsh_cards.MediaReadCard)
    view.upsert_todo_block([{"content": "t", "status": "pending"}])
    assert isinstance(view._todo_card, dsh_cards.TodoCard)


def test_panel_and_card_base_keep_vendor_payload_out():
    """T3 边界守卫：通用面板不读载荷字段，卡片基础件不含厂商工具名。"""
    panel = Path("src/ide/gui/panels/chat/panel.py").read_text(encoding="utf-8")
    assert "payload.get" not in panel
    for forbidden in ("kimi-acp", "dsh-acp", "diff_hunks", "result_summary"):
        assert forbidden not in panel

    card_base = Path(
        "src/ide/gui/panels/chat/card_base.py").read_text(encoding="utf-8")
    lowered = card_base.lower()
    for forbidden in ("askuserquestion", "readmediafile", "read_image",
                      "todo_write", "diff_hunks", "media_path",
                      "result_summary", "error_detail"):
        assert forbidden not in lowered
