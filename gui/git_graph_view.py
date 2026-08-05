"""Git 提交历史图列表视图：QTreeView + 委托自绘（图形列/refs 徽标）。

实施计划：文档/修改记录/2026-0802-1542_Git提交历史图美化计划.md（T3/T4）。

- 图形列（GraphColumnDelegate）：逐格绘制 git --graph 解析出的图线格，
  颜色取格携带的 TERMINAL_PACK 键（None 格 = git 未着色的单 lane 线性段，
  映射 terminal default_fg）；纯图线连接行半高（D5），仅直线图元不做
  贝塞尔平滑（方案丙远期预留）
- 提交列（CommitColumnDelegate）：hash（hash_fg 弱化）+ refs 徽标（圆角
  胶囊，四型分色取自 GIT_GRAPH_PACK，背景/边框按色值派生透明度）+
  subject（HEAD 行加粗），空间不足省略号截断
- 主题/字号：apply_theme(theme) 挂 MainWindow.switch_theme 链、
  refresh_font() 挂 _apply_font_size 链（同对话框既有先例）
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from core.git.log import CommitGraph, CommitRow
from gui.settings import KEY_THEME
from gui.theme import get_mono_family, get_theme_palette, load_settings

#: 列索引
COL_GRAPH = 0
COL_COMMIT = 1
COL_AUTHOR = 2
COL_DATE = 3

_ROW_VPAD = 5  # 行高在字体度量外的纵向余量（两侧合计）
_BADGE_HPAD = 7  # 徽标胶囊文字两侧横向内边距
_BADGE_GAP = 5  # hash/徽标/subject 之间的间距


def _row_height(widget_font: QFont) -> int:
    return QFontMetrics(widget_font).height() + _ROW_VPAD * 2


def _height_for(index: QModelIndex, font: QFont) -> int:
    """行高：纯图线连接行半高（D5），其余全高；提示行全高。"""
    model = index.model()
    row = model.row_of(index) if isinstance(model, CommitGraphModel) else None
    base = _row_height(font)
    return base // 2 if (row is not None and row.is_connector) else base


class _RowHeightDelegate(QStyledItemDelegate):
    """作者/时间列：默认渲染 + 行高覆写（连接行半高；宽度保留内容宽）。

    行高不走模型 SizeHintRole：QSize(-1, h) 的 -1 宽度会污染表头
    ResizeToContents 的内容宽计算（实证列宽被钳到 40px）。
    """

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _height_for(index, option.font))


class CommitGraphModel(QAbstractTableModel):
    """提交图模型：行 = 提交行/连接行 + 末尾可选截断提示行。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[CommitRow] = []
        self._hint: str | None = None
        self.max_graph_cells = 0  # 图形列宽 = 最大格数 × 格宽（视图取用）

    def set_graph(self, graph: CommitGraph) -> None:
        self.beginResetModel()
        self._rows = list(graph.rows)
        self._hint = graph.truncated_hint
        self.max_graph_cells = max(
            (len(r.graph) for r in self._rows), default=0)
        self.endResetModel()

    def row_of(self, index: QModelIndex) -> CommitRow | None:
        """index → CommitRow；截断提示行返回 None。"""
        if 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]
        return None

    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows) + (1 if self._hint else 0)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 4

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return ("图", "提交", "作者", "时间")[section]
        return None

    def data(self, index: QModelIndex, role: int = 0):
        if not index.isValid():
            return None
        row = self.row_of(index)
        if role == Qt.ItemDataRole.UserRole:
            return row  # None 即截断提示行（委托据此分流）
        if role == Qt.ItemDataRole.DisplayRole:
            if row is None:
                return self._hint if index.column() == COL_COMMIT else None
            if index.column() == COL_AUTHOR:
                return row.author
            if index.column() == COL_DATE:
                # 相对时间 + 绝对时间并列（VS Code 式双时间）
                if row.abs_date:
                    return f"{row.rel_date} · {row.abs_date}"
                return row.rel_date
            return None  # 图形列/提交列由委托自绘
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class GraphColumnDelegate(QStyledItemDelegate):
    """图形列委托：图线格 → 圆点/直线图元，颜色取 TERMINAL_PACK 映射。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors: dict[str, QColor] = {}  # 包键 → 色值；None 键用 default
        self._default = QColor("#1a1a1a")
        self._accent = QColor("#0765d4")
        self._cell_w = 8

    def apply_theme(self, terminal_pack: dict, accent: str) -> None:
        self._colors = {
            k: QColor(v) for k, v in terminal_pack.items()
            if k not in ("find_bg", "find_cur") and not k.startswith("default_")
        }
        self._default = QColor(terminal_pack["default_fg"])
        self._accent = QColor(accent)

    def refresh_font(self) -> None:
        font = QFont(get_mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        self._cell_w = max(4, QFontMetrics(font).horizontalAdvance("0"))

    @property
    def cell_width(self) -> int:
        return self._cell_w

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        _fill_selection(painter, option, self._accent)
        model = index.model()
        if not isinstance(model, CommitGraphModel):
            return
        row = model.row_of(index)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 0, 0, 0)  # 左内边距防首格节点裁切
        cw, top, height = self._cell_w, rect.top(), rect.height()
        cy = top + height / 2
        for i, (ch, key) in enumerate(row.graph):
            color = self._colors.get(key, self._default) if key else self._default
            cx = rect.left() + i * cw + cw / 2
            if ch == " ":
                continue
            if ch == "*":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                radius = min(cw, height) * 0.32
                painter.drawEllipse(int(cx - radius), int(cy - radius),
                                    int(radius * 2), int(radius * 2))
                continue
            painter.setPen(QPen(color, 2))
            left, right = rect.left() + i * cw, rect.left() + (i + 1) * cw
            if ch == "|":
                painter.drawLine(int(cx), top, int(cx), top + height)
            elif ch == "/":
                painter.drawLine(left, top + height, right, top)
            elif ch == "\\":
                painter.drawLine(left, top, right, top + height)
            elif ch in ("-", "_"):
                painter.drawLine(left, int(cy), right, int(cy))
            # 其余字符（八爪鱼合并的 . 等）不绘制
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        model = index.model()
        row = model.row_of(index) if isinstance(model, CommitGraphModel) else None
        cells = len(row.graph) if row is not None else 1
        return QSize(max(1, cells) * self._cell_w,
                     _height_for(index, option.font))


class CommitColumnDelegate(QStyledItemDelegate):
    """提交列委托：hash（弱化）+ refs 胶囊徽标 + subject（HEAD 加粗）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badge_colors: dict[str, QColor] = {}
        self._hash_color = QColor("#767676")
        self._text_color = QColor("#1d1d1f")
        self._muted_color = QColor("#86868b")
        self._accent = QColor("#0765d4")

    def apply_theme(self, palette: dict) -> None:
        graph_pack = palette["git_graph"]
        self._badge_colors = {
            k: QColor(v) for k, v in graph_pack.items() if k != "hash_fg"
        }
        self._hash_color = QColor(graph_pack["hash_fg"])
        self._text_color = QColor(palette["text"])
        self._muted_color = QColor(palette["muted_text"])
        self._accent = QColor(palette["accent"])

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        _fill_selection(painter, option, self._accent)
        model = index.model()
        if not isinstance(model, CommitGraphModel):
            return
        row = model.row_of(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(6, 0, -4, 0)
        if row is None:  # 截断提示行
            painter.setPen(self._muted_color)
            painter.setFont(option.font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter,
                             model.data(index, Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        fm = QFontMetrics(option.font)
        x = rect.left()
        cy = rect.top() + rect.height() / 2
        # hash（弱化色等宽感，保持 UI 字体以省一次字体切换）
        painter.setFont(option.font)
        painter.setPen(self._hash_color)
        painter.drawText(x, rect.top(), fm.horizontalAdvance(row.commit),
                         rect.height(), Qt.AlignmentFlag.AlignVCenter, row.commit)
        x += fm.horizontalAdvance(row.commit) + _BADGE_GAP
        # refs 胶囊徽标
        badge_h = fm.height() + 2
        for badge in row.refs:
            color = self._badge_colors.get(badge.kind, self._muted_color)
            text_w = fm.horizontalAdvance(badge.label)
            w = text_w + _BADGE_HPAD * 2
            if x + w > rect.right():
                break  # 空间不足：徽标整体让位 subject（不再绘制后续徽标）
            bg = QColor(color)
            bg.setAlphaF(0.12)
            border = QColor(color)
            border.setAlphaF(0.45)
            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(
                x, int(cy - badge_h / 2), w, badge_h, badge_h / 2, badge_h / 2)
            painter.setPen(color)
            painter.drawText(
                x + _BADGE_HPAD, int(cy - badge_h / 2), text_w, badge_h,
                Qt.AlignmentFlag.AlignVCenter, badge.label)
            x += w + _BADGE_GAP
        # subject（HEAD 行加粗；省略号截断）
        font = QFont(option.font)
        if row.is_head:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._text_color)
        remaining = rect.right() - x
        if remaining > 0:
            elided = fm.elidedText(row.subject, Qt.TextElideMode.ElideRight,
                                   remaining)
            painter.drawText(x, rect.top(), remaining, rect.height(),
                             Qt.AlignmentFlag.AlignVCenter, elided)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _height_for(index, option.font))


def _fill_selection(painter: QPainter, option: QStyleOptionViewItem,
                    accent: QColor) -> None:
    """选中行底色：主题 accent 低透明度填充（自定义委托不走路径 qss item）。"""
    if option.state & QStyle.StateFlag.State_Selected:
        bg = QColor(accent)
        bg.setAlphaF(0.18)
        painter.fillRect(option.rect, bg)


class GitGraphView(QTreeView):
    """提交历史图列表：四列（图 | 提交 | 作者 | 时间）+ 双委托自绘。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = CommitGraphModel(self)
        self._graph_delegate = GraphColumnDelegate(self)
        self._commit_delegate = CommitColumnDelegate(self)
        self.setModel(self._model)
        self.setItemDelegateForColumn(COL_GRAPH, self._graph_delegate)
        self.setItemDelegateForColumn(COL_COMMIT, self._commit_delegate)
        cell_delegate = _RowHeightDelegate(self)
        self.setItemDelegateForColumn(COL_AUTHOR, cell_delegate)
        self.setItemDelegateForColumn(COL_DATE, cell_delegate)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(False)  # 连接行半高
        self.setAllColumnsShowFocus(True)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        header = self.header()
        header.setStretchLastSection(False)  # 默认 True 会拉伸时间列挤压提交列
        header.setSectionResizeMode(COL_GRAPH, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_COMMIT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AUTHOR, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        self.refresh_font()
        self.apply_theme(load_settings()[KEY_THEME])

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_graph(self, graph: CommitGraph) -> None:
        self._model.set_graph(graph)
        # 模型重置后 ResizeToContents 列不自动重算，显式触发（作者/时间列）
        self.header().resizeSections(QHeaderView.ResizeMode.ResizeToContents)
        self.setColumnWidth(
            COL_GRAPH,
            self._model.max_graph_cells * self._graph_delegate.cell_width + 10)

    # ------------------------------------------------------------------
    # 主题/字号链
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        palette = get_theme_palette(theme)
        self._graph_delegate.apply_theme(palette["terminal"], palette["accent"])
        self._commit_delegate.apply_theme(palette)
        self.viewport().update()

    def refresh_font(self) -> None:
        self._graph_delegate.refresh_font()
        if self._model.max_graph_cells:
            self.setColumnWidth(
                COL_GRAPH,
                self._model.max_graph_cells * self._graph_delegate.cell_width + 10)
        self.viewport().update()
