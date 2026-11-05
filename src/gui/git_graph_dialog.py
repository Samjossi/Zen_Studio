"""Git 提交历史图对话框：仓库信息行、共享提交图内容与刷新入口。"""
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.git import log as git_log
from core.git.service import GitStatusService
from gui.git_graph_content import GitGraphContent


class GitGraphDialog(QDialog):
    """提交历史图弹窗：信息行（仓库根 + 当前分支）+ 完整四列提交图。"""

    def __init__(self, service: GitStatusService, parent: QWidget | None = None) -> None:
        """
        :param service: 仓库可用性单一判定来源（只读其 is_enabled/repo_root
            结论，不重复探测；对话框不触发其 refresh）
        """
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("提交历史图")
        self.resize(860, 560)

        self._info_label = QLabel(self)
        self._content = GitGraphContent(service, parent=self)
        refresh_btn = QPushButton("刷新(&R)", self)
        refresh_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._info_label, 1)
        top.addWidget(refresh_btn, 0)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._content, 1)

    def showEvent(self, event: QShowEvent) -> None:
        """每次打开自动重拉一次。"""
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        if not self._service.is_enabled:
            self._info_label.setText("（非 git 仓库）")
        else:
            repo_root = self._service.repo_root
            branch = git_log.current_branch(repo_root)
            branch_text = branch if branch else "(detached 或未知)"
            self._info_label.setText(f"{repo_root}　分支：{branch_text}")
        self._content.reload()

    def apply_theme(self, theme: str) -> None:
        self._content.apply_theme(theme)

    def refresh_font(self) -> None:
        self._content.refresh_font()
