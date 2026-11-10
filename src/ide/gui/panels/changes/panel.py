"""右栏下 Git 变更面板：头部计数与收起按钮包裹可复用变更列表。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ide.core.git.service import ChangeEntry
from ide.gui.panels.changes.list import ChangesList


class ChangesPanel(QWidget):
    """右栏（下）Git 变更面板。"""

    file_opened = Signal(str)
    diff_opened = Signal(str)
    deleted_activated = Signal(str)
    collapse_requested = Signal()

    MIN_HEIGHT = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._changes: list[ChangeEntry] | None = None
        header = self._build_header()
        self._build_list()

        card = QFrame(self)
        card.setObjectName("PanelCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(header)
        card_layout.addWidget(self._list, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 下边距与状态栏共同形成面板底部的统一留白。
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(0)

        self._render()

    def _build_header(self) -> QWidget:
        self._title = QLabel("已变更", self)
        self._title.setObjectName("PanelTitle")
        collapse_button = QPushButton("−", self)
        collapse_button.setFixedSize(28, 22)
        collapse_button.setToolTip("隐藏变更面板")
        collapse_button.clicked.connect(self.collapse_requested)

        header = QWidget(self)
        row = QHBoxLayout(header)
        row.addWidget(self._title, 1)
        row.addWidget(collapse_button)
        row.setContentsMargins(4, 2, 4, 2)
        return header

    def _build_list(self) -> None:
        self._list = ChangesList(self)
        self._list.file_opened.connect(self.file_opened)
        self._list.diff_opened.connect(self.diff_opened)
        self._list.deleted_activated.connect(self.deleted_activated)

    def apply_changes(
        self,
        changes: list[ChangeEntry] | None,
        repo_root: str | None,
        theme: str,
    ) -> None:
        """喂入变更数据并重绘（None = 非 Git 仓库/服务未启用）。"""
        self._changes = changes
        self._list.apply_changes(changes, repo_root, theme)
        self._render()

    def apply_theme(self, theme: str) -> None:
        """主题切换时按缓存数据重绘。"""
        self._list.apply_theme(theme)

    def _render(self) -> None:
        count = "" if self._changes is None else f" ({len(self._changes)})"
        self._title.setText(f"已变更{count}")

    def _on_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        self._list._on_double_clicked(item, column)
