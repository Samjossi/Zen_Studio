"""MainWindow 职责外移的控制器（AFCP 2.1/3.2 整改任务 2.3）。

主窗口只留菜单 ctx 协调本职（面板显隐槽、主题/字号应用、焦点分发）；
以下两类独立职责迁出为组合成员，依赖在构造函数显式化：

- `GitStatusController`：Git 服务编排（创建/去抖/四面板扇出刷新/工作区重建）
- `WindowStateStore`：窗口几何与 splitter 状态持久化（读恢复/写保存）
"""
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter, QStatusBar

from core.git import GitStatusService
from gui.panels import FileExplorer, ViewerPanel
from gui.panels.changes import ChangesPanel
from gui.panels.chat import ChatPanel
from gui.settings import (
    KEY_SPLITTER_CHAT,
    KEY_THEME,
    KEY_WINDOW_GEOMETRY,
    decode_state,
    encode_state,
    update_settings,
)
from gui.theme import load_settings


class GitStatusController(QObject):
    """Git 状态编排：服务创建/重建、去抖汇流、四面板扇出刷新。

    事件源三处：窗口激活（schedule_refresh）、查看器外部重载（去抖）、
    手动菜单（refresh 直调）；扇出四面：文件树着色 / 查看器徽标 /
    变更面板 / 状态栏统计。
    """

    #: 刷新去抖间隔（ms）：连续触发合并为一次，防进程风暴
    REFRESH_DEBOUNCE_MS = 300
    #: 删除行双击的状态栏提示时长（ms）
    HINT_TIMEOUT_MS = 3000

    def __init__(
        self,
        file_explorer: FileExplorer,
        viewer_panel: ViewerPanel,
        changes_panel: ChangesPanel,
        status_bar: QStatusBar,
        stat_label: QLabel,
        collapse_handler: Callable[[], None],
        parent: QObject,
    ) -> None:
        """
        :param collapse_handler: 变更面板「−」收起的显隐处理（主窗口单一入口槽）
        """
        super().__init__(parent)
        self._explorer = file_explorer
        self._viewer = viewer_panel
        self._changes = changes_panel
        self._status_bar = status_bar
        self._stat_label = stat_label
        self._service = GitStatusService(file_explorer.root_dir)
        viewer_panel.set_git_service(self._service)
        self._wire_signals(collapse_handler)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.REFRESH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)
        viewer_panel.externally_reloaded.connect(self._debounce.start)
        self.refresh()

    @property
    def service(self) -> GitStatusService:
        return self._service

    def _wire_signals(self, collapse_handler: Callable[[], None]) -> None:
        """三处事件源接线（变更面板联动 + 文件打开统计同步）。"""
        # 变更面板：双击打开并入查看器管线；删除行双击 → 状态栏提示
        self._changes.file_opened.connect(self._viewer.open_file)
        self._changes.file_opened.connect(lambda _path: self._update_stat_label())
        self._changes.deleted_activated.connect(
            lambda path: self._status_bar.showMessage(
                f"文件已删除，待提交：{path}", self.HINT_TIMEOUT_MS))
        self._changes.collapse_requested.connect(collapse_handler)
        # 切换查看文件时同步状态栏统计（查看器徽标由 open_file 内部自刷）
        self._explorer.file_opened.connect(lambda _path: self._update_stat_label())

    # ------------------------------------------------------------------
    # 刷新（去抖汇流点）
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """刷新服务 → 文件树着色 → 查看器徽标 → 变更面板 → 状态栏。"""
        self._service.refresh()
        self._explorer.apply_git_status(self._service)
        self._viewer.refresh_git_badge()
        self._changes.apply_changes(
            self._service.collect_changes() if self._service.is_enabled else None,
            self._service.repo_root,
            load_settings()[KEY_THEME],
        )
        self._update_stat_label()

    def schedule_refresh(self) -> None:
        """去抖触发（窗口激活兜底终端 checkout 等外部 git 操作）。"""
        self._debounce.start()

    def rebuild(self, root_dir: str) -> None:
        """工作区切换：服务重建 + 查看器重新注入 + 立即刷新。"""
        self._service = GitStatusService(root_dir)
        self._viewer.set_git_service(self._service)
        self.refresh()

    def _update_stat_label(self) -> None:
        """状态栏常驻区显示当前查看文件的 `+a -b` 统计。"""
        stat = self._service.numstat_of(self._viewer.current_path or "")
        self._stat_label.setText(f"+{stat[0]} -{stat[1]}  " if stat else "")


class WindowStateStore:
    """窗口几何与四处 splitter 状态的读写持久化（settings.json 窗口状态键）。"""

    def __init__(
        self,
        window: QMainWindow,
        splitters: dict[str, QSplitter],
        chat_panel: ChatPanel,
    ) -> None:
        """
        :param splitters: 配置键 → splitter（KEY_SPLITTER_MAIN/MIDDLE/RIGHT）
        :param chat_panel: 聊天面板（其内 splitter 状态经 restore_state/save_state 自管）
        """
        self._window = window
        self._splitters = splitters
        self._chat_panel = chat_panel

    def restore(self) -> None:
        """启动时恢复窗口几何与各分隔栏；无记录或数据损坏时保留默认布局。"""
        settings = load_settings()
        if geometry := settings.get(KEY_WINDOW_GEOMETRY):
            self._window.restoreGeometry(decode_state(geometry))
        for key, splitter in self._splitters.items():
            if state := settings.get(key):
                splitter.restoreState(decode_state(state))
        self._chat_panel.restore_state(settings.get(KEY_SPLITTER_CHAT))

    def save(self) -> None:
        """关闭时一次性保存窗口几何与四处分隔栏状态。"""
        update_settings({
            KEY_WINDOW_GEOMETRY: encode_state(self._window.saveGeometry()),
            **{key: encode_state(splitter.saveState())
               for key, splitter in self._splitters.items()},
            KEY_SPLITTER_CHAT: self._chat_panel.save_state(),
        })
