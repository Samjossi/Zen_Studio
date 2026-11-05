# Copyright (C) 2026 EclipseSource GmbH and others.
# SPDX-License-Identifier: EPL-2.0
"""Theia scm-history-graph-lanes.spec.ts 的 Python 等价回归。"""
from __future__ import annotations

import pytest

from core.git.graph import GraphEdge, compute_graph_rows


def lanes(commits):
    return [row.lane for row in compute_graph_rows(commits)]


def edges(rows, index, kind):
    return [edge for edge in rows[index].edges if edge.kind == kind]


def test_empty_and_single_root():
    assert compute_graph_rows([]) == ()
    rows = compute_graph_rows([("R", ())])
    assert lanes([("R", ())]) == [0]
    assert rows[0].edges == ()
    assert not rows[0].has_top_line
    assert not rows[0].has_continuation


def test_linear_history_uses_one_lane_and_node_segments():
    commits = [("C", ("B",)), ("B", ("A",)), ("A", ())]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, 0, 0]
    assert [row.color for row in rows] == [0, 0, 0]
    assert all(not row.edges for row in rows)
    assert [row.has_top_line for row in rows] == [False, True, True]
    assert [row.has_continuation for row in rows] == [True, True, False]


def test_branch_and_merge():
    commits = [
        ("M", ("D", "E")),
        ("D", ("B",)),
        ("E", ("B",)),
        ("B", ("A",)),
        ("A", ()),
    ]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, 0, 1, 0, 0]
    assert edges(rows, 0, "branch-out") == [
        GraphEdge(from_lane=0, to_lane=1, color=1, kind="branch-out")
    ]
    assert edges(rows, 0, "merge-in") == []
    assert edges(rows, 2, "merge-in") == [
        GraphEdge(from_lane=0, to_lane=1, color=0, kind="merge-in")
    ]
    assert rows[2].has_top_line
    assert not rows[2].has_continuation
    assert not any(edge.from_lane == edge.to_lane == 1 for edge in rows[3].edges)


def test_lane_is_reused_after_merge_convergence():
    commits = [
        ("M", ("D", "E")),
        ("D", ("B",)),
        ("E", ("B",)),
        ("B", ("A",)),
        ("X", ("Y",)),
        ("A", ()),
        ("Y", ()),
    ]
    rows = compute_graph_rows(commits)
    assert rows[4].lane == 1


def test_parallel_independent_branches():
    commits = [
        ("A2", ("A1",)),
        ("B2", ("B1",)),
        ("A1", ()),
        ("B1", ()),
    ]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, 1, 0, 1]
    assert rows[1].edges == (
        GraphEdge(from_lane=0, to_lane=0, color=0, kind="pass-through"),
    )


@pytest.mark.parametrize("parent_count", [3, 4, 5])
def test_octopus_merge_assigns_one_lane_per_parent(parent_count):
    parents = tuple(f"P{index}" for index in range(1, parent_count + 1))
    commits = [("O", parents), *((parent, ()) for parent in parents)]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, *range(parent_count)]
    assert edges(rows, 0, "branch-out") == [
        GraphEdge(from_lane=0, to_lane=lane, color=lane, kind="branch-out")
        for lane in range(1, parent_count)
    ]
    assert edges(rows, 0, "merge-in") == []
    assert all(row.color == row.lane % 8 for row in rows)


def test_eight_simultaneous_branches_have_distinct_lanes_and_colors():
    commits = [(chr(65 + index), (chr(97 + index),)) for index in range(8)]
    rows = compute_graph_rows(commits)
    assert {row.lane for row in rows} == set(range(8))
    assert [row.color for row in rows] == list(range(8))


def test_color_wraps_at_eight():
    commits = [(f"C{index}", (f"P{index}",)) for index in range(10)]
    rows = compute_graph_rows(commits)
    assert [row.color for row in rows] == [index % 8 for index in range(10)]


def test_edge_type_invariants():
    commits = [
        ("M", ("D", "E")),
        ("D", ("B",)),
        ("E", ("B",)),
        ("B", ()),
    ]
    rows = compute_graph_rows(commits)
    for row in rows:
        assert all(
            edge.from_lane == edge.to_lane
            for edge in row.edges if edge.kind == "pass-through"
        )
        assert all(
            edge.from_lane == row.lane
            for edge in row.edges if edge.kind == "branch-out"
        )
        assert all(
            edge.to_lane == row.lane
            for edge in row.edges if edge.kind == "merge-in"
        )


def test_two_sibling_branches_share_parent_without_connecting_to_each_other():
    commits = [("A", ("P",)), ("B", ("P",)), ("P", ("Q",)), ("Q", ())]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, 1, 0, 0]
    assert edges(rows, 0, "branch-out") == []
    assert edges(rows, 1, "branch-out") == []
    assert not rows[1].has_top_line
    assert rows[1].has_continuation
    assert edges(rows, 1, "merge-in") == []
    assert edges(rows, 1, "pass-through") == [
        GraphEdge(from_lane=0, to_lane=0, color=0, kind="pass-through")
    ]
    assert GraphEdge(
        from_lane=1, to_lane=0, color=1, kind="merge-in"
    ) in rows[2].edges
    assert not any(
        edge.kind == "pass-through" and edge.from_lane == 1
        for edge in rows[2].edges
    )


def test_three_sibling_branches_converge_with_two_merge_edges():
    commits = [("A", ("P",)), ("B", ("P",)), ("C", ("P",)), ("P", ())]
    rows = compute_graph_rows(commits)
    assert lanes(commits) == [0, 1, 2, 0]
    assert all(not edges(rows, index, "branch-out") for index in range(3))
    assert not rows[1].has_top_line and not rows[2].has_top_line
    assert edges(rows, 1, "merge-in") == []
    assert edges(rows, 2, "merge-in") == []
    assert edges(rows, 3, "merge-in") == [
        GraphEdge(from_lane=1, to_lane=0, color=1, kind="merge-in"),
        GraphEdge(from_lane=2, to_lane=0, color=2, kind="merge-in"),
    ]
