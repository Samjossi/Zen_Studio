"""输出区：消息列表、追加、流式块上屏、自动滚动。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextBrowser


class ChatOutput(QTextBrowser):
    """聊天消息显示区（纯文本渲染，第一阶段）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setStyleSheet("QTextBrowser { border: 1px solid #888; }")

    def append_message(self, role: str, content: str) -> None:
        """追加一条完整消息（role 为显示名，如"我"/"AI"）。"""
        self.append(f"<b>{role}：</b>")
        self.append(f"{content}<br>")
        self._scroll_to_bottom()

    def begin_stream(self, role: str) -> None:
        """开始一条流式消息（先上前缀）。"""
        self.append(f"<b>{role}：</b>")

    def append_stream_chunk(self, chunk: str) -> None:
        """流式块上屏（追加到当前光标处）。"""
        self.moveCursor(self.textCursor().MoveOperation.End)
        self.insertPlainText(chunk)
        self._scroll_to_bottom()

    def end_stream(self) -> None:
        """结束一条流式消息（补空行分隔）。"""
        self.append("<br>")

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
