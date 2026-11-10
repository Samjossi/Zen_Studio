"""可复用的 Git 扁平变更列表：状态着色、增减行数与双击分派。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QWidget

from ide.core.git import status as git_status
from ide.core.git.service import ChangeEntry
from ide.gui.theme import FALLBACK_THEME, git_status_color


class ChangesList(QTreeWidget):
    """无头部工作区变更列表，由右栏下变更面板使用。"""

    file_opened = Signal(str)
    diff_opened = Signal(str)
    deleted_activated = Signal(str)

    _STAT_COL_MIN = 25

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._changes: list[ChangeEntry] | None = None
        self._repo_root: str | None = None
        self._theme = FALLBACK_THEME

        self.setColumnCount(3)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        header_view = self.header()
        # 关闭末列拉伸，文件名列才能吃掉剩余空间，增减列保持紧凑贴右。
        header_view.setStretchLastSection(False)
        # Qt 默认最小列宽会钳掉统计列的紧凑宽度。
        header_view.setMinimumSectionSize(1)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self._render()

    def apply_changes(
        self,
        changes: list[ChangeEntry] | None,
        repo_root: str | None,
        theme: str,
    ) -> None:
        """喂入变更数据并重绘（None = 非 Git 仓库/服务未启用）。"""
        self._changes = changes
        self._repo_root = repo_root
        self._theme = theme
        self._render()

    def apply_theme(self, theme: str) -> None:
        """按缓存数据与新主题重绘，不重新查询 Git。"""
        self._theme = theme
        self._render()

    def _render(self) -> None:
        self.clear()
        if self._changes is None:
            self._add_placeholder("（非 Git 仓库）")
        elif not self._changes:
            self._add_placeholder("（无变更）")
        else:
            for entry in self._changes:
                self._add_entry(entry)
        self._fit_stat_columns()

    def _fit_stat_columns(self) -> None:
        """用 QStyle 的尺寸提示收紧统计列，包含 QSS item padding。"""
        for col in (1, 2):
            hint = self.sizeHintForColumn(col)
            self.setColumnWidth(col, max(self._STAT_COL_MIN, hint))

    def _add_placeholder(self, text: str) -> None:
        item = QTreeWidgetItem([text, "", ""])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        hint_color = git_status_color(self._theme, git_status.IGNORED)
        if hint_color:
            item.setForeground(0, QColor(hint_color))
        self.addTopLevelItem(item)

    def _add_entry(self, entry: ChangeEntry) -> None:
        path, file_status = entry.path, entry.status
        added, deleted = entry.added, entry.deleted
        item = QTreeWidgetItem([
            path,
            f"+{added}" if added else "",
            f"-{deleted}" if deleted else "",
        ])
        item.setToolTip(0, path)

        color = git_status_color(self._theme, file_status)
        if color:
            item.setForeground(0, QColor(color))
        if file_status == git_status.DELETED:
            font = QFont(self.font())
            font.setStrikeOut(True)
            item.setFont(0, font)

        for col, status_key in ((1, git_status.UNTRACKED), (2, git_status.DELETED)):
            item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            stat_color = git_status_color(self._theme, status_key)
            if stat_color:
                item.setForeground(col, QColor(stat_color))

        abs_path = f"{self._repo_root}/{path}" if self._repo_root else path
        item.setData(0, Qt.ItemDataRole.UserRole, (abs_path, file_status))
        self.addTopLevelItem(item)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if payload is None:
            return
        abs_path, file_status = payload
        if file_status == git_status.DELETED:
            self.deleted_activated.emit(item.text(0))
        elif file_status in (git_status.MODIFIED, git_status.UNTRACKED):
            # 未跟踪文件与已修改文件同走空基准对比，保持新建文件双击语义一致。
            self.diff_opened.emit(abs_path)
        else:
            self.file_opened.emit(abs_path)
