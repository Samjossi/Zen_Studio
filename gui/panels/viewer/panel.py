"""文件查看面板：标题行（路径 + Git 差异徽标 + 状态提示）+ 查看器 + 外部修改自动重载。

AI-first 主频场景：agent 直接写盘为主修改路径，`QFileSystemWatcher` 监视当前文件，
外部修改（AI 写盘）→ 防抖自动重载并保留滚动位置，标题行提示"已重新加载"。

Git 差异徽标（2026-07-20，见 文档/修改记录/2026-0720-0131 计划阶段三）：
set_git_service() 注入 GitStatusService 后，open_file 查询 numstat，
标题行路径后追加 `+a -b` 徽标（无改动/非仓库不显示）；外部重载时发射
externally_reloaded 供主窗口联动刷新 Git 状态。

查找浮层（2026-07-20，见 文档/修改记录/2026-0720-0510 计划任务 5.1）：
右上角悬浮（不占布局，对齐终端查找浮层形态），当前文档搜索 + 命中高亮
（经 CodeViewer.set_search_highlights，与当前行高亮合并上屏）+ 上一个/下一个；
编辑菜单「查找」按焦点分发进入，Esc 关闭。

图片预览（2026-07-29，见 work plans/2026-0729-1102_图片文件预览功能实施计划）：
QStackedLayout 双页——文本页 CodeViewer（兼占位提示）/ 图片页 ImageViewer，
open_file 按扩展名分流；图片页标题行显示 ◀ ▶ 适应 100% 按钮，
查找浮层在图片页降级为弱提示。
"""
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.git.service import GitStatusService
from gui.panels.find_bar import FindBar
from gui.panels.viewer.code_viewer import CodeViewer
from gui.panels.viewer.highlighter import PygmentsHighlighter
from gui.panels.viewer.image_viewer import IMAGE_EXTS, ImageViewer
from gui.settings import KEY_THEME
from gui.theme import load_settings, get_theme_palette

#: 大文件守卫：超过 1 MB 截断显示并提示
MAX_BYTES = 1_048_576
#: watcher 防抖（编辑器保存常触发多次 fileChanged）
RELOAD_DEBOUNCE_MS = 150
#: 状态提示展示时长
HINT_TIMEOUT_MS = 3000


