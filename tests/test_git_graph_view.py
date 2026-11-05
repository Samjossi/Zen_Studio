"""提交图结构化布局、三类边像素、tooltip 与刷新状态测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QHelpEvent, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QToolTip

from core.git.graph import GraphEdge, GraphRow
from core.git.log import CommitGraph, CommitRow, RefBadge
from gui.git_graph_view import (
    COL_AUTHOR,
    COL_COMMIT,
    COL_DATE,
    COL_GRAPH,
    GitGraphView,
)
from gui.theme import get_theme_palette


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _commit(
    number: int,
    *,
    head: bool = False,
    graph_row: GraphRow | None = None,
) -> CommitRow:
    refs = (
        RefBadge("head", "HEAD"),
        RefBadge("branch", "main"),
        RefBadge("remote", "origin/main"),
        RefBadge("tag", "v1.0"),
    ) if head else ()
    return CommitRow(
        graph_row=graph_row or GraphRow(
            lane=0, color=0, edges=(), has_continuation=False, has_top_line=False),
        commit_id=f"full-{number:03d}",
        is_head=head,
        commit="same-short-hash",
        subject=f"提交主题 {number}",
        author="测试作者",
        rel_date="3 小时前",
        abs_date="2026-11-06 04:48",
        refs=refs,
    )


def _graph(count: int = 3, *, lanes: int = 1) -> CommitGraph:
    return CommitGraph(
        rows=tuple(
            _commit(
                index,
                head=index == 0,
                graph_row=GraphRow(
                    lane=lanes - 1,
                    color=(lanes - 1) % 8,
                    edges=(),
                    has_continuation=index < count - 1,
                    has_top_line=index > 0,
                ),
            )
            for index in range(count)
        ),
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


def _has_ink(image: QImage, x: float, y: float, radius: int = 2) -> bool:
    return any(
        image.pixelColor(px, py) != QColor(Qt.GlobalColor.white)
        for py in range(max(0, round(y) - radius), min(image.height(), round(y) + radius + 1))
        for px in range(max(0, round(x) - radius), min(image.width(), round(x) + radius + 1))
    )


def _show_graph(qapp, graph: CommitGraph, *, graph_only: bool = True) -> GitGraphView:
    view = GitGraphView()
    view.set_graph_only(graph_only)
    view.set_graph(graph)
    view.show()
    qapp.processEvents()
    return view


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
        "same-short-hash", "提交主题 0", "测试作者", "3 小时前", "2026-11-06 04:48",
        "HEAD：HEAD", "本地分支：main", "远程分支：origin/main", "标签：v1.0",
    ):
        assert text in tooltip
    view.deleteLater()


def test_real_tooltip_event_uses_commit_and_ignores_truncation_hint(qapp):
    graph = CommitGraph(
        rows=(_commit(0, head=True),),
        truncated_hint="仅显示最近 500 条",
    )
    view = _show_graph(qapp, graph)
    view.resize(240, 120)
    qapp.processEvents()

    first_tooltip = ""
    for row, expected in ((0, True), (1, False)):
        QToolTip.hideText()
        qapp.processEvents()
        rect = view.visualRect(view._model.index(row, COL_GRAPH))
        event = QHelpEvent(QEvent.Type.ToolTip, rect.center(), QPoint(100, 100))
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


def test_geometry_matches_reference_and_scales_with_font(qapp):
    original_size = qapp.font().pointSizeF()
    view = _show_graph(qapp, _graph())
    delegate = view._graph_delegate
    assert delegate.lane_gap == 22
    assert delegate.node_diameter == 10
    assert delegate.graph_row_height == 22
    assert view.rowHeight(view._model.index(0, COL_GRAPH)) == 22
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
    view.set_graph(_graph(lanes=20))
    view.show()
    qapp.processEvents()
    content_width = view._graph_delegate.content_width(20)
    assert view.columnWidth(COL_GRAPH) == max(content_width, view.viewport().width())
    assert view.columnWidth(COL_GRAPH) > view.viewport().width()
    assert view.horizontalScrollBar().maximum() > 0
    view.close()
    view.deleteLater()


def test_graph_only_column_uses_viewport_without_stretching_lanes(qapp):
    view = _show_graph(qapp, _graph())
    view.resize(440, 120)
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


@pytest.mark.parametrize("kind", ["pass-through", "branch-out", "merge-in"])
def test_all_edge_types_emit_pixels_at_reference_endpoints(qapp, kind):
    if kind == "pass-through":
        row = GraphRow(
            lane=1, color=1,
            edges=(GraphEdge(0, 0, 0, kind),),
            has_continuation=False, has_top_line=False,
        )
        start = (11, 1)
        end = (11, 21)
    elif kind == "branch-out":
        row = GraphRow(
            lane=0, color=0,
            edges=(GraphEdge(0, 1, 1, kind),),
            has_continuation=False, has_top_line=False,
        )
        start = (11, 11)
        end = (33, 21)
    else:
        row = GraphRow(
            lane=0, color=0,
            edges=(GraphEdge(1, 0, 1, kind),),
            has_continuation=False, has_top_line=False,
        )
        start = (33, 1)
        end = (11, 11)
    view = _show_graph(qapp, CommitGraph(rows=(_commit(0, graph_row=row),), truncated_hint=None))
    image = _paint_row(view)
    assert _has_ink(image, *start)
    assert _has_ink(image, *end)
    view.close()
    view.deleteLater()


def test_node_top_and_bottom_segments_follow_graph_flags(qapp):
    connected = GraphRow(
        lane=0, color=0, edges=(), has_continuation=True, has_top_line=True)
    isolated = GraphRow(
        lane=0, color=0, edges=(), has_continuation=False, has_top_line=False)
    view = _show_graph(qapp, CommitGraph(rows=(
        _commit(0, graph_row=connected),
        _commit(1, graph_row=isolated),
    ), truncated_hint=None))
    connected_image = _paint_row(view, 0)
    isolated_image = _paint_row(view, 1)
    assert _has_ink(connected_image, 11, 1)
    assert _has_ink(connected_image, 11, connected_image.height() - 2)
    assert not _has_ink(isolated_image, 11, 1)
    assert not _has_ink(isolated_image, 11, isolated_image.height() - 2)
    view.close()
    view.deleteLater()


def test_nodes_cover_line_endpoints_and_head_uses_background_inner_dot(qapp):
    palette = get_theme_palette("cloud")
    continuing = GraphRow(
        lane=0, color=0, edges=(), has_continuation=True, has_top_line=True)
    graph = CommitGraph(rows=(
        _commit(0, head=True, graph_row=continuing),
        _commit(1, graph_row=continuing),
    ), truncated_hint=None)
    view = _show_graph(qapp, graph)
    view.apply_theme("cloud")
    head = _paint_row(view, 0)
    normal = _paint_row(view, 1)
    lane_color = QColor(palette["terminal"]["blue"])
    assert head.pixelColor(11, head.height() // 2) == QColor(palette["card_bg"])
    assert normal.pixelColor(11, normal.height() // 2) == lane_color
    assert _has_ink(head, 11, 1)
    view.close()
    view.deleteLater()


def test_hover_state_changes_full_row_background(qapp):
    view = _show_graph(qapp, _graph())
    normal = _paint_row(view)
    hovered = _paint_row(view, hover=True)
    assert sum(
        normal.pixel(x, y) != hovered.pixel(x, y)
        for y in range(normal.height()) for x in range(normal.width())
    ) > 0
    view.close()
    view.deleteLater()


def test_full_and_graph_only_modes_share_row_geometry(qapp):
    graph = _graph(2, lanes=3)
    view = _show_graph(qapp, graph, graph_only=False)
    full_height = view.rowHeight(view._model.index(0, COL_GRAPH))
    full_width = view._graph_delegate.content_width(3)
    view.set_graph_only(True)
    qapp.processEvents()
    assert view.rowHeight(view._model.index(0, COL_GRAPH)) == full_height
    assert view._graph_delegate.content_width(3) == full_width
    view.close()
    view.deleteLater()


def test_refresh_restores_full_identity_and_both_scroll_positions(qapp):
    view = GitGraphView()
    view.set_graph_only(True)
    view.resize(160, 100)
    view.set_graph(_graph(40, lanes=20))
    view.show()
    qapp.processEvents()
    view.setCurrentIndex(view._model.index(20, COL_GRAPH))
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum() // 2)
    qapp.processEvents()
    vertical_value = view.verticalScrollBar().value()
    horizontal_value = view.horizontalScrollBar().value()
    assert vertical_value > 0 and horizontal_value > 0

    view.set_graph(_graph(40, lanes=20))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is not None and current_row.commit_id == "full-020"
    assert view.verticalScrollBar().value() == vertical_value
    assert view.horizontalScrollBar().value() == horizontal_value

    view.set_graph(_graph(1, lanes=20))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is None or current_row.commit_id != "full-020"
    assert view.horizontalScrollBar().value() <= view.horizontalScrollBar().maximum()
    view.close()
    view.deleteLater()
