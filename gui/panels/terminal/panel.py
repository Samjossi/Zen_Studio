"""终端面板装配：标题行（shell + 状态 + 重开）+ TerminalWidget + PtySession 生命周期。

接线职责：session 字节流 → screen 喂入 → widget 刷新（层间单向依赖的唯一交汇点）。
"""
import os

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.panels.terminal.palette import AnsiPalette
from gui.panels.terminal.screen import TerminalScreen
from gui.panels.terminal.session import PtySession
from gui.panels.terminal.widget import TerminalWidget
from gui.theme import get_family, load_settings


class TerminalPanel(QWidget):
    """中栏下终端面板（真 PTY 单实例：spawn $SHELL，cwd=项目根）。"""

    #: 面板最小高度（px）：标题行约 26px + 约 6 行终端文本，
    #: 配合主窗口 middle_splitter.setCollapsible(1, False) 生效
    MIN_HEIGHT = 140

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
        self._restart = QPushButton("重开", self)
        self._restart.setFixedHeight(22)
        self._restart.setVisible(False)
        self._restart.clicked.connect(self._start_session)

        title_row = QWidget(self)
        row = QHBoxLayout(title_row)
        row.addWidget(self._title, 1)
        row.addWidget(self._status)
        row.addWidget(self._restart)
        row.setContentsMargins(4, 2, 4, 2)

        # DEBUG: 临时边框，确认各层容器真实边界（诊断"内容显示在中间"）
        self.setStyleSheet("TerminalPanel { border: 2px solid red; }")
        title_row.setStyleSheet("border: 1px solid blue;")

        self.terminal = TerminalWidget(self._palette, self)
        self.terminal.set_screen(self._screen)
        self.terminal.set_session(self._session)

        layout = QVBoxLayout(self)
        layout.addWidget(title_row)
        # stretch=1：多余高度全给终端区，标题行只保留自身内容高度（防膨胀抢空间）
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

    def _start_session(self) -> None:
        """（重）开会话：重建屏幕模型 + spawn 新进程。"""
        rows, cols = self.terminal.grid_size()
        self._screen = TerminalScreen(cols, rows)
        self.terminal.set_screen(self._screen)
        self._status.setText("")
        self._restart.setVisible(False)
        self._session.start(cols, rows)

    def _on_data(self, data: bytes) -> None:
        self._screen.feed(data)
        self.terminal.notify_data()

    def _on_exited(self, rc: int) -> None:
        self._status.setText(f"[进程已退出 code {rc}]")
        self._restart.setVisible(True)

    def apply_theme(self, family: str) -> None:
        """切换配色族：色板换新 + 全量重绘（入参为族名 light/dark）。"""
        self._palette = AnsiPalette(family)
        self.terminal.apply_palette(self._palette)