class ViewerPanel(QWidget):
    """中栏（上）文件查看面板（只读 + 语法高亮 + 外部修改自动重载）。"""

    #: 外部修改自动重载完成时发射（供主窗口联动刷新 Git 状态）
    externally_reloaded = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_path: str | None = None
        #: Git 状态服务（set_git_service 注入；None = 差异徽标不启用）
        self._git_service: GitStatusService | None = None

        title_row = self._build_title_row()

        # 高亮/行号配色取自主题调色板（资源包下沉，每主题自带全套）
        palette = get_theme_palette(load_settings()[KEY_THEME])
        self.viewer = CodeViewer(palette["chrome"], self)
        self._highlighter = PygmentsHighlighter(self.viewer.document(), palette["syntax"])
        # 图片页：位图/SVG/GIF 预览（与文本页 QStackedLayout 切换）
        self.image_viewer = ImageViewer(palette, self)
        self.image_viewer.info_changed.connect(self._show_hint)
        self._stack = QStackedLayout()
        self._stack.addWidget(self.viewer)
        self._stack.addWidget(self.image_viewer)

        # PanelCard 圆角卡片包裹：标题行 + 查看器整体入卡，卡片统一描边
        # （CodeViewer 自身描边已由 qss 去除，防双重边框）
        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(title_row)
        card_layout.addLayout(self._stack, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）
        layout.setContentsMargins(6, 6, 6, 2)
        layout.setSpacing(0)

        self._init_file_watch()
        self._build_find_bar()

    def _build_title_row(self) -> QWidget:
        """标题行：路径标签 + Git 差异徽标 + 图片按钮组 + 提示标签。"""
        self._path_label = QLabel("（未打开文件）", self)
        self._path_label.setObjectName("PanelTitle")  # 样式由主题 qss 统一
        self._git_badge = QLabel("", self)
        self._git_badge.setObjectName("PanelHint")  # 与提示同款弱化的次要文字样式
        self._git_badge.setVisible(False)
        self._hint_label = QLabel("", self)
        self._hint_label.setObjectName("PanelHint")

        title_row = QWidget(self)
        title_layout = QHBoxLayout(title_row)
        title_layout.addWidget(self._path_label, 1)
        title_layout.addWidget(self._git_badge)
        title_layout.addWidget(self._build_image_buttons(title_row))
        title_layout.addWidget(self._hint_label)
        title_layout.setContentsMargins(4, 2, 4, 2)
        return title_row

    def _build_image_buttons(self, parent: QWidget) -> QWidget:
        """图片页按钮组：◀ ▶ 翻页 + 适应/100%（仅图片页可见，文本页隐藏）。"""
        self._image_buttons = QWidget(parent)
        row = QHBoxLayout(self._image_buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for text, tip, slot in (
            ("◀", "上一张（同目录）", lambda: self.image_viewer.step(-1)),
            ("▶", "下一张（同目录）", lambda: self.image_viewer.step(1)),
            ("适应", "适应窗口", lambda: self.image_viewer.fit()),
            ("100%", "实际像素", lambda: self.image_viewer.actual()),
        ):
            button = QToolButton(self._image_buttons)
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        self._image_buttons.setVisible(False)
        return self._image_buttons

    def _init_file_watch(self) -> None:
        """外部变更监视：fileChanged → 去抖重载（编辑器等连续写合并为一次）。"""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(RELOAD_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._reload)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def current_path(self) -> str | None:
        """当前查看文件的绝对路径（未打开为 None）。"""
        return self._current_path

    def open_file(self, path: str) -> None:
        """打开文件：图片分流图片页；文本读取 → 守卫判定 → 上屏高亮 → 更新 watcher。"""
        p = Path(path)
        if not p.is_file():
            return self._show_placeholder(f"（文件不存在：{path}）")
        if p.suffix.lower().lstrip(".") in IMAGE_EXTS:
            return self._open_image(p)
        try:
            raw = p.read_bytes()
        except OSError as e:
            return self._show_placeholder(f"（读取失败：{e}）")

        truncated = len(raw) > MAX_BYTES
        if truncated:
            raw = raw[:MAX_BYTES]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._show_placeholder(f"（二进制文件，共 {len(raw)} 字节，不支持预览）", path=str(p))

        # 渲染 + 监视（重命名替换保存的场景需重建监视路径）
        self._watch(str(p))
        self._stack.setCurrentWidget(self.viewer)
        self._image_buttons.setVisible(False)
        scroll = self.viewer.verticalScrollBar().value() if self._current_path == str(p) else 0
        self.viewer.setPlainText(text)
        self._highlighter.set_source(p.name, text)
        self.viewer.verticalScrollBar().setValue(scroll)

        title = str(p) + ("（已截断：超过 1 MB）" if truncated else "")
        self._path_label.setText(title)
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层开启中换文件/重载：按新文档重搜；未开启则清残留高亮
        if self._find_bar.isVisible():
            self._update_search()
        else:
            self._clear_search()

    def _open_image(self, p: Path) -> None:
        """图片页上屏：ImageViewer 加载 + watcher 挂载 + Git 徽标刷新。

        加载失败（损坏/解码失败/超防护阈值）回落文本页占位提示。
        """
        if error := self.image_viewer.open_image(p):
            return self._show_placeholder(f"（图片无法预览：{error}）", path=str(p))
        self._watch(str(p))
        self._stack.setCurrentWidget(self.image_viewer)
        self._image_buttons.setVisible(True)
        self._path_label.setText(str(p))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层绑定文本文档，切图片页即关闭并清残留高亮
        if self._find_bar.isVisible():
            self._hide_find()

    def set_git_service(self, service: GitStatusService | None) -> None:
        """注入 Git 状态服务（None 表示禁用差异徽标）。"""
        self._git_service = service
        self.refresh_git_badge()

    def refresh_git_badge(self) -> None:
        """重查当前文件 numstat 并刷新标题行 `+a -b` 徽标。"""
        service = self._git_service
        line_stats = (
            service.numstat_of(self._current_path)
            if service is not None and service.is_enabled and self._current_path
            else None
        )
        if line_stats is None:
            self._git_badge.setVisible(False)
            self._git_badge.setToolTip("")
            return
        added, deleted = line_stats
        self._git_badge.setText(f"+{added} -{deleted}")
        self._git_badge.setToolTip(f"相对 HEAD：新增 {added} 行，删除 {deleted} 行")
        self._git_badge.setVisible(True)

    def apply_theme(self, theme: str) -> None:
        """切换主题：同步高亮器与双页查看器配色包（入参为主题名）。"""
        palette = get_theme_palette(theme)
        self._highlighter.set_theme(palette["syntax"])
        self.viewer.apply_theme(palette["chrome"])
        self.image_viewer.apply_theme(palette)

    def refresh_font(self) -> None:
        """全局字号调整：查看器等宽字体重建（行号栏宽随新字宽重算）。"""
        self.viewer.refresh_font()

    # ------------------------------------------------------------------
    # 查找浮层（右上角悬浮；当前文档搜索 + 命中高亮 + 上一个/下一个）
    # ------------------------------------------------------------------
    def _build_find_bar(self) -> None:
        """装配共用 FindBar 组件（外观/定位/按键自闭环），搜索语义接本面板。"""
        self._find_bar = FindBar(self.viewer, "查找（当前文档）")
        self._find_matches: list[QTextCursor] = []
        self._find_current = -1
        self._find_bar.input.textChanged.connect(self._update_search)
        self._find_bar.step_requested.connect(self._find_step)
        self._find_bar.close_requested.connect(self._hide_find)

    def show_find(self) -> None:
        """打开查找浮层（编辑菜单「查找」焦点分发入口）；图片页降级弱提示。"""
        if self._stack.currentWidget() is self.image_viewer:
            return self._show_hint("图片不支持查找")
        self._find_bar.show_and_focus()
        self._update_search()

    def _hide_find(self) -> None:
        self._find_bar.setVisible(False)
        self._clear_search()
        self.viewer.setFocus()

    def _update_search(self) -> None:
        """收集当前文档全部命中并高亮；有命中即跳转首个。"""
        text = self._find_bar.input.text()
        self._find_matches = []
        if text:
            doc = self.viewer.document()
            cursor = doc.find(text, 0)
            while not cursor.isNull():
                self._find_matches.append(QTextCursor(cursor))  # 拷贝：find 复用同一对象
                cursor = doc.find(text, cursor.position())
        self._find_current = 0 if self._find_matches else -1
        self._apply_find(jump=True)

    def _find_step(self, delta: int) -> None:
        """上一个/下一个：环形步进并跳转。"""
        if self._find_matches:
            self._find_current = (self._find_current + delta) % len(self._find_matches)
            self._apply_find(jump=True)

    def _apply_find(self, jump: bool) -> None:
        """高亮上屏 + 视口跳转当前命中。"""
        self.viewer.set_search_highlights(self._find_matches, self._find_current)
        if jump and self._find_current >= 0:
            self.viewer.setTextCursor(self._find_matches[self._find_current])
            self.viewer.centerCursor()

    def _clear_search(self) -> None:
        self._find_matches = []
        self._find_current = -1
        self.viewer.set_search_highlights([], -1)

    # ------------------------------------------------------------------
    # 外部修改自动重载
    # ------------------------------------------------------------------
    def _watch(self, path: str) -> None:
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

    def _on_file_changed(self, _path: str) -> None:
        self._debounce.start()  # 防抖：连续写多次只重载一次

    def _reload(self) -> None:
        if not self._current_path:
            return
        if not Path(self._current_path).is_file():
            if self._watcher.files():
                self._watcher.removePaths(self._watcher.files())
            return self._show_placeholder(f"（文件已被删除：{self._current_path}）")
        self.open_file(self._current_path)
        self._show_hint("已重新加载（外部修改）")
        self.externally_reloaded.emit()

    # ------------------------------------------------------------------
    # 显示辅助
    # ------------------------------------------------------------------
    def _show_placeholder(self, text: str, path: str | None = None) -> None:
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._stack.setCurrentWidget(self.viewer)  # 占位提示统一回落文本页
        self._image_buttons.setVisible(False)
        self.viewer.setPlainText("")
        self._highlighter.set_source("", "")
        self._path_label.setText(path or "（未打开文件）")
        self._current_path = None
        self._git_badge.setVisible(False)
        self._show_hint(text, sticky=True)

    def _show_hint(self, text: str, sticky: bool = False) -> None:
        self._hint_label.setText(text)
        if not sticky:
            QTimer.singleShot(HINT_TIMEOUT_MS, self._hint_label.clear)
