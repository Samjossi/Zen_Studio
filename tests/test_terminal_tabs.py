"""终端标签栏溢出治理行为断言（2026-1003-0212 计划 T2）。

覆盖：① `_spawn` 达 MAX_TABS 后不再新增；② `spawn_agent_session` 满员抛
RuntimeError（经桥透传 ACP -32603）；③「+」钮 39→40→39 禁用/恢复与 tooltip
文案；④ ElideRight 与水平 Ignored 尺寸策略配置生效；⑤ 标签 tooltip 承载完整标题。

PtySession 以 Qt 信号桩替换（offscreen 下不起真实进程）。
"""
import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QSizePolicy

from gui.panels.terminal.panel import TerminalPanel

MAX_TABS = TerminalPanel.MAX_TABS


class _FakePtySession(QObject):
    """PtySession 轻量桩：保留信号与生命周期接口，不起真实 PTY。"""

    data_received = Signal(bytes)
    process_exited = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alive = False

    def start(self, columns, rows, cwd=None, argv=None):
        self._alive = True

    def terminate(self):
        self._alive = False

    def resize(self, rows, columns):
        pass

    def is_alive(self):
        return self._alive

    def write(self, data):
        pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel(qapp, monkeypatch):
    monkeypatch.setattr("gui.panels.terminal.panel.PtySession", _FakePtySession)
    p = TerminalPanel(auto_spawn=False)
    yield p
    p.deleteLater()
    qapp.processEvents()


def _fill(panel, n):
    for _ in range(n):
        panel._spawn()


def test_spawn_stops_at_max_tabs(panel):
    _fill(panel, MAX_TABS)
    assert panel._tab_bar.count() == MAX_TABS
    panel._spawn()  # 满员后新建应被守卫拦截
    assert panel._tab_bar.count() == MAX_TABS


def test_agent_session_raises_at_max_tabs(panel):
    _fill(panel, MAX_TABS)
    with pytest.raises(RuntimeError, match="终端数量已达上限"):
        panel.spawn_agent_session(["echo"], None, "🤖 echo")


def test_new_button_cap_cycle(panel):
    _fill(panel, MAX_TABS - 1)
    assert panel._btn_new.isEnabled()
    assert panel._btn_new.toolTip() == "新建终端"

    _fill(panel, 1)  # 39 → 40：禁用并换提示
    assert not panel._btn_new.isEnabled()
    assert panel._btn_new.toolTip() == f"终端数量已达上限（{MAX_TABS}）"

    panel._close_tab(0)  # 40 → 39：恢复
    assert panel._btn_new.isEnabled()
    assert panel._btn_new.toolTip() == "新建终端"


def test_overflow_compression_config(panel):
    assert panel._tab_bar.elideMode() == Qt.TextElideMode.ElideRight
    assert panel._tab_bar.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
    assert not panel._tab_bar.usesScrollButtons()  # 2026-0818-1111 定版不回归


def test_tab_tooltip_carries_full_title(panel):
    panel.spawn_agent_session(["kimi"], None, "🤖 kimi 一个相当长的命令标题")
    idx = panel._tab_bar.count() - 1
    assert panel._tab_bar.tabToolTip(idx) == "🤖 kimi 一个相当长的命令标题"
