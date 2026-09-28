"""Git 变更面板双击分派单元测试（2026-0929-0558 计划 T1）。

分派规则（panel._on_double_clicked）：
    已删除 D            → deleted_activated（状态栏提示）
    已修改 M / 未跟踪 U → diff_opened（变更对比视图；U 走空基准全绿路径）
    忽略条目 / 冲突态   → file_opened（查看器）
offscreen 构造 ChangesPanel + apply_changes 喂假数据，直接调用槽函数
断言三路信号去向。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from core.git import status as git_status
from core.git.service import ChangeEntry
from gui.panels.changes.panel import ChangesPanel

REPO = "/repo"  # 虚构仓库根（不落盘）


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _panel_with(status: str) -> ChangesPanel:
    """构造只含一条指定状态变更条目的面板。"""
    panel = ChangesPanel()
    panel.apply_changes(
        [ChangeEntry(path="a.py", status=status, added=1, deleted=0)],
        REPO, "cloud")
    return panel


def _double_click(panel: ChangesPanel) -> dict[str, list]:
    """双击唯一条目并捕获三路信号。"""
    fired: dict[str, list] = {"file": [], "diff": [], "deleted": []}
    panel.file_opened.connect(fired["file"].append)
    panel.diff_opened.connect(fired["diff"].append)
    panel.deleted_activated.connect(fired["deleted"].append)
    item = panel._list.topLevelItem(0)
    panel._on_double_clicked(item, 0)
    return fired


def test_double_click_modified_opens_diff(qapp):
    fired = _double_click(_panel_with(git_status.MODIFIED))
    assert fired["diff"] == [f"{REPO}/a.py"]
    assert not fired["file"] and not fired["deleted"]


def test_double_click_untracked_opens_diff(qapp):
    """未跟踪新文件与已修改同走对比视图（空基准 diff 整篇全绿）。"""
    fired = _double_click(_panel_with(git_status.UNTRACKED))
    assert fired["diff"] == [f"{REPO}/a.py"]
    assert not fired["file"] and not fired["deleted"]


def test_double_click_deleted_activates_hint(qapp):
    fired = _double_click(_panel_with(git_status.DELETED))
    assert fired["deleted"] == ["a.py"]
    assert not fired["file"] and not fired["diff"]


def test_double_click_ignored_opens_file(qapp):
    fired = _double_click(_panel_with(git_status.IGNORED))
    assert fired["file"] == [f"{REPO}/a.py"]
    assert not fired["diff"] and not fired["deleted"]


def test_double_click_conflict_opens_file(qapp):
    fired = _double_click(_panel_with(git_status.CONFLICT))
    assert fired["file"] == [f"{REPO}/a.py"]
    assert not fired["diff"] and not fired["deleted"]
