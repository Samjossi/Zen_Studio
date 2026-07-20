"""输出区：消息列表、追加、流式块上屏、自动滚动。"""
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QTextBrowser

from gui.popups import exec_standard_context_menu


class ChatOutput(QTextBrowser):
    """聊天消息显示区（纯文本渲染，第一阶段）。"""

    def __init__(self, reasoning_color: str, parent=None) -> None:
        """:param reasoning_color: 思维链前景色（主题资源包 chat.reasoning_fg 注入）。"""
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        # 样式由主题 qss 统一（透明融入侧栏，无边框）
        self.setObjectName("ChatOutput")
        self._reasoning_color = QColor(reasoning_color)

    def set_reasoning_color(self, color: str) -> None:
        """主题切换时更新思维链前景色（仅影响此后追加的块）。"""
        self._reasoning_color = QColor(color)

    def contextMenuEvent(self, event) -> None:
        """标准编辑菜单透明化（见 gui/popups.py 与 0751 计划 §3.1）。"""
        exec_standard_context_menu(self, event)

    def append_message(self, role: str, content: str) -> None:
        """追加一条完整消息（role 为显示名，如"我"/"AI"）。"""
        self.append(f"<b>{role}：</b>")
        self.append(f"{content}<br>")
        self._scroll_to_bottom()

    def begin_stream(self, role: str) -> None:
        """开始一条流式消息（先上前缀）。"""
        self.append(f"<b>{role}：</b>")

    def append_stream_chunk(self, chunk: str) -> None:
        """流式块上屏（显式默认格式，防思维链灰斜格式经光标位置继承）。"""
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk, QTextCharFormat())
        self._scroll_to_bottom()

    def append_reasoning_chunk(self, chunk: str) -> None:
        """思维链块上屏：灰字斜体，与正文样式区分。"""
        fmt = QTextCharFormat()
        fmt.setForeground(self._reasoning_color)
        fmt.setFontItalic(True)
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk, fmt)
        self._scroll_to_bottom()

    def end_reasoning(self) -> None:
        """思维链结束：插入空行与正文分隔（显式默认格式）。"""
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText("\n\n", QTextCharFormat())

    def end_stream(self) -> None:
        """结束一条流式消息（补空行分隔）。"""
        self.append("<br>")

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
