"""终端面板装配：头部栏（tab 区 + 操作组）+ TerminalWidget + 多 PTY 会话栈。

接线职责：session 字节流 → screen 喂入 → widget 刷新（层间单向依赖的唯一交汇点）。
阶段二（见 文档/修改记录/2026-0719-0955_终端面板多会话tab区计划_阶段二.md）：
多会话 tab 区（每会话一套 PtySession+TerminalScreen，widget 重绑定切换）、
序号标题（终端N，递增不复用）、右键菜单功能分层、查找浮层（当前屏搜索，不占布局）。
"""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from gui.panels.find_bar import FindBar
from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.session import PROJECT_ROOT, PtySession
from gui.panels.terminal.widget import TerminalWidget
from gui.popups import make_translucent_popup
from gui.settings import KEY_THEME
from gui.theme import load_settings, get_theme_palette


@dataclass
class _Session:
    """单会话值对象：一套 PTY 会话 + 屏幕模型 + tab 标题 + 退出码。"""
    session: PtySession
    screen: TerminalScreen
    title: str
    exit_code: int | None = None


class TerminalPanel(QWidget):
    """中栏下终端面板（真 PTY 多会话 tab：spawn $SHELL，cwd=项目根）。"""

    #: 面板最小高度（px）：头部栏约 28px + 约 5 行终端文本，
    #: 配合主窗口 middle_splitter.setCollapsible(1, False) 生效
    MIN_HEIGHT = 140

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        # ANSI 配色取自主题调色板（资源包下沉，每主题自带全套）
        self._palette = AnsiPalette(get_theme_palette(load_settings()[KEY_THEME])["terminal"])
        self._sessions: list[_Session] = []
        self._serial = 0  # tab 序号计数（只增不复用：关闭「终端2」后再新建得「终端3」；全关后归零重计）
        self._find_matches: list[tuple[int, int, int]] = []  # 查找命中段缓存
        self._cwd = str(PROJECT_ROOT)  # 新会话工作目录（工作区切换经 set_cwd 更新；已存在会话不动）

        self._build_header()
        self._build_terminal_area()
        self._build_find_bar()
        self._connect_signals()

        # 首次启动延迟到拿到真实网格尺寸：构造时控件尚未布局（高≈0 → 网格仅 1 行），
        # 立即 spawn 会让 bash 首屏输出被 pyte resize 的 xterm 沉底语义固定到末行
        self._has_pending_start = True
        self.terminal.installEventFilter(self)

    # ------------------------------------------------------------------
    # UI 构建（仅装配；跨组件信号统一在 _connect_signals 接线）
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        """头部栏：tab 区（左，可滚动）+ 固定操作组（右），单行高度锁定。"""
        self._tab_bar = QTabBar(self)
        self._tab_bar.setObjectName("TerminalTabs")
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setExpanding(False)       # tab 不拉伸，左侧自然排列
        self._tab_bar.setUsesScrollButtons(True)  # 溢出滚动箭头兜底
        self._tab_bar.setDrawBase(False)         # 不画基线，融入头部栏

        self._btn_new = QPushButton("+", self)
        self._btn_new.setFixedSize(28, 22)
        # 加粗加大：继承应用全局字体（思源黑体），字重 Bold、字号 +3pt
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 3)
        self._btn_new.setFont(font)
        self._btn_new.setToolTip("新建终端")

        self._status = QLabel("", self)
        self._status.setObjectName("PanelHint")

        self._btn_clear = QPushButton("清屏", self)
        self._btn_clear.setFixedHeight(22)
        self._btn_clear.setToolTip("清屏（Ctrl+L）")
        self._btn_clear.setEnabled(False)

        # 单行头部栏，高度锁定（随字号动态计算），杜绝"标题行膨胀"复发
        self._header = QWidget(self)
        self._header.setObjectName("TerminalHeader")
        row = QHBoxLayout(self._header)
        row.addWidget(self._tab_bar, 1)
        row.addWidget(self._status)
        row.addWidget(self._btn_clear)
        row.addWidget(self._btn_new)  # 最右端：原「−」隐藏按钮位置
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
        self._lock_header_height()

    def _build_terminal_area(self) -> None:
        """终端区：PanelCard 圆角卡片包裹头部栏 + TerminalWidget（卡片统一描边）。

        TerminalWidget 为 paintEvent 自绘背景，qss border-radius 对其无效，
        故以 QFrame 容器承载圆角：终端矩形缩进卡片内边距内，四角由卡片背景兜住。
        """
        self.terminal = TerminalWidget(self._palette, self)
        self.terminal.set_placeholder("没有终端，点击 + 创建")

        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(self._header)
        # stretch=1：多余高度全给终端区（双保险，配合头部栏固定高度）
        card_layout.addWidget(self.terminal, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）；
        # 下边距 6px + 状态栏定高 26px = 底部总间距 32px（一体化设计）
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(0)

    def _build_find_bar(self) -> None:
        """装配共用 FindBar 组件（外观/定位/按键自闭环），搜索语义接本面板。"""
        self._find_bar = FindBar(self.terminal, "查找（当前屏）")
        self._find_bar.input.textChanged.connect(self._update_search)
        self._find_bar.step_requested.connect(self._find_step)
        self._find_bar.close_requested.connect(self._hide_find)

    def _connect_signals(self) -> None:
        """跨组件信号统一接线（本面板的接线图）。"""
        self._tab_bar.currentChanged.connect(self._switch_tab)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)
        self._btn_new.clicked.connect(lambda: self._spawn())
        self._btn_clear.clicked.connect(self._on_clear)
        # widget 只发原始事件，会话决策全在本层（单向依赖）
        self.terminal.context_menu_requested.connect(self._on_context_menu)
        self.terminal.find_requested.connect(self._show_find)

    # ------------------------------------------------------------------
    # 事件过滤：终端区 resize（首启/浮层重定位）+ 查找框按键
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.terminal and event.type() == QEvent.Type.Resize:
            # TerminalWidget 首次获得有效尺寸（≥2 行）时启动首个会话
            if self._has_pending_start and self.terminal.get_grid_size()[0] >= 2:
                self._has_pending_start = False
                # 延迟一轮事件循环：合并窗口管理器紧随其后的二次 resize
                QTimer.singleShot(0, self._spawn)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # 会话栈
    # ------------------------------------------------------------------
    def _current(self) -> _Session | None:
        idx = self._tab_bar.currentIndex()
        return self._sessions[idx] if 0 <= idx < len(self._sessions) else None

    def _spawn(self) -> None:
        """新建会话：以当前网格尺寸 spawn（此刻控件尺寸已稳定），入栈并切为新 tab。"""
        row_count, column_count = self.terminal.get_grid_size()
        session = PtySession(self)
        screen = TerminalScreen(column_count, row_count)
        self._serial += 1
        session_entry = _Session(session=session, screen=screen, title=f"终端{self._serial}")
        # 闭包捕获 session_entry：多会话数据各自进各自 screen（不错绑）
        session.data_received.connect(
            lambda data, session_entry=session_entry: self._on_data(session_entry, data))
        session.process_exited.connect(
            lambda return_code, session_entry=session_entry: self._on_exited(session_entry, return_code))
        session.start(column_count, row_count, cwd=self._cwd)
        self._sessions.append(session_entry)
        idx = self._tab_bar.addTab(session_entry.title)
        self._tab_bar.setCurrentIndex(idx)  # 触发 _switch_tab 完成绑定

    def _switch_tab(self, idx: int) -> None:
        """切换活动会话：widget 重绑定 + 尺寸补同步 + 状态/按钮刷新。"""
        if 0 <= idx < len(self._sessions):
            session_entry = self._sessions[idx]
            self.terminal.set_screen(session_entry.screen)
            self.terminal.set_session(session_entry.session)
            # 切回时按当前网格补 resize（后台期间面板尺寸可能变过；resize 只作用活动会话）
            row_count, column_count = self.terminal.get_grid_size()
            if session_entry.screen.line_count != row_count or session_entry.screen.column_count != column_count:
                session_entry.screen.resize(row_count, column_count)
                session_entry.session.resize(row_count, column_count)
        else:
            # 空状态：无会话（保留面板，终端区显示占位引导）
            self.terminal.set_screen(None)
            self.terminal.set_session(None)
        self._clear_search()  # 高亮绑定的是旧屏幕，切换即失效
        self._refresh_status()

    def _close_tab(self, idx: int) -> None:
        """关闭会话：进程回收（幂等）+ 断信号 + 出栈；全关后进入空状态。"""
        if not (0 <= idx < len(self._sessions)):
            return
        session_entry = self._sessions.pop(idx)
        session_entry.session.data_received.disconnect()
        session_entry.session.process_exited.disconnect()
        session_entry.session.terminate()
        self._tab_bar.removeTab(idx)  # currentChanged 自然触发 _switch_tab（索引已对齐）
        if not self._sessions:
            self._serial = 0  # 全关归零：下一个新建重新从「终端1」开始
            self._switch_tab(-1)

    def _restart_active(self) -> None:
        """重开当前会话（右键菜单；无会话时等价新建）。"""
        session_entry = self._current()
        if session_entry is None:
            self._spawn()
            return
        row_count, column_count = self.terminal.get_grid_size()
        session_entry.screen = TerminalScreen(column_count, row_count)
        self.terminal.set_screen(session_entry.screen)
        session_entry.session.start(column_count, row_count)  # PtySession.start 内部幂等 terminate 旧进程
        session_entry.exit_code = None
        self._refresh_status()

    def _kill_active(self) -> None:
        """终止当前会话（右键菜单）；退出码经 process_exited 回报。"""
        if (session_entry := self._current()) is not None:
            session_entry.session.terminate()

    def _on_clear(self) -> None:
        """清屏：写 Ctrl+L，由 shell/readline 自清并把提示符重绘到顶行（真实终端语义）。"""
        if (session_entry := self._current()) is not None and session_entry.session.is_alive():
            session_entry.session.write(b"\x0c")

    # ------------------------------------------------------------------
    # 会话事件（信号闭包绑定各自 _Session）
    # ------------------------------------------------------------------
    def _on_data(self, session_entry: _Session, data: bytes) -> None:
        session_entry.screen.feed(data)
        if session_entry is self._current():
            self.terminal.notify_data()

    def _on_exited(self, session_entry: _Session, return_code: int) -> None:
        session_entry.exit_code = return_code
        if session_entry is self._current():
            self._refresh_status()

    def _refresh_status(self) -> None:
        """状态行与清屏按钮可用态跟随活动会话。"""
        session_entry = self._current()
        if session_entry is None:
            self._status.setText("")
            self._btn_clear.setEnabled(False)
        elif session_entry.exit_code is not None:
            self._status.setText(f"[进程已退出 code {session_entry.exit_code}]")
            self._btn_clear.setEnabled(False)
        else:
            self._status.setText("")
            self._btn_clear.setEnabled(session_entry.session.is_alive())

    def count_tabs(self) -> int:
        return self._tab_bar.count()

    # ------------------------------------------------------------------
    # 公开接口（终端菜单/编辑菜单调用；与头部按钮、右键菜单同一实现路径）
    # ------------------------------------------------------------------
    def new_session(self) -> None:
        """新建终端会话（同头部「＋」）。"""
        self._spawn()

    def clear_active(self) -> None:
        """清屏当前会话（写 Ctrl+L，shell 自清）。"""
        self._on_clear()

    def restart_active(self) -> None:
        """重开当前会话（无会话时等价新建；保持该会话原工作目录）。"""
        self._restart_active()

    def kill_active(self) -> None:
        """终止当前会话。"""
        self._kill_active()

    def has_alive_session(self) -> bool:
        """活动会话存在且进程存活（菜单启用态依据）。"""
        session_entry = self._current()
        return session_entry is not None and session_entry.session.is_alive()

    def set_cwd(self, cwd: str) -> None:
        """设置新会话工作目录（工作区切换）；已存在会话不受影响。"""
        self._cwd = cwd

    def show_find(self) -> None:
        """打开查找浮层（编辑菜单「查找」焦点分发入口）。"""
        self._show_find()

    def refresh_font(self) -> None:
        """全局字号调整：终端字体重建 + 头部栏高度重算。

        活动会话经 TerminalWidget.refresh_font 内的网格重算同步 resize；
        后台会话在 _switch_tab 切回时按既有逻辑补 resize。
        """
        self.terminal.refresh_font()
        self._lock_header_height()

    # ------------------------------------------------------------------
    # 右键菜单（功能分层：复制/粘贴 → 清屏/重开/终止/关闭此终端）
    # ------------------------------------------------------------------
    def _on_context_menu(self, global_pos) -> None:
        session_entry = self._current()
        has_alive = session_entry is not None and session_entry.session.is_alive()
        menu = make_translucent_popup(QMenu(self))
        # 剪贴板层：复制执行在 widget（自治），此处仅按状态启停
        action_copy = menu.addAction("复制")
        action_copy.setEnabled(self.terminal.has_selection())
        action_copy.triggered.connect(self.terminal.copy_selection)
        action_paste = menu.addAction("粘贴")
        action_paste.setEnabled(has_alive and bool(QGuiApplication.clipboard().text()))
        action_paste.triggered.connect(self.terminal.paste_clipboard)
        menu.addSeparator()
        action_clear = menu.addAction("清屏")
        action_clear.setEnabled(has_alive)
        action_clear.triggered.connect(self._on_clear)
        action_restart = menu.addAction("重开")
        action_restart.triggered.connect(self._restart_active)
        menu.addSeparator()
        action_kill = menu.addAction("终止")
        action_kill.setEnabled(has_alive)
        action_kill.triggered.connect(self._kill_active)
        action_close = menu.addAction("关闭此终端")
        action_close.setEnabled(session_entry is not None)
        action_close.triggered.connect(lambda: self._close_tab(self._tab_bar.currentIndex()))
        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # 查找浮层（最小版：当前屏搜索 + 命中高亮 + 上一个/下一个）
    # ------------------------------------------------------------------
    def _show_find(self) -> None:
        self._find_bar.show_and_focus()
        self._update_search()

    def _hide_find(self) -> None:
        self._find_bar.setVisible(False)
        self._clear_search()
        self.terminal.setFocus()

    def _update_search(self) -> None:
        """在当前活动屏快照中收集全部命中段并高亮首个。"""
        text = self._find_bar.input.text()
        session_entry = self._current()
        runs: list[tuple[int, int, int]] = []
        if text and session_entry is not None:
            for y, row in enumerate(session_entry.screen.snapshot()):
                line = "".join(ch for ch, _ in row)
                start = 0
                while (x := line.find(text, start)) >= 0:
                    runs.append((y, x, x + len(text)))
                    start = x + 1  # 步进 1：允许重叠命中
        self._find_matches = runs
        self.terminal.set_search_highlight(runs, 0 if runs else -1)

    def _find_step(self, delta: int) -> None:
        if self._find_matches:
            current = self.terminal.current_match + delta
            self.terminal.set_search_highlight(
                self._find_matches, current % len(self._find_matches))

    def _clear_search(self) -> None:
        self._find_matches = []
        self.terminal.set_search_highlight([], -1)

    # ------------------------------------------------------------------
    # 头部栏与主题
    # ------------------------------------------------------------------
    def _lock_header_height(self) -> None:
        """锁定头部栏高度：按钮内容高 + 上下边距（随字号动态计算，防文本截断）。"""
        margins = self._header.layout().contentsMargins()
        self._header.setFixedHeight(
            self._btn_clear.sizeHint().height() + margins.top() + margins.bottom())

    def apply_theme(self, theme: str) -> None:
        """切换主题：色板换新 + 全量重绘（入参为主题名）。"""
        self._palette = AnsiPalette(get_theme_palette(theme)["terminal"])
        self.terminal.apply_palette(self._palette)
        self._lock_header_height()  # 主题切换可能带来字号变化，头部栏高度重算
