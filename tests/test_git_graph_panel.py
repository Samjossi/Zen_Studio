"""内嵌提交图加载边界、真实拓扑 fixture 与共享内容降级测试。"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import gui.git_graph_content as content_mod
from core.git import log as git_log
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


def _service(repo: Path, enabled: bool = True):
    return SimpleNamespace(
        is_enabled=enabled,
        repo_root=str(repo),
        head_label="main" if enabled else None,
        is_head_detached=False,
        status_of=lambda _path: None,
        status_of_dir=lambda _path: None,
    )


def test_real_merge_fixture_has_connectors_curves_and_all_ref_kinds(merged_repo):
    graph = git_log.fetch_commit_rows(str(merged_repo))
    assert graph is not None
    commits = [row for row in graph.rows if not row.is_connector]
    assert {row.subject for row in commits} >= {
        "base", "feature subject", "main subject", "merge feature"}
    assert any(row.is_connector for row in graph.rows)
    chars = {ch for row in graph.rows for ch, _color in row.graph}
    assert {"/", "\\"} <= chars
    head = commits[0]
    assert head.is_head
    assert {badge.kind for badge in head.refs} >= {"head", "branch", "remote", "tag"}


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
    assert not dialog._content._view.isColumnHidden(2)
    assert not dialog._content._view.isColumnHidden(3)
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
