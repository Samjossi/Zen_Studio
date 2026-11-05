"""Git 提交历史图视图：完整四列表格或 graph-only 单列拓扑图。

- 图形列消费 git --graph 的泳道格与 ANSI 色键，斜线使用贝塞尔曲线；
  graph-only 采用独立泳道度量，完整模式保留半高连接行。
- 完整模式显示 hash、refs 徽标与主题；graph-only 的完整元数据只在
  整行 tooltip 中按需显示。
- 主题与字号重绘使用现有模型缓存；刷新按 hash 恢复选中与横纵滚动值。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeView,
    QWidget,
)

from core.git.log import CommitGraph, CommitRow
from gui.settings import KEY_THEME
from gui.theme import get_mono_family, get_theme_palette, load_settings

COL_GRAPH = 0
COL_COMMIT = 1
COL_AUTHOR = 2
COL_DATE = 3

SUPPORTED_GRAPH_CHARS = frozenset(" *|\\/-_.")

_ROW_VPAD = 5
_GRAPH_ONLY_VPAD = 6
_GRAPH_HPAD = 8
_BADGE_HPAD = 7
_BADGE_GAP = 5
_REF_KIND_LABELS = {
    "head": "HEAD",
    "branch": "本地分支",
    "remote": "远程分支",
    "tag": "标签",
}


def _row_height(widget_font: QFont) -> int:
    return QFontMetrics(widget_font).height() + _ROW_VPAD * 2


def _height_for(index: QModelIndex, font: QFont) -> int:
    """完整模式的连接行半高；graph-only 由图形委托统一提供行高。"""
    model = index.model()
    row = model.row_of(index) if isinstance(model, CommitGraphModel) else None
    base = _row_height(font)
    return base // 2 if (row is not None and row.is_connector) else base


def is_supported_graph(graph: CommitGraph) -> bool:
    """未知图线字符交给富文本降级，避免结构化视图静默断线。"""
    return all(
        ch in SUPPORTED_GRAPH_CHARS
        for row in graph.rows
        for ch, _color in row.graph
    )


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
        if role == Qt.ItemDataRole.ToolTipRole and row is not None and not row.is_connector:
            times = " · ".join(part for part in (row.rel_date, row.abs_date) if part)
            refs = "；".join(
                f"{_REF_KIND_LABELS.get(badge.kind, badge.kind)}：{badge.label}"
                for badge in row.refs
            ) or "无"
            return (
                f"提交：{row.commit}\n主题：{row.subject}\n作者：{row.author}\n"
                f"时间：{times}\n引用：{refs}"
            )
        if role == Qt.ItemDataRole.DisplayRole:
            if row is None:
                return self._hint if index.column() in (COL_GRAPH, COL_COMMIT) else None
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
    """图形列委托：统一泳道度量，颜色取 TERMINAL_PACK 映射。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors: dict[str, QColor] = {}
        self._default = QColor("#1a1a1a")
        self._accent = QColor("#0765d4")
        self._cell_w = 8
        self._row_h = _row_height(QFont(get_mono_family()))
        self._node_radius = 2.5
        self._line_width = 2
        self._graph_only = False
        self._hover_row = -1

    def apply_theme(self, terminal_pack: dict, accent: str) -> None:
        self._colors = {
            k: QColor(v) for k, v in terminal_pack.items()
            if k not in ("find_bg", "find_cur") and not k.startswith("default_")
        }
        self._default = QColor(terminal_pack["default_fg"])
        self._accent = QColor(accent)

    def set_graph_only(self, graph_only: bool) -> None:
        self._graph_only = graph_only
        if not graph_only:
            self._hover_row = -1
        self.refresh_font()

    def set_hover_row(self, row: int) -> None:
        self._hover_row = row

    def refresh_font(self) -> None:
        font = QFont(get_mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        metrics = QFontMetrics(font)
        if self._graph_only:
            self._cell_w = max(18, metrics.horizontalAdvance("0") + 9)
            self._row_h = max(28, metrics.height() + _GRAPH_ONLY_VPAD * 2)
            self._node_radius = min(7.0, max(4.0, metrics.height() * 0.27))
            self._line_width = min(3, max(2, round(self._cell_w / 9)))
        else:
            self._cell_w = max(4, metrics.horizontalAdvance("0"))
            self._row_h = _row_height(font)
            self._node_radius = 2.5
            self._line_width = 2

    @property
    def cell_width(self) -> int:
        return self._cell_w

    @property
    def lane_gap(self) -> int:
        return self._cell_w

    @property
    def node_diameter(self) -> int:
        return round(self._node_radius * 2)

    @property
    def graph_row_height(self) -> int:
        return self._row_h

    def content_width(self, cells: int) -> int:
        cells = max(1, cells)
        if self._graph_only:
            return cells * self._cell_w + _GRAPH_HPAD * 2
        return cells * self._cell_w + 10

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        model = index.model()
        if not isinstance(model, CommitGraphModel):
            return
        hovered = self._graph_only and index.row() == self._hover_row
        _fill_selection(painter, option, self._accent, hovered=hovered)
        row = model.row_of(index)
        if row is None:
            if self._graph_only:
                painter.save()
                painter.setPen(self._default)
                painter.setFont(option.font)
                painter.drawText(
                    option.rect.adjusted(_GRAPH_HPAD, 0, -_GRAPH_HPAD, 0),
                    Qt.AlignmentFlag.AlignVCenter,
                    model.data(index, Qt.ItemDataRole.DisplayRole) or "",
                )
                painter.restore()
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        padding = _GRAPH_HPAD if self._graph_only else 4
        rect = option.rect.adjusted(padding, 0, 0, 0)
        cw, top, height = self._cell_w, rect.top(), rect.height()
        cy = top + height / 2
        for i, (ch, key) in enumerate(row.graph):
            color = self._colors.get(key, self._default) if key else self._default
            cx = rect.left() + i * cw + cw / 2
            if ch == " ":
                continue
            if ch == "*":
                radius = (
                    self._node_radius if self._graph_only
                    else min(cw, height) * 0.32
                )
                if self._graph_only and (row.is_head or hovered):
                    painter.setPen(QPen(self._accent, self._line_width))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    outer = radius + 2
                    painter.drawEllipse(
                        int(cx - outer), int(cy - outer),
                        int(outer * 2), int(outer * 2),
                    )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(
                    int(cx - radius), int(cy - radius),
                    int(radius * 2), int(radius * 2),
                )
                continue
            painter.setPen(QPen(color, self._line_width))
            if ch == "|":
                painter.drawLine(int(cx), top, int(cx), top + height)
                continue
            if self._graph_only:
                left, right = cx - cw, cx
            else:
                left = rect.left() + i * cw
                right = left + cw
            if ch in ("/", "\\"):
                path = QPainterPath()
                if ch == "/":
                    path.moveTo(left, top + height)
                    path.cubicTo(left, cy, right, cy, right, top)
                else:
                    path.moveTo(left, top)
                    path.cubicTo(left, cy, right, cy, right, top + height)
                painter.drawPath(path)
            elif ch in ("-", "_", "."):
                painter.drawLine(
                    int(round(left)), int(cy), int(round(right)), int(cy))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        model = index.model()
        row = model.row_of(index) if isinstance(model, CommitGraphModel) else None
        cells = len(row.graph) if row is not None else 1
        height = (
            self._row_h if self._graph_only
            else _height_for(index, option.font)
        )
        return QSize(self.content_width(cells), height)


class CommitColumnDelegate(QStyledItemDelegate):
    """完整弹窗提交列：hash、refs 徽标与主题。"""

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
        painter.setFont(option.font)
        painter.setPen(self._hash_color)
        painter.drawText(x, rect.top(), fm.horizontalAdvance(row.commit),
                         rect.height(), Qt.AlignmentFlag.AlignVCenter, row.commit)
        x += fm.horizontalAdvance(row.commit) + _BADGE_GAP

        badge_h = fm.height() + 2
        for badge in row.refs:
            color = self._badge_colors.get(badge.kind, self._muted_color)
            text_w = fm.horizontalAdvance(badge.label)
            w = text_w + _BADGE_HPAD * 2
            if x + w > rect.right():
                break
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
                x + _BADGE_HPAD, int(cy - badge_h / 2), w - _BADGE_HPAD * 2, badge_h,
                Qt.AlignmentFlag.AlignVCenter, badge.label)
            x += w + _BADGE_GAP
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
                    accent: QColor, hovered: bool = False) -> None:
    """选中/悬停行使用主题 accent 弱色背景，覆盖整个图形列。"""
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    if not selected and not hovered:
        return
    bg = QColor(accent)
    bg.setAlphaF(0.18 if selected else 0.08)
    painter.fillRect(option.rect, bg)


