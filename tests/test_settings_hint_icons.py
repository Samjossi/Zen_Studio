"""设置中心灰色提示收纳（问号图标 + tooltip）与保留文案简化验证。

offscreen 实例化 SettingsDialog：
- 六条成段说明收进问号图标（原灰字 QLabel 不复存在，tooltip 含原文关键句）；
- 图标色随 apply_theme 按 muted_text 令牌重刷；
- 副标题与权限页描述按 D6 口径精简。
"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

import gui.settings as settings_mod
from gui.settings import KEY_THEME, load_settings
from gui.settings_dialog import SettingsDialog, _PAGE_REGISTRY
from gui.theme import get_theme_palette
from llm.permission_policy import MODE_AUTO_GUARDED, MODE_CONFIRM_ALL, PERMISSION_MODE_LABELS

#: 持久化隔离目录（项目纪律：临时文件一律落 ./.temp/，禁用系统临时目录）
_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_settings_hint_icons"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def isolated_settings(monkeypatch):
    """settings 读写重定向到 ./.temp/ 隔离文件（防污染真实 settings.json）。"""
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _TEMP_DIR / "settings.json"
    settings_file.unlink(missing_ok=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    return settings_file


def _make_dialog() -> SettingsDialog:
    """桩 ctx 构建设置中心（同 test_notification 范式，chat_tabs=None 走降级守卫）。"""

    class _FakeBar:
        def showMessage(self, *a, **k):
            pass

    ctx = QWidget()
    ctx.statusBar = lambda: _FakeBar()
    ctx.STATUS_MSG_TIMEOUT_MS = 2000
    ctx.STATUS_MSG_LONG_MS = 5000
    ctx.FONT_SIZE_MIN = 8
    ctx.FONT_SIZE_MAX = 24
    ctx.apply_model_selection = lambda *a: None
    ctx.switch_theme = lambda *a: None
    ctx.set_font_size = lambda *a: None
    ctx.set_terminal_swap_copy_paste = lambda *a: None
    ctx.set_chat_renderer = lambda *a: None
    ctx.open_settings_file = lambda: None
    ctx.reset_settings = lambda: None
    ctx.chat_tabs = None
    return SettingsDialog(ctx)


def test_collapsed_hints_live_in_icon_tooltips(qapp, isolated_settings):
    """六条收纳说明：图标 tooltip 含原文关键句，页面上不再有对应灰字。"""
    dialog = _make_dialog()
    tooltips = [icon.toolTip() for icon in dialog._hint_icons]
    assert any("可选范围为 assets/sounds/正在使用/" in t for t in tooltips)
    assert any("notify-send" in t for t in tooltips)
    assert any("Ctrl+Shift+C/V 发中断" in t for t in tooltips)
    assert any("设为 0 立即关闭" in t for t in tooltips)
    assert any("对话区渲染切换对新建会话标签生效" in t for t in tooltips)
    assert any("配置文件为只读查看" in t for t in tooltips)
    remaining = [label.text() for label in dialog._hint_labels]
    assert not any("可选范围为" in t or "notify-send" in t for t in remaining)


def test_hint_icons_follow_theme_token(qapp, isolated_settings):
    """图标内联 style 按 muted_text 令牌重刷（含圆圈描边同色）。"""
    dialog = _make_dialog()
    theme = load_settings()[KEY_THEME]
    dialog.apply_theme(theme)
    expected = get_theme_palette(theme)["muted_text"]
    assert dialog._hint_icons, "通知页等处应已建问号图标"
    for icon in dialog._hint_icons:
        assert expected in icon.styleSheet()
        assert icon.text() == "?"


def test_retained_texts_simplified(qapp, isolated_settings):
    """D6：副标题去套话；权限页描述精简；模型页空白窗提示压缩。"""
    subtitles = [subtitle for _, subtitle, _, _ in _PAGE_REGISTRY]
    assert not any("切换即时生效" in s for s in subtitles)
    assert not any("双向同步" in s for s in subtitles)
    assert PERMISSION_MODE_LABELS[MODE_CONFIRM_ALL][1] == "所有工具调用都弹窗确认"
    assert PERMISSION_MODE_LABELS[MODE_AUTO_GUARDED][1] == "仅危险命令弹窗，其余自动放行"
    dialog = _make_dialog()
    remaining = [label.text() for label in dialog._hint_labels]
    assert "空白窗口不可用；打开文件夹后生效。" in remaining
    assert not any("模型选择不可用" in t for t in remaining)


def _row_containing(page: QWidget, widget: QWidget) -> QHBoxLayout | None:
    """在页顶层布局里找直接装着指定控件的行布局。"""
    layout = page.layout()
    for i in range(layout.count()):
        row = layout.itemAt(i).layout()
        if row is None:
            continue
        if any(row.itemAt(j).widget() is widget for j in range(row.count())):
            return row
    return None


def test_icons_attach_to_related_rows(qapp, isolated_settings):
    """图标落在所说明区块的开关行内（通知页两开关行各一枚）。"""
    dialog = _make_dialog()
    for checkbox in (dialog._notify_sound_check, dialog._notify_banner_check):
        row = _row_containing(checkbox.parentWidget(), checkbox)
        assert row is not None
        widgets = [row.itemAt(i).widget() for i in range(row.count())]
        assert sum(isinstance(w, QLabel) and w.text() == "?" for w in widgets) == 1
