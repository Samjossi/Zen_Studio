"""提交图紧凑布局、曲线像素与刷新状态恢复测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from core.git.log import CommitGraph, CommitRow, RefBadge
from gui.git_graph_view import (
    COL_AUTHOR,
    COL_COMMIT,
    COL_DATE,
    COL_GRAPH,
    GitGraphView,
    GraphColumnDelegate,
    _row_height,
)
from gui.theme import get_theme_palette


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _commit(number: int, *, head: bool = False) -> CommitRow:
    return CommitRow(
        graph=(("*", "red"),),
        is_head=head,
        commit=f"c{number:03d}",
        subject=f"提交主题 {number}",
        author="测试作者",
        rel_date="3 小时前",
        abs_date="2026-11-06 04:48",
        refs=(RefBadge("head", "HEAD"), RefBadge("branch", "main")) if head else (),
    )


def _graph(count: int = 3) -> CommitGraph:
    return CommitGraph(rows=tuple(_commit(i, head=i == 0) for i in range(count)), truncated_hint=None)


def test_compact_view_hides_metadata_columns_and_tooltip_preserves_them(qapp):
    view = GitGraphView()
    view.set_graph(_graph())
    assert not view.header().isHidden()
    assert not view.isColumnHidden(COL_AUTHOR)
    assert not view.isColumnHidden(COL_DATE)

    view.set_compact(True)
    assert view.header().isHidden()
    assert view.isColumnHidden(COL_AUTHOR)
    assert view.isColumnHidden(COL_DATE)
    assert not view.isColumnHidden(COL_GRAPH)
    assert not view.isColumnHidden(COL_COMMIT)
    assert view._commit_delegate._compact

    tooltip = view._model.data(view._model.index(0, COL_COMMIT), Qt.ItemDataRole.ToolTipRole)
    for text in ("c000", "提交主题 0", "测试作者", "3 小时前", "2026-11-06 04:48", "HEAD", "main"):
        assert text in tooltip
    view.deleteLater()


def test_graph_width_tracks_maximum_lane_cells(qapp):
    view = GitGraphView()
    graph = CommitGraph(rows=(
        _commit(0),
        CommitRow(graph=(("|", None), (" ", None), ("*", "green"))),
    ), truncated_hint=None)
    view.set_graph(graph)
    assert view.columnWidth(COL_GRAPH) == 3 * view._graph_delegate.cell_width + 10
    view.deleteLater()


@pytest.mark.parametrize(("char", "connector"), [("/", False), ("\\", True)])
def test_curve_and_connector_rows_emit_pixels(qapp, char, connector):
    model_graph = CommitGraph(rows=(
        CommitRow(graph=((char, "red"),), is_connector=connector),
    ), truncated_hint=None)
    view = GitGraphView()
    view.set_graph(model_graph)
    view.show()
    qapp.processEvents()
    index = view._model.index(0, COL_GRAPH)
    height = _row_height(view.font()) // 2 if connector else _row_height(view.font())
    assert view.rowHeight(index) == height

    image = QImage(40, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 40, height)
    option.font = view.font()
    painter = QPainter(image)
    delegate = GraphColumnDelegate()
    palette = get_theme_palette("cloud")
    delegate.apply_theme(palette["terminal"], palette["accent"])
    delegate.paint(painter, option, index)
    painter.end()

    colored = sum(
        image.pixelColor(x, y) != Qt.GlobalColor.white
        for y in range(height)
        for x in range(4, 20)
    )
    assert colored > 0
    view.deleteLater()


def test_refresh_restores_selection_and_scroll_and_tolerates_removed_commit(qapp):
    view = GitGraphView()
    view.resize(240, 100)
    view.set_graph(_graph(40))
    view.show()
    qapp.processEvents()
    selected = view._model.index(20, COL_COMMIT)
    view.setCurrentIndex(selected)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    qapp.processEvents()
    scroll_value = view.verticalScrollBar().value()
    assert scroll_value > 0

    view.set_graph(_graph(40))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is not None and current_row.commit == "c020"
    assert view.verticalScrollBar().value() == scroll_value

    view.set_graph(_graph(1))
    qapp.processEvents()
    current_row = view._model.row_of(view.currentIndex())
    assert current_row is None or current_row.commit != "c020"
    view.deleteLater()
