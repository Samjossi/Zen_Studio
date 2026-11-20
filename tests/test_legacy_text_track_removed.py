"""classic 旧文本轨下线：旧值归一、设置界面无旧轨、面板只建卡片轨。

这些断言锁定「删除后不可通过配置恢复」的边界：配置键仍兼容读取，
但值域只剩 cards，且 UI 与面板都不再存在第二轨入口。
"""
import json

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from ide.gui import settings as settings_mod
from ide.gui import settings_dialog as dialog_mod
from ide.gui.panels.chat.panel import ChatPanel
from ide.gui.panels.chat.transcript import ChatTranscriptView
from ide.gui.settings import (
    CHAT_RENDERER_CARDS,
    CHAT_RENDERERS,
    KEY_CHAT_RENDERER,
    load_settings,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_classic_value_normalizes_to_cards_without_rewriting(tmp_path, monkeypatch):
    """历史 classic 静默归一为 cards，且不改写用户配置文件。"""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({KEY_CHAT_RENDERER: "classic"}), encoding="utf-8")
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)

    loaded = load_settings()

    assert loaded[KEY_CHAT_RENDERER] == CHAT_RENDERER_CARDS
    assert CHAT_RENDERERS == (CHAT_RENDERER_CARDS,)
    assert not hasattr(settings_mod, "CHAT_RENDERER_CLASSIC")
    # 读取只归一内存值，不迁移也不回写旧存档
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        KEY_CHAT_RENDERER: "classic"}


def test_settings_dialog_no_longer_offers_renderer_choice(qapp, monkeypatch):
    """外观页只剩主题/字号/色边，不再构建「对话区渲染」下拉与槽。"""
    monkeypatch.setattr(dialog_mod, "REGISTRY", {})
    monkeypatch.setattr(dialog_mod, "_PAGE_REGISTRY", (
        ("外观", "主题、字号与聊天面板外观。",
         "_build_appearance_page", "_reload_appearance"),
    ))
    # 只构建外观页：apply_font_size 还会刷新权限页黑名单控件，跳过
    monkeypatch.setattr(
        dialog_mod.SettingsDialog, "apply_font_size", lambda self: None)
    host = QWidget()
    host.FONT_SIZE_MIN = 8
    host.FONT_SIZE_MAX = 24
    host.STATUS_MSG_TIMEOUT_MS = 3000
    host.chat_tabs = None
    dialog = dialog_mod.SettingsDialog(host)

    assert not hasattr(dialog, "_renderer_combo")
    assert not hasattr(dialog, "_on_renderer_activated")
    assert hasattr(dialog, "_theme_combo")
    assert hasattr(dialog, "_font_spin")
    dialog.deleteLater()


def test_chat_panel_always_builds_card_track(qapp, tmp_path):
    """面板构造不再按设置选轨，恒定实例化 ChatTranscriptView。"""
    panel = ChatPanel(None, None, str(tmp_path))

    assert isinstance(panel.output, ChatTranscriptView)
    assert not hasattr(panel, "_cards_track")
    panel.deleteLater()