class GitGraphView(QTreeView):
    """提交历史图：完整四列表格，或带整行悬停的 graph-only 拓扑图。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = CommitGraphModel(self)
        self._graph_delegate = GraphColumnDelegate(self)
        self._commit_delegate = CommitColumnDelegate(self)
        self._graph_only = False
        self.setModel(self._model)
        self.setItemDelegateForColumn(COL_GRAPH, self._graph_delegate)
        self.setItemDelegateForColumn(COL_COMMIT, self._commit_delegate)
        cell_delegate = _RowHeightDelegate(self)
        self.setItemDelegateForColumn(COL_AUTHOR, cell_delegate)
        self.setItemDelegateForColumn(COL_DATE, cell_delegate)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        header = self.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(1)
        header.setSectionResizeMode(COL_GRAPH, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_COMMIT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AUTHOR, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        self.refresh_font()
        self.apply_theme(load_settings()[KEY_THEME])

    @property
    def graph_only(self) -> bool:
        return self._graph_only

    def set_graph_only(self, graph_only: bool) -> None:
        self._graph_only = graph_only
        self._graph_delegate.set_graph_only(graph_only)
        self.setMouseTracking(graph_only)
        self.setHeaderHidden(graph_only)
        for column in (COL_COMMIT, COL_AUTHOR, COL_DATE):
            self.setColumnHidden(column, graph_only)
        self._resize_columns()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_graph(self, graph: CommitGraph) -> None:
        current_row = self._model.row_of(self.currentIndex())
        selected_commit = current_row.commit if current_row is not None else ""
        vertical_value = self.verticalScrollBar().value()
        horizontal_value = self.horizontalScrollBar().value()
        self._graph_delegate.set_hover_row(-1)
        self._model.set_graph(graph)
        self._resize_columns()
        if selected_commit:
            restored = False
            for row_index, row in enumerate(graph.rows):
                if row.commit == selected_commit:
                    column = COL_GRAPH if self._graph_only else COL_COMMIT
                    index = self._model.index(row_index, column)
                    self.selectionModel().setCurrentIndex(
                        index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    restored = True
                    break
            if not restored:
                self.selectionModel().clearSelection()
                self.selectionModel().setCurrentIndex(
                    QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.verticalScrollBar().setValue(vertical_value)
        self.horizontalScrollBar().setValue(horizontal_value)

    def _resize_columns(self) -> None:
        if not self._graph_only:
            self.header().resizeSections(QHeaderView.ResizeMode.ResizeToContents)
        content_width = self._graph_delegate.content_width(
            self._model.max_graph_cells)
        width = (
            max(content_width, self.viewport().width())
            if self._graph_only else content_width
        )
        if self.columnWidth(COL_GRAPH) != width:
            self.setColumnWidth(COL_GRAPH, width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._graph_only:
            self._resize_columns()

    def viewportEvent(self, event) -> bool:
        if self._graph_only and event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            tooltip = self._model.data(index, Qt.ItemDataRole.ToolTipRole)
            if not tooltip:
                QToolTip.hideText()
                event.accept()
                return True
        if self._graph_only and event.type() in (
                QEvent.Type.MouseMove, QEvent.Type.Leave):
            row = (
                self.indexAt(event.position().toPoint()).row()
                if event.type() == QEvent.Type.MouseMove else -1
            )
            if row != self._graph_delegate._hover_row:
                self._graph_delegate.set_hover_row(row)
                self.viewport().update()
        return super().viewportEvent(event)

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
        self._resize_columns()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()
