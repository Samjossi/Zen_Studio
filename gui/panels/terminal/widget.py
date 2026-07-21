"""终端控件：自绘字符网格 + 键盘输入 + 回滚滚动条（唯一碰 Qt 绘制/键盘的层）。

组合关系：TerminalScreen（语义快照）+ AnsiPalette（配色）+ PtySession（I/O，鸭子类型）。
刷新节流 30ms 聚合（帧率封顶 ~33fps，防大量输出刷屏卡顿）。
阶段二新增：空会话占位绘制、查找命中高亮、Ctrl+F/右键菜单请求信号（均只发事件，
决策在 panel，保持层间单向依赖）。
阶段三新增：鼠标拖选选区（可视快照行坐标，滚动即清除）、Ctrl+Shift+C 复制 /
Ctrl+Shift+V 粘贴（写剪贴板无副作用、粘贴与键盘输入同路径，均 widget 自治）。
2026-07-21 新增：复制/粘贴快捷键反转开关（set_swap_copy_paste，设置菜单勾选项
经 panel 即时注入；反转后 Ctrl+C/V 复制粘贴、Ctrl+Shift+C/V 回落 VT100 转换）。
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget

from gui.theme import get_mono_family

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.selection import SelectionController

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
        # 选区状态机（纯逻辑外置 SelectionController；可视快照行坐标，滚动即清除）
        self._selection = SelectionController()
        # 复制/粘贴快捷键反转标志（panel 装配注入；默认 False = Ctrl+Shift+C/V 复制粘贴）
        self._swap_copy_paste = False

        font = QFont(get_mono_family())  # 库内等宽族（Sarasa Term SC），注册缺失回退 monospace
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self.setFont(font)
        font_metrics = self.fontMetrics()
        self._cell_width = max(1, font_metrics.horizontalAdvance("M"))
        self._cell_height = max(1, font_metrics.height())
        self._ascent = font_metrics.ascent()

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

    def set_swap_copy_paste(self, enabled: bool) -> None:
        """复制/粘贴快捷键反转（设置菜单勾选项，即时生效、无需重启）。

        True：Ctrl+C/V 复制粘贴，Ctrl+Shift+C/V 不拦截、落入 key_to_bytes
        发 \\x03（SIGINT）/ \\x16（quoted-insert）；False 为默认的反向布局。
        """
        self._swap_copy_paste = enabled

    def apply_palette(self, palette: AnsiPalette) -> None:
        """主题切换：换色板全量重绘（屏幕模型只含颜色名，免重算）。"""
        self._palette = palette
        self.update()

    def refresh_font(self) -> None:
        """全局字号调整：重建等宽字体（跟随 app 字号）并重算单元格。

        网格尺寸随字宽变化 → 经与 resizeEvent 相同的链路同步 screen/session。
        """
        font = QFont(get_mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self.setFont(font)
        font_metrics = self.fontMetrics()
        self._cell_width = max(1, font_metrics.horizontalAdvance("M"))
        self._cell_height = max(1, font_metrics.height())
        self._ascent = font_metrics.ascent()
        self.clear_selection()  # 网格尺寸变化，选区坐标失效
        row_count, column_count = self.get_grid_size()
        if self._screen and (self._screen.line_count != row_count
                             or self._screen.column_count != column_count):
            self._screen.resize(row_count, column_count)
        if self._session:
            self._session.resize(row_count, column_count)
        self._refresh_scrollbar()
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
    # 选区（状态机在 SelectionController；此处为薄委托 + 视图失效时机）
    # ------------------------------------------------------------------
    def has_selection(self) -> bool:
        """是否存在有效选区（退化为点的点击不算）。"""
        return self._selection.has_selection()

    def clear_selection(self) -> None:
        """清除选区（幂等）；滚动/输入/新数据推屏/换屏/resize 时调用。"""
        if self._selection.clear():
            self.update()

    def selected_text(self) -> str:
        """选区纯文本：归一化阅读序 + 跨行拼接 + 行尾 rstrip（网格补空白不带上屏）。"""
        if self._screen is None:
            return ""
        return self._selection.extract_text(self._screen.snapshot(self._scroll_offset))

    def copy_selection(self) -> None:
        """复制选区到剪贴板（快捷键与右键菜单共用入口；复制后保留选区）。"""
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

    def _pos_to_cell(self, pos) -> tuple[int, int]:
        """像素坐标 → 网格 (y, x)，clamp 进网格（拖入滚动条区不越界）。"""
        row_count, column_count = self.get_grid_size()
        return SelectionController.pos_to_cell(
            pos.x(), pos.y(), self._cell_width, self._cell_height, row_count, column_count)

    # ------------------------------------------------------------------
    # 网格尺寸
    # ------------------------------------------------------------------
    def get_grid_size(self) -> tuple[int, int]:
        """(row_count, column_count) 当前网格尺寸。"""
        bar_width = self._scrollbar.width() if self._scrollbar.isVisible() else 0
        column_count = max(1, (self.width() - bar_width) // self._cell_width)
        row_count = max(1, self.height() // self._cell_height)
        return row_count, column_count

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        bar_width = self._scrollbar.sizeHint().width()
        self._scrollbar.setGeometry(self.width() - bar_width, 0, bar_width, self.height())
        self.clear_selection()  # 网格尺寸变化，选区坐标失效
        row_count, column_count = self.get_grid_size()
        if self._screen and (self._screen.line_count != row_count
                             or self._screen.column_count != column_count):
            self._screen.resize(row_count, column_count)
        if self._session:
            self._session.resize(row_count, column_count)
        self._refresh_scrollbar()
        self.update()

    # ------------------------------------------------------------------
    # 滚动
    # ------------------------------------------------------------------
    def _refresh_scrollbar(self) -> None:
        total = self._screen.count_scrollback_lines() if self._screen else 0
        self._scrollbar.setRange(0, total)
        self._scrollbar.setPageStep(self.get_grid_size()[0])
        if self._scroll_offset == 0:
            self._scrollbar.setValue(total)  # 跟随底部
        self._scrollbar.setVisible(total > 0)

    def _on_scroll(self, value: int) -> None:
        total = self._screen.count_scrollback_lines() if self._screen else 0
        self._scroll_offset = max(0, total - value)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.clear_selection()  # 回滚即视图迁移，选区坐标失效
        if self._scrollbar.isVisible():
            self._scrollbar.setValue(self._scrollbar.value() - event.angleDelta().y() // 40)
        event.accept()

    # ------------------------------------------------------------------
    # 鼠标选区（事件转发 SelectionController；左键拖选，退化为点击则清除）
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._screen is not None:
            self._selection.press(self._pos_to_cell(event.position().toPoint()))
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # 未开 mouseTracking：move 仅在按住按键时到达，拖拽场景够用
        if (self._selection.has_anchor()
                and event.buttons() & Qt.MouseButton.LeftButton):
            if self._selection.drag(self._pos_to_cell(event.position().toPoint())):
                self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._selection.release():
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
        # 复制粘贴快捷键：拦截在 VT100 转换之前。默认 Ctrl+Shift+C/V；
        # 反转模式（设置 ▸ 终端：Ctrl+C/V 复制粘贴）改为 Ctrl+C/V 复制粘贴，
        # Ctrl+Shift+C/V 不拦截、自然落入 key_to_bytes 发 \x03(SIGINT)/\x16(quoted-insert)
        # ——key_to_bytes 只检查 Ctrl 不排斥 Shift，反向路径零新增代码。
        # 已知预期行为（严格交换，无智能回退）：反转模式下无选区按 Ctrl+C 无任何效果。
        copy_paste_modifiers = (Qt.KeyboardModifier.ControlModifier
                                if self._swap_copy_paste else
                                Qt.KeyboardModifier.ControlModifier
                                | Qt.KeyboardModifier.ShiftModifier)
        if event.modifiers() == copy_paste_modifiers:
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
    # 绘制（paintEvent 为编排者；五阶段各成私有方法，绘制顺序即层叠次序）
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        palette = self._palette
        painter = QPainter(self)
        painter.fillRect(self.rect(), palette.default_bg)
        if self._screen is None:
            self._paint_placeholder(painter)
            painter.end()
            return
        row_count, column_count = self.get_grid_size()
        snapshot = self._screen.snapshot(self._scroll_offset)
        normal_font = QFont(self.font())
        bold_font = QFont(normal_font)
        bold_font.setBold(True)
        # 层叠次序：网格 → 选区 → 查找命中 → 光标（后画者覆盖先画者）
        self._paint_grid(painter, snapshot, row_count, column_count, normal_font, bold_font)
        self._paint_selection(painter, snapshot, column_count)
        self._paint_search_runs(painter)
        self._paint_cursor(painter, snapshot, column_count, normal_font)
        painter.end()

    def _paint_placeholder(self, painter: QPainter) -> None:
        """空会话占位：居中引导文本（背景已由 paintEvent 统一填充）。"""
        if self._placeholder:
            hint = QColor(self._palette.default_fg)
            hint.setAlphaF(0.45)
            painter.setPen(hint)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)

    def _paint_grid(self, painter: QPainter, snapshot: list, row_count: int,
                    column_count: int, normal_font: QFont, bold_font: QFont) -> None:
        """网格渲染：逐行同色格合并为一趟绘制（减少 fillRect/drawText 调用）。"""
        palette = self._palette
        for y, row in enumerate(snapshot[:row_count]):
            limit = min(column_count, len(row))  # 控件列数可能暂时宽于屏幕列数（resize 竞态）
            py = y * self._cell_height
            x = 0
            while x < limit:
                fg, bg, bold, underline = self._cell_colors(row[x])
                run = 1
                while x + run < limit and self._cell_colors(row[x + run]) == (fg, bg, bold, underline):
                    run += 1
                px = x * self._cell_width
                if bg != palette.default_bg:
                    painter.fillRect(px, py, self._cell_width * run, self._cell_height, bg)
                text = "".join(row[x + i][0] for i in range(run))
                if any(t != " " for t in text):
                    font = QFont(bold_font) if bold else QFont(normal_font)
                    font.setUnderline(underline)
                    painter.setFont(font)
                    painter.setPen(fg)
                    painter.drawText(px, py + self._ascent, text)
                x += run

    def _paint_selection(self, painter: QPainter, snapshot: list, column_count: int) -> None:
        """选区高亮（文本之后、查找与光标之前）：前景色淡染，明暗主题通用。"""
        if not self._selection.has_selection():
            return
        (sel_y0, sel_x0), (sel_y1, sel_x1) = self._selection.normalized()
        sel_color = QColor(self._palette.default_fg)
        sel_color.setAlphaF(0.28)
        for sel_y in range(sel_y0, min(sel_y1, len(snapshot) - 1) + 1):
            start_col = sel_x0 if sel_y == sel_y0 else 0
            end_col = sel_x1 + 1 if sel_y == sel_y1 else column_count  # 端点含端格
            painter.fillRect(start_col * self._cell_width, sel_y * self._cell_height,
                             (end_col - start_col) * self._cell_width, self._cell_height, sel_color)

    def _paint_search_runs(self, painter: QPainter) -> None:
        """查找命中叠加（光标之前，保持光标可见）：普通命中淡染、当前命中深染。"""
        for index, (hit_y, hit_x0, hit_x1) in enumerate(self._search_runs):
            color = (self._palette.find_cur if index == self._search_current
                     else self._palette.find_bg)
            painter.fillRect(hit_x0 * self._cell_width, hit_y * self._cell_height,
                             (hit_x1 - hit_x0) * self._cell_width, self._cell_height, color)

    def _paint_cursor(self, painter: QPainter, snapshot: list,
                      column_count: int, normal_font: QFont) -> None:
        """光标（仅当前屏视图内）：反显单元格。"""
        cursor = self._screen.cursor
        if not (cursor and self._scroll_offset == 0 and cursor.y < len(snapshot)
                and 0 <= cursor.x < min(column_count, len(snapshot[cursor.y]))):
            return
        cursor_px = cursor.x * self._cell_width
        cursor_py = cursor.y * self._cell_height
        painter.fillRect(cursor_px, cursor_py, self._cell_width, self._cell_height, self._palette.default_fg)
        char = snapshot[cursor.y][cursor.x][0]
        if char != " ":
            painter.setFont(normal_font)
            painter.setPen(self._palette.default_bg)
            painter.drawText(cursor_px, cursor_py + self._ascent, char)

    def _cell_colors(self, cell):
        """(char, CellStyle) → (fg, bg, bold, underline)（应用 reverse 反转）。"""
        _, style = cell
        fg_name, bg_name = style.fg, style.bg
        if style.reverse:
            fg_name, bg_name = bg_name, fg_name
        fg = self._palette.default_fg if fg_name == "default" else self._palette.color(fg_name)
        bg = self._palette.default_bg if bg_name == "default" else self._palette.color(bg_name, self._palette.default_bg)
        return fg, bg, style.bold, style.underline
