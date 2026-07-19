"""只读代码查看器：QPlainTextEdit + 行号栏 + 当前行高亮。

AI-first 定位：永久只读（产品决策，`setReadOnly` 可逆）；行号为人与 AI 的对话坐标系。
行号栏采用 Qt 经典 lineNumberArea 模式。
软换行（2026-07-19 决策）：WidgetWidthWrap + 单词边界优先，超长行自动折行不出水平滚动条；
行号按逻辑行（block）编号，折出的续行无行号（与 VS Code 一致）。
"""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextFormat, QTextOption
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit, QWidget

from gui.theme import mono_family

#: 查看器控件配色（行号/当前行；文本高亮配色见 highlighter.PALETTES）
CHROME: dict[str, dict[str, str]] = {
    "light": {"ln_fg": "#999999", "ln_bg": "#f5f5f5", "cur_bg": "#eef4fb"},
    "dark": {"ln_fg": "#6b717d", "ln_bg": "#26292e", "cur_bg": "#2f333a"},
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
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_area_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_area_width()
        self._highlight_current_line()

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """切换行号/当前行配色并刷新。"""
        self._chrome = CHROME.get(theme, CHROME["light"])
        self._line_area.update()
        self._highlight_current_line()

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
    # 当前行高亮
    # ------------------------------------------------------------------
    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(self._chrome["cur_bg"]))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
