"""Git 提交历史图对话框：只读 ASCII 拓扑图的纯展示弹窗（无图内交互）。

实施计划：work plans/2026-0802-1507_Git提交历史图弹窗显示计划.md（T2）。

- 非模态单例：MainWindow 惰性创建 + show/raise/activateWindow（同设置
  中心 `_settings_dialog` 先例；计划 D2）
- 数据拉取仅两处：showEvent 自动重拉 + 底部「刷新」按钮；不挂
  GitStatusController 去抖汇流（低频查看；计划 D3）
- 降级占位：非仓库/拉取失败置占位文案，信息行与刷新按钮照常可用
  （重试语义），不弹任何错误框（计划 D6）
- 等宽字体单一来源 get_mono_family()（同查看器/终端），字号跟随 app
  全局字号；refresh_font() 挂 MainWindow._apply_font_size 链（同
  viewer/terminal 先例），文本区配色走主题 qss 全局规则不新增主题键
"""
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.git import log as git_log
from core.git.service import GitStatusService
from gui.theme import get_mono_family


class GitGraphDialog(QDialog):
    """提交历史图弹窗：信息行（仓库根 + 当前分支）+ 等宽只读文本区 + 刷新按钮。"""

    def __init__(self, service: GitStatusService, parent: QWidget | None = None) -> None:
        """
        :param service: 仓库可用性单一判定来源（只读其 is_enabled/repo_root
            结论，不重复探测；对话框不触发其 refresh）
        """
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("提交历史图")
        self.resize(720, 520)

        self._info_label = QLabel(self)
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._apply_font()
        refresh_btn = QPushButton("刷新(&R)", self)
        refresh_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._info_label, 1)
        top.addWidget(refresh_btn, 0)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._text, 1)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        """每次打开自动重拉一次（两处拉取时机之一；计划 D3）。"""
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        """重拉提交图；非仓库/失败走占位文案（静默降级，不弹错误框）。"""
        if not self._service.is_enabled:
            self._info_label.setText("（非 git 仓库）")
            self._text.setPlainText("当前工作区不在 git 仓库内。")
            return
        repo_root = self._service.repo_root
        branch = git_log.current_branch(repo_root)
        branch_text = branch if branch else "(detached 或未知)"
        self._info_label.setText(f"{repo_root}　分支：{branch_text}")
        graph = git_log.fetch_commit_graph(repo_root)
        self._text.setPlainText(
            graph if graph is not None else "提交历史获取失败（可能尚无提交）。")

    # ------------------------------------------------------------------
    # 字体
    # ------------------------------------------------------------------
    def refresh_font(self) -> None:
        """全局字号调整跟随（挂 MainWindow._apply_font_size 链，同查看器先例）。"""
        self._apply_font()

    def _apply_font(self) -> None:
        font = QFont(get_mono_family())  # 库内等宽族，注册缺失回退 monospace
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self._text.setFont(font)
