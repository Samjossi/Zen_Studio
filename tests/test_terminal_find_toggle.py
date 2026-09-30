"""终端面板 Ctrl+F 查找浮层电门回归测试。

覆盖（与文件树搜索栏/查看面板同一电门范式）：
- 焦点在终端区按 Ctrl+F 打开浮层、再按关闭（widget find_requested → panel 电门）；
- 焦点在查找输入框内按 Ctrl+F 关闭（输入框内 Ctrl+F 与 Esc 同出口，
  终端面板无 QShortcut 路径，全靠 FindBar 输入框分支）。

需 offscreen 平台（tests/conftest.py 已统一注入 QT_QPA_PLATFORM=offscreen）。
"""
import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui.panels.terminal.panel import TerminalPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_ctrl_f_toggles_find_bar(qapp):
    panel = TerminalPanel(auto_spawn=False)
    panel.resize(500, 300)
    panel.show()
    panel.terminal.setFocus()
    pump(200)
    assert not panel._find_bar.isVisible()

    # 焦点在终端区：Ctrl+F 开 → 再按关 → 再按重开
    QTest.keyClick(panel.terminal, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible(), "Ctrl+F 未打开查找浮层"
    assert QApplication.focusWidget() is panel._find_bar.input, "打开后焦点未入查找输入框"

    # 焦点在输入框内：Ctrl+F 与 Esc 同出口关闭
    QTest.keyClick(panel._find_bar.input, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert not panel._find_bar.isVisible(), "输入框内 Ctrl+F 未关闭浮层"
    assert QApplication.focusWidget() is panel.terminal, "关闭后焦点未归还终端"

    QTest.keyClick(panel.terminal, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible(), "关闭态再按 Ctrl+F 未重开浮层"
    panel.deleteLater()
