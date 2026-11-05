"""Git 提交历史图视图：摘要双列或完整四列表格。

- 图形列消费完整 commit id/parents 计算出的 GraphRow，三种边和节点线
  使用统一的 Qt 原生几何；内嵌与完整模式共享同一拓扑模型。
- 摘要模式显示固定宽拓扑与提交主题，完整模式显示 hash、refs 徽标与主题；
  摘要模式的完整元数据在整行 tooltip 中按需显示。
- 主题与字号重绘使用现有模型缓存；刷新按完整 id 恢复选中与横纵滚动值。
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
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeView,
    QWidget,
)

from core.git.log import CommitGraph, CommitRow
from gui.git_graph_renderer import GitGraphRenderer
from gui.settings import KEY_THEME
from gui.theme import get_theme_palette, load_settings

COL_GRAPH = 0
COL_COMMIT = 1
COL_AUTHOR = 2
COL_DATE = 3

_BADGE_HPAD = 7
_BADGE_GAP = 5
_REF_KIND_LABELS = {
    "head": "HEAD",
    "branch": "本地分支",
    "remote": "远程分支",
    "tag": "标签",
}


def _row_height(widget_font: QFont) -> int:
    return max(22, QFontMetrics(widget_font).height() + 4)


class _RowHeightDelegate(QStyledItemDelegate):
    """文本列使用默认渲染，并与图形列共享统一提交行高。"""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _row_height(option.font))


class CommitGraphModel(QAbstractTableModel):
    """提交图模型：每条提交一行，末尾可追加一条截断提示。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[CommitRow] = []
        self._hint: str | None = None
        self.max_graph_cells = 0

    def set_graph(self, graph: CommitGraph) -> None:
        self.beginResetModel()
        self._rows = list(graph.rows)
        self._hint = graph.truncated_hint
        max_lane = -1
        for row in self._rows:
            graph_row = row.graph_row
            max_lane = max(max_lane, graph_row.lane)
            for edge in graph_row.edges:
                max_lane = max(max_lane, edge.from_lane, edge.to_lane)
        self.max_graph_cells = max_lane + 1
        self.endResetModel()

    def row_of(self, index: QModelIndex) -> CommitRow | None:
        if 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]
        return None

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
            return row
        if role == Qt.ItemDataRole.ToolTipRole and row is not None:
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
                if row.abs_date:
                    return f"{row.rel_date} · {row.abs_date}"
                return row.rel_date
            return None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class GraphColumnDelegate(QStyledItemDelegate):
    """图形列委托：绘制共享整行状态，拓扑委托给共享渲染器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._renderer = GitGraphRenderer()
        self._accent = QColor("#0765d4")
        self._summary_mode = False
        self._hover_row = -1

    def apply_theme(
        self, terminal_pack: dict, accent: str, background: str,
    ) -> None:
        self._renderer.apply_theme(terminal_pack, background)
        self._accent = QColor(accent)

    def set_summary_mode(self, summary_mode: bool) -> None:
        self._summary_mode = summary_mode

    def set_hover_row(self, row: int) -> None:
        self._hover_row = row

    def refresh_font(self) -> None:
        self._renderer.refresh_font()

    @property
    def cell_width(self) -> int:
        return self._renderer.lane_width

    @property
    def lane_gap(self) -> int:
        return self._renderer.lane_width

    @property
    def node_diameter(self) -> int:
        return round(self._renderer.node_radius * 2)

    @property
    def graph_row_height(self) -> int:
        return self._renderer.row_height

    def content_width(self, lanes: int) -> int:
        return self._renderer.content_width(lanes)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        model = index.model()
        if not isinstance(model, CommitGraphModel):
            return
        hovered = self._summary_mode and index.row() == self._hover_row
        _fill_selection(painter, option, self._accent, hovered=hovered)
        row = model.row_of(index)
        if row is None:
            return
        self._renderer.paint(painter, option.rect, row.graph_row, row.is_head)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        model = index.model()
        row = model.row_of(index) if isinstance(model, CommitGraphModel) else None
        lanes = 1
        if row is not None:
            graph_row = row.graph_row
            lanes = max(
                [graph_row.lane + 1]
                + [max(edge.from_lane, edge.to_lane) + 1
                   for edge in graph_row.edges]
            )
        return QSize(self.content_width(lanes), self.graph_row_height)


class CommitColumnDelegate(QStyledItemDelegate):
    """提交列：内嵌模式仅画主题，完整模式画 hash、refs 徽标与主题。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badge_colors: dict[str, QColor] = {}
        self._hash_color = QColor("#767676")
        self._text_color = QColor("#1d1d1f")
        self._muted_color = QColor("#86868b")
        self._accent = QColor("#0765d4")
        self._summary_mode = False
        self._hover_row = -1

    def apply_theme(self, palette: dict) -> None:
        graph_pack = palette["git_graph"]
        self._badge_colors = {
            k: QColor(v) for k, v in graph_pack.items() if k != "hash_fg"
        }
        self._hash_color = QColor(graph_pack["hash_fg"])
        self._text_color = QColor(palette["text"])
        self._muted_color = QColor(palette["muted_text"])
        self._accent = QColor(palette["accent"])

    def set_summary_mode(self, summary_mode: bool) -> None:
        self._summary_mode = summary_mode

    def set_hover_row(self, row: int) -> None:
        self._hover_row = row

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        hovered = self._summary_mode and index.row() == self._hover_row
        _fill_selection(painter, option, self._accent, hovered=hovered)
        model = index.model()
        if not isinstance(model, CommitGraphModel):
            return
        row = model.row_of(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(6, 0, -4, 0)
        if row is None:
            painter.setPen(self._muted_color)
            painter.setFont(option.font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter,
                             model.data(index, Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return
        if self._summary_mode:
            font = QFont(option.font)
            if row.is_head:
                font.setBold(True)
            painter.setFont(font)
            painter.setPen(self._text_color)
            elided = QFontMetrics(font).elidedText(
                row.subject, Qt.TextElideMode.ElideRight, rect.width())
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided)
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
        return QSize(size.width(), _row_height(option.font))


def _fill_selection(painter: QPainter, option: QStyleOptionViewItem,
                    accent: QColor, hovered: bool = False) -> None:
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    if not selected and not hovered:
        return
    bg = QColor(accent)
    bg.setAlphaF(0.18 if selected else 0.08)
    painter.fillRect(option.rect, bg)


class GitGraphView(QTreeView):
    """提交历史图：完整四列表格，或带整行悬停的拓扑与主题摘要双列。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = CommitGraphModel(self)
        self._graph_delegate = GraphColumnDelegate(self)
        self._commit_delegate = CommitColumnDelegate(self)
        self._summary_mode = False
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
    def summary_mode(self) -> bool:
        return self._summary_mode

    def set_summary_mode(self, summary_mode: bool) -> None:
        self._summary_mode = summary_mode
        self._graph_delegate.set_summary_mode(summary_mode)
        self._commit_delegate.set_summary_mode(summary_mode)
        self._set_hover_row(-1)
        self.setMouseTracking(summary_mode)
        self.setHeaderHidden(summary_mode)
        self.setColumnHidden(COL_COMMIT, False)
        for column in (COL_AUTHOR, COL_DATE):
            self.setColumnHidden(column, summary_mode)
        self._resize_columns()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def _set_hover_row(self, row: int) -> None:
        self._graph_delegate.set_hover_row(row)
        self._commit_delegate.set_hover_row(row)

    def set_graph(self, graph: CommitGraph) -> None:
        current_row = self._model.row_of(self.currentIndex())
        selected_commit = current_row.commit_id if current_row is not None else ""
        vertical_value = self.verticalScrollBar().value()
        horizontal_value = self.horizontalScrollBar().value()
        self._set_hover_row(-1)
        self._model.set_graph(graph)
        self._resize_columns()
        if selected_commit:
            restored = False
            for row_index, row in enumerate(graph.rows):
                if row.commit_id == selected_commit:
                    column = COL_GRAPH if self._summary_mode else COL_COMMIT
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
        if not self._summary_mode:
            self.header().resizeSections(QHeaderView.ResizeMode.ResizeToContents)
        width = self._graph_delegate.content_width(self._model.max_graph_cells)
        if self.columnWidth(COL_GRAPH) != width:
            self.setColumnWidth(COL_GRAPH, width)

    def viewportEvent(self, event) -> bool:
        if self._summary_mode and event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            tooltip = self._model.data(index, Qt.ItemDataRole.ToolTipRole)
            if not tooltip:
                QToolTip.hideText()
                event.accept()
                return True
        if self._summary_mode and event.type() in (
                QEvent.Type.MouseMove, QEvent.Type.Leave):
            index = (
                self.indexAt(event.position().toPoint())
                if event.type() == QEvent.Type.MouseMove else QModelIndex()
            )
            row = index.row() if self._model.row_of(index) is not None else -1
            if row != self._graph_delegate._hover_row:
                self._set_hover_row(row)
                self.viewport().update()
        return super().viewportEvent(event)

    def apply_theme(self, theme: str) -> None:
        palette = get_theme_palette(theme)
        self._graph_delegate.apply_theme(
            palette["terminal"], palette["accent"], palette["card_bg"])
        self._commit_delegate.apply_theme(palette)
        self.viewport().update()

    def refresh_font(self) -> None:
        self._graph_delegate.refresh_font()
        self._resize_columns()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()
