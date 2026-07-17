"""主窗口：三栏式布局。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter, QVBoxLayout, QWidget


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

        # 中栏垂直拆分：上半部分 + 中栏下
        middle_splitter = QSplitter(Qt.Orientation.Vertical)
        middle_splitter.addWidget(make_panel("中栏"))
        middle_splitter.addWidget(make_panel("中栏下"))
        middle_splitter.setSizes([550, 250])

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(make_panel("左栏"))
        splitter.addWidget(middle_splitter)
        splitter.addWidget(make_panel("右栏"))
        splitter.setSizes([250, 700, 250])

        self.setCentralWidget(splitter)
