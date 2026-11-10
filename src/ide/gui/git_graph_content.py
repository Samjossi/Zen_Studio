"""可复用的提交历史图内容：结构化视图、彩色富文本降级与占位页。"""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ide.core.git import log as git_log
from ide.core.git.ansi import sgr_to_html
from ide.core.git.log import CommitGraph
from ide.core.git.service import GitStatusService
from ide.gui.git_graph_view import GitGraphView
from ide.gui.popups import SimplifiedMenuTextBrowser
from ide.gui.settings import KEY_THEME
from ide.gui.theme import get_mono_family, get_theme_palette, load_settings


class GitGraphContent(QWidget):
    """提交图共享内容；只消费服务已有的仓库可用性结论。"""

    def __init__(
        self,
        service: GitStatusService | None,
        summary_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._placeholder = QLabel(self)
        self._placeholder.setWordWrap(True)
        self._fallback = SimplifiedMenuTextBrowser(self)
        self._fallback.setOpenExternalLinks(False)
        self._view = GitGraphView(self)
        self._view.set_summary_mode(summary_mode)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._fallback)
        self._stack.addWidget(self._view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._theme = load_settings()[KEY_THEME]
        self._last_graph: CommitGraph | None = None
        self._last_fallback_text: str | None = None
        self._apply_font()

    def set_service(self, service: GitStatusService) -> None:
        self._service = service

    def set_summary_mode(self, summary_mode: bool) -> None:
        self._view.set_summary_mode(summary_mode)

    def reload(self) -> None:
        """重拉提交图；结构化取数失败回退富文本，两级失败走占位文案。"""
        self._last_graph = None
        if self._service is None or not self._service.is_enabled:
            self._show_placeholder("当前工作区不在 git 仓库内。")
            return
        graph = git_log.fetch_commit_rows(self._service.repo_root)
        if graph is not None:
            self._last_graph = graph
            self._last_fallback_text = None
            self._view.set_graph(graph)
            self._stack.setCurrentWidget(self._view)
            return
        text = git_log.fetch_commit_graph(self._service.repo_root, colored=True)
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
        if self._last_fallback_text is None:
            return
        terminal = get_theme_palette(self._theme)["terminal"]
        body = sgr_to_html(
            self._last_fallback_text,
            lambda key: terminal.get(key) if key else None)
        self._fallback.setHtml(f"<pre>{body}</pre>")

    def apply_theme(self, theme: str) -> None:
        """按缓存重绘视图与降级文本，不重新查询 Git。"""
        self._theme = theme
        self._view.apply_theme(theme)
        self._render_fallback()

    def refresh_font(self) -> None:
        self._apply_font()
        self._view.refresh_font()

    def _apply_font(self) -> None:
        font = QFont(get_mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self._fallback.setFont(font)
