"""Git 变更面板：已变更文件列表（状态着色 + 增减行数），VS Code SCM 面板简化版。

2026-07-20（见 work plans/2026-0720-0215_Git变更面板实施计划.md 阶段二）：
- 文件名按状态着色（复用 theme.GIT_STATUS_COLORS 明暗两族）：天蓝 M / 绿 U / 红 D，
  已删除文件追加删除线字体
- 右侧绿 `+N` / 红 `-N` 行数（不补零）；未跟踪目录折叠行（`dir/`）无数字
- 双击文件行 → file_opened 绝对路径；删除行双击 → deleted_activated（文件已不存在）
- 头部栏标题（含数量）+ 「−」收起按钮 → collapse_requested，显隐逻辑归主窗口
- 空态占位行：changes=None 非 Git 仓库 / 空列表 无变更
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.git import status as git_status
from gui.theme import git_status_color


class ChangesPanel(QWidget):
    """右栏（下）Git 变更面板。"""

    #: 双击文件行发射（绝对路径），供主窗口并入查看器打开管线
    file_opened = Signal(str)
    #: 双击已删除行发射（相对路径），供主窗口状态栏提示
    deleted_activated = Signal(str)
    #: 头部「−」按钮点击发射，显隐单一入口归主窗口
    collapse_requested = Signal()

    #: 面板最小高度（px）：头部栏 + 约 3 行列表，配合 splitter.setCollapsible(False)
    MIN_HEIGHT = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        #: 最近一次喂入的数据（apply_theme 重绘复用）
        self._changes: list[dict] | None = None
        self._repo_root: str | None = None
        self._family = "light"

        # 头部栏：标题（含数量）+ 「−」收起按钮
        self._title = QLabel("已变更", self)
        self._title.setObjectName("PanelTitle")
        btn_collapse = QPushButton("−", self)
        btn_collapse.setFixedSize(28, 22)
        btn_collapse.setToolTip("隐藏变更面板")
        btn_collapse.clicked.connect(self.collapse_requested)

        header = QWidget(self)
        row = QHBoxLayout(header)
        row.addWidget(self._title, 1)
        row.addWidget(btn_collapse)
        row.setContentsMargins(4, 2, 4, 2)

        # 变更列表：文件名 | +增 | -减（无表头）
        self._list = QTreeWidget(self)
        self._list.setColumnCount(3)
        self._list.setHeaderHidden(True)
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        header_view = self._list.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)

        # PanelCard 圆角卡片包裹（对齐查看器/终端既有样式）
        card = QFrame(self)
        card.setObjectName("PanelCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(header)
        card_layout.addWidget(self._list, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        layout.setContentsMargins(6, 2, 6, 6)
        layout.setSpacing(0)

        self._render()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def apply_changes(self, changes: list[dict] | None, repo_root: str | None, family: str) -> None:
        """喂入变更数据并重绘（None = 非 Git 仓库/服务未启用）。"""
        self._changes = changes
        self._repo_root = repo_root
        self._family = family
        self._render()

    def apply_theme(self, family: str) -> None:
        """主题切换时按新族重绘（数据不变）。"""
        self._family = family
        self._render()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self) -> None:
        self._list.clear()
        if self._changes is None:
            self._title.setText("已变更")
            self._add_placeholder("（非 Git 仓库）")
            return
        self._title.setText(f"已变更 ({len(self._changes)})")
        if not self._changes:
            self._add_placeholder("（无变更）")
            return
        for entry in self._changes:
            self._add_entry(entry)

    def _add_placeholder(self, text: str) -> None:
        item = QTreeWidgetItem([text, "", ""])
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # 不可选不可点
        hint_color = git_status_color(self._family, git_status.IGNORED)
        if hint_color:
            item.setForeground(0, QColor(hint_color))
        self._list.addTopLevelItem(item)

    def _add_entry(self, entry: dict) -> None:
        path, st = entry["path"], entry["status"]
        added, deleted = entry["added"], entry["deleted"]
        item = QTreeWidgetItem([
            path,
            f"+{added}" if added else "",
            f"-{deleted}" if deleted else "",
        ])
        item.setToolTip(0, path)

        color = git_status_color(self._family, st)
        if color:
            item.setForeground(0, QColor(color))
        if st == git_status.DELETED:
            font = QFont(self._list.font())
            font.setStrikeOut(True)
            item.setFont(0, font)

        # 增减列：绿 + / 红 -，右对齐（色值复用状态注册表的 untracked/deleted）
        for col, status_key in ((1, git_status.UNTRACKED), (2, git_status.DELETED)):
            item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            stat_color = git_status_color(self._family, status_key)
            if stat_color:
                item.setForeground(col, QColor(stat_color))

        # UserRole 存 (绝对路径/相对路径, 状态)，双击分派用
        abs_path = f"{self._repo_root}/{path}" if self._repo_root else path
        item.setData(0, Qt.ItemDataRole.UserRole, (abs_path, st))
        self._list.addTopLevelItem(item)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if payload is None:
            return
        abs_path, st = payload
        if st == git_status.DELETED:
            self.deleted_activated.emit(item.text(0))
        else:
            self.file_opened.emit(abs_path)
