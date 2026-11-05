"""提交图 graph-only 布局、复杂图线像素、tooltip 与刷新状态测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QHelpEvent, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QToolTip

from core.git.log import CommitGraph, CommitRow, RefBadge
from gui.git_graph_view import (
    COL_AUTHOR,
    COL_COMMIT,
    COL_DATE,
    COL_GRAPH,
    GitGraphView,
    _row_height,
)
from gui.theme import get_theme_palette


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _commit(number: int, *, head: bool = False, cells: int = 1) -> CommitRow:
    graph = (("*", "red"),) + tuple((" ", None) for _ in range(cells - 1))
    refs = (
        RefBadge("head", "HEAD"),
        RefBadge("branch", "main"),
        RefBadge("remote", "origin/main"),
        RefBadge("tag", "v1.0"),
    ) if head else ()
    return CommitRow(
        graph=graph,
        is_head=head,
        commit=f"c{number:03d}",
        subject=f"提交主题 {number}",
        author="测试作者",
        rel_date="3 小时前",
        abs_date="2026-11-06 04:48",
        refs=refs,
    )


def _graph(count: int = 3, *, cells: int = 1) -> CommitGraph:
    return CommitGraph(
        rows=tuple(_commit(i, head=i == 0, cells=cells) for i in range(count)),
        truncated_hint=None,
    )


def _paint_row(view: GitGraphView, row: int = 0, *, hover: bool = False) -> QImage:
    index = view._model.index(row, COL_GRAPH)
    height = view.rowHeight(index)
    width = max(160, view._graph_delegate.content_width(
        view._model.max_graph_cells))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    option.font = view.font()
    view._graph_delegate.set_hover_row(row if hover else -1)
    painter = QPainter(image)
    view._graph_delegate.paint(painter, option, index)
    painter.end()
    view._graph_delegate.set_hover_row(-1)
    return image


def _has_ink(image: QImage, x: int, y: int, radius: int = 2) -> bool:
    return any(
        image.pixelColor(px, py) != QColor(Qt.GlobalColor.white)
        for py in range(max(0, y - radius), min(image.height(), y + radius + 1))
        for px in range(max(0, x - radius), min(image.width(), x + radius + 1))
    )


def test_graph_only_hides_all_text_columns_and_tooltip_preserves_metadata(qapp):
    view = GitGraphView()
    view.set_graph(_graph())
    assert not view.header().isHidden()
    for column in (COL_COMMIT, COL_AUTHOR, COL_DATE):
        assert not view.isColumnHidden(column)

    view.set_graph_only(True)
    assert view.graph_only
    assert view.header().isHidden()
    assert not view.isColumnHidden(COL_GRAPH)
    for column in (COL_COMMIT, COL_AUTHOR, COL_DATE):
        assert view.isColumnHidden(column)

    tooltip = view._model.data(view._model.index(0, COL_GRAPH), Qt.ItemDataRole.ToolTipRole)
    for text in (
        "c000", "提交主题 0", "测试作者", "3 小时前", "2026-11-06 04:48",
        "HEAD：HEAD", "本地分支：main", "远程分支：origin/main", "标签：v1.0",
    ):
        assert text in tooltip
    view.deleteLater()


def test_real_tooltip_event_uses_graph_row_and_ignores_connector(qapp):
    graph = CommitGraph(rows=(
        _commit(0, head=True),
        CommitRow(graph=(("|", None),), is_connector=True),
    ), truncated_hint="仅显示最近 500 条")
    view = GitGraphView()
    view.set_graph_only(True)
    view.set_graph(graph)
    view.resize(240, 120)
    view.show()
    qapp.processEvents()

    first_tooltip = ""
    for row, expected in ((0, True), (1, False), (2, False)):
        QToolTip.hideText()
        qapp.processEvents()
        rect = view.visualRect(view._model.index(row, COL_GRAPH))
        local = rect.center()
        event = QHelpEvent(QEvent.Type.ToolTip, local, QPoint(100, 100))
        QApplication.sendEvent(view.viewport(), event)
        qapp.processEvents()
        index = view._model.index(row, COL_GRAPH)
        if expected:
            first_tooltip = QToolTip.text()
            assert "提交主题 0" in first_tooltip
        else:
            assert event.isAccepted()
            assert view._model.data(index, Qt.ItemDataRole.ToolTipRole) is None
    assert "提交主题 0" in first_tooltip
    QToolTip.hideText()
    view.close()
    view.deleteLater()


def test_graph_only_geometry_has_minimums_and_scales_with_font(qapp):
    original_size = qapp.font().pointSizeF()
    view = GitGraphView()
    view.set_graph_only(True)
    view.set_graph(_graph())
    view.show()
    qapp.processEvents()
    delegate = view._graph_delegate
    assert delegate.lane_gap >= 18
    assert delegate.node_diameter >= 8
    assert delegate.graph_row_height >= 28
    assert view.rowHeight(view._model.index(0, COL_GRAPH)) == delegate.graph_row_height
    original = (delegate.lane_gap, delegate.node_diameter, delegate.graph_row_height)
    try:
        font = qapp.font()
        font.setPointSizeF(original_size + 5)
        qapp.setFont(font)
        view.refresh_font()
        qapp.processEvents()
        assert delegate.lane_gap > original[0]
        assert delegate.node_diameter >= original[1]
        assert delegate.graph_row_height > original[2]
    finally:
        font = qapp.font()
        font.setPointSizeF(original_size)
        qapp.setFont(font)
        view.refresh_font()
        view.close()
        view.deleteLater()


def test_graph_width_tracks_lanes_and_wide_graph_has_horizontal_scroll(qapp):
    view = GitGraphView()
    view.set_graph_only(True)
    view.resize(120, 100)
    view.set_graph(_graph(cells=20))
    view.show()
    qapp.processEvents()
    content_width = view._graph_delegate.content_width(20)
    assert view.columnWidth(COL_GRAPH) == max(content_width, view.viewport().width())
    assert view.columnWidth(COL_GRAPH) > view.viewport().width()
    assert view.horizontalScrollBar().maximum() > 0
    view.close()
    view.deleteLater()


def test_graph_only_column_uses_viewport_without_stretching_lanes(qapp):
    view = GitGraphView()
    view.set_graph_only(True)
    view.set_graph(_graph())
    view.resize(440, 120)
    view.show()
    qapp.processEvents()
    lane_gap = view._graph_delegate.lane_gap
    assert view.columnWidth(COL_GRAPH) == view.viewport().width()
    assert view._graph_delegate.lane_gap == lane_gap
    view.resize(240, 120)
    qapp.processEvents()
    assert view.columnWidth(COL_GRAPH) == max(
        view._graph_delegate.content_width(1), view.viewport().width())
    assert view._graph_delegate.lane_gap == lane_gap
    view.close()
    view.deleteLater()


@pytest.mark.parametrize("char", ["*", "|", "-", "_", ".", "/", "\\"])
def test_all_graph_primitives_emit_pixels(qapp, char):
    graph = CommitGraph(rows=(CommitRow(graph=(
        (" ", None), (char, "red"),
    ), is_connector=char != "*"),), truncated_hint=None)
    view = GitGraphView()
    view.set_graph_only(True)
    view.set_graph(graph)
    view.show()
    qapp.processEvents()
    image = _paint_row(view)
    assert any(
        image.pixelColor(x, y) != QColor(Qt.GlobalColor.white)
        for y in range(image.height())
        for x in range(image.width())
    )
    view.close()
    view.deleteLater()


@pytest.mark.parametrize(("char", "start_y", "end_y"), [
    ("/", "bottom", "top"),
    ("\\", "top", "bottom"),
])
def test_curve_endpoints_land_on_adjacent_lane_centers(qapp, char, start_y, end_y):
    view = GitGraphView()
    view.set_graph_only(True)
    view.set_graph(CommitGraph(rows=(CommitRow(graph=(
        (" ", None), (char, "red"),
    )),), truncated_hint=None))
    view.show()
    qapp.processEvents()
    image = _paint_row(view)
    lane = view._graph_delegate.lane_gap
    left_center = 8 + lane // 2
    right_center = left_center + lane
    ys = {"top": 1, "bottom": image.height() - 2}
    assert _has_ink(image, left_center, ys[start_y])
    assert _has_ink(image, right_center, ys[end_y])
    view.close()
    view.deleteLater()


def test_connector_is_full_height_only_in_graph_only(qapp):
    graph = CommitGraph(rows=(CommitRow(
        graph=(("|", None),), is_connector=True),), truncated_hint=None)
    view = GitGraphView()
    view.set_graph(graph)
    view.show()
    qapp.processEvents()
    index = view._model.index(0, COL_GRAPH)
    assert view.rowHeight(index) == _row_height(view.font()) // 2
    view.set_graph_only(True)
    qapp.processEvents()
    assert view.rowHeight(index) == view._graph_delegate.graph_row_height
    view.close()
    view.deleteLater()


def test_head_and_hover_states_emit_accent_pixels(qapp):
    view = GitGraphView()
    view.apply_theme("cloud")
    view.set_graph_only(True)
    view.set_graph(_graph())
    view.show()
    qapp.processEvents()
    normal = _paint_row(view)
    head = _paint_row(view)
    accent = QColor(get_theme_palette("cloud")["accent"])
    assert any(
        head.pixelColor(x, y) == accent
        for y in range(head.height()) for x in range(head.width())
    )
    hovered = _paint_row(view, hover=True)
    assert sum(
        normal.pixel(x, y) != hovered.pixel(x, y)
        for y in range(normal.height()) for x in range(normal.width())
    ) > 0
    view.close()
    view.deleteLater()


def test_refresh_restores_selection_and_both_scroll_positions(qapp):
    view = GitGraphView()
    view.set_graph_only(True)
    view.resize(160, 100)
    view.set_graph(_graph(40, cells=20))
    view.show()
    qapp.processEvents()
    selected = view._model.index(20, COL_GRAPH)
    view.setCurrentIndex(selected)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum() // 2)
    qapp.processEvents()
    vertical_value = view.verticalScrollBar().value()
    horizontal_value = view.horizontalScrollBar().value()
    assert vertical_value > 0 and horizontal_value > 0

    view.set_graph(_graph(40, cells=20))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is not None and current_row.commit == "c020"
    assert view.verticalScrollBar().value() == vertical_value
    assert view.horizontalScrollBar().value() == horizontal_value

    view.set_graph(_graph(1, cells=20))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is None or current_row.commit != "c020"
    assert view.horizontalScrollBar().value() <= view.horizontalScrollBar().maximum()
    view.close()
    view.deleteLater()
