"""空窗占位部件（一窗一根/空白新窗口，work plans/2026-0831-2350 计划 D4）。

空白窗口（workspace_root=None）不造假根（哨兵目录会污染 sessions/布局
哈希，已否决）：文件树槽位换 `WelcomePanel`（「未打开文件夹」提示 +
「打开文件夹…」按钮 + 最近打开项目快捷列表，点击均走主窗口
open_folder_here 换根填充路径——空窗无旧根可留恋，进程级替换正好
不浪费窗口）；聊天槽位换 `PlaceholderPanel` 置灰占位。工作区落定后
新进程回到正常三栏形态。
"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


def _make_card(parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
    """PanelCard 圆角卡片容器（同终端面板范式：qss border-radius 对自绘/
    容器部件需 WA_StyledBackground 才会绘制）。"""
    card = QFrame(parent)
    card.setObjectName("PanelCard")
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(8)
    return card, layout


class PlaceholderPanel(QWidget):
    """置灰占位卡片：居中单行提示（空窗聊天槽位等禁用槽位通用）。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        card, card_layout = _make_card(self)
        label = QLabel(text, card)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setEnabled(False)  # 置灰占位语义：提示文本随禁用态变浅
        card_layout.addStretch(1)
        card_layout.addWidget(label)
        card_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距体系与其他面板一致（6px，下同）
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(0)


class WelcomePanel(QWidget):
    """空窗文件树槽位欢迎占位：「未打开文件夹」提示 + 「打开文件夹…」按钮
    + 最近打开项目快捷列表（2026-09-01 迭代，原计划 §5 缓办项）。"""

    #: 用户点击「打开文件夹…」（主窗口接 open_folder_here 弹对话框选目录）
    open_folder_requested = Signal()
    #: 用户点击最近项目条目（主窗口接 open_folder_here 直达，就地填充）
    open_project_requested = Signal(str)

    def __init__(self, recent_projects: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        """:param recent_projects: 最近项目路径列表（建窗时刻快照，调用方
            已过滤消失目录；空列表/None 则整块不渲染）"""
        super().__init__(parent)
        card, card_layout = _make_card(self)
        label = QLabel("未打开文件夹", card)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("打开文件夹…", card)
        button.clicked.connect(self.open_folder_requested)
        card_layout.addStretch(1)
        card_layout.addWidget(label)
        card_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        if recent_projects:
            # 条目只显示项目名（2026-09-01 精简：完整路径不必全堆在卡片上），
            # 用户要看具体地址时鼠标悬停 toolTip 即得全路径
            recent_label = QLabel("最近打开：", card)
            recent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addSpacing(16)
            card_layout.addWidget(recent_label)
            for path in recent_projects:
                item = QPushButton(Path(path).name, card)
                item.setToolTip(path)
                item.clicked.connect(
                    lambda checked=False, target=path:
                        self.open_project_requested.emit(target))
                card_layout.addWidget(item)
        card_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(0)
