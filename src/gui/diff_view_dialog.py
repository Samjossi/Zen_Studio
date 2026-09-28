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
- 单侧缺失按空文本对比：基准侧无该文件（新文件）→ 整篇全绿；工作区
  无该文件（盘上不存在）→ 整篇全红，信息行同步标注
- 跳转查看器：「查看文件」按钮 + del/add 行行号锚点 →
  viewer_jump_requested(绝对路径, 新侧行号|None)，由 GitStatusController
  接 ViewerPanel.open_file 并置前主窗口；跳转后关闭本对话框
  （子窗恒在父窗之上，不关闭会遮挡查看器目标行）
- 降级走 QStackedWidget 占位页（非仓库/仓外文件/读取失败/文件过大/
  无差异），不弹任何错误框
- 主题/字号链：apply_theme(theme) 保留为公共接口（热切换已随
  2026-0820-1642 计划移除）；refresh_font() 挂 MainWindow.
  _apply_font_size 链（同查看器/提交历史图先例）
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFont, QResizeEvent, QShowEvent
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


def first_change_line(rows: list[DiffRow] | None) -> int | None:
    """首个变更行（del/add）映射到工作区的新侧行号；无法定位返回 None。

    del 行自身无新侧行号，取其后首个新侧行号（删除位置的下文）；
    文件尾全删时取上文最后新侧行号。「查看文件」按钮的落点。
    """
    if not rows:
        return None
    targets = _row_jump_targets(rows)
    for (kind, _, _, _), target in zip(rows, targets):
        if kind in ("del", "add") and target is not None:
            return target
    return None


def _row_jump_targets(rows: list[DiffRow]) -> list[int | None]:
    """逐行跳转目标（新侧行号）：add/ctx 取自身；del 取其后首个新侧行号
    （文件尾全删取上文最后新侧行号）；hunk 行 None（不可跳转）。"""
    own = [new_no for _, _, _, new_no in rows]
    targets: list[int | None] = []
    last_seen: int | None = None
    for index, (kind, _, _, new_no) in enumerate(rows):
        if kind == "hunk":
            targets.append(None)
        elif kind == "del":
            nxt = next((n for n in own[index + 1:] if n is not None), None)
            targets.append(nxt if nxt is not None else last_seen)
        else:
            targets.append(new_no)
        if new_no is not None:
            last_seen = new_no
    return targets


