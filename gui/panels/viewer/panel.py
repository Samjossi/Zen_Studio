"""文件查看面板：标题行（路径 + Git 差异徽标 + 状态提示）+ CodeViewer + 外部修改自动重载。

AI-first 主频场景：agent 直接写盘为主修改路径，`QFileSystemWatcher` 监视当前文件，
外部修改（AI 写盘）→ 防抖自动重载并保留滚动位置，标题行提示"已重新加载"。

Git 差异徽标（2026-07-20，见 work plans/2026-0720-0131 计划阶段三）：
set_git_service() 注入 GitStatusService 后，open_file 查询 numstat，
标题行路径后追加 `+a -b` 徽标（无改动/非仓库不显示）；外部重载时发射
externally_reloaded 供主窗口联动刷新 Git 状态。
"""
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui.panels.viewer.code_viewer import CodeViewer
from gui.panels.viewer.highlighter import PygmentsHighlighter
from gui.theme import get_family, load_settings

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

        # 高亮/行号配色只认明暗两族（light/dark），当前主题名先转族名
        family = get_family(load_settings()["theme"])
        self.viewer = CodeViewer(family, self)
        self._highlighter = PygmentsHighlighter(self.viewer.document(), family)

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

    def apply_theme(self, family: str) -> None:
        """切换配色族：同步高亮器与查看器控件配色（入参为族名 light/dark）。"""
        self._highlighter.set_theme(family)
        self.viewer.apply_theme(family)

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
