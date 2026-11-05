"""内嵌提交图加载边界、真实 parents 拓扑 fixture 与共享内容降级测试。"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import gui.git_graph_content as content_mod
from core.git import log as git_log
from core.git.graph import GraphEdge
from core.git.log import CommitGraph, CommitRow
from gui.git_graph_content import GitGraphContent
from gui.git_graph_dialog import GitGraphDialog
from gui.panels.file_explorer.explorer import FileExplorer
from gui.panels.git_graph_panel import GitGraphPanel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "test_git_graph_panel"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, name: str, text: str, subject: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", subject)


@pytest.fixture(scope="module")
def merged_repo() -> Path:
    repo = WORKDIR / "merged"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Zen Tester")
    _git(repo, "config", "user.email", "zen@example.invalid")
    _commit(repo, "app.py", "base\n", "base")
    _git(repo, "checkout", "-b", "feature")
    _commit(repo, "feature.py", "feature\n", "feature subject")
    _git(repo, "checkout", "main")
    _commit(repo, "app.py", "base\nmain\n", "main subject")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    _git(repo, "tag", "v1.0")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


@pytest.fixture(scope="module")
def parallel_repo() -> Path:
    repo = WORKDIR / "parallel"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Zen Tester")
    _git(repo, "config", "user.email", "zen@example.invalid")
    _commit(repo, "app.py", "base\n", "base")
    for branch in ("alpha", "beta", "gamma"):
        _git(repo, "checkout", "-b", branch, "main")
        _commit(repo, f"{branch}.py", "one\n", f"{branch} one")
        _commit(repo, f"{branch}.py", "two\n", f"{branch} two")
    _git(repo, "checkout", "main")
    _commit(repo, "app.py", "base\nmain\n", "main advances")
    _git(repo, "merge", "--no-ff", "alpha", "beta", "gamma", "-m", "octopus merge")
    _git(repo, "checkout", "-b", "feature", "HEAD~1")
    _commit(repo, "feature.py", "feature\n", "long feature")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "feature", "-m", "final merge")
    return repo


@pytest.fixture(scope="module")
def truncated_repo() -> Path:
    repo = WORKDIR / "truncated"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Zen Tester")
    _git(repo, "config", "user.email", "zen@example.invalid")
    for index in range(git_log.MAX_COUNT + 1):
        _git(repo, "commit", "--allow-empty", "-m", f"commit {index}")
    return repo


def _service(repo: Path, enabled: bool = True):
    return SimpleNamespace(
        is_enabled=enabled,
        repo_root=str(repo),
        head_label="main" if enabled else None,
        is_head_detached=False,
        status_of=lambda _path: None,
        status_of_dir=lambda _path: None,
    )


def test_structured_fetch_uses_full_ids_parents_and_topo_order(monkeypatch, merged_repo):
    calls = []

    def fake_git(_repo, *args):
        calls.append(args)
        if args[:2] == ("rev-list", "--all"):
            return "1\n"
        full_id = "a" * 40
        parent_id = "b" * 40
        return "\x1f".join((
            full_id, parent_id, "abc123", "subject", "author", "now",
            "2026-11-06T05:54:00+08:00", "HEAD -> refs/heads/main",
        )) + "\x1e"

    monkeypatch.setattr(git_log.runner, "run_git", fake_git)
    graph = git_log.fetch_commit_rows(str(merged_repo))
    assert graph is not None
    command = calls[0]
    assert "--topo-order" in command
    assert "--graph" not in command
    assert "--color=always" not in command
    assert "%H%x1f%P%x1f%h" in command[-1]
    assert graph.rows[0].commit_id == "a" * 40
    assert graph.rows[0].parent_ids == ("b" * 40,)
    assert graph.rows[0].commit == "abc123"


def test_real_merge_fixture_matches_git_topology_and_all_ref_kinds(merged_repo):
    graph = git_log.fetch_commit_rows(str(merged_repo))
    assert graph is not None
    expected_ids = subprocess.check_output(
        ["git", "rev-list", "--topo-order", "--all"],
        cwd=merged_repo, text=True,
    ).splitlines()
    assert [row.commit_id for row in graph.rows] == expected_ids
    assert len(graph.rows) == len(expected_ids)
    assert {row.subject for row in graph.rows} >= {
        "base", "feature subject", "main subject", "merge feature"}
    merge = next(row for row in graph.rows if row.subject == "merge feature")
    assert len(merge.parent_ids) == 2
    assert any(edge.kind == "branch-out" for edge in merge.graph_row.edges)
    assert {edge.kind for row in graph.rows for edge in row.graph_row.edges} >= {
        "pass-through", "merge-in", "branch-out"}
    head = graph.rows[0]
    assert head.is_head
    assert {badge.kind for badge in head.refs} >= {"head", "branch", "remote", "tag"}


def test_parallel_fixture_has_exact_octopus_edges_and_long_lanes(parallel_repo):
    graph = git_log.fetch_commit_rows(str(parallel_repo))
    assert graph is not None
    assert {row.subject for row in graph.rows} >= {
        "alpha two", "beta two", "gamma two", "octopus merge"}
    octopus = next(row for row in graph.rows if row.subject == "octopus merge")
    assert [edge for edge in octopus.graph_row.edges if edge.kind == "branch-out"] == [
        GraphEdge(from_lane=0, to_lane=2, color=2, kind="branch-out"),
        GraphEdge(from_lane=0, to_lane=3, color=3, kind="branch-out"),
        GraphEdge(from_lane=0, to_lane=4, color=4, kind="branch-out"),
    ]
    assert GraphEdge(
        from_lane=1, to_lane=0, color=1, kind="merge-in",
    ) in octopus.graph_row.edges
    assert GraphEdge(
        from_lane=1, to_lane=1, color=1, kind="pass-through",
    ) in octopus.graph_row.edges
    assert max(row.graph_row.lane for row in graph.rows) >= 4


def test_truncation_keeps_500_real_rows_and_open_graph_boundary(truncated_repo):
    graph = git_log.fetch_commit_rows(str(truncated_repo))
    assert graph is not None
    assert len(graph.rows) == git_log.MAX_COUNT
    assert graph.truncated_hint is not None
    assert f"{git_log.MAX_COUNT + 1}" in graph.truncated_hint
    loaded_ids = {row.commit_id for row in graph.rows}
    assert all(len(commit_id) == 40 for commit_id in loaded_ids)
    assert graph.rows[-1].parent_ids[0] not in loaded_ids
    assert graph.rows[-1].graph_row.has_continuation


def test_panel_graph_only_geometry_and_repaint_do_not_repeat_git(
        qapp, parallel_repo, monkeypatch):
    calls = 0
    real_fetch = content_mod.git_log.fetch_commit_rows

    def counted_fetch(repo_root):
        nonlocal calls
        calls += 1
        return real_fetch(repo_root)

    monkeypatch.setattr(content_mod.git_log, "fetch_commit_rows", counted_fetch)
    panel = GitGraphPanel()
    panel.set_service(_service(parallel_repo))
    panel.resize(240, 360)
    panel.show()
    qapp.processEvents()
    assert calls == 1
    view = panel._content._view
    assert view.graph_only and view.header().isHidden()
    assert all(view.isColumnHidden(column) for column in (1, 2, 3))
    expected_width = view._graph_delegate.content_width(view._model.max_graph_cells)
    assert view.columnWidth(0) == max(expected_width, view.viewport().width())

    panel.resize(160, 300)
    panel.apply_theme("mint")
    panel.refresh_font()
    qapp.processEvents()
    assert calls == 1
    assert view.columnWidth(0) == max(
        view._graph_delegate.content_width(view._model.max_graph_cells),
        view.viewport().width(),
    )
    panel.close()
    panel.deleteLater()


def test_panel_loads_once_per_entry_and_manual_refresh(qapp, merged_repo, monkeypatch):
    calls = 0
    real_fetch = content_mod.git_log.fetch_commit_rows

    def counted_fetch(repo_root):
        nonlocal calls
        calls += 1
        return real_fetch(repo_root)

    monkeypatch.setattr(content_mod.git_log, "fetch_commit_rows", counted_fetch)
    explorer = FileExplorer(str(merged_repo))
    explorer.resize(240, 400)
    explorer.show()
    explorer.apply_git_status(_service(merged_repo), "cloud")
    qapp.processEvents()
    assert calls == 0

    explorer._mode_button.click()
    qapp.processEvents()
    assert explorer._stack.currentWidget() is explorer._git_panel
    assert isinstance(explorer._git_panel, GitGraphPanel)
    assert calls == 1
    explorer._mode_button.click()
    qapp.processEvents()
    assert calls == 1
    explorer._mode_button.click()
    qapp.processEvents()
    assert calls == 2
    explorer._git_panel._refresh_button.click()
    assert calls == 3
    explorer.deleteLater()


def test_non_git_and_empty_repository_use_placeholders(qapp, merged_repo):
    content = GitGraphContent(_service(merged_repo, enabled=False))
    content.reload()
    assert content._stack.currentWidget() is content._placeholder
    assert "不在 git 仓库" in content._placeholder.text()
    content.deleteLater()

    empty = WORKDIR / "empty"
    shutil.rmtree(empty, ignore_errors=True)
    empty.mkdir(parents=True)
    _git(empty, "init")
    content = GitGraphContent(_service(empty))
    content.reload()
    assert content._stack.currentWidget() is content._placeholder
    assert "尚无提交" in content._placeholder.text()
    content.deleteLater()


def test_fallback_and_theme_rerender_do_not_repeat_git(qapp, merged_repo, monkeypatch):
    calls = {"rows": 0, "text": 0}

    def no_rows(_repo):
        calls["rows"] += 1
        return None

    def colored_text(_repo, colored=False):
        assert colored
        calls["text"] += 1
        return "\x1b[31m*\x1b[m abc123 fallback"

    monkeypatch.setattr(content_mod.git_log, "fetch_commit_rows", no_rows)
    monkeypatch.setattr(content_mod.git_log, "fetch_commit_graph", colored_text)
    content = GitGraphContent(_service(merged_repo))
    content.reload()
    assert content._stack.currentWidget() is content._fallback
    assert "fallback" in content._fallback.toPlainText()
    content.apply_theme("mint")
    assert calls == {"rows": 1, "text": 1}
    content.deleteLater()


def test_dialog_reuses_content_and_keeps_full_columns(qapp, merged_repo, monkeypatch):
    graph = CommitGraph(rows=(CommitRow(commit="abc123", subject="subject"),), truncated_hint=None)
    calls = 0
    monkeypatch.setattr(content_mod.git_log, "current_branch", lambda _repo: "main")

    def fetch_rows(_repo):
        nonlocal calls
        calls += 1
        return graph

    monkeypatch.setattr(content_mod.git_log, "fetch_commit_rows", fetch_rows)
    dialog = GitGraphDialog(_service(merged_repo))
    dialog.show()
    qapp.processEvents()
    assert calls == 1
    assert dialog._content._stack.currentWidget() is dialog._content._view
    assert not dialog._content._view.header().isHidden()
    assert not dialog._content._view.graph_only
    assert all(not dialog._content._view.isColumnHidden(column) for column in (1, 2, 3))
    assert "分支：main" in dialog._info_label.text()
    dialog.reload()
    assert calls == 2
    dialog.close()
    dialog.deleteLater()


def test_dialog_keeps_fallback_and_non_git_regression(qapp, merged_repo, monkeypatch):
    monkeypatch.setattr(content_mod.git_log, "current_branch", lambda _repo: "main")
    monkeypatch.setattr(content_mod.git_log, "fetch_commit_rows", lambda _repo: None)
    monkeypatch.setattr(
        content_mod.git_log, "fetch_commit_graph",
        lambda _repo, colored=False: "* abc123 fallback" if colored else None,
    )
    service = _service(merged_repo)
    dialog = GitGraphDialog(service)
    dialog.reload()
    assert dialog._content._stack.currentWidget() is dialog._content._fallback
    assert "fallback" in dialog._content._fallback.toPlainText()

    service.is_enabled = False
    dialog.reload()
    assert dialog._content._stack.currentWidget() is dialog._content._placeholder
    assert dialog._info_label.text() == "（非 git 仓库）"
    dialog.deleteLater()
