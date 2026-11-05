"""文件树头部当前分支：Git 数据层、文本截断与面板显隐回归测试。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QApplication

import core.git.service as service_mod
from core.git.service import GitStatusService
from gui.panels.file_explorer.explorer import FileExplorer, _format_branch_label

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "test_file_tree_branch"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return result.stdout.strip()


def _fresh_dir(name: str) -> Path:
    path = WORKDIR / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _commit_readme(repo: Path) -> None:
    (repo / "README.md").write_text("branch test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo, "-c", "user.name=Zen Test", "-c", "user.email=zen@example.invalid",
        "commit", "-m", "initial",
    )


def test_head_label_covers_unborn_branch_switch_and_detached():
    repo = _fresh_dir("repo")
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True, text=True, timeout=10, check=True,
    )
    service = GitStatusService(str(repo))

    service.refresh()
    assert service.head_label == "main"
    assert service.is_head_detached is False

    _commit_readme(repo)
    _git(repo, "checkout", "-b", "feature/test")
    service.refresh()
    assert service.head_label == "feature/test"
    assert service.is_head_detached is False

    short_hash = _git(repo, "rev-parse", "--short", "HEAD")
    _git(repo, "checkout", "--detach")
    service.refresh()
    assert service.head_label == short_hash
    assert service.is_head_detached is True


def test_head_label_is_none_outside_repo_and_for_non_directory(monkeypatch):
    plain = _fresh_dir("plain")
    # 项目内目录会继承外层 Zen Studio 仓库，非仓库场景须隔离该探测边界
    with monkeypatch.context() as patch:
        patch.setattr(service_mod.runner, "find_repo_root", lambda _path: None)
        service = GitStatusService(str(plain))
        service.refresh()
        assert service.is_enabled is False
        assert service.head_label is None

    file_path = plain / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    service = GitStatusService(str(file_path))
    service.refresh()
    assert service.is_enabled is False
    assert service.head_label is None


def test_head_query_failure_keeps_previous_label(monkeypatch):
    repo = _fresh_dir("failure")
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True, text=True, timeout=10, check=True,
    )
    service = GitStatusService(str(repo))
    service.refresh()
    assert service.head_label == "main"

    monkeypatch.setattr(service_mod.log, "current_branch", lambda _root: None)
    service.refresh()
    assert service.head_label == "main"
    assert service.is_head_detached is False


def test_format_branch_label_short_and_elided(qapp):
    metrics = QFontMetrics(qapp.font())
    assert _format_branch_label("main", metrics, 1000) == "⎇ main"

    label = "feature/very-long-branch-name-for-testing"
    formatted = _format_branch_label(label, metrics, 100)
    assert formatted.startswith("⎇ ")
    assert "…" in formatted
    assert metrics.horizontalAdvance(formatted) <= 100
    assert _format_branch_label(label, metrics, 0) == ""


def _fake_service(label: str | None, detached: bool = False):
    return SimpleNamespace(
        is_enabled=label is not None,
        head_label=label,
        is_head_detached=detached,
        status_of=lambda _path: None,
        status_of_dir=lambda _path: None,
    )


def test_explorer_updates_branch_visibility_tooltip_and_pixels(qapp):
    explorer = FileExplorer(str(PROJECT_ROOT))
    explorer.resize(360, 300)
    explorer.show()
    explorer.apply_git_status(_fake_service(None))
    qapp.processEvents()
    assert explorer.branch_label.text() == ""
    assert explorer.branch_label.isVisible() is False
    without_branch = explorer.grab().toImage()

    explorer.apply_git_status(_fake_service("main"))
    qapp.processEvents()
    assert explorer.branch_label.text() == "⎇ main"
    assert explorer.branch_label.isVisible() is True
    assert explorer.branch_label.toolTip() == "main"
    with_branch = explorer.grab().toImage()

    rect = explorer.branch_label.geometry()
    changed_pixels = sum(
        QColor(without_branch.pixel(x, y)) != QColor(with_branch.pixel(x, y))
        for y in range(rect.top(), rect.bottom() + 1)
        for x in range(rect.left(), rect.right() + 1)
    )
    assert changed_pixels > 0, "分支标签区域必须产生实际像素变化"

    explorer.apply_git_status(_fake_service("a1b2c3d", detached=True))
    assert explorer.branch_label.text() == "⎇ a1b2c3d"
    assert explorer.branch_label.toolTip() == "游离头指针（detached HEAD）：a1b2c3d"

    explorer.apply_git_status(_fake_service(None))
    assert explorer.branch_label.isVisible() is False
    assert explorer.branch_label.text() == ""
    explorer.close()


def test_explorer_elides_long_branch_without_shrinking_project_name(qapp):
    explorer = FileExplorer(str(PROJECT_ROOT))
    explorer.resize(240, 300)
    explorer.show()
    label = "feature/very-long-branch-name-for-testing"
    explorer.apply_git_status(_fake_service(label))
    qapp.processEvents()

    assert explorer.branch_label.text() != f"⎇ {label}"
    assert "…" in explorer.branch_label.text()
    assert explorer.branch_label.toolTip() == label
    assert explorer.path_label.text() == PROJECT_ROOT.name
    assert explorer.path_label.width() >= explorer.path_label.fontMetrics().horizontalAdvance(
        PROJECT_ROOT.name)
    explorer.close()
