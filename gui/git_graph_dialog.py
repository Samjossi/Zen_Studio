"""Git 提交历史图对话框：结构化列表主形态 + 彩色富文本降级形态。

实施计划：work plans/2026-0802-1507_Git提交历史图弹窗显示计划.md（T2）、
work plans/2026-0802-1542_Git提交历史图美化计划.md（T1/T4）。

- 非模态单例：MainWindow 惰性创建 + show/raise/activateWindow（同设置
  中心 `_settings_dialog` 先例；1507 计划 D2）
- 数据拉取仅两处：showEvent 自动重拉 + 底部「刷新」按钮；不挂
  GitStatusController 去抖汇流（低频查看；1507 计划 D3）
- 主形态：GitGraphView 结构化列表（委托自绘图形列/refs 徽标分栏）；
  解析失败（--graph 格式漂移）整窗回退彩色富文本形态（1542 计划 D3），
  两级失败再走占位文案（非仓库/尚无提交），不弹任何错误框
- 主题/字号链：apply_theme(theme) 挂 MainWindow.switch_theme 链（视图/
  富文本重渲染均用缓存数据，不重复 spawn git）；refresh_font() 挂
  MainWindow._apply_font_size 链（同查看器/终端先例）
"""
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.git import log as git_log
from core.git.ansi import sgr_to_html
from core.git.service import GitStatusService
from gui.git_graph_view import GitGraphView
from gui.settings import KEY_THEME
from gui.theme import get_mono_family, get_theme_palette, load_settings


class GitGraphDialog(QDialog):
    """提交历史图弹窗：信息行（仓库根 + 当前分支）+ 三页栈中部 + 刷新按钮。"""

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
        self._placeholder = QLabel(self)
        self._placeholder.setWordWrap(True)
        self._fallback = QTextBrowser(self)  # 降级形态：彩色富文本（D3 回退）
        self._fallback.setOpenExternalLinks(False)
        self._view = GitGraphView(self)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._fallback)
        self._stack.addWidget(self._view)
        refresh_btn = QPushButton("刷新(&R)", self)
        refresh_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._info_label, 1)
        top.addWidget(refresh_btn, 0)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._stack, 1)

        self._theme = load_settings()[KEY_THEME]
        #: 缓存最近一次拉取结果（主题切换重渲染用，避免重复 spawn git）
        self._last_fallback_text: str | None = None
        self._apply_font()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        """每次打开自动重拉一次（两处拉取时机之一；1507 计划 D3）。"""
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        """重拉提交图；结构化解析失败回退富文本，两级失败走占位文案。"""
        if not self._service.is_enabled:
            self._info_label.setText("（非 git 仓库）")
            self._show_placeholder("当前工作区不在 git 仓库内。")
            return
        repo_root = self._service.repo_root
        branch = git_log.current_branch(repo_root)
        branch_text = branch if branch else "(detached 或未知)"
        self._info_label.setText(f"{repo_root}　分支：{branch_text}")

        graph = git_log.fetch_commit_rows(repo_root)
        if graph is not None:
            self._view.set_graph(graph)
            self._stack.setCurrentWidget(self._view)
            return
        # D3 回退：--graph 解析失败（格式漂移）→ 彩色富文本形态
        text = git_log.fetch_commit_graph(repo_root, colored=True)
        if text is not None:
            self._last_fallback_text = text
            self._render_fallback()
            self._stack.setCurrentWidget(self._fallback)
            return
        self._show_placeholder("提交历史获取失败（可能尚无提交）。")

    def _show_placeholder(self, text: str) -> None:
        self._last_fallback_text = None
        self._placeholder.setText(text)
        self._stack.setCurrentWidget(self._placeholder)

    def _render_fallback(self) -> None:
        """降级形态渲染：SGR → HTML，颜色经 TERMINAL_PACK 主题映射（D4）。"""
        if self._last_fallback_text is None:
            return
        terminal = get_theme_palette(self._theme)["terminal"]
        body = sgr_to_html(
            self._last_fallback_text,
            lambda key: terminal.get(key) if key else None)
        self._fallback.setHtml(f"<pre>{body}</pre>")

    # ------------------------------------------------------------------
    # 主题/字号链
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """主题切换跟随（挂 MainWindow.switch_theme 链）：视图/富文本重渲染。"""
        self._theme = theme
        self._view.apply_theme(theme)
        self._render_fallback()

    def refresh_font(self) -> None:
        """全局字号调整跟随（挂 MainWindow._apply_font_size 链，同查看器先例）。"""
        self._apply_font()
        self._view.refresh_font()

    def _apply_font(self) -> None:
        font = QFont(get_mono_family())  # 库内等宽族，注册缺失回退 monospace
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self._fallback.setFont(font)
