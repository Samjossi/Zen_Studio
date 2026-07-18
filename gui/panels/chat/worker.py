"""流式工作线程：后台跑 LLM generator，信号上屏，避免阻塞 UI。"""
from PySide6.QtCore import QThread, Signal

from llm import LanguageModel, Message


class ChatWorker(QThread):
    """在后台线程执行流式 chat，逐块发射文本信号。"""

    #: 流式文本块
    chunk_received = Signal(str)
    #: 完成（正常或异常均触发，附带错误信息或空串）
    finished_with_error = Signal(str)

    def __init__(self, llm: LanguageModel, messages: list[Message], parent=None) -> None:
        super().__init__(parent)
        self._llm = llm
        self._messages = messages

    def run(self) -> None:
        try:
            for chunk in self._llm.chat(self._messages):
                self.chunk_received.emit(chunk)
            self.finished_with_error.emit("")
        except Exception as e:  # noqa: BLE001 — 异常统一上屏，不崩溃
            self.finished_with_error.emit(str(e))
