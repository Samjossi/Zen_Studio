"""右键菜单统一精简断言：只读区恰为「复制」「全选」、全局无图标、快捷键正交。

统一标准（2026-1016-1122 计划）：
- 只读文本展示控件右键 = 「复制」「全选」两项，无图标、文本无 \\t 快捷键
  显示后缀；复制仅在有选区时可用；
- 可编辑输入框保留完整编辑项，但去图标 + 中文化；
- 菜单项与 Ctrl+C / Ctrl+A 快捷键正交并存——菜单不显示快捷键，按键照常生效。

探针手法：app 级事件过滤器捕获 QMenu Show 事件并立即关闭（真实 exec
走嵌套事件循环随即返回——PySide6 无法 monkeypatch Qt 类方法，真弹
模态菜单会卡死 offscreen 用例）。需 offscreen 平台
（tests/conftest.py 已统一注入 QT_QPA_PLATFORM=offscreen）。
"""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu

from gui.panels.chat.cards import BodyHtml, BodyText
from gui.panels.viewer.code_viewer import CodeViewer
from gui.panels.viewer.markdown_view import MarkdownView
from gui.popups import (
    SimplifiedMenuTextBrowser,
    SimplifiedMenuTextEdit,
    TranslucentMenuLineEdit,
    TranslucentMenuPlainTextEdit,
)
from gui.theme import get_theme_palette


def _app():
    return QApplication.instance() or QApplication([])


class _MenuGrabber(QObject):
    """捕获每个 Show 的 QMenu 并立即关闭，令真实 exec 即刻返回。"""

    def __init__(self, app):
        super().__init__()
        self.menus: list[QMenu] = []
        app.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QMenu):
            self.menus.append(watched)
            QTimer.singleShot(0, watched.close)
        return False


_grabber: _MenuGrabber | None = None


def _grabber_of(app) -> _MenuGrabber:
    global _grabber
    if _grabber is None:
        _grabber = _MenuGrabber(app)
    return _grabber


def _context_event(widget, pos=None):
    """构造作用于 widget 的鼠标右键事件（缺省点左上角内容区）。"""
    pos = pos if pos is not None else QPoint(6, 10)
    return QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, widget.mapToGlobal(pos))


def _popup_menu(widget, event):
    """投喂右键事件并返回捕获到的菜单（弹出即关，不阻塞）。"""
    grabber = _grabber_of(_app())
    grabber.menus.clear()
    widget.contextMenuEvent(event)
    assert grabber.menus, f"{type(widget).__name__} 右键未弹出任何菜单"
    return grabber.menus[-1]


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _assert_simplified(menu):
    """精简规约：无图标、文本无 \\t 快捷键显示后缀。"""
    for action in menu.actions():
        assert action.icon().isNull(), f"菜单项带图标：{action.text()}"
        assert "\t" not in action.text(), f"菜单文本残留快捷键后缀：{action.text()}"


def _assert_readonly_pair(widget):
    """只读控件断言全集：两项、无图标、复制启停随选区。"""
    # 无选区：复制不可用，全选恒可用
    widget.textCursor().clearSelection()
    menu = _popup_menu(widget, _context_event(widget))
    assert _texts(menu) == ["复制", "全选"]
    _assert_simplified(menu)
    copy_action, select_all = menu.actions()[0], menu.actions()[1]
    assert not copy_action.isEnabled()
    assert select_all.isEnabled()
    # 有选区：复制可用
    widget.selectAll()
    menu = _popup_menu(widget, _context_event(widget))
    assert _texts(menu) == ["复制", "全选"]
    assert menu.actions()[0].isEnabled()


