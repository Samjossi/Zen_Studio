"""终端选区改造断言（2026-0913-0710 计划）：绝对行坐标 + 滚动不清选区。

分三层：
- SelectionController 纯逻辑（press/drag/normalized/extract_text 新签名）；
- TerminalScreen 绝对行窗口（推屏稳定、回滚溢出基线前移、历史清空代数）；
- TerminalWidget 行为（offscreen）：notify_data / 滚轮不再清选区，选区文本钉住内容。

需 offscreen 平台（tests/conftest.py 已统一注入 QT_QPA_PLATFORM=offscreen）。
"""
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.selection import SelectionController
from gui.panels.terminal.widget import TerminalWidget
from gui.theme import THEME_PALETTES


def _feed_lines(screen: TerminalScreen, count: int, prefix: str = "line") -> None:
    for i in range(count):
        screen.feed(f"{prefix}{i}\r\n".encode())


def _row_text(row: list) -> str:
    return "".join(ch for ch, _ in row).rstrip()


# ----------------------------------------------------------------------
# SelectionController 纯逻辑
# ----------------------------------------------------------------------
class TestSelectionController:
    def test_drag_normalized_reverse(self):
        ctl = SelectionController()
        ctl.press((10, 5))
        ctl.drag((3, 2))  # 向上拖
        assert ctl.has_selection()
        assert ctl.normalized() == ((3, 2), (10, 5))
        ctl.release()
        assert ctl.has_selection()  # 非退化拖拽：release 后选区保留

    def test_degenerate_click_clears(self):
        ctl = SelectionController()
        ctl.press((7, 7))
        ctl.release()
        assert not ctl.has_selection()

    def test_extract_text_rows_cover_range(self):
        """extract_text 新签名：rows 恰覆盖归一化后 [y0, y1]，首行截 x0、末行含 x1。"""
        ctl = SelectionController()
        ctl.press((100, 2))
        ctl.drag((102, 3))
        rows = [
            [("a", None), ("b", None), ("c", None), ("d", None)],
            [("e", None), ("f", None), ("g", None), ("h", None)],
            [("i", None), ("j", None), ("k", None), ("l", None)],
        ]
        assert ctl.extract_text(rows) == "cd\nefgh\nijkl"

    def test_extract_text_rstrip_trailing_blanks(self):
        ctl = SelectionController()
        ctl.press((0, 0))
        ctl.drag((0, 3))
        rows = [[("x", None), (" ", None), (" ", None), (" ", None)]]
        assert ctl.extract_text(rows) == "x"


# ----------------------------------------------------------------------
# TerminalScreen 绝对行窗口
# ----------------------------------------------------------------------
class TestAbsRows:
    def test_abs_row_stable_across_push(self):
        """推屏不改变已有内容的绝对行号。"""
        screen = TerminalScreen(20, 5, history=100)
        _feed_lines(screen, 10)
        base, _ = screen.abs_window()
        before = _row_text(screen.abs_rows(base + 2, base + 3)[0])
        _feed_lines(screen, 10)
        after = _row_text(screen.abs_rows(base + 2, base + 3)[0])
        assert before == after == "line2"

    def test_overflow_advances_base_and_clamps(self):
        """回滚溢出：基线前移，掉出窗口的旧行号被 clamp 到现存最早行。"""
        screen = TerminalScreen(20, 4, history=5)
        _feed_lines(screen, 12)
        base, stop = screen.abs_window()
        rows = screen.abs_rows(base - 3, base + 1)  # 起点早于基线 → clamp
        assert _row_text(rows[0]) != "line0"  # line0 已被丢弃
        assert len(screen.abs_rows(stop - 1, stop + 5)) == 1  # 终点越界 → clamp

    def test_visible_abs_roundtrip(self):
        screen = TerminalScreen(20, 4, history=50)
        _feed_lines(screen, 20)
        for offset in (0, 3, 7):
            for y in range(4):
                row = screen.visible_to_abs(y, offset)
                assert screen.abs_to_visible(row, offset) == y

    def test_history_generation_bumps_on_reset(self):
        """reset / ED 3 清空历史时代数递增（widget 据此作废选区）。"""
        screen = TerminalScreen(20, 4, history=50)
        gen = screen.history_generation
        _feed_lines(screen, 10)
        screen.feed(b"\x1b[3J")  # ED 3：清回滚
        assert screen.history_generation == gen + 1
        screen.feed(b"\x1bc")  # RIS：全量 reset
        assert screen.history_generation == gen + 2


# ----------------------------------------------------------------------
# TerminalWidget 行为（offscreen）
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def terminal(qapp):
    palette = AnsiPalette(THEME_PALETTES["cloud"]["terminal"])
    widget = TerminalWidget(palette)
    widget.resize(800, 480)
    widget.show()  # 隐藏控件不收 resizeEvent，offscreen 下需显式 show
    screen = TerminalScreen(80, 20, history=200)
    _feed_lines(screen, 50)
    widget.set_screen(screen)
    yield widget
    widget.deleteLater()


def _mouse(type_, pos, button=Qt.MouseButton.LeftButton,
           buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(type_, QPointF(pos), QPointF(pos), button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def _drag_select(widget: TerminalWidget, start: tuple[int, int],
                 end: tuple[int, int]) -> None:
    widget.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, QPointF(*start)))
    widget.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, QPointF(*end)))
    widget.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, QPointF(*end),
                                    buttons=Qt.MouseButton.NoButton))


class TestTerminalWidgetSelection:
    def test_new_output_keeps_selection(self, terminal):
        """回归主诉：拖选后来新输出（notify_data），选区不清、文本钉住原内容。"""
        _drag_select(terminal, (10, 10), (200, 60))
        assert terminal.has_selection()
        text_before = terminal.selected_text()
        assert text_before
        _feed_lines(terminal._screen, 5, prefix="new")
        terminal.notify_data()
        assert terminal.has_selection()
        assert terminal.selected_text() == text_before

    def test_wheel_keeps_selection(self, terminal):
        """滚轮回滚不再清选区，且文本内容不变。"""
        _drag_select(terminal, (10, 10), (200, 60))
        text_before = terminal.selected_text()
        terminal._scrollbar.setValue(terminal._scrollbar.maximum() - 5)
        assert terminal.has_selection()
        assert terminal.selected_text() == text_before

    def test_resize_still_clears_selection(self, terminal):
        """resize 重排使绝对行号失效的语义保留。"""
        _drag_select(terminal, (10, 10), (200, 60))
        terminal.resize(600, 480)
        assert not terminal.has_selection()

    def test_drag_beyond_top_autoscrolls(self, terminal):
        """拖出上边缘：立即回滚并把选区活动端点扩进回滚区。"""
        terminal.mousePressEvent(
            _mouse(QMouseEvent.Type.MouseButtonPress, QPointF(10, 100)))
        offset_before = terminal._scroll_offset
        terminal.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, QPointF(10, -5)))
        assert terminal._scroll_offset > offset_before  # 已向上回滚
        anchor_y = terminal._selection._anchor[0]
        top_y = terminal._selection.normalized()[0][0]
        assert top_y < anchor_y  # 端点扩进了比锚点更早的历史行
        terminal.mouseReleaseEvent(
            _mouse(QMouseEvent.Type.MouseButtonRelease, QPointF(10, -5),
                   buttons=Qt.MouseButton.NoButton))
        assert not terminal._autoscroll_timer.isActive()  # 松键即停
