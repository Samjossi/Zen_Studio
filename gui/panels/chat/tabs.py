"""AI 会话标签容器：全局 ModelBar + 多标签 ChatPanel（上限 4）。

多标签改造（2026-07-22，work plans/2026-0722-0756 计划 P3）：
- 每标签一个独立 ChatPanel + 独立 provider 实例（D6 方案 A：每标签
  独立 kimi acp 连接，完全隔离、并行无锁竞争）
- ModelBar 为全局控件（D5）：所有标签共用一个模型选择，切换广播到
  全部标签的 provider 实例；持久化仍由 ModelBar 自管
- busy 汇总：任一标签响应中即禁用全局 ModelBar 并发射 busy_changed
  （主窗口联动禁用设置菜单 AI 模型组），防切换中途打断正在响应的标签
- 停止按钮路由：优先停当前活动标签，活动标签空闲时停任一忙标签
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTabWidget, QToolButton, QVBoxLayout, QWidget

from gui.panels.chat.model_bar import ModelBar
from gui.panels.chat.panel import ChatPanel
from gui.settings import KEY_MODEL_BACKEND, KEY_MODEL_VERSION, update_settings


class ChatTabs(QWidget):
    """左栏 AI 聊天区：顶部全局模型行 + 标签化会话（新建/关闭，上限 4）。"""

    #: 汇总忙碌态：任一标签响应中即 True（主窗口联动禁用菜单 AI 模型组）
    busy_changed = Signal(bool)

    #: 标签数上限（用户决策 D1：约束长驻 kimi acp 进程数与 token 消耗）
    MAX_TABS = 4

    def __init__(self, workspace_root: str, parent: QWidget | None = None) -> None:
        """
        :param workspace_root: 工作区根（透传每个标签的 provider 与 @相对路径）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._workspace_root = workspace_root
        self._tab_seq = 0  # 标题序号单调递增（关闭不复用，防「会话 2」指代漂移）
        self._busy_panels: set[ChatPanel] = set()

        self.model_bar = ModelBar(self)
        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
        self._tabs.setDocumentMode(True)
        # 「+」新建按钮：固定右上角（达上限禁用并提示）
        self._add_button = QToolButton(self)
        self._add_button.setText("+")
        self._add_button.setToolTip("新建 AI 会话标签")
        self._tabs.setCornerWidget(self._add_button, Qt.Corner.TopRightCorner)

        layout = QVBoxLayout(self)
        layout.addWidget(self.model_bar)
        layout.addWidget(self._tabs, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._add_button.clicked.connect(self.add_tab)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self.model_bar.selection_changed.connect(self._on_selection_changed)
        self.model_bar.stop_requested.connect(self._route_stop)

        self.add_tab()  # 首标签：等价改造前的单聊天面板

    # ------------------------------------------------------------------
    # 标签生命周期
    # ------------------------------------------------------------------
    def add_tab(self) -> None:
        """新建会话标签（当前全局后端/版本）；达上限忽略（按钮已禁用兜底）。"""
        if self._tabs.count() >= self.MAX_TABS:
            return
        panel = ChatPanel(
            self.model_bar.current_backend(),
            self.model_bar.current_version(),
            self._workspace_root,
            self,
        )
        panel.busy_changed.connect(lambda busy, p=panel: self._on_tab_busy(p, busy))
        self._tab_seq += 1
        self._tabs.addTab(panel, f"会话 {self._tab_seq}")
        self._tabs.setCurrentWidget(panel)
        self._refresh_add_button()

    def _close_tab(self, index: int) -> None:
        """关闭标签：保底一个不关；清理其 provider（终止 kimi acp 进程）。

        busy 清算在 panel.close() 之后：close 等 worker 退出，其收尾信号
        经 _on_tab_busy 自然驱动重算——提前清除会把"响应中禁切模型"
        不变量在 worker 实际停止前打破。
        """
        if self._tabs.count() <= 1:
            return
        panel = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if isinstance(panel, ChatPanel):
            panel.close()
            self._busy_panels.discard(panel)
            self._recompute_busy()
            panel.deleteLater()
        self._refresh_add_button()

    def _refresh_add_button(self) -> None:
        """上限治理：达 4 禁用「+」并以 tooltip 提示。"""
        at_max = self._tabs.count() >= self.MAX_TABS
        self._add_button.setEnabled(not at_max)
        self._add_button.setToolTip(
            f"已达标签上限 {self.MAX_TABS}" if at_max else "新建 AI 会话标签")

    # ------------------------------------------------------------------
    # 全局模型选择（D5：ModelBar 切换 → 广播全部标签）
    # ------------------------------------------------------------------
    def _on_selection_changed(self, backend: str, version: object) -> None:
        """ModelBar 用户切换 → 广播到全部标签的 provider 实例。"""
        for panel in self._panels():
            panel.set_model_selection(backend, version if isinstance(version, str) else None)

    def apply_model_selection(self, backend: str, version: str | None) -> None:
        """菜单驱动切换（设置菜单 ▸ AI 模型）：恢复 ModelBar + 广播全部标签。

        ModelBar.set_selection 全程阻断信号（不写盘、不发 selection_changed），
        故写盘与广播在此显式补齐；发送中（busy）由菜单侧禁用入口。
        """
        self.model_bar.set_selection(backend, version)
        backend = self.model_bar.current_backend()
        version = self.model_bar.current_version()
        update_settings({KEY_MODEL_BACKEND: backend, KEY_MODEL_VERSION: version})
        self._on_selection_changed(backend, version)

    # ------------------------------------------------------------------
    # busy 汇总与停止路由（任务 15/16）
    # ------------------------------------------------------------------
    def _on_tab_busy(self, panel: ChatPanel, is_busy: bool) -> None:
        """单标签忙碌态变化 → 汇总重算（任一忙即整体忙）。"""
        if is_busy:
            self._busy_panels.add(panel)
        else:
            self._busy_panels.discard(panel)
        self._recompute_busy()

    def _recompute_busy(self) -> None:
        any_busy = bool(self._busy_panels)
        self.model_bar.set_busy(any_busy)  # 双下拉禁用 + 停止按钮可见
        self.busy_changed.emit(any_busy)

    def _route_stop(self) -> None:
        """停止按钮路由：优先当前活动标签，其空闲时停任一忙标签。"""
        current = self._tabs.currentWidget()
        if isinstance(current, ChatPanel) and current in self._busy_panels:
            current.request_stop()
            return
        if self._busy_panels:
            next(iter(self._busy_panels)).request_stop()

    # ------------------------------------------------------------------
    # 对外兼容接口（主窗口/WindowStateStore 消费；转发到标签）
    # ------------------------------------------------------------------
    def _panels(self) -> list[ChatPanel]:
        return [self._tabs.widget(i) for i in range(self._tabs.count())
                if isinstance(self._tabs.widget(i), ChatPanel)]

    def apply_theme(self, theme: str) -> None:
        """主题切换转发全部标签（思维链前景色）。"""
        for panel in self._panels():
            panel.apply_theme(theme)

    def save_state(self) -> str:
        """当前活动标签的分隔栏状态（多标签共用同一持久化键）。"""
        current = self._tabs.currentWidget()
        return current.save_state() if isinstance(current, ChatPanel) else ""

    def restore_state(self, state: str | None) -> None:
        """恢复分隔栏到全部标签（启动时通常仅首标签）。"""
        for panel in self._panels():
            panel.restore_state(state)

    def reset_layout(self) -> None:
        """恢复默认布局：全部标签的输出/输入区回初始尺寸。"""
        for panel in self._panels():
            panel.reset_layout()
