"""右栏上的提交拓扑与主题摘要双列面板。"""
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from ide.core.git.service import GitStatusService
from ide.gui.git_graph_content import GitGraphContent


class GitGraphPanel(QWidget):
    """进入页面或显式点击刷新时加载，隐藏期间不主动查询提交历史。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = GitGraphContent(None, summary_mode=True, parent=self)
        self._refresh_button = QToolButton(self)
        self._refresh_button.setObjectName("GitGraphRefreshButton")
        self._refresh_button.setText("⟳")
        self._refresh_button.setToolTip("刷新提交历史图")
        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addStretch(1)
        top.addWidget(self._refresh_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._content, 1)

    def set_service(self, service: GitStatusService) -> None:
        self._content.set_service(service)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        self._content.reload()

    def apply_theme(self, theme: str) -> None:
        self._content.apply_theme(theme)

    def refresh_font(self) -> None:
        self._content.refresh_font()
