"""输入区：Enter 发送 / Shift+Enter 换行。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTextEdit


class ChatInput(QTextEdit):
    """聊天输入框。"""

    #: 请求发送（携带输入文本）
    send_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("输入消息，Enter 发送 / Shift+Enter 换行")
        self.setAcceptRichText(False)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)  # Shift+Enter 换行
                return
            text = self.toPlainText().strip()
            if text and self.isEnabled():
                self.send_requested.emit(text)
            return
        super().keyPressEvent(event)
