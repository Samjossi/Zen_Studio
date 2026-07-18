"""输出区：消息列表、追加、流式块上屏、自动滚动。"""
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QTextBrowser


class ChatOutput(QTextBrowser):
    """聊天消息显示区（纯文本渲染，第一阶段）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        # 样式由主题 qss 统一（透明融入侧栏，无边框）
        self.setObjectName("ChatOutput")

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
        fmt.setForeground(QColor("#888"))
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
