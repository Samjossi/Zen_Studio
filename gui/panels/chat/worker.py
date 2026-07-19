"""流式工作线程：后台跑 LLM generator，信号上屏，避免阻塞 UI。"""
from PySide6.QtCore import QThread, Signal

from llm import LanguageModel, Message


class ChatWorker(QThread):
    """在后台线程执行流式 chat，逐块发射 Chunk 信号。

    停止机制（2026-07-19，见 文档/选型记录/2026-0719-0747_AI会话停止功能
    选型报告.md，B+A+A+B 方案）：
    - 立即层：`request_stop()` 直调 `llm.cancel()` 协议取消（打断阻塞等待）
    - 检查点层：`_stop_requested` 标志，每 chunk 轮询，接住取消后的残余流
    - 竞态层：worker 未 start 先置标志，`run()` 首轮即返（不建生成器）
    归一化：无论 break 还是异常，只要标志置位一律按"用户中断"收尾
    （kimi-cli 被 terminate 后 rc≠0 会抛 RuntimeError，不能误判为失败）。
    """

    #: 流式块（Chunk，kind 区分正文/思维链）
    chunk_received = Signal(object)
    #: 完成（正常或异常均触发，附带错误信息或空串）
    finished_with_error = Signal(str)
    #: 用户主动停止（与正常/失败分离的第三态）
    stopped_by_user = Signal()

    def __init__(self, llm: LanguageModel, messages: list[Message], parent=None) -> None:
        super().__init__(parent)
        self._llm = llm
        self._messages = messages
        self._stop_requested = False

    def request_stop(self) -> None:
        """请求停止：置标志 + 协议取消。可从任意线程调用，幂等。"""
        self._stop_requested = True
        try:
            self._llm.cancel()
        except Exception:  # noqa: BLE001 — cancel 失败不阻断停止（close/finally 兜底）
            pass

    def run(self) -> None:
        gen = None
        try:
            if self._stop_requested:  # 竞态层：未启动即被取消，不建生成器
                self.stopped_by_user.emit()
                return
            gen = self._llm.chat(self._messages)
            for chunk in gen:
                if self._stop_requested:  # 检查点层：残余流丢弃
                    break
                self.chunk_received.emit(chunk)
        except Exception as e:  # noqa: BLE001 — 异常统一上屏，不崩溃
            if self._stop_requested:
                self.stopped_by_user.emit()
            else:
                self.finished_with_error.emit(str(e))
            return
        finally:
            if gen is not None:
                gen.close()  # GeneratorExit 触发 provider finally 资源兜底
        if self._stop_requested:
            self.stopped_by_user.emit()
        else:
            self.finished_with_error.emit("")
