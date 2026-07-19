"""终端面板装配：头部栏（shell + 状态 + 清屏/重开/隐藏）+ TerminalWidget + PtySession 生命周期。

接线职责：session 字节流 → screen 喂入 → widget 刷新（层间单向依赖的唯一交汇点）。
头部栏为正式单行设计（见 work plans/2026-0719-0955_终端面板单行头部栏重构计划_阶段一.md）：
固定高度防膨胀；清屏走 Ctrl+L 语义（shell 自清，提示符重绘到顶行）；
隐藏经 hide_requested 信号交由主窗口接线（视图菜单可恢复）。
"""
import os

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.session import PtySession
from gui.panels.terminal.widget import TerminalWidget
from gui.theme import get_family, load_settings


class TerminalPanel(QWidget):
    """中栏下终端面板（真 PTY 单实例：spawn $SHELL，cwd=项目根）。"""

    #: 面板最小高度（px）：头部栏约 28px + 约 5 行终端文本，
    #: 配合主窗口 middle_splitter.setCollapsible(1, False) 生效
    MIN_HEIGHT = 140

    #: 请求隐藏整个面板（头部栏「−」按钮 → 主窗口接线，视图菜单可恢复）
    hide_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        # 调色板只认明暗两族（light/dark），当前主题名先转族名
        self._palette = AnsiPalette(get_family(load_settings()["theme"]))
        self._screen = TerminalScreen()
        self._session = PtySession(self)

        self._title = QLabel(self._shell_name(), self)
        self._title.setObjectName("PanelTitle")  # 样式由主题 qss 统一
        self._status = QLabel("", self)
        self._status.setObjectName("PanelHint")
        self._btn_clear = QPushButton("清屏", self)
        self._btn_clear.setFixedHeight(22)
        self._btn_clear.setToolTip("清屏（Ctrl+L）")
        self._btn_clear.clicked.connect(self._on_clear)
        self._restart = QPushButton("重开", self)
        self._restart.setFixedHeight(22)
        self._restart.clicked.connect(self._start_session)
        self._btn_hide = QPushButton("−", self)
        self._btn_hide.setFixedSize(28, 22)
        self._btn_hide.setToolTip("隐藏终端面板（视图菜单可恢复）")
        self._btn_hide.clicked.connect(self.hide_requested.emit)

        # 单行头部栏：左标识（shell 名 + 状态），右操作组（清屏/重开/隐藏）；
        # 高度锁定（随字号动态计算），杜绝"标题行膨胀抢终端空间"复发
        self._header = QWidget(self)
        self._header.setObjectName("TerminalHeader")  # qss 定制钩子（本期沿用通用规则）
        row = QHBoxLayout(self._header)
        row.addWidget(self._title, 1)
        row.addWidget(self._status)
        row.addWidget(self._btn_clear)
        row.addWidget(self._restart)
        row.addWidget(self._btn_hide)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
        self._lock_header_height()

        self.terminal = TerminalWidget(self._palette, self)
        self.terminal.set_screen(self._screen)
        self.terminal.set_session(self._session)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        # stretch=1：多余高度全给终端区（双保险，配合头部栏固定高度）
        layout.addWidget(self.terminal, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._session.data_received.connect(self._on_data)
        self._session.process_exited.connect(self._on_exited)

        # 首次启动延迟到拿到真实网格尺寸：构造时控件尚未布局（高≈0 → 网格仅 1 行），
        # 立即 spawn 会让 bash 首屏输出被 pyte resize 的 xterm 沉底语义固定到末行
        # （表现为大片空行 + 提示符贴底）。等 TerminalWidget 首次有效 resize 再开会话。
        self._pending_start = True
        self.terminal.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """TerminalWidget 首次获得有效尺寸（≥2 行）时启动首个会话。"""
        if (watched is self.terminal and self._pending_start
                and event.type() == QEvent.Type.Resize
                and self.terminal.grid_size()[0] >= 2):
            self._pending_start = False
            # 延迟一轮事件循环：合并窗口管理器紧随其后的二次 resize
            QTimer.singleShot(0, self._start_session)
        return super().eventFilter(watched, event)

    @staticmethod
    def _shell_name() -> str:
        return os.path.basename(os.environ.get("SHELL", "/bin/bash"))

    def _lock_header_height(self) -> None:
        """锁定头部栏高度：按钮内容高 + 上下边距（随字号动态计算，防文本截断）。"""
        margins = self._header.layout().contentsMargins()
        self._header.setFixedHeight(
            self._btn_clear.sizeHint().height() + margins.top() + margins.bottom())

    def _start_session(self) -> None:
        """（重）开会话：重建屏幕模型 + spawn 新进程。"""
        rows, cols = self.terminal.grid_size()
        self._screen = TerminalScreen(cols, rows)
        self.terminal.set_screen(self._screen)
        self._status.setText("")
        self._btn_clear.setEnabled(True)
        self._session.start(cols, rows)

    def _on_clear(self) -> None:
        """清屏：写 Ctrl+L，由 shell/readline 自清并把提示符重绘到顶行（真实终端语义）。"""
        if self._session.is_alive():
            self._session.write(b"\x0c")

    def _on_data(self, data: bytes) -> None:
        self._screen.feed(data)
        self.terminal.notify_data()

    def _on_exited(self, rc: int) -> None:
        self._status.setText(f"[进程已退出 code {rc}]")
        self._btn_clear.setEnabled(False)  # 会话已死，清屏无意义（重开按钮常驻可用）

    def apply_theme(self, family: str) -> None:
        """切换配色族：色板换新 + 全量重绘（入参为族名 light/dark）。"""
        self._palette = AnsiPalette(family)
        self.terminal.apply_palette(self._palette)
        self._lock_header_height()  # 主题切换可能带来字号变化，头部栏高度重算