class DiffViewDialog(QDialog):
    """变更对比弹窗：信息行（文件路径 + 对比基准）+ 二页栈（占位/差异视图）
    +「查看文件」/「刷新」按钮。"""

    #: 跳转查看器请求（绝对路径, 新侧行号|None；载荷对齐对话区
    #: file_open_requested 范式），controllers 接 ViewerPanel.open_file
    viewer_jump_requested = Signal(str, object)

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
        #: 信息行完整文案（ElideMiddle 省略前的原文，tooltip/重省略用）
        self._info_text = ""

        self._info_label = QLabel(self)
        self._placeholder = QLabel(self)
        self._placeholder.setWordWrap(True)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        # 行号锚点（diffjump:N）由 anchorClicked 自处理；置 False 防
        # 默认 setSource 导航把 diff 内容覆盖掉
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        self._browser.setReadOnly(True)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._browser)
        self._jump_btn = QPushButton("查看文件(&V)", self)
        self._jump_btn.setToolTip("在查看器中打开该文件并定位到首个变更行")
        self._jump_btn.setEnabled(False)  # 待 reload 判定工作区文件在盘后启用
        self._jump_btn.clicked.connect(self._on_jump_clicked)
        refresh_btn = QPushButton("刷新(&R)", self)
        refresh_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._info_label, 1)
        top.addWidget(self._jump_btn, 0)
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
        """重拉基准/工作区内容并重建差异；单侧缺失按空文本对比，读取失败走占位文案。"""
        if self._path is None:
            return
        # 每次重拉先禁用「查看文件」，确认工作区文件在盘的分支再逐个启用
        self._jump_btn.setEnabled(False)
        if not self._service.is_enabled:
            self._set_info("（非 git 仓库）")
            self._show_placeholder("当前工作区不在 git 仓库内。")
            return
        repo_root = self._service.repo_root
        try:
            rel = str(Path(self._path).resolve().relative_to(repo_root))
        except ValueError:
            self._show_placeholder("文件不在当前仓库内。")
            return
        self._set_info(f"{rel}　基准：@{{upstream}}（无上游时回退 HEAD）")

        worktree = git_content.fetch_worktree_content(repo_root, rel)
        worktree_missing = False
        if worktree is None:
            if (Path(repo_root) / rel).exists():
                # 文件在盘但读不出（二进制/编码）：查看器自有二进制分流，
                # 跳转仍可用
                self._jump_btn.setEnabled(True)
                self._show_placeholder("文件读取失败（可能为二进制或不支持的编码）。")
                return
            # 盘上不存在（如暂存后被删）：按空文本对比，整篇全红
            worktree = ""
            worktree_missing = True
        else:
            self._jump_btn.setEnabled(True)
        base = git_content.fetch_base_content(repo_root, rel)
        if base is None:
            # base 为 None 无法区分「ref 中不存在」与「ref 中是二进制」，
            # 一律按新文件空基准处理（整篇全绿）；二进制误判仅表现为
            # 全绿呈现，可接受，不为边界加重数据层
            base = ""
            self._set_info(f"{rel}　基准：无（新文件，基准侧为空）")
        if worktree_missing:
            self._set_info(self._info_text + "　工作区：无（文件已删除）")
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

    def _set_info(self, text: str) -> None:
        """信息行赋值入口：存全文（tooltip 全路径）+ 按当前宽度中间省略。"""
        self._info_text = text
        self._info_label.setToolTip(text)
        self._elide_info()

    def _elide_info(self) -> None:
        """长路径中间省略：首段目录头与尾段文件名是定位关键，中段可省。"""
        width = self._info_label.width()
        if width < 50:  # 尚未布局完成（show 前），先放全文，resizeEvent 兜底
            self._info_label.setText(self._info_text)
            return
        self._info_label.setText(self._info_label.fontMetrics().elidedText(
            self._info_text, Qt.TextElideMode.ElideMiddle, width))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide_info()

    def _show_placeholder(self, text: str) -> None:
        self._rows = None
        self._placeholder.setText(text)
        self._stack.setCurrentWidget(self._placeholder)

    # ------------------------------------------------------------------
    # 跳转查看器
    # ------------------------------------------------------------------
    def _on_jump_clicked(self) -> None:
        """「查看文件」按钮：定位首个变更行（无行序列时从文件头打开）。"""
        self._jump_to_viewer(first_change_line(self._rows))

    def _on_anchor_clicked(self, url: QUrl) -> None:
        """del/add 行行号锚点（diffjump:N）：定位该变更行。"""
        scheme, _, line_text = url.toString().partition("diffjump:")
        if scheme or not line_text.isdigit():
            return
        self._jump_to_viewer(int(line_text))

    def _jump_to_viewer(self, line: int | None) -> None:
        if self._path is None:
            return
        self.viewer_jump_requested.emit(self._path, line)
        # 跳转后关闭：子窗恒在父窗之上，不关闭会遮挡查看器目标行；
        # 非模态单例重开成本仅一次双击
        self.close()

    def _render(self) -> None:
        """表格化逐行渲染：旧行号 | 新行号 | 内容三列 + 整行底色 + hunk 蓝带。

        整行浅红/浅绿底色是「一眼认出 diff」的主信号（三家参考实现共识，
        文字前景色只是辅助层）；hunk 行跨三列蓝带蓝字与上下文拉开第二层次。
        内容 cell 内嵌 <pre> 保留缩进空白（Qt 富文本子集实证通路）；
        cellspacing/cellpadding 清零防整行底色断缝；行号列 width=1 收缩
        贴内容列（HTML 表格最小宽技法），列间距由行号尾空格承担。
        del/add 行的行号渲染为 diffjump:N 锚点（点击跳查看器对应变更行），
        锚点颜色走默认样式表 a.del/a.add 类选择器，抵消 Qt 默认链接色与
        下划线、保持行号随行染色观感。
        """
        if self._rows is None:
            return
        chat = get_theme_palette(self._theme)["chat"]
        self._browser.document().setDefaultStyleSheet(
            "a { text-decoration: none; }\n"
            f"a.add {{ color: {chat['diff_add_fg']}; }}\n"
            f"a.del {{ color: {chat['diff_del_fg']}; }}")
        targets = _row_jump_targets(self._rows)
        parts = ['<table cellspacing="0" cellpadding="0" width="100%">']
        for (kind, text, old_no, new_no), target in zip(self._rows, targets):
            if kind == "hunk":
                parts.append(
                    f'<tr><td colspan="3" bgcolor="{chat["diff_hunk_bg"]}">'
                    f'<pre><font color="{chat["diff_hunk_fg"]}">{escape(text)}</font></pre>'
                    "</td></tr>")
                continue
            bg = {"del": chat["diff_del_bg"], "add": chat["diff_add_bg"]}.get(kind)
            fg = {"del": chat["diff_del_fg"], "add": chat["diff_add_fg"]}.get(kind)
            bg_attr = f' bgcolor="{bg}"' if bg else ""

            def no_cell(no: int | None, anchor: int | None = None) -> str:
                num = "" if no is None else str(no)
                if num and anchor is not None:
                    # 锚点行号色由默认样式表 a.del/a.add 承担，不再内嵌 font
                    num = f'<a class="{kind}" href="diffjump:{anchor}">{num}</a>'
                elif num and fg:
                    num = f'<font color="{fg}">{num}</font>'
                # 尾空格充当列间距（cellpadding 清零后数字与邻列会粘连）
                return f'<td{bg_attr} align="right" width="1"><pre>{num} </pre></td>'

            if kind == "del":  # 删除行无新侧行号，锚点挂旧侧行号
                cells = no_cell(old_no, target) + no_cell(new_no)
            elif kind == "add":
                cells = no_cell(old_no) + no_cell(new_no, target)
            else:
                cells = no_cell(old_no) + no_cell(new_no)

            content = escape(text)
            if fg:
                content = f'<font color="{fg}">{content}</font>'
            parts.append(
                f"<tr>{cells}"
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
