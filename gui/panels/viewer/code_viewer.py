"""只读代码查看器：QPlainTextEdit + 行号栏 + 当前行高亮。

AI-first 定位：永久只读（产品决策，`setReadOnly` 可逆）；行号为人与 AI 的对话坐标系。
行号栏采用 Qt 经典 lineNumberArea 模式。
软换行（2026-07-19 决策）：WidgetWidthWrap + 单词边界优先，超长行自动折行不出水平滚动条；
行号按逻辑行（block）编号，折出的续行无行号（与 VS Code 一致）。
"""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextCursor, QTextFormat, QTextOption
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit, QWidget

from gui.popups import exec_standard_context_menu
from gui.theme import mono_family

#: 查看器控件配色（行号/当前行/查找命中；文本高亮配色见 highlighter.PALETTES）
CHROME: dict[str, dict[str, str]] = {
    "light": {
        "ln_fg": "#999999", "ln_bg": "#f5f5f5", "cur_bg": "#eef4fb",
        "find_bg": "#fff3bf", "find_cur": "#ffd43b",
    },
    "dark": {
        "ln_fg": "#6b717d", "ln_bg": "#26292e", "cur_bg": "#2f333a",
        "find_bg": "#5c4a0f", "find_cur": "#8a6d1a",
    },
}


class _LineNumberArea(QWidget):
    """行号栏侧条（绘制委托给 CodeViewer.paint_line_numbers）。"""

    def __init__(self, viewer: "CodeViewer") -> None:
        super().__init__(viewer)
        self._viewer = viewer

    def sizeHint(self) -> QSize:
        return QSize(self._viewer.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._viewer.paint_line_numbers(event)


class CodeViewer(QPlainTextEdit):
    """只读代码查看器（等宽字体、行号栏、当前行高亮、软换行）。"""

    def __init__(self, theme: str = "light", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)  # AI-first：永久只读（产品决策；setReadOnly 可逆）
        # 软换行：按控件宽度折行，优先单词边界、无空格长串任意处硬断（不出水平滚动条）
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        font = QFont(mono_family())  # 库内等宽族（Sarasa Term SC），注册缺失回退 monospace
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self.setFont(font)

        self._chrome = CHROME.get(theme, CHROME["light"])
        self._search_selections: list[QTextEdit.ExtraSelection] = []  # 查找命中高亮
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_area_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_area_width()
        self._highlight_current_line()

    def contextMenuEvent(self, event) -> None:
        """标准编辑菜单透明化（见 gui/popups.py 与 0751 计划 §3.1）。"""
        exec_standard_context_menu(self, event)

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """切换行号/当前行配色并刷新。"""
        self._chrome = CHROME.get(theme, CHROME["light"])
        self._line_area.update()
        self._highlight_current_line()

    def refresh_font(self) -> None:
        """全局字号调整：重建等宽字体（跟随 app 字号），行号栏宽随新字宽重算。"""
        font = QFont(mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self.setFont(font)
        self._update_area_width()
        self._line_area.update()

    # ------------------------------------------------------------------
    # 查找命中高亮（ViewerPanel 查找浮层驱动；与当前行高亮合并上屏）
    # ------------------------------------------------------------------
    def set_search_highlights(self, matches: list[QTextCursor], current: int) -> None:
        """设置查找命中高亮：matches 为命中区间光标列表，current 为当前命中索引。

        空列表即清除（查找浮层关闭/清空输入时调用）。
        """
        self._search_selections = []
        for i, cursor in enumerate(matches):
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(
                QColor(self._chrome["find_cur" if i == current else "find_bg"]))
            selection.cursor = cursor
            self._search_selections.append(selection)
        self._refresh_extra_selections()

    # ------------------------------------------------------------------
    # 行号栏
    # ------------------------------------------------------------------
    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_area_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_area_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor(self._chrome["ln_bg"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(self._chrome["ln_fg"]))
                painter.drawText(0, top, self._line_area.width() - 4, height,
                                 Qt.AlignmentFlag.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        painter.end()

    # ------------------------------------------------------------------
    # 当前行高亮（与查找命中高亮合并上屏）
    # ------------------------------------------------------------------
    def _highlight_current_line(self) -> None:
        self._refresh_extra_selections()

    def _refresh_extra_selections(self) -> None:
        """ExtraSelection 唯一出口：当前行 + 查找命中，防互相覆盖。"""
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(self._chrome["cur_bg"]))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection] + self._search_selections)
