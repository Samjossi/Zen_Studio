"""查看面板 Ctrl+F 查找快捷键回归测试（对应 2026-0930-1314 计划 T4）。

覆盖：
- 焦点在查看面板内按 Ctrl+F 打开查找浮层且焦点入输入框；
- 浮层打开时再按 Ctrl+F 关闭浮层并清空命中（Ctrl+F 电门，与文件树搜索栏同范式）；
- 菜单「查找」路径维持打开语义：浮层可见时调 show_find 保持打开并重回焦输入框；
- Esc 关闭浮层并清空命中（既有行为回归）；
- Markdown 阅览模式触发查找自动切源码模式再开浮层（对应 2026-0930-1353 计划 T2）。

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


def test_ctrl_f_toggles_bar_and_esc_closes(qapp):
    panel = make_panel()
    QTest.keyClick(panel.viewer, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible()
    panel._find_bar.input.setText("hello")
    pump(100)
    assert len(panel._find_matches) == 2, "当前文档命中数异常"

    # 菜单「查找」路径维持打开语义：可见时调 show_find 保持打开并重回焦
    panel.viewer.setFocus()
    pump(50)
    panel.show_find()
    pump(100)
    assert panel._find_bar.isVisible(), "菜单路径不应关闭浮层"
    assert QApplication.focusWidget() is panel._find_bar.input, "菜单路径未重回焦输入框"

    # 再按 Ctrl+F：电门关闭浮层、清空命中、焦点归还查看器
    QTest.keyClick(panel._find_bar.input, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert not panel._find_bar.isVisible(), "再按 Ctrl+F 未关闭浮层"
    assert panel._find_matches == [], "电门关闭后命中未清空"

    # 关闭态再按 Ctrl+F 重开；Esc 关闭（既有行为回归）
    QTest.keyClick(panel.viewer, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert panel._find_bar.isVisible(), "关闭态再按 Ctrl+F 未重开浮层"
    QTest.keyClick(panel._find_bar.input, Qt.Key.Key_Escape)
    pump(100)
    assert not panel._find_bar.isVisible(), "Esc 未关闭浮层"
    assert panel._find_matches == [], "关闭后命中未清空"
    panel.deleteLater()


def make_md_panel() -> ViewerPanel:
    shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True)
    target = WORKDIR / "note.md"
    target.write_text("# 标题\n\nhello markdown\n\n再来一句 hello\n")
    panel = ViewerPanel(workspace_root=str(WORKDIR))
    panel.resize(500, 400)
    panel.show()
    panel.open_file(str(target))
    panel.activateWindow()
    pump(200)
    return panel


def assert_auto_switch_to_source(panel: ViewerPanel) -> None:
    assert panel._md_switch.isChecked(), "触发查找未自动切到源码模式"
    assert panel._md_mode_label.text() == "源码模式"
    assert panel._stack.currentWidget() is panel.viewer, "当前页未切到源码 CodeViewer"
    assert panel._find_bar.isVisible(), "自动切源码后查找浮层未打开"
    panel._find_bar.input.setText("hello")
    pump(100)
    assert len(panel._find_matches) == 2, "源码模式下未搜到文档内容"


def test_md_preview_ctrl_f_auto_switches_to_source(qapp):
    panel = make_md_panel()
    assert panel._stack.currentWidget() is panel.markdown_view, "md 默认应进阅览渲染页"
    assert panel._md_switch_box.isVisible()
    assert not panel._md_switch.isChecked()

    QTest.keyClick(panel.markdown_view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(100)
    assert_auto_switch_to_source(panel)
    panel.deleteLater()


def test_md_preview_menu_find_auto_switches_to_source(qapp):
    panel = make_md_panel()
    panel.show_find()  # 菜单「查找」分发路径同一入口
    pump(100)
    assert_auto_switch_to_source(panel)
    panel.deleteLater()
