"""终端面板装配：头部栏（tab 区 + 操作组）+ TerminalWidget + 多 PTY 会话栈。

接线职责：session 字节流 → screen 喂入 → widget 刷新（层间单向依赖的唯一交汇点）。
阶段二（见 work plans/2026-0719-0955_终端面板多会话tab区计划_阶段二.md）：
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
    QLineEdit,
    QMenu,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.session import PtySession
from gui.panels.terminal.widget import TerminalWidget
from gui.theme import get_family, load_settings


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
        # 调色板只认明暗两族（light/dark），当前主题名先转族名
        self._palette = AnsiPalette(get_family(load_settings()["theme"]))
        self._sessions: list[_Session] = []
        self._serial = 0  # tab 序号计数（只增不复用：关闭「终端2」后再新建得「终端3」）
        self._find_matches: list[tuple[int, int, int]] = []  # 查找命中段缓存

        # ---- 头部栏：tab 区（左，可滚动）+ 固定操作组（右） ----
        self._tab_bar = QTabBar(self)
        self._tab_bar.setObjectName("TerminalTabs")
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setExpanding(False)       # tab 不拉伸，左侧自然排列
        self._tab_bar.setUsesScrollButtons(True)  # 溢出滚动箭头兜底
        self._tab_bar.setDrawBase(False)         # 不画基线，融入头部栏
        self._tab_bar.currentChanged.connect(self._switch_tab)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)

        self._btn_new = QPushButton("+", self)
        self._btn_new.setFixedSize(28, 22)
        # 加粗加大：继承应用全局字体（思源黑体），字重 Bold、字号 +3pt
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 3)
        self._btn_new.setFont(font)
        self._btn_new.setToolTip("新建终端")
        self._btn_new.clicked.connect(lambda: self._spawn())

        self._status = QLabel("", self)
        self._status.setObjectName("PanelHint")

        self._btn_clear = QPushButton("清屏", self)
        self._btn_clear.setFixedHeight(22)
        self._btn_clear.setToolTip("清屏（Ctrl+L）")
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._on_clear)

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

        self.terminal = TerminalWidget(self._palette, self)
        self.terminal.set_placeholder("没有终端，点击 + 创建")

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        # stretch=1：多余高度全给终端区（双保险，配合头部栏固定高度）
        layout.addWidget(self.terminal, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # widget 只发原始事件，会话决策全在本层（单向依赖）
        self.terminal.context_menu_requested.connect(self._on_context_menu)
        self.terminal.find_requested.connect(self._show_find)

        # ---- 查找浮层：终端区右上角悬浮（不占布局，对齐 Theia 范式） ----
        self._find_bar = QFrame(self.terminal)
        self._find_bar.setObjectName("TerminalFindBar")
        find_row = QHBoxLayout(self._find_bar)
        find_row.setContentsMargins(6, 3, 6, 3)
        find_row.setSpacing(4)
        self._find_input = QLineEdit(self._find_bar)
        self._find_input.setPlaceholderText("查找（当前屏）")
        self._find_input.setFixedWidth(180)
        self._find_input.textChanged.connect(self._update_search)
        self._find_input.installEventFilter(self)  # Enter=下一个 / Esc=关闭
        btn_prev = QPushButton("↑", self._find_bar)
        btn_next = QPushButton("↓", self._find_bar)
        btn_close = QPushButton("×", self._find_bar)
        for b in (btn_prev, btn_next, btn_close):
            b.setFixedSize(24, 22)
        btn_prev.setToolTip("上一个")
        btn_next.setToolTip("下一个")
        btn_prev.clicked.connect(lambda: self._find_step(-1))
        btn_next.clicked.connect(lambda: self._find_step(1))
        btn_close.clicked.connect(self._hide_find)
        find_row.addWidget(self._find_input)
        find_row.addWidget(btn_prev)
        find_row.addWidget(btn_next)
        find_row.addWidget(btn_close)
        self._find_bar.setVisible(False)

        # 首次启动延迟到拿到真实网格尺寸：构造时控件尚未布局（高≈0 → 网格仅 1 行），
        # 立即 spawn 会让 bash 首屏输出被 pyte resize 的 xterm 沉底语义固定到末行
        self._pending_start = True
        self.terminal.installEventFilter(self)

    # ------------------------------------------------------------------
    # 事件过滤：终端区 resize（首启/浮层重定位）+ 查找框按键
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.terminal:
            if event.type() == QEvent.Type.Resize:
                # TerminalWidget 首次获得有效尺寸（≥2 行）时启动首个会话
                if self._pending_start and self.terminal.grid_size()[0] >= 2:
                    self._pending_start = False
                    # 延迟一轮事件循环：合并窗口管理器紧随其后的二次 resize
                    QTimer.singleShot(0, self._spawn)
                if self._find_bar.isVisible():
                    self._place_find_bar()
        elif watched is self._find_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._hide_find()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._find_step(1)
                return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # 会话栈
    # ------------------------------------------------------------------
    def _current(self) -> _Session | None:
        idx = self._tab_bar.currentIndex()
        return self._sessions[idx] if 0 <= idx < len(self._sessions) else None

    def _spawn(self) -> None:
        """新建会话：以当前网格尺寸 spawn（此刻控件尺寸已稳定），入栈并切为新 tab。"""
        rows, cols = self.terminal.grid_size()
        sess = PtySession(self)
        screen = TerminalScreen(cols, rows)
        self._serial += 1
        entry = _Session(session=sess, screen=screen, title=f"终端{self._serial}")
        # 闭包捕获 entry：多会话数据各自进各自 screen（不错绑）
        sess.data_received.connect(lambda data, e=entry: self._on_data(e, data))
        sess.process_exited.connect(lambda rc, e=entry: self._on_exited(e, rc))
        sess.start(cols, rows)
        self._sessions.append(entry)
        idx = self._tab_bar.addTab(entry.title)
        self._tab_bar.setCurrentIndex(idx)  # 触发 _switch_tab 完成绑定

    def _switch_tab(self, idx: int) -> None:
        """切换活动会话：widget 重绑定 + 尺寸补同步 + 状态/按钮刷新。"""
        if 0 <= idx < len(self._sessions):
            entry = self._sessions[idx]
            self.terminal.set_screen(entry.screen)
            self.terminal.set_session(entry.session)
            # 切回时按当前网格补 resize（后台期间面板尺寸可能变过；resize 只作用活动会话）
            rows, cols = self.terminal.grid_size()
            if entry.screen.lines != rows or entry.screen.columns != cols:
                entry.screen.resize(rows, cols)
                entry.session.resize(rows, cols)
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
        entry = self._sessions.pop(idx)
        entry.session.data_received.disconnect()
        entry.session.process_exited.disconnect()
        entry.session.terminate()
        self._tab_bar.removeTab(idx)  # currentChanged 自然触发 _switch_tab（索引已对齐）
        if not self._sessions:
            self._switch_tab(-1)

    def _restart_active(self) -> None:
        """重开当前会话（右键菜单；无会话时等价新建）。"""
        entry = self._current()
        if entry is None:
            self._spawn()
            return
        rows, cols = self.terminal.grid_size()
        entry.screen = TerminalScreen(cols, rows)
        self.terminal.set_screen(entry.screen)
        entry.session.start(cols, rows)  # PtySession.start 内部幂等 terminate 旧进程
        entry.exit_code = None
        self._refresh_status()

    def _kill_active(self) -> None:
        """终止当前会话（右键菜单）；退出码经 process_exited 回报。"""
        if (entry := self._current()) is not None:
            entry.session.terminate()

    def _on_clear(self) -> None:
        """清屏：写 Ctrl+L，由 shell/readline 自清并把提示符重绘到顶行（真实终端语义）。"""
        if (entry := self._current()) is not None and entry.session.is_alive():
            entry.session.write(b"\x0c")

    # ------------------------------------------------------------------
    # 会话事件（信号闭包绑定各自 _Session）
    # ------------------------------------------------------------------
    def _on_data(self, entry: _Session, data: bytes) -> None:
        entry.screen.feed(data)
        if entry is self._current():
            self.terminal.notify_data()

    def _on_exited(self, entry: _Session, rc: int) -> None:
        entry.exit_code = rc
        if entry is self._current():
            self._refresh_status()

    def _refresh_status(self) -> None:
        """状态行与清屏按钮可用态跟随活动会话。"""
        entry = self._current()
        if entry is None:
            self._status.setText("")
            self._btn_clear.setEnabled(False)
        elif entry.exit_code is not None:
            self._status.setText(f"[进程已退出 code {entry.exit_code}]")
            self._btn_clear.setEnabled(False)
        else:
            self._status.setText("")
            self._btn_clear.setEnabled(entry.session.is_alive())

    def tab_count(self) -> int:
        return self._tab_bar.count()

    # ------------------------------------------------------------------
    # 右键菜单（功能分层：复制/粘贴 → 清屏/重开/终止/关闭此终端）
    # ------------------------------------------------------------------
    def _on_context_menu(self, global_pos) -> None:
        entry = self._current()
        alive = entry is not None and entry.session.is_alive()
        menu = QMenu(self)
        # 剪贴板层：复制执行在 widget（自治），此处仅按状态启停
        act_copy = menu.addAction("复制")
        act_copy.setEnabled(self.terminal.has_selection())
        act_copy.triggered.connect(self.terminal.copy_selection)
        act_paste = menu.addAction("粘贴")
        act_paste.setEnabled(alive and bool(QGuiApplication.clipboard().text()))
        act_paste.triggered.connect(self.terminal.paste_clipboard)
        menu.addSeparator()
        act_clear = menu.addAction("清屏")
        act_clear.setEnabled(alive)
        act_clear.triggered.connect(self._on_clear)
        act_restart = menu.addAction("重开")
        act_restart.triggered.connect(self._restart_active)
        menu.addSeparator()
        act_kill = menu.addAction("终止")
        act_kill.setEnabled(alive)
        act_kill.triggered.connect(self._kill_active)
        act_close = menu.addAction("关闭此终端")
        act_close.setEnabled(entry is not None)
        act_close.triggered.connect(lambda: self._close_tab(self._tab_bar.currentIndex()))
        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # 查找浮层（最小版：当前屏搜索 + 命中高亮 + 上一个/下一个）
    # ------------------------------------------------------------------
    def _show_find(self) -> None:
        self._place_find_bar()
        self._find_bar.setVisible(True)
        self._find_bar.raise_()
        self._find_input.setFocus()
        self._find_input.selectAll()
        self._update_search()

    def _hide_find(self) -> None:
        self._find_bar.setVisible(False)
        self._clear_search()
        self.terminal.setFocus()

    def _place_find_bar(self) -> None:
        """浮层定位于终端区右上角（子控件坐标系，随终端 resize 重定位）。"""
        self._find_bar.adjustSize()
        x = max(0, self.terminal.width() - self._find_bar.width() - 16)
        self._find_bar.move(x, 6)

    def _update_search(self) -> None:
        """在当前活动屏快照中收集全部命中段并高亮首个。"""
        text = self._find_input.text()
        entry = self._current()
        runs: list[tuple[int, int, int]] = []
        if text and entry is not None:
            for y, row in enumerate(entry.screen.snapshot()):
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

    def apply_theme(self, family: str) -> None:
        """切换配色族：色板换新 + 全量重绘（入参为族名 light/dark）。"""
        self._palette = AnsiPalette(family)
        self.terminal.apply_palette(self._palette)
        self._lock_header_height()  # 主题切换可能带来字号变化，头部栏高度重算
