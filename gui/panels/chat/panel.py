"""聊天面板装配：上输出 + 下输入（含模型行），连接 LLM 流式线程。"""
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from llm import Chunk, KimiAcpLLM, KimiCliLLM, Message, get_llm
from gui.panels.chat.input import ChatInput
from gui.panels.chat.model_bar import ModelBar
from gui.panels.chat.output import ChatOutput
from gui.panels.chat.permission_dialog import PermissionDialog
from gui.panels.chat.worker import ChatWorker

#: 系统提示词（第一阶段固定）
SYSTEM_PROMPT = "你是 Zen Studio IDE 的内置助手，回答简洁，使用中文。"

#: 后端 registry 名 → 显示名（切换提示与忙碌占位文案）
BACKEND_LABELS = {"kimi-cli": "Kimi CLI", "kimi-acp": "Kimi ACP"}

#: 审批等待超时（秒）；超时按拒绝兜底，防 agent 永久阻塞
PERMISSION_TIMEOUT_S = 180


class ChatPanel(QWidget):
    """左栏 AI 聊天面板。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._history: list[Message] = []
        self._worker: ChatWorker | None = None
        self._stream_buffer = ""
        self._seen_reasoning = False
        self._llm_name = "kimi-cli"  # 当前后端（registry 名，本期唯一）

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
        self.model_bar.selection_changed.connect(self._on_selection_changed)
        self._wire_permission_handler()

    # ------------------------------------------------------------------
    # ACP 审批回环（reader 线程 → GUI 线程模态框）
    # ------------------------------------------------------------------
    def _wire_permission_handler(self) -> None:
        try:
            llm = get_llm("kimi-acp")
        except KeyError:
            return  # kimi 不可用，后端未注册
        if isinstance(llm, KimiAcpLLM):
            llm.set_permission_handler(self._ask_permission)

    def _ask_permission(self, params: dict) -> str | None:
        """ACP 审批处理器：在 agent reader 线程被调用；转 GUI 线程弹模态框。

        返回选中的 optionId；用户关闭或超时返回 None（上层按拒绝兜底）。
        """
        done = threading.Event()
        choice: list[str | None] = [None]

        def ask() -> None:
            try:
                dialog = PermissionDialog(params, self)
                dialog.exec()
                choice[0] = dialog.selected_option_id()
            finally:
                done.set()

        # QTimer.singleShot(receiver, callable)：callable 在 receiver 所在（GUI）线程执行
        QTimer.singleShot(0, self, ask)
        if not done.wait(timeout=PERMISSION_TIMEOUT_S):
            return None
        return choice[0]

    # ------------------------------------------------------------------
    # 后端/版本切换
    # ------------------------------------------------------------------
    def _on_selection_changed(self, backend: str, version: object) -> None:
        """切换后端/版本：写 provider 单例状态，下次请求生效。

        上下文不迁移（各 CLI 会话各自独立），切后端时输出提示行。
        """
        if backend != self._llm_name:
            self.output.append_message("系统", f"已切换到 {BACKEND_LABELS.get(backend, backend)} 后端，开始新会话")
        self._llm_name = backend
        llm = get_llm(backend)
        if isinstance(llm, (KimiCliLLM, KimiAcpLLM)) and isinstance(version, str):
            llm.set_model(version)

    # ------------------------------------------------------------------
    # 发送与流式接收
    # ------------------------------------------------------------------
    def _on_send(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # 上一次未结束，忽略（输入框此时已禁用）
        try:
            llm = get_llm(self._llm_name)
        except KeyError:
            self.output.append_message("系统", f"后端不可用：{self._llm_name}（未检测到本机 agent CLI）")
            return
        self.input.clear()
        self._set_busy(True)

        self._history.append({"role": "user", "content": text})
        self.output.append_message("我", text)
        self.output.begin_stream("AI")
        self._stream_buffer = ""
        self._seen_reasoning = False

        messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history)

        self._worker = ChatWorker(llm, messages, self)
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
        if busy:
            busy_text = f"{BACKEND_LABELS.get(self._llm_name, 'AI')} 响应中…"
        else:
            busy_text = "输入消息，Enter 发送 / Shift+Enter 换行"
        self.input.setPlaceholderText(busy_text)
