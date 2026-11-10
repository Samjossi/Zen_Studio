# Copyright (C) 2026 EclipseSource GmbH and others.
# SPDX-License-Identifier: EPL-2.0
"""Git 提交拓扑的 Qt 原生几何渲染。

视觉几何移植自 Eclipse Theia SCM History Graph 的
packages/scm/src/browser/scm-history-graph-widget.tsx。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

from ide.core.git.graph import GraphRow
from ide.gui.theme import get_mono_family

_LANE_COLOR_KEYS = (
    "blue", "red", "green", "magenta",
    "cyan", "brown", "brightblue", "brightmagenta",
)


class GitGraphRenderer:
    def __init__(self) -> None:
        self._colors = [QColor("#0451a5") for _ in _LANE_COLOR_KEYS]
        self._background = QColor("#ffffff")
        self._lane_width = 22
        self._row_height = 22
        self._node_radius = 5.0
        self._head_radius = 7.0
        self._inner_radius = 2.0
        self._line_width = 1
        self.refresh_font()

    def apply_theme(self, terminal_pack: dict, background: str) -> None:
        self._colors = [
            QColor(terminal_pack[key]) for key in _LANE_COLOR_KEYS
        ]
        self._background = QColor(background)

    def refresh_font(self) -> None:
        font = QFont(get_mono_family())
        if app := QApplication.instance():
            font.setPointSizeF(app.font().pointSizeF())
        metrics = QFontMetrics(font)
        self._lane_width = max(22, metrics.horizontalAdvance("0") + 13)
        self._row_height = max(22, metrics.height() + 4)
        self._node_radius = min(8.0, max(5.0, metrics.height() * 5 / 16))
        self._head_radius = self._node_radius + 2
        self._inner_radius = max(2.0, self._node_radius * 0.4)
        self._line_width = min(3, max(1, round(metrics.height() / 16)))

    @property
    def lane_width(self) -> int:
        return self._lane_width

    @property
    def row_height(self) -> int:
        return self._row_height

    @property
    def node_radius(self) -> float:
        return self._node_radius

    def content_width(self, lanes: int) -> int:
        return max(1, lanes) * self._lane_width

    def lane_x(self, lane: int, rect: QRect) -> float:
        return rect.left() + lane * self._lane_width + self._lane_width / 2

    def paint(
        self,
        painter: QPainter,
        rect: QRect,
        row: GraphRow,
        is_head: bool,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = float(rect.top())
        bottom = float(rect.top() + rect.height())
        cy = top + rect.height() / 2
        commit_x = self.lane_x(row.lane, rect)
        commit_color = self._colors[row.color % 8]

        for edge in row.edges:
            if edge.kind != "pass-through":
                continue
            x = self.lane_x(edge.from_lane, rect)
            self._set_pen(painter, self._colors[edge.color % 8])
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))

        for edge in row.edges:
            if edge.kind != "merge-in":
                continue
            source_x = self.lane_x(edge.from_lane, rect)
            path = QPainterPath(QPointF(source_x, top))
            path.cubicTo(
                QPointF(source_x, cy),
                QPointF(commit_x, cy),
                QPointF(commit_x, cy),
            )
            self._set_pen(painter, self._colors[edge.color % 8])
            painter.drawPath(path)

        self._set_pen(painter, commit_color)
        if row.has_top_line:
            painter.drawLine(
                QPointF(commit_x, top), QPointF(commit_x, cy))
        if row.has_continuation:
            painter.drawLine(
                QPointF(commit_x, cy), QPointF(commit_x, bottom))

        for edge in row.edges:
            if edge.kind != "branch-out":
                continue
            source_x = self.lane_x(edge.from_lane, rect)
            target_x = self.lane_x(edge.to_lane, rect)
            path = QPainterPath(QPointF(source_x, cy))
            path.cubicTo(
                QPointF(source_x, bottom),
                QPointF(target_x, cy),
                QPointF(target_x, bottom),
            )
            self._set_pen(painter, self._colors[edge.color % 8])
            painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(commit_color)
        radius = self._head_radius if is_head else self._node_radius
        painter.drawEllipse(QPointF(commit_x, cy), radius, radius)
        if is_head:
            painter.setBrush(self._background)
            painter.drawEllipse(
                QPointF(commit_x, cy), self._inner_radius, self._inner_radius)
        painter.restore()

    def _set_pen(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(QPen(
            color, self._line_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
