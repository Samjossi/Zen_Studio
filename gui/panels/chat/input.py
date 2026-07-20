"""输入区：Enter 发送 / Shift+Enter 换行；支持文件拖入插入 @路径 引用。"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTextEdit

from gui.popups import exec_standard_context_menu


class ChatInput(QTextEdit):
    """聊天输入框。

    拖放文件引用（方案 A：纯文本透传，由后端 agent CLI 解析 @路径）：
    从文件树或系统文件管理器拖入本地文件，在落点插入 `@工作区相对路径 `；
    目录带尾 `/`，多选逐个插入、空格分隔；工作区外路径退化为绝对路径。
    """

    #: 请求发送（携带输入文本）
    send_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("输入消息，Enter 发送 / Shift+Enter 换行")
        self.setAcceptRichText(False)
        #: 工作区根路径（set_workspace_root 注入）；None 时文件拖入静默忽略
        self._workspace_root: Path | None = None

    # ------------------------------------------------------------------
    # 工作区根路径（拖入文件时据此计算 @相对路径）
    # ------------------------------------------------------------------
    def set_workspace_root(self, root: str) -> None:
        self._workspace_root = Path(root).resolve()

    # ------------------------------------------------------------------
    # 右键菜单：标准编辑菜单透明化（Qt 内部创建的 QMenu 不经修复点，
    # 须替代默认 contextMenuEvent——见 gui/popups.py 与 0751 计划 §3.1）
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event)

    # ------------------------------------------------------------------
    # 键盘
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 拖放：本地文件 → @路径 引用
    # ------------------------------------------------------------------
    @staticmethod
    def _has_local_file(mime) -> bool:
        return mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())

    def dragEnterEvent(self, event) -> None:
        if self._has_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)  # 文本等其余拖拽走默认行为

    def dragMoveEvent(self, event) -> None:
        if self._has_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not self._has_local_file(event.mimeData()):
            super().dropEvent(event)  # 文本等其余拖拽走默认行为
            return
        if self._workspace_root is None:
            event.ignore()  # 未注入工作区根：静默忽略（防御，正常装配不会发生）
            return
        # 在落点（而非当前光标）插入，符合拖拽直觉
        self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.insertPlainText(self._mention_text(url.toLocalFile()))
        event.acceptProposedAction()

    def _mention_text(self, path: str) -> str:
        """本地路径 → '@相对路径 '（目录带尾 '/'，工作区外用绝对路径）。"""
        p = Path(path).resolve()
        try:
            text = p.relative_to(self._workspace_root).as_posix()
        except ValueError:
            # 工作区外路径：插入绝对路径，后端 CLI 同样可解析
            text = p.as_posix()
        if p.is_dir() and not text.endswith("/"):
            text += "/"
        return f"@{text} "
