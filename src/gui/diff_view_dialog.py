"""变更对比对话框：Unified Diff 单窗格视图（红 = 删除 / 绿 = 新增）。

实施计划：work plans/2026-0924-1749_变更对比UnifiedDiff视图落地计划.md（D3）。

- 非模态单例：GitStatusController 惰性创建 + show/raise/activateWindow
  （同提交历史图对话框先例）；showEvent 按当前文件重拉内容，
  外部并发改动不自动刷新（重开或点「刷新」，与提交历史图策略一致）
- 数据链路：基准侧 content.fetch_base_content（@{upstream} 优先、HEAD
  兜底）vs 工作区 content.fetch_worktree_content → core.diff_view.
  unified_rows 行序列（含双侧行号）→ QTextBrowser 表格化渲染
  （三列：旧行号|新行号|内容；del/add 整行浅红/浅绿底，hunk 跨列蓝带，
  色值取 chat 包 diff_* 六键）
- 降级走 QStackedWidget 占位页（非仓库/仓外文件/读取失败/基准侧无
  内容/文件过大/无差异），不弹任何错误框
- 主题/字号链：apply_theme(theme) 保留为公共接口（热切换已随
  2026-0820-1642 计划移除）；refresh_font() 挂 MainWindow.
  _apply_font_size 链（同查看器/提交历史图先例）
"""
from __future__ import annotations

from html import escape
from pathlib import Path

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

from core.diff_view import MAX_DIFF_LINES, DiffRow, unified_rows
from core.git import content as git_content
from core.git.service import GitStatusService
from gui.settings import KEY_THEME
from gui.theme import get_mono_family, get_theme_palette, load_settings


class DiffViewDialog(QDialog):
    """变更对比弹窗：信息行（文件路径 + 对比基准）+ 二页栈（占位/差异视图）+ 刷新按钮。"""

    def __init__(self, service: GitStatusService, parent: QWidget | None = None) -> None:
        """
        :param service: 仓库可用性单一判定来源（只读其 is_enabled/repo_root
            结论，不重复探测；对话框不触发其 refresh）
        """
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("变更对比")
        self.resize(860, 560)

        #: 当前对比文件（绝对路径）；None = 尚未打开任何文件
        self._path: str | None = None
        #: 缓存最近一次行序列（主题切换重渲染用，避免重复 spawn git）
        self._rows: list[DiffRow] | None = None

        self._info_label = QLabel(self)
        self._placeholder = QLabel(self)
        self._placeholder.setWordWrap(True)
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        self._browser.setReadOnly(True)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._browser)
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
        self._apply_font()

    # ------------------------------------------------------------------
    # 打开入口
    # ------------------------------------------------------------------
    def show_diff(self, abs_path: str) -> None:
        """打开指定文件的对比视图并置前（双击变更面板的唯一入口）。"""
        self._path = abs_path
        if self.isVisible():
            # showEvent 只在隐藏→显示时触发；已可见时换文件须显式重拉
            self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event: QShowEvent) -> None:
        """每次打开自动重拉一次（两处拉取时机之一，另一处为刷新按钮）。"""
        super().showEvent(event)
        if self._path is not None:
            self.reload()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """重拉基准/工作区内容并重建差异；任一环失败走占位文案。"""
        if self._path is None:
            return
        if not self._service.is_enabled:
            self._info_label.setText("（非 git 仓库）")
            self._show_placeholder("当前工作区不在 git 仓库内。")
            return
        repo_root = self._service.repo_root
        try:
            rel = str(Path(self._path).resolve().relative_to(repo_root))
        except ValueError:
            self._show_placeholder("文件不在当前仓库内。")
            return
        self._info_label.setText(
            f"{rel}　基准：@{{upstream}}（无上游时回退 HEAD）")

        worktree = git_content.fetch_worktree_content(repo_root, rel)
        if worktree is None:
            self._show_placeholder("文件读取失败（可能为二进制或不支持的编码）。")
            return
        base = git_content.fetch_base_content(repo_root, rel)
        if base is None:
            self._show_placeholder("基准侧无该文件内容（文件可能未纳入版本管理）。")
            return
        if (len(base.splitlines()) > MAX_DIFF_LINES
                or len(worktree.splitlines()) > MAX_DIFF_LINES):
            self._show_placeholder(f"文件过大，暂不支持对比（上限 {MAX_DIFF_LINES} 行）。")
            return
        rows = unified_rows(base, worktree)
        if not rows:
            self._show_placeholder("与基准无差异。")
            return
        self._rows = rows
        self._render()
        self._stack.setCurrentWidget(self._browser)

    def _show_placeholder(self, text: str) -> None:
        self._rows = None
        self._placeholder.setText(text)
        self._stack.setCurrentWidget(self._placeholder)

    def _render(self) -> None:
        """表格化逐行渲染：旧行号 | 新行号 | 内容三列 + 整行底色 + hunk 蓝带。

        整行浅红/浅绿底色是「一眼认出 diff」的主信号（三家参考实现共识，
        文字前景色只是辅助层）；hunk 行跨三列蓝带蓝字与上下文拉开第二层次。
        内容 cell 内嵌 <pre> 保留缩进空白（Qt 富文本子集实证通路）；
        cellspacing/cellpadding 清零防整行底色断缝；行号列 width=1 收缩
        贴内容列（HTML 表格最小宽技法），列间距由行号尾空格承担。
        """
        if self._rows is None:
            return
        chat = get_theme_palette(self._theme)["chat"]
        parts = ['<table cellspacing="0" cellpadding="0" width="100%">']
        for kind, text, old_no, new_no in self._rows:
            if kind == "hunk":
                parts.append(
                    f'<tr><td colspan="3" bgcolor="{chat["diff_hunk_bg"]}">'
                    f'<pre><font color="{chat["diff_hunk_fg"]}">{escape(text)}</font></pre>'
                    "</td></tr>")
                continue
            bg = {"del": chat["diff_del_bg"], "add": chat["diff_add_bg"]}.get(kind)
            fg = {"del": chat["diff_del_fg"], "add": chat["diff_add_fg"]}.get(kind)
            bg_attr = f' bgcolor="{bg}"' if bg else ""

            def no_cell(no: int | None) -> str:
                num = "" if no is None else str(no)
                if num and fg:
                    num = f'<font color="{fg}">{num}</font>'
                # 尾空格充当列间距（cellpadding 清零后数字与邻列会粘连）
                return f'<td{bg_attr} align="right" width="1"><pre>{num} </pre></td>'

            content = escape(text)
            if fg:
                content = f'<font color="{fg}">{content}</font>'
            parts.append(
                f"<tr>{no_cell(old_no)}{no_cell(new_no)}"
                f'<td{bg_attr} width="100%"><pre>{content}</pre></td></tr>')
        parts.append("</table>")
        self._browser.setHtml("".join(parts))

    # ------------------------------------------------------------------
    # 主题/字号链
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """主题切换跟随（热切换已随 1642 计划移除，方法保留为公共接口）：重渲染差异行。"""
        self._theme = theme
        self._render()

    def refresh_font(self) -> None:
        """全局字号调整跟随（挂 MainWindow._apply_font_size 链，同查看器先例）。"""
        self._apply_font()

    def _apply_font(self) -> None:
        font = QFont(get_mono_family())  # 库内等宽族，注册缺失回退 monospace
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self._browser.setFont(font)
