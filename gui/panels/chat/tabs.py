"""AI 会话标签容器：新建注入值 + 多标签 ChatPanel（上限 4）。

多标签改造（2026-07-22，文档/修改记录/2026-0722-0756 计划 P3）：
- 每标签一个独立 ChatPanel + 独立 provider 实例（D6 方案 A：每标签
  独立 kimi acp 连接，完全隔离、并行无锁竞争）
- ModelBar 为每标签底行实例（2026-0724-2354 计划，纯视图组件）

异构后台选择（2026-08-03，文档/修改记录/2026-0803-0112 计划，翻案
2026-0724-2354 计划 D5「全部标签共享同一选择 + 广播」）：
- 选择状态每标签自持：(backend, version) 的有效值唯一来源是各标签的
  ModelBar（回退后的有效值以 ModelBar 为准）；本容器不再广播同步
- 容器只留「新建注入值」：_backend/_version 语义收缩为「下一个新建
  标签的初始选择 + 重启默认」，来源 = 最近一次用户显式切换（任一入口）；
  零标签时菜单/设置中心切换只更新该注入值（等价改造前语义）
- 持久化一期语义不变：KEY_MODEL_BACKEND 与记忆表写「最近使用值」，
  不做每标签三元组持久化（二期候选）
- 菜单/设置中心驱动切换只作用于当前活动标签，不再「一键洗全部会话」
- busy 粒度收窄：各标签独立禁用自身三按钮（原「任一忙禁全部」是为防
  广播切换打断响应中标签，广播废除后失去依据）；busy_changed 对外
  改报「当前活动标签忙闲」（主窗口联动设置中心模型页禁用）
- 标签标题恒为「会话 N」（2026-08-03 用户决策，翻案 D6「会话 N ·
  后台名」拼接）：后台辨识归各标签底部 ModelBar，不占标签标题
- 停止归各标签本地：输入区底行发送/停止双态按钮直停本标签
  （2026-0724-2305 计划 T5，替代原全局停止按钮 + _route_stop 路由）

推理强度四级（2026-08-06，用户拍板：每接口静态声明 + 记忆表即时生效）：
- ModelBar 第四级 effort_changed 信号由本容器收敛：注入值 _effort
  更新 + model_efforts 记忆表写盘（与 model_versions 同构）+ 本标签
  provider 鸭子类型 set_effort 即时生效；切后台/接口/模型路径按（新）
  接口记忆表解析并应用（None = 未定制，agent 默认强度生效）

全关与关闭卡顿治理（2026-07-22，文档/修改记录/2026-0722-1117 计划）：
- 标签可全部关闭（不再保底一个）；零标签时 QStackedWidget 切到占位页
  （提示文案 + 居中「新建会话」按钮）；选择状态在本容器不随标签消失，
  新建标签时注入恢复（替代原「ModelBar 全局常驻」语义）
- 序号语义：非全关不复用已关闭序号（防「会话 2」指代漂移）；
  全部关闭即 _tab_seq 重置，再新建从「会话 1」开始（用户决策）
- 关闭异步化：_close_tab 立即摘标签返回；terminate/wait 等重等待
  全部移入 ChatPanel 的 daemon 清理线程，GUI 线程零阻塞
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.panels.chat.panel import ChatPanel
from gui.settings import (
    KEY_MODEL_BACKEND,
    KEY_MODEL_EFFORTS,
    KEY_MODEL_VERSIONS,
    load_settings,
    remember_model_effort,
    remember_model_version,
    update_settings,
)


class ChatTabs(QWidget):
    """左栏 AI 聊天区：标签化会话（新建/关闭，上限 4）+ 新建注入值持有。"""

    #: 当前活动标签忙碌态（主窗口联动禁用设置中心模型页；标签切换时重报）
    busy_changed = Signal(bool)

    #: 当前活动标签模型选择变化（携带 registry 后端名 + 版本载荷；主窗口
    #: 联动设置中心展示值；零标签时为新建注入值变化）
    selection_changed = Signal(str, object)

    #: 任一标签一轮对话结束（转发 ChatPanel.turn_finished；主窗口联动
    #: Git 状态去抖刷新，诊断报告 文档/修改记录/2026-0731-1256 方案 A）
    turn_finished = Signal()

    #: 正文文件路径链接点击（转发 ChatPanel.file_open_requested，载荷
    #: (绝对路径, 行号|None)；主窗口接查看器 open_file，1836 计划 L2-5）
    file_open_requested = Signal(str, object)

    #: 标签数上限（用户决策 D1：约束长驻 kimi acp 进程数与 token 消耗）
    MAX_TABS = 4

    #: 左栏最小宽度（px，2026-0806-0401 计划 D4/T4 单一来源）：
    #: 取 2026-0724-2354 计划 T6 实证链数值（默认 320 ≥ 底行双下拉 +
    #: 附件/发送按钮静态下限 315）；MainWindow 构造处 setMinimumWidth +
    #: splitter setCollapsible(0, False) 双闸，用户拖到 320 即触底
    MIN_WIDTH = 320

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
        self._tab_seq = 0  # 序号：非全关不复用（防指代漂移）；全关即重置
        self._busy_panels: set[ChatPanel] = set()
        #: 标签序号簿记（panel → 会话 N）：标题「会话 N」的序号来源；
        #: 关闭时随 panel 一并剔除
        self._tab_numbers: dict[ChatPanel, int] = {}
        self._load_injection_value()

        self._tabs = QTabWidget(self)
        # 主题接线：base.qss #ChatTabs 段（透明 tab + 激活 accent 下划线，
        # 对齐 #TerminalTabs 范式；2026-0722-1725 走查 F1）
        self._tabs.setObjectName("ChatTabs")
        self._tabs.setTabsClosable(True)
        self._tabs.setDocumentMode(True)
        # 「+」新建按钮：固定右上角（达上限禁用并提示）
        self._add_button = QToolButton(self)
        self._add_button.setText("+")
        self._add_button.setToolTip("新建 AI 会话标签")
        self._tabs.setCornerWidget(self._add_button, Qt.Corner.TopRightCorner)

        # 页 0 = 零标签占位页，页 1 = 标签区（全关后引导用户新建）
        self._stack = QStackedWidget(self)
        self._placeholder = self._build_placeholder()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._tabs)
        self._stack.setCurrentWidget(self._tabs)

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack, 1)
        # 面板级 6px 外边距体系（对齐 viewer/terminal 面板；下边距 6px 同
        # 终端面板）；顶部模型行已于 2026-0724-2354 计划移除（选择下移
        # 各标签输入区底行），上边距与左右对齐面板级 6px
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        self._add_button.clicked.connect(self.add_tab)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        # 标签切换 → 重报当前标签忙闲（busy_changed 语义 = 当前活动标签）
        # 与当前标签选择（设置中心展示值跟随）
        self._tabs.currentChanged.connect(self._on_current_changed)

        self.add_tab()  # 首标签：等价改造前的单聊天面板

    def _load_injection_value(self) -> None:
        """新建注入值装载（2026-0803-0112 计划 D1）：语义 = 「下一个新建
        标签的初始选择 + 重启默认」，来源 = 最近一次用户显式切换（任一
        入口）。启动时从 settings 读取一次（「最近使用值」持久化，一期
        语义不变）。模型记忆表（2026-0731-0052 计划 D1/D4）：启动模型取
        当前接口的记忆值，无记忆 = None（未定制，UI 落该接口模型列表
        首项）。"""
        settings = load_settings()
        self._backend: str | None = settings.get(KEY_MODEL_BACKEND)
        self._version: str | None = settings[KEY_MODEL_VERSIONS].get(
            self._backend or "")
        #: 推理强度注入值（2026-0806 计划）：当前接口的 model_efforts 记忆
        #: 值；None = 未定制（agent 默认强度生效，UI 勾选接口默认项纯呈现）
        self._effort: str | None = settings[KEY_MODEL_EFFORTS].get(
            self._backend or "")

    def _build_placeholder(self) -> QWidget:
        """零标签占位页：提示文案 + 居中「新建会话」按钮（沿用全局主题，不引新色值）。"""
        page = QWidget(self)
        hint = QLabel("暂无 AI 会话", page)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        new_button = QPushButton("新建会话", page)
        new_button.clicked.connect(self.add_tab)
        page_layout = QVBoxLayout(page)
        page_layout.addStretch(1)
        page_layout.addWidget(hint)
        page_layout.addWidget(new_button, 0, Qt.AlignmentFlag.AlignCenter)
        page_layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # 选择状态查询（设置中心展示值经此回读，2026-0803-0112 计划 T5）
    # ------------------------------------------------------------------
    def current_backend(self) -> str | None:
        """当前有效后端：当前活动标签 ModelBar 的回退后有效值；
        零标签时回退新建注入值。"""
        current = self._tabs.currentWidget()
        if isinstance(current, ChatPanel):
            return current.model_bar.current_backend()
        return self._backend

    def current_version(self) -> str | None:
        """当前有效版本（模型别名）；零标签时回退新建注入值。"""
        current = self._tabs.currentWidget()
        if isinstance(current, ChatPanel):
            return current.model_bar.current_version()
        return self._version

    # ------------------------------------------------------------------
    # 标签生命周期
    # ------------------------------------------------------------------
    def add_tab(self) -> None:
        """新建会话标签（注入「新建注入值」作初始选择）；达上限忽略
        （按钮已禁用兜底）。新建后该标签选择即独立，不再受其余标签影响。"""
        if self._tabs.count() >= self.MAX_TABS:
            return
        panel = ChatPanel(
            self._backend,
            self._version,
            self._workspace_root,
            self,
            effort=self._effort,
        )
        panel.busy_changed.connect(lambda busy, p=panel: self._on_tab_busy(p, busy))
        panel.model_bar.selection_changed.connect(
            lambda backend, version, p=panel: self._on_selection_changed(p, backend, version))
        panel.model_bar.effort_changed.connect(
            lambda backend, effort, p=panel: self._on_effort_changed(p, backend, effort))
        panel.turn_finished.connect(self.turn_finished)
        panel.file_open_requested.connect(self.file_open_requested)
        if self._backend is None:
            # 启动无持久化记录：注入值取首标签经 ModelBar 回退后的有效值
            # （不写盘——与原启动恢复「阻断信号不落盘」语义一致）
            self._backend = panel.model_bar.current_backend()
            self._version = panel.model_bar.current_version()
        self._tab_seq += 1
        self._tab_numbers[panel] = self._tab_seq
        self._tabs.addTab(panel, self._tab_title(panel))
        self._tabs.setCurrentWidget(panel)
        self._stack.setCurrentWidget(self._tabs)  # 从占位页回升标签区
        self._refresh_add_button()
        # 新建即当前：三按钮忙闲按本标签（必为空闲）刷新一次，防前标签
        # 响应中新建出的标签继承禁用态（currentChanged 槽也会重报对外）
        panel.model_bar.set_busy(panel in self._busy_panels)

    def _tab_title(self, panel: ChatPanel) -> str:
        """标签标题恒为「会话 N」（2026-08-03 用户决策）：异构后台后标题
        曾拼后台显示名（D6「会话 N · Kimi」）以辨识标签，用户认定画蛇
        添足予以去除——后台辨识归各标签底部 ModelBar，不占标签标题。"""
        number = self._tab_numbers.get(panel, 0)
        return f"会话 {number}"

    def _close_tab(self, index: int) -> None:
        """关闭标签：允许关到 0（全关重置序号、切占位页）；清理异步化。

        标签摘除与 busy 清算在 GUI 线程同步完成（毫秒级）；
        terminate/wait/deleteLater 移交 panel.close() 的 daemon 清理线程，
        worker 收尾信号在 close() GUI 段已断开，不会再触碰本容器。
        """
        panel = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if isinstance(panel, ChatPanel):
            panel.close()
            self._busy_panels.discard(panel)
            self._tab_numbers.pop(panel, None)
        if self._tabs.count() == 0:
            self._tab_seq = 0  # 全关即重置：下次新建从「会话 1」开始
            self._stack.setCurrentWidget(self._placeholder)
            # 零标签：当前活动标签概念消失，忙闲落定 False（防设置中心
            # 模型页残留禁用态）
            self.busy_changed.emit(False)
        self._refresh_add_button()

    def _refresh_add_button(self) -> None:
        """上限治理：达 4 禁用「+」并以 tooltip 提示。"""
        at_max = self._tabs.count() >= self.MAX_TABS
        self._add_button.setEnabled(not at_max)
        self._add_button.setToolTip(
            f"已达标签上限 {self.MAX_TABS}" if at_max else "新建 AI 会话标签")

    # ------------------------------------------------------------------
    # 模型选择（2026-0803-0112 计划：每标签自持，容器只留新建注入值；
    # 翻案 2026-0724-2354 计划 D5「共享同一选择 + 广播」）
    # ------------------------------------------------------------------
    def _on_selection_changed(self, sender: ChatPanel, backend: str, version: object) -> None:
        """某标签底行下拉用户切换 → 本标签 provider 切换 + 注入值更新 +
        写盘。

        不再广播其余标签——各标签选择独立（D1）。sender 必须为当前活动
        标签才更新注入值与写盘：注入值语义 = 「最近一次用户显式切换」，
        非活动标签不存在用户操作路径（ModelBar 菜单交互必使其所在标签
        成为当前），校验仅作防御。

        载荷语义（2026-0731-0052 计划 D3/D4）：version 为 str = 用户显式
        选定模型 → 写入记忆表（锁内合并，防多开覆盖）；version 为 None =
        切后台/接口未指定 → 查该接口记忆（无记忆保持 None，UI 落首项，
        不写记忆条目，保留跟随首项默认态）。
        """
        if sender is not self._tabs.currentWidget():
            return
        self._backend = backend
        settings = load_settings()
        if isinstance(version, str):
            self._version = version
            remember_model_version(backend, version)
        else:
            self._version = settings[KEY_MODEL_VERSIONS].get(backend)
        update_settings({KEY_MODEL_BACKEND: backend})
        # 本标签 provider 切换（原经广播路径完成；set_model_selection 内
        # ModelBar.set_selection 全程阻断信号，不回环、不重复写盘）
        sender.set_model_selection(backend, self._version)
        # 推理强度（2026-0806 计划）：切后台/接口/模型后按（新）接口的
        # 记忆表解析并应用——None = 未定制，agent 默认强度生效；同接口
        # 内换模型时记忆值不变，重应用幂等
        self._effort = settings[KEY_MODEL_EFFORTS].get(backend)
        sender.set_effort(self._effort)
        # 设置中心展示值跟随当前标签（载荷用 ModelBar 回退后有效值，
        # 与 UI 一致；version 为 None 时 ModelBar 已落记忆/首项）
        self.selection_changed.emit(
            sender.model_bar.current_backend(),
            sender.model_bar.current_version())

    def _on_effort_changed(self, sender: ChatPanel, backend: str, effort: str) -> None:
        """某标签底行第四级用户显式选定强度（2026-0806 计划）→ 注入值
        更新 + 记忆表写盘 + 本标签 provider 即时生效。

        不广播其余标签（各标签选择独立，D1）；sender 必须为当前活动标签
        （与 _on_selection_changed 同款防御）。强度记忆表与模型记忆表
        同构（model_efforts，锁内合并防多开覆盖）。
        """
        if sender is not self._tabs.currentWidget():
            return
        self._effort = effort
        remember_model_effort(backend, effort)
        sender.set_effort(effort)

    def apply_model_selection(self, backend: str, version: str | None) -> None:
        """菜单/设置中心驱动切换：注入值更新 + 写盘 + 只作用当前活动标签。

        载荷语义同 _on_selection_changed（str 写记忆 / None 查记忆）。
        零标签时只更新注入值与写盘，新建标签时注入生效（等价改造前
        「广播空列表 no-op」语义）；发送中（busy）由菜单/设置中心侧禁用
        入口。其余标签不动——它们已是用户显式选择的独立会话（D3）。
        """
        self._backend = backend
        settings = load_settings()
        if isinstance(version, str):
            self._version = version
            remember_model_version(backend, version)
        else:
            self._version = settings[KEY_MODEL_VERSIONS].get(backend)
        update_settings({KEY_MODEL_BACKEND: backend})
        # 推理强度注入值随接口记忆表解析（2026-0806 计划，与 _on_selection_changed 同款）
        self._effort = settings[KEY_MODEL_EFFORTS].get(backend)
        current = self._tabs.currentWidget()
        if not isinstance(current, ChatPanel):
            # 零标签：设置中心展示值 = 注入值
            self.selection_changed.emit(self._backend, self._version)
            return
        # 传解析后的注入值（version=None 已查记忆表），与改造前广播路径
        # 语义一致；set_model_selection 内 UI 阻断同步不回环
        current.set_model_selection(backend, self._version)
        current.set_effort(self._effort)  # 强度应用（None = 未定制）
        # 回退后的有效值以 ModelBar 为准（失效 backend 被静默回退时，
        # 设置中心展示真实落点而非请求值）
        self.selection_changed.emit(
            current.model_bar.current_backend(),
            current.model_bar.current_version())

    # ------------------------------------------------------------------
    # busy 粒度（2026-0803-0112 计划 D4：各标签独立禁用；busy_changed
    # 改报「当前活动标签忙闲」；停止归各标签输入区双态按钮）
    # ------------------------------------------------------------------
    def _on_tab_busy(self, panel: ChatPanel, is_busy: bool) -> None:
        """单标签忙碌态变化 → 只禁用本标签三按钮；若本标签为当前活动
        标签则同步对外重报（设置中心模型页禁用跟随当前标签）。"""
        if is_busy:
            self._busy_panels.add(panel)
        else:
            self._busy_panels.discard(panel)
        panel.model_bar.set_busy(is_busy)
        if panel is self._tabs.currentWidget():
            self.busy_changed.emit(is_busy)

    def _on_current_changed(self, index: int) -> None:
        """标签切换 → 重报当前标签忙闲与当前标签选择（设置中心模型页
        禁用态与展示值跟随当前活动标签，2026-0803-0112 计划 T3/T5）。"""
        del index  # 以 currentWidget 为准（-1 零标签路径统一）
        current = self._tabs.currentWidget()
        if isinstance(current, ChatPanel):
            self.busy_changed.emit(current in self._busy_panels)
            self.selection_changed.emit(
                current.model_bar.current_backend(),
                current.model_bar.current_version())
        else:
            self.busy_changed.emit(False)
            self.selection_changed.emit(self._backend, self._version)

    def is_busy(self) -> bool:
        """当前活动标签响应中（设置中心模型页禁用依据，2026-0803-0112
        计划 D4：原「任一标签响应中」随广播废除失去依据）。"""
        current = self._tabs.currentWidget()
        return isinstance(current, ChatPanel) and current in self._busy_panels

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

    def save_state(self) -> str | None:
        """当前活动标签的分隔栏状态（多标签共用同一持久化键）。

        零标签（全关后）返回 None：WindowStateStore.save 跳过该键、保留
        文件中的旧值——回写 "" 会把用户调好的分隔比例静默洗成默认。
        """
        current = self._tabs.currentWidget()
        return current.save_state() if isinstance(current, ChatPanel) else None

    def restore_state(self, state: str | None) -> None:
        """恢复分隔栏到全部标签（启动时通常仅首标签）。"""
        for panel in self._panels():
            panel.restore_state(state)

    def reset_layout(self) -> None:
        """恢复默认布局：全部标签的输出/输入区回初始尺寸。"""
        for panel in self._panels():
            panel.reset_layout()
