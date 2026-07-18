"""聊天面板装配：上输出 + 下输入（含模型行），连接 LLM 流式线程。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from llm import Chunk, DeepSeekLLM, Message, ModelVersion, get_llm
from gui.panels.chat.input import ChatInput
from gui.panels.chat.model_bar import ModelBar
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
        self._seen_reasoning = False

        self.output = ChatOutput(self)
        self.input = ChatInput(self)
        self.model_bar = ModelBar(self)

        # 输入区容器：模型行在上，输入框在下
        input_area = QWidget(self)
        input_layout = QVBoxLayout(input_area)
        input_layout.addWidget(self.model_bar)
        input_layout.addWidget(self.input)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.output)
        splitter.addWidget(input_area)
        splitter.setSizes([550, 180])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        layout.setContentsMargins(0, 0, 0, 0)

        self.input.send_requested.connect(self._on_send)
        self.model_bar.model_changed.connect(self._on_model_changed)

    # ------------------------------------------------------------------
    # 版本切换
    # ------------------------------------------------------------------
    def _on_model_changed(self, version: ModelVersion) -> None:
        """切换版本项：写 provider 单例状态，下次请求生效。"""
        llm = get_llm("deepseek")
        if isinstance(llm, DeepSeekLLM):
            llm.set_version(version)

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
        self._seen_reasoning = False

        messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history)

        self._worker = ChatWorker(get_llm("deepseek"), messages, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_with_error.connect(self._on_finished)
        self._worker.start()

    def _on_chunk(self, chunk: Chunk) -> None:
        if chunk.kind == "reasoning":
            # 思维链只上屏，不入 buffer/历史（DeepSeek 约束：不得回传）
            self._seen_reasoning = True
            self.output.append_reasoning_chunk(chunk.text)
            return
        if self._seen_reasoning:
            self.output.end_reasoning()  # 思维链与正文之间插空行
            self._seen_reasoning = False
        self._stream_buffer += chunk.text
        self.output.append_stream_chunk(chunk.text)

    def _on_finished(self, error: str) -> None:
        if error:
            if self._seen_reasoning:
                self.output.end_reasoning()
                self._seen_reasoning = False
            self.output.append_stream_chunk(f"\n[请求失败] {error}")
            self._history.pop()  # 失败的用户消息不入历史
        else:
            self._history.append({"role": "assistant", "content": self._stream_buffer})
        self.output.end_stream()
        self._set_busy(False)
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.model_bar.setEnabled(not busy)
        self.input.setPlaceholderText("AI 回复中…" if busy else "输入消息，Enter 发送 / Shift+Enter 换行")
