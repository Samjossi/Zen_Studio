"""主窗口：三栏式布局 + 菜单栏/状态栏骨架。"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.panels import FileExplorer, ViewerPanel
from gui.panels.chat import ChatPanel
from gui.theme import apply_theme, load_settings, save_theme


def make_panel(title: str) -> QWidget:
    """创建一个占位面板。"""
    panel = QWidget()
    layout = QVBoxLayout(panel)
    label = QLabel(title)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    panel.setStyleSheet("QWidget { border: 1px solid #888; }")
    return panel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Zen Studio")
        self.resize(1200, 800)

        # 中栏垂直拆分：上为文件查看器（只读+高亮），下为占位面板
        middle_splitter = QSplitter(Qt.Orientation.Vertical)
        self.viewer_panel = ViewerPanel()
        middle_splitter.addWidget(self.viewer_panel)
        middle_splitter.addWidget(make_panel("中栏下"))
        middle_splitter.setSizes([550, 250])

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

        current_theme = load_settings()["theme"]
        self.action_light = menu_view.addAction("浅色主题(&L)")
        self.action_light.setCheckable(True)
        self.action_light.setChecked(current_theme == "light")
        self.action_light.triggered.connect(lambda: self._switch_theme("light"))

        self.action_dark = menu_view.addAction("暗色主题(&D)")
        self.action_dark.setCheckable(True)
        self.action_dark.setChecked(current_theme == "dark")
        self.action_dark.triggered.connect(lambda: self._switch_theme("dark"))

    def _switch_theme(self, theme: str) -> None:
        """切换主题：持久化 + 即时应用，并同步菜单勾选态与查看器配色。"""
        save_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
        self.viewer_panel.apply_theme(theme)
        self.action_dark.setChecked(theme == "dark")
        self.action_light.setChecked(theme == "light")
        self.statusBar().showMessage(f"已切换为{'暗色' if theme == 'dark' else '浅色'}主题", 3000)