class TestReadOnlyPair:
    """只读文本展示控件：右键恰为「复制」「全选」两项。"""

    def test_body_text(self):
        _app()
        _assert_readonly_pair(BodyText("第一行\n第二行"))

    def test_body_html(self):
        _app()
        _assert_readonly_pair(BodyHtml("<b>AI：</b>正文测试"))

    def test_simplified_menu_text_browser(self):
        _app()
        browser = SimplifiedMenuTextBrowser()
        browser.setPlainText("第一行\n第二行")
        _assert_readonly_pair(browser)

    def test_simplified_menu_text_edit(self):
        _app()
        edit = SimplifiedMenuTextEdit()
        edit.setPlainText("第一行\n第二行")
        _assert_readonly_pair(edit)

    def test_body_html_on_anchor(self):
        """右键落在锚点上：Qt 会多出 Copy Link Location，精简后仍恰两项。"""
        _app()
        edit = BodyHtml('<a href="file:a.py">a.py</a>')
        edit.resize(200, 60)
        edit.show()
        cursor = edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        rect = edit.cursorRect(cursor)
        pos = QPoint(rect.x() + 2, rect.y() + rect.height() // 2)
        assert edit.anchorAt(pos), "前置条件：事件落点须在锚点上"
        menu = _popup_menu(edit, _context_event(edit, pos))
        assert _texts(menu) == ["复制", "全选"]
        _assert_simplified(menu)


class TestViewerMenus:
    """中栏查看器：自绘菜单同为「复制」「全选」两项。"""

    def test_code_viewer(self):
        _app()
        viewer = CodeViewer(get_theme_palette("graphite")["chrome"])
        viewer.setPlainText("line1\nline2")
        menu = viewer._build_context_menu()
        assert _texts(menu) == ["复制", "全选"]
        _assert_simplified(menu)
        assert not menu.actions()[0].isEnabled()  # 无选区复制置灰
        viewer.selectAll()
        menu = viewer._build_context_menu()
        assert menu.actions()[0].isEnabled()

    def test_markdown_view(self):
        _app()
        view = MarkdownView(get_theme_palette("graphite"))
        view.setPlainText("正文")
        menu = view._build_context_menu()
        assert _texts(menu) == ["复制", "全选"]
        _assert_simplified(menu)
        assert not menu.actions()[0].isEnabled()
        view.selectAll()
        menu = view._build_context_menu()
        assert menu.actions()[0].isEnabled()


class TestEditableInputs:
    """可编辑输入框：完整编辑项保留，去图标 + 中文化。"""

    _EXPECTED = {"撤销", "重做", "剪切", "复制", "粘贴", "删除", "全选"}

    def test_translucent_menu_line_edit(self):
        _app()
        edit = TranslucentMenuLineEdit()
        edit.setText("abc")
        menu = _popup_menu(edit, _context_event(edit))
        assert set(_texts(menu)) == self._EXPECTED
        _assert_simplified(menu)

    def test_translucent_menu_plain_text_edit(self):
        _app()
        edit = TranslucentMenuPlainTextEdit()
        edit.setPlainText("abc")
        menu = _popup_menu(edit, _context_event(edit))
        assert set(_texts(menu)) == self._EXPECTED
        _assert_simplified(menu)


class TestShortcutOrthogonal:
    """快捷键与菜单项正交：菜单不显示快捷键，Ctrl+C / Ctrl+A 照常生效。"""

    @staticmethod
    def _send_ctrl(widget, key):
        event = QKeyEvent(
            QEvent.Type.KeyPress, key, Qt.KeyboardModifier.ControlModifier)
        QApplication.sendEvent(widget, event)

    def test_ctrl_c_copies(self):
        _app()
        edit = BodyText("第一段\n第二段")
        edit.selectAll()
        QApplication.clipboard().clear()
        self._send_ctrl(edit, Qt.Key.Key_C)
        assert QApplication.clipboard().text() == "第一段\n第二段"

    def test_viewer_ctrl_a_ctrl_c(self):
        _app()
        viewer = CodeViewer(get_theme_palette("graphite")["chrome"])
        viewer.setPlainText("alpha\nbeta")
        viewer.show()
        viewer.setFocus()
        QApplication.clipboard().clear()
        self._send_ctrl(viewer, Qt.Key.Key_A)
        assert viewer.textCursor().selectedText().replace(" ", "\n") == "alpha\nbeta"
        self._send_ctrl(viewer, Qt.Key.Key_C)
        assert QApplication.clipboard().text() == "alpha\nbeta"
