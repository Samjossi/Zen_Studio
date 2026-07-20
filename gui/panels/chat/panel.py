"""聊天面板装配：上输出 + 下输入（含模型行），连接 LLM 流式线程。"""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QSplitter, QVBoxLayout, QWidget

from gui.settings import (
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSION,
    KEY_THEME,
    decode_state,
    encode_state,
    update_settings,
)
from gui.theme import get_theme_palette, load_settings
from llm import (
    BACKEND_KIMI_ACP,
    BACKEND_LABELS,
    Chunk,
    KimiAcpLLM,
    KimiCliLLM,
    LLMRegistry,
    Message,
    PermissionParams,
)
from gui.panels.chat.input import ChatInput
from gui.panels.chat.model_bar import ModelBar
from gui.panels.chat.output import ChatOutput
from gui.panels.chat.permission_dialog import PermissionDialog
from gui.panels.chat.worker import ChatWorker

#: 系统提示词（第一阶段固定）
SYSTEM_PROMPT = "你是 Zen Studio IDE 的内置助手，回答简洁，使用中文。"

#: 审批等待超时（秒）；超时按拒绝兜底，防 agent 永久阻塞
PERMISSION_TIMEOUT_S = 180


class ChatPanel(QWidget):
    """左栏 AI 聊天面板。"""

    #: 发送/停止状态变化（供主窗口联动禁用设置菜单 AI 模型组）
    busy_changed = Signal(bool)

    #: 默认布局尺寸（px）：输出区 / 输入区（初排与 reset_layout 单点来源）
    DEFAULT_SPLITTER_SIZES = [550, 180]

    def __init__(
        self,
        llm_registry: LLMRegistry,
        parent: QWidget | None = None,
        workspace_root: str | None = None,
    ) -> None:
        """
        :param llm_registry: LLM 注册表（main.py 显式装配，经 MainWindow 注入）；
            provider 单例状态（模型选择、审批处理器）由本面板写入
        :param parent: 父控件
        :param workspace_root: 工作区根路径（拖入文件计算 @相对路径 用，由 MainWindow 注入）
        """
        super().__init__(parent)
        self._llm_registry = llm_registry
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._history: list[Message] = []
        self._worker: ChatWorker | None = None
        self._stream_buffer = ""
        self._has_seen_reasoning = False

        self.output = ChatOutput(self._reasoning_color_of(load_settings()[KEY_THEME]), self)
        self.input = ChatInput(self)
        if workspace_root is not None:
            self.input.set_workspace_root(workspace_root)
        self.model_bar = ModelBar(self)
        # 启动一致性：后端与版本取 ModelBar 恢复后的持久化选择，
        # 并主动同步 provider 单例，避免"UI 显示与后端生效"不一致
        self._llm_name = self.model_bar.current_backend()
        self._sync_backend_model()

        self._build_layout()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI 构建与接线
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        """布局装配：PanelCard 单卡片整合（输出区 + 模型行 + 输入框）。

        卡片内保留垂直 splitter（输出/输入比例可调、状态持久化不变）；
        ChatOutput 透明融入卡片白底，输入框/下拉保留自身 6px 圆角嵌于卡内。
        """
        input_area = QWidget(self)
        input_layout = QVBoxLayout(input_area)
        input_layout.addWidget(self.model_bar)
        input_layout.addWidget(self.input)
        input_layout.setContentsMargins(0, 4, 0, 0)
        input_layout.setSpacing(4)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self.output)
        self._splitter.addWidget(input_area)
        self._splitter.setSizes(self.DEFAULT_SPLITTER_SIZES)

        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(self._splitter, 1)
        card_layout.setContentsMargins(8, 6, 8, 8)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）；
        # 下边距 6px + 状态栏定高 26px = 底部总间距 32px（一体化设计）
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

    def _connect_signals(self) -> None:
        """跨组件信号统一接线（本面板的接线图）。"""
        self.input.send_requested.connect(self._on_send)
        self.model_bar.selection_changed.connect(self._on_selection_changed)
        self.model_bar.stop_requested.connect(self._on_stop)
        self._wire_permission_handler()

    # ------------------------------------------------------------------
    # 主题（思维链前景色随主题资源包切换；由 MainWindow.switch_theme 统一调用）
    # ------------------------------------------------------------------
    @staticmethod
    def _reasoning_color_of(theme: str) -> str:
        """主题名 → 思维链前景色（资源包 chat.reasoning_fg）。"""
        return get_theme_palette(theme)["chat"]["reasoning_fg"]

    def apply_theme(self, theme: str) -> None:
        """主题切换：更新输出区思维链前景色（仅影响此后追加的块）。"""
        self.output.set_reasoning_color(self._reasoning_color_of(theme))

    # ------------------------------------------------------------------
    # 输出/输入区分隔栏状态持久化（由 MainWindow 统一调用）
    # ------------------------------------------------------------------
    def save_state(self) -> str:
        """分隔栏状态 → base64 字符串。"""
        return encode_state(self._splitter.saveState())

    def restore_state(self, state: str | None) -> None:
        """恢复分隔栏；None 或损坏数据静默保留默认尺寸。"""
        if state:
            self._splitter.restoreState(decode_state(state))

    def reset_layout(self) -> None:
        """恢复默认布局：输出/输入区回初始尺寸（视图菜单「恢复默认布局」）。"""
        self._splitter.setSizes(self.DEFAULT_SPLITTER_SIZES)

    # ------------------------------------------------------------------
    # 菜单驱动的模型切换（设置菜单 ▸ AI 模型）
    # ------------------------------------------------------------------
    def apply_model_selection(self, backend: str, version: str | None) -> None:
        """菜单驱动切换（等价 ModelBar 用户切换）：恢复 UI + 写盘 + 后端同步。

        ModelBar.set_selection 全程阻断信号（不发 selection_changed、不写盘），
        故写盘与 provider 同步在此显式补齐；发送中（busy）由菜单侧禁用入口。
        """
        self.model_bar.set_selection(backend, version)
        backend = self.model_bar.current_backend()
        version = self.model_bar.current_version()
        update_settings({KEY_MODEL_BACKEND: backend, KEY_MODEL_VERSION: version})
        self._on_selection_changed(backend, version)

    # ------------------------------------------------------------------
    # ACP 审批回环（reader 线程 → GUI 线程模态框）
    # ------------------------------------------------------------------
    def _wire_permission_handler(self) -> None:
        try:
            llm = self._llm_registry.get(BACKEND_KIMI_ACP)
        except KeyError:
            return  # kimi 不可用，后端未注册
        if isinstance(llm, KimiAcpLLM):
            llm.set_permission_handler(self._ask_permission)

    def _ask_permission(self, params: PermissionParams) -> str | None:
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
    def _sync_backend_model(self) -> None:
        """把 ModelBar 当前选中版本写入 provider 单例（启动时调用一次）。"""
        version = self.model_bar.current_version()
        try:
            llm = self._llm_registry.get(self._llm_name)
        except KeyError:
            return  # 后端不可用（未检测到本机 agent CLI），发送时再提示
        if isinstance(llm, (KimiCliLLM, KimiAcpLLM)) and isinstance(version, str):
            llm.set_model(version)

    def _on_selection_changed(self, backend: str, version: object) -> None:
        """切换后端/版本：写 provider 单例状态，下次请求生效。

        上下文不迁移（各 CLI 会话各自独立），切后端时输出提示行。
        """
        if backend != self._llm_name:
            self.output.append_message("系统", f"已切换到 {BACKEND_LABELS.get(backend, backend)} 后端，开始新会话")
        self._llm_name = backend
        llm = self._llm_registry.get(backend)
        if isinstance(llm, (KimiCliLLM, KimiAcpLLM)) and isinstance(version, str):
            llm.set_model(version)

    # ------------------------------------------------------------------
    # 发送与流式接收
    # ------------------------------------------------------------------
    def _on_send(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # 上一次未结束，忽略（输入框此时已禁用）
        try:
            llm = self._llm_registry.get(self._llm_name)
        except KeyError:
            self.output.append_message("系统", f"后端不可用：{self._llm_name}（未检测到本机 agent CLI）")
            return
        self.input.clear()
        self._set_busy(True)

        self._history.append({"role": "user", "content": text})
        self.output.append_message("我", text)
        self.output.begin_stream("AI")
        self._stream_buffer = ""
        self._has_seen_reasoning = False

        messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history)

        self._worker = ChatWorker(llm, messages, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_with_error.connect(self._on_finished)
        self._worker.stopped_by_user.connect(self._on_stopped)
        self._worker.start()

    def _on_stop(self) -> None:
        """停止按钮：协议取消（立即层）+ 标志轮询（检查点层），幂等。"""
        if self._worker is not None:
            self._worker.request_stop()
            self.input.setPlaceholderText("正在停止…")

    def _on_chunk(self, chunk: Chunk) -> None:
        if chunk.kind == "reasoning":
            # 思维链只上屏，不入 buffer/历史（DeepSeek 约束：不得回传）
            self._has_seen_reasoning = True
            self.output.append_reasoning_chunk(chunk.text)
            return
        if self._has_seen_reasoning:
            self.output.end_reasoning()  # 思维链与正文之间插空行
            self._has_seen_reasoning = False
        self._stream_buffer += chunk.text
        self.output.append_stream_chunk(chunk.text)

    def _on_finished(self, error: str) -> None:
        if error:
            if self._has_seen_reasoning:
                self.output.end_reasoning()
                self._has_seen_reasoning = False
            self.output.append_stream_chunk(f"\n[请求失败] {error}")
            self._history.pop()  # 失败的用户消息不入历史
        else:
            self._history.append({"role": "assistant", "content": self._stream_buffer})
        self.output.end_stream()
        self._set_busy(False)
        self._worker = None

    def _on_stopped(self) -> None:
        """用户中断收尾（第三态）：整体回滚——中断轮不入历史；
        屏幕已输出内容不擦除（可复制兜底），追加停止标注。"""
        if self._has_seen_reasoning:
            self.output.end_reasoning()
            self._has_seen_reasoning = False
        self._history.pop()  # 回滚用户消息；半截回复随 _stream_buffer 丢弃
        self.output.append_stream_chunk("\n⏹ 已手动停止")
        self.output.end_stream()
        self._set_busy(False)
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        # 输入框保持可编辑（Enter 发送有 isRunning 守卫拦截，文本不丢）；
        # 模型行双下拉禁用防切后端，停止按钮 busy 时可见
        self.model_bar.set_busy(busy)
        self.busy_changed.emit(busy)  # 主窗口联动禁用设置菜单 AI 模型组
        if busy:
            busy_text = f"{BACKEND_LABELS.get(self._llm_name, 'AI')} 响应中…点击 ■ 停止可中断"
        else:
            busy_text = "输入消息，Enter 发送 / Shift+Enter 换行"
        self.input.setPlaceholderText(busy_text)
