# Copyright (C) 2026 EclipseSource GmbH and others.
# SPDX-License-Identifier: EPL-2.0
"""提交 DAG 泳道计算。

算法移植自 Eclipse Theia SCM History Graph 的
packages/scm/src/browser/scm-history-graph-lanes.ts。
输入必须为拓扑有序、由新到旧的完整提交 id 与有序父提交 id。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

GraphEdgeKind = Literal["pass-through", "branch-out", "merge-in"]


@dataclass(frozen=True)
class GraphEdge:
    from_lane: int
    to_lane: int
    color: int
    kind: GraphEdgeKind


@dataclass(frozen=True)
class GraphRow:
    lane: int
    color: int
    edges: tuple[GraphEdge, ...]
    has_continuation: bool
    has_top_line: bool


def compute_graph_rows(
    commits: Iterable[tuple[str, Sequence[str]]],
) -> tuple[GraphRow, ...]:
    lanes: list[str | None] = []
    lane_colors: list[int | None] = []
    rows: list[GraphRow] = []

    for commit_id, parent_ids in commits:
        my_lane = _index_of(lanes, commit_id)
        has_top_line = my_lane != -1
        if my_lane == -1:
            my_lane = _first_free_lane(lanes)
            _set_lane(lanes, my_lane, commit_id)
            _set_lane(lane_colors, my_lane, my_lane % 8)

        my_color = lane_colors[my_lane]
        if my_color is None:
            my_color = my_lane % 8

        duplicate_lanes: list[tuple[int, int]] = []
        if has_top_line:
            for lane, occupant in enumerate(lanes):
                if occupant == commit_id and lane != my_lane:
                    color = lane_colors[lane]
                    duplicate_lanes.append((
                        lane,
                        lane % 8 if color is None else color,
                    ))
                    lanes[lane] = None
                    lane_colors[lane] = None

        lanes_copy = lanes.copy()
        parent_lanes: list[int] = []
        parent_is_existing: list[bool] = []

        for parent_index, parent_id in enumerate(parent_ids):
            parent_lane = _index_of(lanes_copy, parent_id)
            if parent_lane != -1 and parent_lane != my_lane:
                if has_top_line:
                    parent_lanes.append(parent_lane)
                    parent_is_existing.append(True)
                else:
                    lanes[my_lane] = parent_id
                    parent_lanes.append(my_lane)
                    parent_is_existing.append(False)
            elif parent_lane == my_lane:
                parent_lanes.append(my_lane)
                parent_is_existing.append(False)
            elif parent_index == 0:
                lanes[my_lane] = parent_id
                parent_lanes.append(my_lane)
                parent_is_existing.append(False)
            else:
                new_lane = _first_free_lane(lanes)
                _set_lane(lanes, new_lane, parent_id)
                _set_lane(lane_colors, new_lane, new_lane % 8)
                parent_lanes.append(new_lane)
                parent_is_existing.append(False)

        if not parent_ids:
            lanes[my_lane] = None
            lane_colors[my_lane] = None
        elif my_lane not in parent_lanes:
            if has_top_line:
                lanes[my_lane] = None
                lane_colors[my_lane] = None
            else:
                lanes[my_lane] = parent_ids[0]

        edges: list[GraphEdge] = []
        for lane, occupant in enumerate(lanes_copy):
            if not occupant or occupant == commit_id:
                continue
            color = lane_colors[lane]
            edges.append(GraphEdge(
                from_lane=lane,
                to_lane=lane,
                color=lane % 8 if color is None else color,
                kind="pass-through",
            ))

        for parent_id, to_lane, is_existing in zip(
            parent_ids, parent_lanes, parent_is_existing, strict=True
        ):
            if to_lane == my_lane:
                continue
            color = lane_colors[to_lane]
            edge_color = to_lane % 8 if color is None else color
            if is_existing:
                edges.append(GraphEdge(
                    from_lane=to_lane,
                    to_lane=my_lane,
                    color=edge_color,
                    kind="merge-in",
                ))
            else:
                edges.append(GraphEdge(
                    from_lane=my_lane,
                    to_lane=to_lane,
                    color=edge_color,
                    kind="branch-out",
                ))

        for lane, color in duplicate_lanes:
            edges.append(GraphEdge(
                from_lane=lane,
                to_lane=my_lane,
                color=color,
                kind="merge-in",
            ))

        has_continuation = bool(parent_ids) and (
            parent_lanes[0] == my_lane or lanes[my_lane] == parent_ids[0]
        )
        rows.append(GraphRow(
            lane=my_lane,
            color=my_color,
            edges=tuple(edges),
            has_continuation=has_continuation,
            has_top_line=has_top_line,
        ))

    return tuple(rows)


def _index_of(lanes: list[str | None], commit_id: str) -> int:
    try:
        return lanes.index(commit_id)
    except ValueError:
        return -1


def _first_free_lane(lanes: list[str | None]) -> int:
    try:
        return lanes.index(None)
    except ValueError:
        return len(lanes)


def _set_lane(values: list, index: int, value) -> None:
    if index == len(values):
        values.append(value)
    else:
        values[index] = value
