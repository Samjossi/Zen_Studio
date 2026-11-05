"""文件树 / Git 树双页切换与 Git 刷新扇出回归测试。"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStatusBar, QWidget

import gui.controllers as controllers_mod
import gui.git_graph_content as content_mod
from core.git import status as git_status
from core.git.service import ChangeEntry
from gui.controllers import GitStatusController
from gui.panels.changes.panel import ChangesPanel
from gui.panels.file_explorer.explorer import FileExplorer
from gui.settings import KEY_THEME

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "test_file_tree_git_mode"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fresh_dir(name: str) -> Path:
    path = WORKDIR / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _service(enabled: bool = True):
    return SimpleNamespace(
        is_enabled=enabled,
        repo_root=str(PROJECT_ROOT),
        head_label="main" if enabled else None,
        is_head_detached=False,
        status_of=lambda _path: None,
        status_of_dir=lambda _path: None,
    )


def _show_explorer(qapp, root: Path = PROJECT_ROOT) -> FileExplorer:
    explorer = FileExplorer(str(root))
    explorer._git_panel.reload = lambda: None
    explorer.resize(360, 400)
    explorer.show()
    explorer.apply_git_status(_service(), "cloud")
    qapp.processEvents()
    return explorer


def test_mode_button_switches_commit_graph_and_preserves_model(qapp):
    root = _fresh_dir("toggle")
    selected = root / "selected.txt"
    selected.write_text("selected\n", encoding="utf-8")
    explorer = _show_explorer(qapp, root)
    model = explorer.model
    source_index = explorer.model.index(str(selected))
    assert source_index.isValid()
    proxy_index = explorer.proxy.mapFromSource(source_index)
    explorer.tree.setCurrentIndex(proxy_index)
    explorer.tree.expand(explorer.tree.rootIndex())

    assert explorer._stack.currentWidget() is explorer.tree
    assert explorer._mode_button.text() == "⇄"
    assert explorer._mode_button.toolTip() == "切换到 Git 树"
    assert explorer._mode_button.width() == explorer._mode_button.height() == 24
    assert explorer._mode_button_opacity.opacity() == 0.5
    QApplication.sendEvent(explorer._mode_button, QEvent(QEvent.Type.Enter))
    assert explorer._mode_button_opacity.opacity() == 1.0
    QApplication.sendEvent(explorer._mode_button, QEvent(QEvent.Type.Leave))
    assert explorer._mode_button_opacity.opacity() == 0.5

    explorer._mode_button.click()
    qapp.processEvents()
    assert explorer._stack.currentWidget() is explorer._git_panel
    assert explorer._mode_button.text() == "⇄"
    assert explorer._mode_button.toolTip() == "返回文件树"
    assert explorer._git_panel._content._view.header().isHidden()

    explorer._mode_button.click()
    qapp.processEvents()
    assert explorer._stack.currentWidget() is explorer.tree
    assert explorer.model is model
    assert explorer.tree.currentIndex() == proxy_index
    assert explorer.tree.isExpanded(explorer.tree.rootIndex())
    explorer.deleteLater()


def test_git_mode_closes_search_and_non_git_forces_file_tree(qapp):
    explorer = _show_explorer(qapp)
    explorer._toggle_search_bar()
    explorer._search_matches = ["pending"]
    explorer._search_cursor = 0
    assert explorer._search_bar.isVisible()

    explorer._mode_button.click()
    qapp.processEvents()
    assert not explorer._search_bar.isVisible()
    assert not explorer._refresh_btn.isVisible()
    assert not explorer._search_btn.isVisible()
    assert explorer._search_matches == []
    assert explorer._search_cursor == -1
    explorer._toggle_search_bar()
    assert not explorer._search_bar.isVisible()

    explorer._mode_button.click()
    qapp.processEvents()
    assert explorer._refresh_btn.isVisible()
    assert explorer._search_btn.isVisible()

    explorer._mode_button.click()
    explorer.apply_git_status(_service(False), "cloud")
    qapp.processEvents()
    assert explorer._stack.currentWidget() is explorer.tree
    assert not explorer._mode_button.isVisible()
    assert not explorer._mode_button_spacer.isVisible()
    assert explorer._refresh_btn.isVisible()
    assert explorer._search_btn.isVisible()
    explorer.deleteLater()


class _ViewerStub(QWidget):
    externally_reloaded = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.current_path = None

    def set_git_service(self, _service) -> None:
        pass

    def refresh_git_badge(self) -> None:
        pass

    def open_file(self, _path: str, _line: int | None = None) -> None:
        pass


class _DiffDialogStub(QWidget):
    viewer_jump_requested = Signal(str, object)
    shown: list[str] = []

    def __init__(self, _service, _parent=None) -> None:
        super().__init__(_parent)

    def show_diff(self, path: str) -> None:
        self.shown.append(path)


class _ServiceStub:
    def __init__(self, root: str) -> None:
        self.repo_root = root
        self.is_enabled = True
        self.head_label = "main"
        self.is_head_detached = False
        self.changes = [
            ChangeEntry(path="a.py", status=git_status.MODIFIED, added=1, deleted=0),
        ]
        self.collect_calls = 0

    def refresh(self) -> None:
        pass

    def collect_changes(self) -> list[ChangeEntry]:
        self.collect_calls += 1
        return self.changes

    def numstat_of(self, _path: str):
        return None

    def status_of(self, _path: str):
        return None

    def status_of_dir(self, _path: str):
        return None


def test_controller_reuses_one_changes_snapshot_and_theme_does_not_query(qapp, monkeypatch):
    root = _fresh_dir("controller")
    monkeypatch.setattr(controllers_mod, "GitStatusService", _ServiceStub)
    monkeypatch.setattr(controllers_mod, "DiffViewDialog", _DiffDialogStub)
    monkeypatch.setattr(controllers_mod, "load_settings", lambda: {KEY_THEME: "cloud"})
    log_calls = []
    monkeypatch.setattr(
        content_mod.git_log, "fetch_commit_rows", lambda _root: log_calls.append(_root))
    explorer = FileExplorer(str(root))
    viewer = _ViewerStub()
    changes_panel = ChangesPanel()
    status_bar = QStatusBar()
    window = QMainWindow()
    controller = GitStatusController(
        explorer, viewer, changes_panel, status_bar, QLabel(), lambda: None, window,
    )
    service = controller.service

    assert service.collect_calls == 1
    assert changes_panel._changes is service.changes
    assert log_calls == []
    explorer.apply_theme("dark")
    changes_panel.apply_theme("dark")
    assert service.collect_calls == 1
    assert log_calls == []
    controller.refresh()
    assert service.collect_calls == 2
    assert log_calls == []

    _DiffDialogStub.shown.clear()
    changes_panel._on_double_clicked(changes_panel._list.topLevelItem(0), 0)
    assert _DiffDialogStub.shown == [f"{root}/a.py"]
    changes_panel.apply_changes([
        ChangeEntry(path="deleted.py", status=git_status.DELETED, added=0, deleted=1),
    ], str(root), "cloud")
    changes_panel._on_double_clicked(changes_panel._list.topLevelItem(0), 0)
    assert status_bar.currentMessage() == "文件已删除，待提交：deleted.py"

    controller._debounce.stop()
    window.deleteLater()
    explorer.deleteLater()
    viewer.deleteLater()
    changes_panel.deleteLater()
    status_bar.deleteLater()
