"""查看面板 Ctrl+F 查找快捷键回归测试（对应 2026-0930-1314 计划 T4）。

覆盖：
- 焦点在查看面板内按 Ctrl+F 打开查找浮层且焦点入输入框；
- 浮层打开时再按 Ctrl+F 保持打开并重回焦输入框（show_find 幂等）；
- Esc 关闭浮层并清空命中（既有行为回归）。

临时数据按 AGENTS.md 纪律落项目内 .temp/，不用系统临时目录。
"""
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui.panels.viewer.panel import ViewerPanel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "tests_viewer_find_shortcut"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def make_panel() -> ViewerPanel:
    shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True)
    target = WORKDIR / "doc.txt"
    target.write_text("hello world\nfind me here\nhello again\n")
    panel = ViewerPanel(workspace_root=str(WORKDIR))
    panel.resize(500, 400)
    panel.show()
    panel.open_file(str(target))
    panel.activateWindow()
    panel.viewer.setFocus()
    pump(200)
    return panel


def test_ctrl_f_opens_find_bar_and_focuses(qapp):
    panel = make_panel()
    assert not panel._find_bar.isVisible()

    QTest.keyClick(panel.viewer, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible(), "Ctrl+F 未打开查找浮层"
    assert QApplication.focusWidget() is panel._find_bar.input, "打开后焦点未入查找输入框"
    panel.deleteLater()


def test_ctrl_f_again_keeps_bar_and_esc_closes(qapp):
    panel = make_panel()
    QTest.keyClick(panel.viewer, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible()
    panel._find_bar.input.setText("hello")
    pump(100)
    assert len(panel._find_matches) == 2, "当前文档命中数异常"

    # 焦点回查看器后再按 Ctrl+F：浮层保持打开，焦点重回输入框
    panel.viewer.setFocus()
    pump(50)
    QTest.keyClick(panel.viewer, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible(), "再按 Ctrl+F 不应关闭浮层"
    assert QApplication.focusWidget() is panel._find_bar.input, "再按未重回焦输入框"

    # Esc 关闭（既有行为回归）
    QTest.keyClick(panel._find_bar.input, Qt.Key.Key_Escape)
    pump(100)
    assert not panel._find_bar.isVisible(), "Esc 未关闭浮层"
    assert panel._find_matches == [], "关闭后命中未清空"
    panel.deleteLater()
