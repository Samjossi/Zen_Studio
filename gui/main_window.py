"""主窗口：三栏式布局 + 菜单栏/状态栏骨架。

窗口几何与分隔栏状态持久化（2026-07-19，见 文档/修改记录/
2026-0719-0712_GUI窗口状态与模型选择持久化计划.md）：
启动时 restore，closeEvent 时一次性保存；损坏数据静默回退默认布局。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
)

from gui.panels import FileExplorer, ViewerPanel
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
        # 左栏：AI 聊天面板
        self.chat_panel = ChatPanel()
        self._splitter_main.addWidget(self.chat_panel)
        self._splitter_main.addWidget(self._splitter_middle)

        # 右栏：文件树（根目录为项目根）；双击文件 → 中栏查看器打开
        project_root = str(Path(__file__).resolve().parent.parent)
        self.file_explorer = FileExplorer(project_root)
        self.file_explorer.file_opened.connect(self.viewer_panel.open_file)
        self._splitter_main.addWidget(self.file_explorer)

        self._splitter_main.setSizes([320, 630, 250])
        # 防折叠：右栏文件树最小宽度由 FileExplorer.MIN_WIDTH 约束
        self._splitter_main.setCollapsible(2, False)

        self.setCentralWidget(self._splitter_main)

        self._build_menus()
        self.statusBar().setSizeGripEnabled(False)  # 去掉右下角尺寸把手（原生边框已可缩放）
        self.statusBar().showMessage("就绪")

        self._restore_window_state()

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
        ):
            state = settings.get(key)
            if state:
                splitter.restoreState(decode_state(state))
        self.chat_panel.restore_state(settings.get("splitter_chat"))

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时一次性保存窗口几何与三处分隔栏状态。"""
        # 终端隐藏时先恢复可见再保存：避免把下段 0 尺寸写入持久化（启动始终显示终端）
        if not self.terminal_panel.isVisible():
            self.terminal_panel.setVisible(True)
        update_settings({
            "window_geometry": encode_state(self.saveGeometry()),
            "splitter_main": encode_state(self._splitter_main.saveState()),
            "splitter_middle": encode_state(self._splitter_middle.saveState()),
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

    def _switch_theme(self, theme: str) -> None:
        """切换主题：持久化 + 即时应用，并同步查看器/终端所属族配色。"""
        save_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
        family = get_family(theme)  # 高亮/终端/行号配色只认明暗两族
        self.viewer_panel.apply_theme(family)
        self.terminal_panel.apply_theme(family)
        if theme in self._theme_actions:
            self._theme_actions[theme].setChecked(True)
        self.statusBar().showMessage(f"已切换为{get_label(theme)}主题", 3000)
