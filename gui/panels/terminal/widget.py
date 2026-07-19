"""终端控件：自绘字符网格 + 键盘输入 + 回滚滚动条（唯一碰 Qt 绘制/键盘的层）。

组合关系：TerminalScreen（语义快照）+ AnsiPalette（配色）+ PtySession（I/O，鸭子类型）。
刷新节流 30ms 聚合（帧率封顶 ~33fps，防大量输出刷屏卡顿）。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QKeyEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen

REFRESH_MS = 30  # 刷新节流间隔

#: Qt Key → VT100 序列（不可打印键静态映射表）
_KEY_SEQUENCES: dict[int, bytes] = {
    Qt.Key.Key_Return: b"\r",
    Qt.Key.Key_Enter: b"\r",
    Qt.Key.Key_Backspace: b"\x7f",
    Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Right: b"\x1b[C",
    Qt.Key.Key_Up: b"\x1b[A",
    Qt.Key.Key_Down: b"\x1b[B",
    Qt.Key.Key_Home: b"\x1b[H",
    Qt.Key.Key_End: b"\x1b[F",
    Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~",
    Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Escape: b"\x1b",
}


class TerminalWidget(QWidget):
    """自绘终端：字符网格 + 光标 + 键盘 → VT100 + 回滚滚动。"""

    def __init__(self, palette: AnsiPalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._screen: TerminalScreen | None = None
        self._session = None  # PtySession（鸭子类型，panel 装配注入）
        self._scroll_offset = 0  # 回滚行数（0=跟随当前屏）

        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self.setFont(font)
        fm = self.fontMetrics()
        self._cell_w = max(1, fm.horizontalAdvance("M"))
        self._cell_h = max(1, fm.height())
        self._ascent = fm.ascent()

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.valueChanged.connect(self._on_scroll)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self.update)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ------------------------------------------------------------------
    # 装配（panel 注入）
    # ------------------------------------------------------------------
    def set_screen(self, screen: TerminalScreen) -> None:
        self._screen = screen
        self._scroll_offset = 0
        self._refresh_scrollbar()
        self.update()

    def set_session(self, session) -> None:
        self._session = session

    def apply_palette(self, palette: AnsiPalette) -> None:
        """主题切换：换色板全量重绘（屏幕模型只含颜色名，免重算）。"""
        self._palette = palette
        self.update()

    def notify_data(self) -> None:
        """新数据到达（panel 接线调用）：滚动条跟随 + 节流刷新。"""
        self._refresh_scrollbar()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    # ------------------------------------------------------------------
    # 网格尺寸
    # ------------------------------------------------------------------
    def grid_size(self) -> tuple[int, int]:
        """(rows, cols) 当前网格尺寸。"""
        bar_w = self._scrollbar.width() if self._scrollbar.isVisible() else 0
        cols = max(1, (self.width() - bar_w) // self._cell_w)
        rows = max(1, self.height() // self._cell_h)
        return rows, cols

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        bar_w = self._scrollbar.sizeHint().width()
        self._scrollbar.setGeometry(self.width() - bar_w, 0, bar_w, self.height())
        rows, cols = self.grid_size()
        if self._screen and (self._screen.lines != rows or self._screen.columns != cols):
            self._screen.resize(rows, cols)
        if self._session:
            self._session.resize(rows, cols)
        self._refresh_scrollbar()
        self.update()

    # ------------------------------------------------------------------
    # 滚动
    # ------------------------------------------------------------------
    def _refresh_scrollbar(self) -> None:
        total = self._screen.scrollback_lines() if self._screen else 0
        self._scrollbar.setRange(0, total)
        self._scrollbar.setPageStep(self.grid_size()[0])
        if self._scroll_offset == 0:
            self._scrollbar.setValue(total)  # 跟随底部
        self._scrollbar.setVisible(total > 0)

    def _on_scroll(self, value: int) -> None:
        total = self._screen.scrollback_lines() if self._screen else 0
        self._scroll_offset = max(0, total - value)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._scrollbar.isVisible():
            self._scrollbar.setValue(self._scrollbar.value() - event.angleDelta().y() // 40)
        event.accept()

    # ------------------------------------------------------------------
    # 键盘输入
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._session is not None and self._session.is_alive():
            if data := self.key_to_bytes(event):
                self._session.write(data)
                if self._scroll_offset:  # 输入后回到底部
                    self._scrollbar.setValue(self._scrollbar.maximum())
                return
        super().keyPressEvent(event)

    @staticmethod
    def key_to_bytes(event: QKeyEvent) -> bytes:
        """QKeyEvent → VT100 字节序列；无法映射返回空。"""
        key = event.key()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                return bytes([key - Qt.Key.Key_A + 1])  # Ctrl+A..Z → 0x01..0x1A
            if key == Qt.Key.Key_Space:
                return b"\x00"
            return b""
        if data := _KEY_SEQUENCES.get(key):
            return data
        text = event.text()
        return text.encode("utf-8") if text else b""

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        if self._screen is None:
            return
        pal = self._palette
        rows, cols = self.grid_size()
        snapshot = self._screen.snapshot(self._scroll_offset)

        painter = QPainter(self)
        painter.fillRect(self.rect(), pal.default_bg)
        normal_font = QFont(self.font())
        bold_font = QFont(normal_font)
        bold_font.setBold(True)

        for y, row in enumerate(snapshot[:rows]):
            limit = min(cols, len(row))  # 控件列数可能暂时宽于屏幕列数（resize 竞态）
            py = y * self._cell_h
            x = 0
            while x < limit:
                fg, bg, bold, underline = self._cell_colors(row[x])
                # 相邻同色格合并为一趟绘制（减少 fillRect/drawText 调用）
                run = 1
                while x + run < limit and self._cell_colors(row[x + run]) == (fg, bg, bold, underline):
                    run += 1
                px = x * self._cell_w
                if bg != pal.default_bg:
                    painter.fillRect(px, py, self._cell_w * run, self._cell_h, bg)
                text = "".join(row[x + i][0] for i in range(run))
                if any(t != " " for t in text):
                    font = QFont(bold_font) if bold else QFont(normal_font)
                    font.setUnderline(underline)
                    painter.setFont(font)
                    painter.setPen(fg)
                    painter.drawText(px, py + self._ascent, text)
                x += run

        # 光标（仅当前屏视图内）：反显单元格
        cur = self._screen.cursor
        if (cur and self._scroll_offset == 0 and cur.y < len(snapshot)
                and 0 <= cur.x < min(cols, len(snapshot[cur.y]))):
            cx, cy = cur.x * self._cell_w, cur.y * self._cell_h
            painter.fillRect(cx, cy, self._cell_w, self._cell_h, pal.default_fg)
            char = snapshot[cur.y][cur.x][0]
            if char != " ":
                painter.setFont(normal_font)
                painter.setPen(pal.default_bg)
                painter.drawText(cx, cy + self._ascent, char)
        painter.end()

    def _cell_colors(self, cell):
        """(char, CellStyle) → (fg, bg, bold, underline)（应用 reverse 反转）。"""
        _, style = cell
        fg_name, bg_name = style.fg, style.bg
        if style.reverse:
            fg_name, bg_name = bg_name, fg_name
        fg = self._palette.default_fg if fg_name == "default" else self._palette.color(fg_name)
        bg = self._palette.default_bg if bg_name == "default" else self._palette.color(bg_name, self._palette.default_bg)
        return fg, bg, style.bold, style.underline
