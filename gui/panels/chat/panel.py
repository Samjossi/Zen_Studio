"""聊天面板装配：上输出 + 下输入，连接 LLM 流式线程。

多标签改造（2026-07-22，work plans/2026-0722-0756 计划 P3 任务 13）：
- 自持 provider 实例（不再从共享 registry 取单例）：构造时按当前后端
  自建 KimiCliLLM/KimiAcpLLM(workspace_root)，标签间完全隔离（D6 方案 A）
- ModelBar 上移到 ChatTabs 容器顶部为全局控件（D5），本面板只留输入框
- 审批请求统一提交全局审批队列（PERMISSION_QUEUE），多标签串行弹窗
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QSplitter, QVBoxLayout, QWidget

from gui.panels.chat.input import ChatInput
from gui.panels.chat.output import ChatOutput
from gui.panels.chat.permission_queue import PERMISSION_QUEUE
from gui.panels.chat.worker import ChatWorker
from gui.settings import KEY_PERMISSION_AUTO_ALLOW, KEY_THEME
from gui.theme import get_theme_palette, load_settings
from gui.window_state import decode_state, encode_state
from llm import (
    BACKEND_KIMI_ACP,
    BACKEND_KIMI_CLI,
    BACKEND_LABELS,
    Chunk,
    KimiAcpLLM,
    KimiCliLLM,
    LanguageModel,
    Message,
    PermissionParams,
    kimi_available,
)
from llm.permission_policy import DECISION_ALLOW, decide_permission, select_option_id

#: 系统提示词（第一阶段固定）
SYSTEM_PROMPT = "你是 Zen Studio IDE 的内置助手，回答简洁，使用中文。"


class ChatPanel(QWidget):
    """单个 AI 会话标签页（独立 provider 实例，由 ChatTabs 托管）。"""

    #: 发送/停止状态变化（ChatTabs 汇总后联动禁用全局 ModelBar 与菜单组）
    busy_changed = Signal(bool)

    #: 默认布局尺寸（px）：输出区 / 输入区（初排与 reset_layout 单点来源）
    DEFAULT_SPLITTER_SIZES = [550, 180]

    def __init__(
        self,
        backend: str,
        version: str | None,
        workspace_root: str,
        parent: QWidget | None = None,
    ) -> None:
        """
        :param backend: 初始后端（registry 名；全局模型选择由 ChatTabs 广播更新）
        :param version: 初始模型别名（None = provider 默认模型）
        :param workspace_root: 工作区根（provider cwd 与拖入文件 @相对路径 基准）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._history: list[Message] = []
        self._worker: ChatWorker | None = None
        self._stream_buffer = ""
        self._has_seen_reasoning = False

        self.output = ChatOutput(self._reasoning_color_of(load_settings()[KEY_THEME]), self)
        self.input = ChatInput(self)
        self.input.set_workspace_root(workspace_root)
        self._llm_name = backend
        self._providers = self._build_providers(workspace_root, version)

        self._build_layout()
        self._connect_signals()

    # ------------------------------------------------------------------
    # provider 实例（自持；D6 方案 A：每标签独立连接）
    # ------------------------------------------------------------------
    def _build_providers(self, workspace_root: str, version: str | None) -> dict[str, LanguageModel]:
        """按当前后端/版本自建两个 kimi 后端实例；kimi 不可用时为空 dict。"""
        providers: dict[str, LanguageModel] = {}
        if not kimi_available():
            return providers
        providers[BACKEND_KIMI_CLI] = KimiCliLLM(workspace_root=workspace_root)
        acp = KimiAcpLLM(workspace_root=workspace_root)
        acp.set_permission_handler(self._ask_permission)
        providers[BACKEND_KIMI_ACP] = acp
        if version:  # 启动一致性：预选模型写入两个实例，避免 UI 与后端不一致
            for provider in providers.values():
                if isinstance(provider, (KimiCliLLM, KimiAcpLLM)):
                    provider.set_model(version)
        return providers

    def set_model_selection(self, backend: str, version: str | None) -> None:
        """全局模型选择广播（D5）：记录后端 + 写自身 provider 实例。

        上下文不迁移（各后端会话各自独立），切后端时输出提示行。
        持久化与 ModelBar UI 归 ChatTabs/ModelBar，本方法不管。
        """
        if backend != self._llm_name:
            self.output.append_message(
                "系统", f"已切换到 {BACKEND_LABELS.get(backend, backend)} 后端，开始新会话")
        self._llm_name = backend
        provider = self._providers.get(backend)
        if isinstance(provider, (KimiCliLLM, KimiAcpLLM)) and isinstance(version, str):
            provider.set_model(version)

    def request_stop(self) -> None:
        """停止当前轮次（全局停止按钮经 ChatTabs 路由至此），幂等。"""
        if self._worker is not None:
            self._worker.request_stop()
            self.input.setPlaceholderText("正在停止…")

    def close(self) -> None:
        """标签关闭清理：停轮次并等 worker 退出 + 终止 ACP 长驻子进程。

        不 wait 则 deleteLater 会销毁仍在运行的 ChatWorker(QThread)
        （"QThread: Destroyed while thread is still running"，未定义行为）。
        """
        self.request_stop()
        if self._worker is not None:
            self._worker.wait(3000)  # 协议取消正常毫秒级返回；3s 兜底防永久阻塞
        for provider in self._providers.values():
            if isinstance(provider, KimiAcpLLM):
                provider.close()

    # ------------------------------------------------------------------
    # UI 构建与接线
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        """布局装配：PanelCard 单卡片整合（输出区 + 输入框）。

        卡片内保留垂直 splitter（输出/输入比例可调、状态持久化不变）；
        ChatOutput 透明融入卡片白底，输入框保留自身 6px 圆角嵌于卡内。
        """
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self.output)
        self._splitter.addWidget(self.input)
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

    # ------------------------------------------------------------------
    # 主题（思维链前景色随主题资源包切换；由 ChatTabs 统一转发）
    # ------------------------------------------------------------------
    @staticmethod
    def _reasoning_color_of(theme: str) -> str:
        """主题名 → 思维链前景色（资源包 chat.reasoning_fg）。"""
        return get_theme_palette(theme)["chat"]["reasoning_fg"]

    def apply_theme(self, theme: str) -> None:
        """主题切换：更新输出区思维链前景色（仅影响此后追加的块）。"""
        self.output.set_reasoning_color(self._reasoning_color_of(theme))

    # ------------------------------------------------------------------
    # 输出/输入区分隔栏状态持久化（由 ChatTabs 转发）
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
    # ACP 审批回环（方案 F 默认放手：纯逻辑前置决策，仅黑名单命中走队列弹框）
    # ------------------------------------------------------------------
    def _ask_permission(self, params: PermissionParams) -> str | None:
        """ACP 审批处理器：在 agent reader 线程被调用。

        自动放行开关开（默认）：decide_permission 纯函数前置决策——allow 直接
        同步返回 optionId（零 GUI、零阻塞，不触碰队列/QTimer）；仅危险命令
        黑名单命中才提交全局审批队列弹窗（附命中原因）。开关关：逃生舱，
        恢复逐次确认现状（全部走弹窗）。返回 None 由上层按拒绝兜底。
        """
        if load_settings()[KEY_PERMISSION_AUTO_ALLOW]:
            decision, reason = decide_permission(params)
            if decision == DECISION_ALLOW:
                option_id = select_option_id(params.get("options") or [])
                if option_id is not None:
                    return option_id
                # 决策为 allow 但 agent 未提供 allow 类选项：不静默拒绝
                # （None 会被上层兜底为 reject），降级普通弹窗交还用户裁决
                return PERMISSION_QUEUE.ask(params, self)
            return PERMISSION_QUEUE.ask(params, self, danger_reason=reason)
        return PERMISSION_QUEUE.ask(params, self)

    # ------------------------------------------------------------------
    # 发送与流式接收
    # ------------------------------------------------------------------
    def _on_send(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # 上一次未结束，忽略（输入框此时已禁用）
        provider = self._providers.get(self._llm_name)
        if provider is None:
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

        self._worker = ChatWorker(provider, messages, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_with_error.connect(self._on_finished)
        self._worker.stopped_by_user.connect(self._on_stopped)
        self._worker.start()

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

    def _set_busy(self, is_busy: bool) -> None:
        # 输入框保持可编辑（Enter 发送有 isRunning 守卫拦截，文本不丢）；
        # 全局 ModelBar 禁用与停止按钮归 ChatTabs 汇总处理
        self.busy_changed.emit(is_busy)
        if is_busy:
            busy_text = f"{BACKEND_LABELS.get(self._llm_name, 'AI')} 响应中…点击 ■ 停止可中断"
        else:
            busy_text = "输入消息，Enter 发送 / Shift+Enter 换行"
        self.input.setPlaceholderText(busy_text)
