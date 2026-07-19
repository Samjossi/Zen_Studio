"""终端控件：自绘字符网格 + 键盘输入 + 回滚滚动条（唯一碰 Qt 绘制/键盘的层）。

组合关系：TerminalScreen（语义快照）+ AnsiPalette（配色）+ PtySession（I/O，鸭子类型）。
刷新节流 30ms 聚合（帧率封顶 ~33fps，防大量输出刷屏卡顿）。
阶段二新增：空会话占位绘制、查找命中高亮、Ctrl+F/右键菜单请求信号（均只发事件，
决策在 panel，保持层间单向依赖）。
阶段三新增：鼠标拖选选区（可视快照行坐标，滚动即清除）、Ctrl+Shift+C 复制 /
Ctrl+Shift+V 粘贴（写剪贴板无副作用、粘贴与键盘输入同路径，均 widget 自治）。
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QWheelEvent,
)
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

    #: 请求打开查找浮层（Ctrl+F；panel 决策，拦截在 VT100 转换之前）
    find_requested = Signal()
    #: 请求上下文菜单（global 坐标；菜单内容与动作由 panel 决策）
    context_menu_requested = Signal(object)

    def __init__(self, palette: AnsiPalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._screen: TerminalScreen | None = None
        self._session = None  # PtySession（鸭子类型，panel 装配注入）
        self._scroll_offset = 0  # 回滚行数（0=跟随当前屏）
        self._placeholder = ""  # 空会话占位文本（screen 为 None 时绘制）
        self._search_runs: list[tuple[int, int, int]] = []  # 查找命中段 (y, x0, x1)
        self._search_current = -1  # 当前命中索引
        # 选区（可视快照行坐标 (y, x)，端点含端格；None 表示无选区）
        self._sel_anchor: tuple[int, int] | None = None  # 锚点（按下处）
        self._sel_end: tuple[int, int] | None = None     # 活动端点（拖拽处）

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
    def set_screen(self, screen: TerminalScreen | None) -> None:
        """绑定屏幕模型；None 表示空会话（paintEvent 画占位引导文本）。"""
        self._screen = screen
        self._scroll_offset = 0
        self.clear_selection()  # 换屏（切 tab/重开/清空）选区坐标即失效
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
        if self._scroll_offset == 0:
            # 跟随底部时新输出会推屏，选区指向的内容漂移 → 清除
            self.clear_selection()
        self._refresh_scrollbar()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def set_placeholder(self, text: str) -> None:
        """空会话占位文本（无会话引导态；screen 为 None 时居中绘制）。"""
        self._placeholder = text
        self.update()

    def set_search_highlight(self, runs: list[tuple[int, int, int]], current: int = -1) -> None:
        """查找命中高亮：runs 为 (y, x0, x1) 半开区间段，current 为当前命中索引。"""
        self._search_runs = runs
        self._search_current = current
        self.update()

    @property
    def current_match(self) -> int:
        """当前查找命中索引（无命中为 -1）。"""
        return self._search_current

    # ------------------------------------------------------------------
    # 选区（阶段三：可视快照行坐标，滚动即清除）
    # ------------------------------------------------------------------
    def has_selection(self) -> bool:
        """是否存在有效选区（退化为点的点击不算）。"""
        return (self._sel_anchor is not None and self._sel_end is not None
                and self._sel_anchor != self._sel_end)

    def clear_selection(self) -> None:
        """清除选区（幂等）；滚动/输入/新数据推屏/换屏/resize 时调用。"""
        if self._sel_anchor is not None:
            self._sel_anchor = None
            self._sel_end = None
            self.update()

    def selected_text(self) -> str:
        """选区纯文本：归一化阅读序 + 跨行拼接 + 行尾 rstrip（网格补空白不带上屏）。"""
        if not self.has_selection() or self._screen is None:
            return ""
        (y0, x0), (y1, x1) = self._normalized_selection()
        snapshot = self._screen.snapshot(self._scroll_offset)
        y1 = min(y1, len(snapshot) - 1)  # resize 竞态保护
        lines: list[str] = []
        for y in range(y0, y1 + 1):
            row = snapshot[y]
            a = x0 if y == y0 else 0
            b = x1 + 1 if y == y1 else len(row)  # 端点含端格 → 半开 +1
            lines.append("".join(ch for ch, _ in row[a:b]).rstrip())
        return "\n".join(lines)

    def copy_selection(self) -> None:
        """复制选区到剪贴板（Ctrl+Shift+C / 右键菜单共用；复制后保留选区）。"""
        if text := self.selected_text():
            QGuiApplication.clipboard().setText(text)

    def paste_clipboard(self) -> None:
        """粘贴剪贴板文本进 shell（与键盘输入同路径，经 session.write）。"""
        if self._session is not None and self._session.is_alive():
            if text := QGuiApplication.clipboard().text():
                self._session.write(text.encode("utf-8"))
                self.clear_selection()
                if self._scroll_offset:  # 粘贴后回到底部（同键盘输入语义）
                    self._scrollbar.setValue(self._scrollbar.maximum())

    def _normalized_selection(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """选区按阅读序（先 y 后 x）归一化为 (起, 止)；调用前须保证两端点非 None。"""
        a, b = self._sel_anchor, self._sel_end
        return (a, b) if a <= b else (b, a)

    def _pos_to_cell(self, pos) -> tuple[int, int]:
        """像素坐标 → 网格 (y, x)，clamp 进网格（拖入滚动条区不越界）。"""
        rows, cols = self.grid_size()
        x = min(max(pos.x() // self._cell_w, 0), cols - 1)
        y = min(max(pos.y() // self._cell_h, 0), rows - 1)
        return y, x

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
        self.clear_selection()  # 网格尺寸变化，选区坐标失效
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
        self.clear_selection()  # 回滚即视图迁移，选区坐标失效
        if self._scrollbar.isVisible():
            self._scrollbar.setValue(self._scrollbar.value() - event.angleDelta().y() // 40)
        event.accept()

    # ------------------------------------------------------------------
    # 鼠标选区（左键拖选；退化为点击则清除）
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._screen is not None:
            self._sel_anchor = self._pos_to_cell(event.position().toPoint())
            self._sel_end = self._sel_anchor
            self.update()  # 覆盖旧选区高亮
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # 未开 mouseTracking：move 仅在按住按键时到达，拖拽场景够用
        if (self._sel_anchor is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            cell = self._pos_to_cell(event.position().toPoint())
            if cell != self._sel_end:
                self._sel_end = cell
                self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._sel_anchor is not None:
            if self._sel_anchor == self._sel_end:
                self.clear_selection()  # 退化为点击：不建选区
            else:
                self.update()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # 键盘输入
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Ctrl+F 请求查找浮层：拦截在 VT100 转换之前（否则按 Ctrl 字母规则发 0x06 给 shell）
        if (event.key() == Qt.Key.Key_F
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.find_requested.emit()
            return
        # Ctrl+Shift+C/V 复制粘贴：同样拦截在 VT100 转换之前（Ctrl+C 不加 Shift 仍是 SIGINT）
        if event.modifiers() == (Qt.KeyboardModifier.ControlModifier
                                 | Qt.KeyboardModifier.ShiftModifier):
            if event.key() == Qt.Key.Key_C:
                self.copy_selection()
                return
            if event.key() == Qt.Key.Key_V:
                self.paste_clipboard()
                return
        if self._session is not None and self._session.is_alive():
            if data := self.key_to_bytes(event):
                self._session.write(data)
                self.clear_selection()  # 输入推屏，选区坐标失效
                if self._scroll_offset:  # 输入后回到底部
                    self._scrollbar.setValue(self._scrollbar.maximum())
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        """右键菜单请求：转发 panel 决策（widget 不碰会话生命周期，保持单向依赖）。"""
        self.context_menu_requested.emit(event.globalPos())

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
        pal = self._palette
        if self._screen is None:
            # 空会话占位：整幅背景 + 居中引导文本
            painter = QPainter(self)
            painter.fillRect(self.rect(), pal.default_bg)
            if self._placeholder:
                hint = QColor(pal.default_fg)
                hint.setAlphaF(0.45)
                painter.setPen(hint)
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            painter.end()
            return
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

        # 选区高亮（画在文本之后、查找高亮与光标之前）：前景色淡染，明暗主题通用
        if self.has_selection():
            (sy0, sx0), (sy1, sx1) = self._normalized_selection()
            sel_color = QColor(pal.default_fg)
            sel_color.setAlphaF(0.28)
            for sy in range(sy0, min(sy1, len(snapshot) - 1) + 1):
                a = sx0 if sy == sy0 else 0
                b = sx1 + 1 if sy == sy1 else cols  # 端点含端格
                painter.fillRect(a * self._cell_w, sy * self._cell_h,
                                 (b - a) * self._cell_w, self._cell_h, sel_color)

        # 查找高亮叠加（画在光标之前，保持光标可见）：普通命中淡染、当前命中深染
        for i, (hy, hx0, hx1) in enumerate(self._search_runs):
            color = (QColor(255, 180, 0, 110) if i == self._search_current
                     else QColor(255, 220, 100, 55))
            painter.fillRect(hx0 * self._cell_w, hy * self._cell_h,
                             (hx1 - hx0) * self._cell_w, self._cell_h, color)

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
