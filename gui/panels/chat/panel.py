"""聊天面板装配：上输出 + 下输入，连接 LLM 流式线程。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from llm import Message, get_llm
from gui.panels.chat.input import ChatInput
from gui.panels.chat.output import ChatOutput
from gui.panels.chat.worker import ChatWorker

#: 系统提示词（第一阶段固定）
SYSTEM_PROMPT = "你是 Zen Studio IDE 的内置助手，回答简洁，使用中文。"


class ChatPanel(QWidget):
    """左栏 AI 聊天面板。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[Message] = []
        self._worker: ChatWorker | None = None
        self._stream_buffer = ""

        self.output = ChatOutput(self)
        self.input = ChatInput(self)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.output)
        splitter.addWidget(self.input)
        splitter.setSizes([550, 150])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        layout.setContentsMargins(0, 0, 0, 0)

        self.input.send_requested.connect(self._on_send)

    # ------------------------------------------------------------------
    # 发送与流式接收
    # ------------------------------------------------------------------
    def _on_send(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # 上一次未结束，忽略（输入框此时已禁用）
        self.input.clear()
        self._set_busy(True)

        self._history.append({"role": "user", "content": text})
        self.output.append_message("我", text)
        self.output.begin_stream("AI")
        self._stream_buffer = ""

        messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history)

        self._worker = ChatWorker(get_llm("deepseek"), messages, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_with_error.connect(self._on_finished)
        self._worker.start()

    def _on_chunk(self, chunk: str) -> None:
        self._stream_buffer += chunk
        self.output.append_stream_chunk(chunk)

    def _on_finished(self, error: str) -> None:
        if error:
            self.output.append_stream_chunk(f"\n[请求失败] {error}")
            self._history.pop()  # 失败的用户消息不入历史
        else:
            self._history.append({"role": "assistant", "content": self._stream_buffer})
        self.output.end_stream()
        self._set_busy(False)
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.input.setPlaceholderText("AI 回复中…" if busy else "输入消息，Enter 发送 / Shift+Enter 换行")
