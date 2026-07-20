"""文件查看面板：标题行（路径 + Git 差异徽标 + 状态提示）+ CodeViewer + 外部修改自动重载。

AI-first 主频场景：agent 直接写盘为主修改路径，`QFileSystemWatcher` 监视当前文件，
外部修改（AI 写盘）→ 防抖自动重载并保留滚动位置，标题行提示"已重新加载"。

Git 差异徽标（2026-07-20，见 work plans/2026-0720-0131 计划阶段三）：
set_git_service() 注入 GitStatusService 后，open_file 查询 numstat，
标题行路径后追加 `+a -b` 徽标（无改动/非仓库不显示）；外部重载时发射
externally_reloaded 供主窗口联动刷新 Git 状态。

查找浮层（2026-07-20，见 文档/修改记录/2026-0720-0510 计划任务 5.1）：
右上角悬浮（不占布局，对齐终端查找浮层形态），当前文档搜索 + 命中高亮
（经 CodeViewer.set_search_highlights，与当前行高亮合并上屏）+ 上一个/下一个；
编辑菜单「查找」按焦点分发进入，Esc 关闭。
"""
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.panels.viewer.code_viewer import CodeViewer
from gui.panels.viewer.highlighter import PygmentsHighlighter
from gui.popups import TranslucentMenuLineEdit
from gui.theme import load_settings, theme_palette

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
        self._git_service = None

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
        title_layout.addWidget(self._hint_label)
        title_layout.setContentsMargins(4, 2, 4, 2)

        # 高亮/行号配色取自主题调色板（资源包下沉，每主题自带全套）
        palette = theme_palette(load_settings()["theme"])
        self.viewer = CodeViewer(palette["chrome"], self)
        self._highlighter = PygmentsHighlighter(self.viewer.document(), palette["syntax"])

        # PanelCard 圆角卡片包裹：标题行 + 查看器整体入卡，卡片统一描边
        # （CodeViewer 自身描边已由 qss 去除，防双重边框）
        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(title_row)
        card_layout.addWidget(self.viewer, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）
        layout.setContentsMargins(6, 6, 6, 2)
        layout.setSpacing(0)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(RELOAD_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._reload)

        self._build_find_bar()
        self.viewer.installEventFilter(self)  # resize → 浮层重定位

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def current_path(self) -> str | None:
        """当前查看文件的绝对路径（未打开为 None）。"""
        return self._current_path

    def open_file(self, path: str) -> None:
        """打开文件：读取 → 守卫判定 → 上屏高亮 → 更新 watcher。"""
        p = Path(path)
        if not p.is_file():
            return self._show_placeholder(f"（文件不存在：{path}）")
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

    def set_git_service(self, service) -> None:
        """注入 Git 状态服务（None 表示禁用差异徽标）。"""
        self._git_service = service
        self.refresh_git_badge()

    def refresh_git_badge(self) -> None:
        """重查当前文件 numstat 并刷新标题行 `+a -b` 徽标。"""
        service = self._git_service
        stat = (
            service.numstat_of(self._current_path)
            if service is not None and service.enabled and self._current_path
            else None
        )
        if stat is None:
            self._git_badge.setVisible(False)
            self._git_badge.setToolTip("")
            return
        added, deleted = stat
        self._git_badge.setText(f"+{added} -{deleted}")
        self._git_badge.setToolTip(f"相对 HEAD：新增 {added} 行，删除 {deleted} 行")
        self._git_badge.setVisible(True)

    def apply_theme(self, theme: str) -> None:
        """切换主题：同步高亮器与查看器控件配色包（入参为主题名）。"""
        palette = theme_palette(theme)
        self._highlighter.set_theme(palette["syntax"])
        self.viewer.apply_theme(palette["chrome"])

    def refresh_font(self) -> None:
        """全局字号调整：查看器等宽字体重建（行号栏宽随新字宽重算）。"""
        self.viewer.refresh_font()

    # ------------------------------------------------------------------
    # 查找浮层（右上角悬浮；当前文档搜索 + 命中高亮 + 上一个/下一个）
    # ------------------------------------------------------------------
    def _build_find_bar(self) -> None:
        """构建查找浮层（初始隐藏）；内部按钮信号自闭环。"""
        self._find_bar = QFrame(self.viewer)
        self._find_bar.setObjectName("TerminalFindBar")  # 复用终端浮层同款样式
        find_row = QHBoxLayout(self._find_bar)
        find_row.setContentsMargins(6, 3, 6, 3)
        find_row.setSpacing(4)
        self._find_input = TranslucentMenuLineEdit(self._find_bar)
        self._find_input.setPlaceholderText("查找（当前文档）")
        self._find_input.setFixedWidth(180)
        self._find_input.installEventFilter(self)  # Enter=下一个 / Esc=关闭
        btn_prev = QPushButton("↑", self._find_bar)
        btn_next = QPushButton("↓", self._find_bar)
        btn_close = QPushButton("×", self._find_bar)
        for b in (btn_prev, btn_next, btn_close):
            b.setFixedSize(24, 22)
        btn_prev.setToolTip("上一个")
        btn_next.setToolTip("下一个")
        btn_prev.clicked.connect(lambda: self._find_step(-1))
        btn_next.clicked.connect(lambda: self._find_step(1))
        btn_close.clicked.connect(self._hide_find)
        find_row.addWidget(self._find_input)
        find_row.addWidget(btn_prev)
        find_row.addWidget(btn_next)
        find_row.addWidget(btn_close)
        self._find_bar.setVisible(False)
        self._find_matches: list[QTextCursor] = []
        self._find_current = -1
        self._find_input.textChanged.connect(self._update_search)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """查看器 resize → 浮层重定位；查找框 Esc/Enter 按键处理。"""
        if watched is self.viewer:
            if event.type() == QEvent.Type.Resize and self._find_bar.isVisible():
                self._place_find_bar()
        elif watched is self._find_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._hide_find()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._find_step(1)
                return True
        return super().eventFilter(watched, event)

    def show_find(self) -> None:
        """打开查找浮层（编辑菜单「查找」焦点分发入口）。"""
        self._place_find_bar()
        self._find_bar.setVisible(True)
        self._find_bar.raise_()
        self._find_input.setFocus()
        self._find_input.selectAll()
        self._update_search()

    def _hide_find(self) -> None:
        self._find_bar.setVisible(False)
        self._clear_search()
        self.viewer.setFocus()

    def _place_find_bar(self) -> None:
        """浮层定位于查看器右上角（子控件坐标系，随查看器 resize 重定位）。"""
        self._find_bar.adjustSize()
        x = max(0, self.viewer.width() - self._find_bar.width() - 16)
        self._find_bar.move(x, 6)

    def _update_search(self) -> None:
        """收集当前文档全部命中并高亮；有命中即跳转首个。"""
        text = self._find_input.text()
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
