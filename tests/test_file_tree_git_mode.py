"""文件树 / Git 树双页切换与 Git 刷新扇出回归测试。"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStatusBar, QWidget

import gui.controllers as controllers_mod
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
        head_label="main" if enabled else None,
        is_head_detached=False,
        status_of=lambda _path: None,
        status_of_dir=lambda _path: None,
    )


def _show_explorer(qapp, root: Path = PROJECT_ROOT) -> FileExplorer:
    explorer = FileExplorer(str(root))
    explorer.resize(360, 400)
    explorer.show()
    explorer.apply_git_status(_service(), "cloud")
    qapp.processEvents()
    return explorer


def test_mode_button_switches_flat_list_and_preserves_model(qapp):
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
    changes = [
        ChangeEntry(path="modified.py", status=git_status.MODIFIED, added=2, deleted=1),
        ChangeEntry(path="untracked.py", status=git_status.UNTRACKED, added=3, deleted=0),
        ChangeEntry(path="deleted.py", status=git_status.DELETED, added=0, deleted=4),
    ]
    explorer.apply_git_changes(changes, str(root), "cloud")

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
    assert explorer._stack.currentWidget() is explorer._git_list
    assert explorer._mode_button.text() == "⇄"
    assert explorer._mode_button.toolTip() == "返回文件树"
    assert explorer._git_list.topLevelItemCount() == 3
    assert explorer._git_list.header().isHidden()

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


def test_git_list_signals_are_forwarded_by_explorer(qapp):
    explorer = _show_explorer(qapp)
    root = "/repo"
    explorer.apply_git_changes([
        ChangeEntry(path="modified.py", status=git_status.MODIFIED, added=1, deleted=0),
        ChangeEntry(path="deleted.py", status=git_status.DELETED, added=0, deleted=1),
        ChangeEntry(path="conflict.py", status=git_status.CONFLICT, added=0, deleted=0),
    ], root, "cloud")
    fired = {"diff": [], "deleted": [], "file": []}
    explorer.diff_opened.connect(fired["diff"].append)
    explorer.deleted_activated.connect(fired["deleted"].append)
    explorer.file_opened.connect(fired["file"].append)

    for row in range(3):
        explorer._git_list._on_double_clicked(explorer._git_list.topLevelItem(row), 0)
    assert fired == {
        "diff": ["/repo/modified.py"],
        "deleted": ["deleted.py"],
        "file": ["/repo/conflict.py"],
    }
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
    assert explorer._git_list._changes is service.changes
    assert changes_panel._changes is service.changes
    explorer.apply_theme("dark")
    changes_panel.apply_theme("dark")
    assert service.collect_calls == 1

    _DiffDialogStub.shown.clear()
    explorer.diff_opened.emit(f"{root}/a.py")
    assert _DiffDialogStub.shown == [f"{root}/a.py"]
    explorer.deleted_activated.emit("deleted.py")
    assert status_bar.currentMessage() == "文件已删除，待提交：deleted.py"

    controller._debounce.stop()
    window.deleteLater()
    explorer.deleteLater()
    viewer.deleteLater()
    changes_panel.deleteLater()
    status_bar.deleteLater()
