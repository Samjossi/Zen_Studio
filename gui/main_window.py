"""主窗口：三栏式布局 + 菜单栏/状态栏骨架。"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
)

from gui.panels import FileExplorer, ViewerPanel
from gui.panels.chat import ChatPanel
from gui.panels.terminal import TerminalPanel
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
        middle_splitter = QSplitter(Qt.Orientation.Vertical)
        self.viewer_panel = ViewerPanel()
        self.terminal_panel = TerminalPanel()
        middle_splitter.addWidget(self.viewer_panel)
        middle_splitter.addWidget(self.terminal_panel)
        middle_splitter.setSizes([550, 250])
        # 防折叠：终端栏最小高度由 TerminalPanel.MIN_HEIGHT 约束（collapsible 默认 true 会无视之）
        middle_splitter.setCollapsible(1, False)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        # 左栏：AI 聊天面板
        self.chat_panel = ChatPanel()
        splitter.addWidget(self.chat_panel)
        splitter.addWidget(middle_splitter)

        # 右栏：文件树（根目录为项目根）；双击文件 → 中栏查看器打开
        project_root = str(Path(__file__).resolve().parent.parent)
        self.file_explorer = FileExplorer(project_root)
        self.file_explorer.file_opened.connect(self.viewer_panel.open_file)
        splitter.addWidget(self.file_explorer)

        splitter.setSizes([320, 630, 250])
        # 防折叠：右栏文件树最小宽度由 FileExplorer.MIN_WIDTH 约束
        splitter.setCollapsible(2, False)

        self.setCentralWidget(splitter)

        self._build_menus()
        self.statusBar().setSizeGripEnabled(False)  # 去掉右下角尺寸把手（原生边框已可缩放）
        self.statusBar().showMessage("就绪")

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

        # 视图菜单：噪音过滤开关 + 主题切换
        menu_view = menubar.addMenu("视图(&V)")

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
