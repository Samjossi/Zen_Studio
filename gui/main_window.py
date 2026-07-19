"""主窗口：三栏式布局 + 菜单栏/状态栏骨架。

窗口几何与分隔栏状态持久化（2026-07-19，见 文档/修改记录/
2026-0719-0712_GUI窗口状态与模型选择持久化计划.md）：
启动时 restore，closeEvent 时一次性保存；损坏数据静默回退默认布局。

Git 状态可视化（2026-07-20，见 work plans/2026-0720-0131 计划阶段四）：
事件驱动刷新——窗口激活 / 查看器外部重载联动 / 视图菜单手动刷新，
300ms 去抖后刷新 GitStatusService 并同步文件树着色、查看器差异徽标
与状态栏统计；非 git 环境下所有入口静默跳过。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QSplitter,
)

from core.git import GitStatusService
from gui.panels import FileExplorer, ViewerPanel
from gui.panels.changes import ChangesPanel
from gui.panels.chat import ChatPanel
from gui.panels.terminal import TerminalPanel
from gui.settings import decode_state, encode_state, update_settings
from gui.theme import (
    apply_theme,
    available_themes,
    get_family,
    get_label,
    load_settings,
    save_theme,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Zen Studio")
        self.resize(1200, 800)

        # 中栏垂直拆分：上为文件查看器（只读+高亮），下为内嵌终端（真 PTY）
        self._splitter_middle = QSplitter(Qt.Orientation.Vertical)
        self.viewer_panel = ViewerPanel()
        self.terminal_panel = TerminalPanel()
        self._splitter_middle.addWidget(self.viewer_panel)
        self._splitter_middle.addWidget(self.terminal_panel)
        self._splitter_middle.setSizes([550, 250])
        # 防折叠：终端栏最小高度由 TerminalPanel.MIN_HEIGHT 约束（collapsible 默认 true 会无视之）
        self._splitter_middle.setCollapsible(1, False)

        self._splitter_main = QSplitter(Qt.Orientation.Horizontal)
        # 工作区根：文件树根目录与聊天输入框 @相对路径 计算共用同一来源
        project_root = str(Path(__file__).resolve().parent.parent)
        # 左栏：AI 聊天面板
        self.chat_panel = ChatPanel(workspace_root=project_root)
        self._splitter_main.addWidget(self.chat_panel)
        self._splitter_main.addWidget(self._splitter_middle)

        # 右栏：垂直拆分——上文件树（根目录为项目根）、下 Git 变更面板；
        # 双击文件 → 中栏查看器打开
        self.file_explorer = FileExplorer(project_root)
        self.file_explorer.file_opened.connect(self.viewer_panel.open_file)
        self.changes_panel = ChangesPanel()
        self._splitter_right = QSplitter(Qt.Orientation.Vertical)
        self._splitter_right.addWidget(self.file_explorer)
        self._splitter_right.addWidget(self.changes_panel)
        self._splitter_right.setSizes([340, 170])
        # 防折叠：变更面板最小高度由 ChangesPanel.MIN_HEIGHT 约束
        self._splitter_right.setCollapsible(1, False)
        self._splitter_main.addWidget(self._splitter_right)

        self._splitter_main.setSizes([320, 630, 250])
        # 防折叠：右栏文件树最小宽度由 FileExplorer.MIN_WIDTH 约束
        self._splitter_main.setCollapsible(2, False)

        self.setCentralWidget(self._splitter_main)

        self._build_menus()
        self.statusBar().setSizeGripEnabled(False)  # 去掉右下角尺寸把手（原生边框已可缩放）
        # 状态栏右侧常驻：当前文件 Git 差异统计（无改动/非仓库时为空）
        self._git_stat_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._git_stat_label)
        self.statusBar().showMessage("就绪")

        self._init_git_status()
        self._restore_window_state()

    # ------------------------------------------------------------------
    # Git 状态可视化：事件驱动刷新（窗口激活 / 外部重载 / 手动菜单）
    # ------------------------------------------------------------------
    #: 刷新去抖间隔（ms）：连续触发合并为一次，防进程风暴
    GIT_REFRESH_DEBOUNCE_MS = 300

    def _init_git_status(self) -> None:
        """创建状态服务、接线三处事件源，并做启动时的首次刷新。"""
        self._git_service = GitStatusService(self.file_explorer.root_dir)
        self.viewer_panel.set_git_service(self._git_service)
        # 变更面板：双击打开并入查看器管线；删除行双击 → 状态栏提示
        self.changes_panel.file_opened.connect(self.viewer_panel.open_file)
        self.changes_panel.file_opened.connect(lambda _p: self._update_git_stat_label())
        self.changes_panel.deleted_activated.connect(
            lambda path: self.statusBar().showMessage(f"文件已删除，待提交：{path}", 3000)
        )
        self.changes_panel.collapse_requested.connect(
            lambda: self._set_changes_visible(False)
        )
        self._git_debounce = QTimer(self)
        self._git_debounce.setSingleShot(True)
        self._git_debounce.setInterval(self.GIT_REFRESH_DEBOUNCE_MS)
        self._git_debounce.timeout.connect(self._refresh_git_status)
        self.viewer_panel.externally_reloaded.connect(self._git_debounce.start)
        # 切换查看文件时同步状态栏统计（查看器徽标由 open_file 内部自刷）
        self.file_explorer.file_opened.connect(lambda _p: self._update_git_stat_label())
        self._refresh_git_status()

    def _refresh_git_status(self) -> None:
        """去抖汇流点：刷新服务 → 文件树着色 → 查看器徽标 → 变更面板 → 状态栏。"""
        self._git_service.refresh()
        self.file_explorer.apply_git_status(self._git_service)
        self.viewer_panel.refresh_git_badge()
        service = self._git_service
        self.changes_panel.apply_changes(
            service.changes() if service.enabled else None,
            service.repo_root,
            get_family(load_settings()["theme"]),
        )
        self._update_git_stat_label()

    def _update_git_stat_label(self) -> None:
        """状态栏常驻区显示当前查看文件的 `+a -b` 统计。"""
        stat = self._git_service.numstat_of(self.viewer_panel.current_path or "")
        self._git_stat_label.setText(f"+{stat[0]} -{stat[1]}  " if stat else "")

    def changeEvent(self, event) -> None:
        """窗口重获焦点 → 去抖刷新（兜底终端 checkout 等外部 git 操作）。"""
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self._git_debounce.start()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # 窗口几何与分隔栏状态持久化
    # ------------------------------------------------------------------
    def _restore_window_state(self) -> None:
        """启动时恢复窗口几何与三处分隔栏；无记录或数据损坏时保留默认布局。"""
        settings = load_settings()
        geometry = settings.get("window_geometry")
        if geometry:
            self.restoreGeometry(decode_state(geometry))
        for splitter, key in (
            (self._splitter_main, "splitter_main"),
            (self._splitter_middle, "splitter_middle"),
            (self._splitter_right, "splitter_right"),
        ):
            state = settings.get(key)
            if state:
                splitter.restoreState(decode_state(state))
        self.chat_panel.restore_state(settings.get("splitter_chat"))

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时一次性保存窗口几何与四处分隔栏状态。"""
        # 面板隐藏时先恢复可见再保存：避免把 0 尺寸写入持久化（启动始终显示）
        if not self.terminal_panel.isVisible():
            self.terminal_panel.setVisible(True)
        if not self.changes_panel.isVisible():
            self.changes_panel.setVisible(True)
        update_settings({
            "window_geometry": encode_state(self.saveGeometry()),
            "splitter_main": encode_state(self._splitter_main.saveState()),
            "splitter_middle": encode_state(self._splitter_middle.saveState()),
            "splitter_right": encode_state(self._splitter_right.saveState()),
            "splitter_chat": self.chat_panel.save_state(),
        })
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 菜单栏骨架
    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        # 文件菜单（骨架）
        menu_file = menubar.addMenu("文件(&F)")
        action_quit = menu_file.addAction("退出(&Q)")
        action_quit.triggered.connect(self.close)

        # 编辑菜单（骨架，占位禁用）
        menu_edit = menubar.addMenu("编辑(&E)")
        action_placeholder = menu_edit.addAction("（待实现）")
        action_placeholder.setEnabled(False)

        # 视图菜单：面板显隐 + 噪音过滤开关 + 主题切换
        menu_view = menubar.addMenu("视图(&V)")

        # 终端面板显隐：勾选动作为单一入口
        self.action_terminal = menu_view.addAction("终端面板(&T)")
        self.action_terminal.setCheckable(True)
        self.action_terminal.setChecked(True)
        self.action_terminal.triggered.connect(self._set_terminal_visible)

        self.action_noise_filter = menu_view.addAction("过滤噪音目录(&N)")
        self.action_noise_filter.setCheckable(True)
        self.action_noise_filter.setChecked(True)
        self.action_noise_filter.triggered.connect(
            lambda checked: self.file_explorer.set_noise_filter(checked)
        )

        # 变更面板显隐：勾选动作与面板头部「−」按钮汇入 _set_changes_visible
        self.action_changes = menu_view.addAction("变更面板(&C)")
        self.action_changes.setCheckable(True)
        self.action_changes.setChecked(True)
        self.action_changes.triggered.connect(self._set_changes_visible)

        # Git 状态手动刷新（事件驱动三源之一；非 git 环境静默跳过）
        action_git_refresh = menu_view.addAction("刷新 Git 状态(&G)")
        action_git_refresh.triggered.connect(self._refresh_git_status)

        menu_view.addSeparator()

        # 主题菜单：按注册表动态生成，QActionGroup 互斥
        current_theme = load_settings()["theme"]
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions = {}
        for name in available_themes():
            action = menu_view.addAction(get_label(name))
            action.setCheckable(True)
            action.setChecked(name == current_theme)
            action.triggered.connect(lambda checked, n=name: self._switch_theme(n))
            self._theme_group.addAction(action)
            self._theme_actions[name] = action

    def _set_terminal_visible(self, visible: bool) -> None:
        """终端面板显隐单一入口：视图菜单勾选动作与头部栏「−」按钮汇入。

        注意 setChecked 不触发 triggered，勾选态与可见性须在此一并同步。
        """
        self.action_terminal.setChecked(visible)
        self.terminal_panel.setVisible(visible)

    def _set_changes_visible(self, visible: bool) -> None:
        """变更面板显隐单一入口：视图菜单勾选动作与面板头部「−」按钮汇入。

        注意 setChecked 不触发 triggered，勾选态与可见性须在此一并同步。
        """
        self.action_changes.setChecked(visible)
        self.changes_panel.setVisible(visible)

    def _switch_theme(self, theme: str) -> None:
        """切换主题：持久化 + 即时应用，并同步查看器/终端所属族配色。"""
        save_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
        family = get_family(theme)  # 高亮/终端/行号/Git 状态色只认明暗两族
        self.viewer_panel.apply_theme(family)
        self.terminal_panel.apply_theme(family)
        self.file_explorer.apply_theme(family)
        self.changes_panel.apply_theme(family)
        if theme in self._theme_actions:
            self._theme_actions[theme].setChecked(True)
        self.statusBar().showMessage(f"已切换为{get_label(theme)}主题", 3000)
