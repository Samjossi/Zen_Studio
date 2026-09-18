"""文件树左下角悬浮搜索钮 + 焦点范围搜索回归测试。

覆盖（对应 2026-0919-0110 计划 T5）：
- 搜索钮点击切换搜索栏显隐、展开时对焦点位置做范围快照；
- 范围解析：选中目录搜其下、选中文件搜父目录、无选中搜根；
- 文件名大小写不敏感子串匹配、命中上限截断、命中项展开点亮；
- Enter/Shift+Enter 循环命中、Esc 收起、无命中警示属性；
- refresh 重建模型后搜索状态复位（搜索词保留）。

临时数据按 AGENTS.md 纪律落项目内 .temp/，不用系统临时目录。
"""
import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from gui.panels.file_explorer.explorer import _SEARCH_MAX_RESULTS, FileExplorer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "tests_file_tree_search"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def fresh_workdir(name: str) -> Path:
    path = WORKDIR / name
    shutil.rmtree(path, ignore_errors=True)
    (path / "subdir").mkdir(parents=True)
    (path / "subdir" / "hello_target.txt").write_text("x")
    (path / "Hello_Upper.TXT").write_text("x")
    (path / "alpha.txt").write_text("x")
    return path


def make_explorer(root: Path) -> FileExplorer:
    explorer = FileExplorer(str(root))
    explorer.resize(300, 400)
    explorer.show()
    deadline = time.monotonic() + 5
    while explorer.model.rowCount(explorer.model.index(explorer.root_dir)) == 0 \
            and time.monotonic() < deadline:
        pump(50)
    pump(200)
    return explorer


def wait_search_settled(explorer: FileExplorer, timeout_ms: int = 5000) -> None:
    """等定位接力钻取完成（懒加载深层索引经 directoryLoaded 异步接力）。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while explorer._search_pending is not None and time.monotonic() < deadline:
        pump(100)


def select_path(explorer: FileExplorer, path: Path) -> None:
    src = explorer.model.index(str(path))
    assert src.isValid()
    explorer.tree.setCurrentIndex(explorer.proxy.mapFromSource(src))
    pump(100)


def current_path(explorer: FileExplorer) -> str:
    return explorer._file_path(explorer.tree.currentIndex())


def test_search_button_toggles_bar_and_snapshots_scope(qapp):
    proj = fresh_workdir("toggle")
    explorer = make_explorer(proj)
    select_path(explorer, proj / "subdir")

    assert not explorer._search_bar.isVisible()
    explorer._search_btn.click()
    assert explorer._search_bar.isVisible(), "点击搜索钮未展开搜索栏"
    assert explorer._search_scope == str(proj / "subdir"), "范围快照未取焦点目录"

    explorer._search_btn.click()
    assert not explorer._search_bar.isVisible(), "再次点击未收起搜索栏"
    assert explorer._search_scope is None
    explorer.deleteLater()


def test_scope_resolution_dir_file_none(qapp):
    proj = fresh_workdir("scope")
    explorer = make_explorer(proj)

    # 无选中 → 根目录
    assert explorer._current_focus_dir() == explorer.root_dir

    # 选中目录 → 该目录
    select_path(explorer, proj / "subdir")
    assert explorer._current_focus_dir() == str(proj / "subdir")

    # 选中文件 → 父目录
    select_path(explorer, proj / "alpha.txt")
    assert explorer._current_focus_dir() == str(proj)
    explorer.deleteLater()


def test_search_case_insensitive_and_reveals_match(qapp):
    proj = fresh_workdir("reveal")
    explorer = make_explorer(proj)
    explorer._toggle_search_bar()  # 无选中 → 范围 = 根目录
    assert explorer._search_scope_dir() == explorer.root_dir

    explorer._search_bar.setText("HELLO")  # 大小写不敏感
    wait_search_settled(explorer)
    pump(200)

    matches = {Path(m).name for m in explorer._search_matches}
    assert matches == {"hello_target.txt", "Hello_Upper.TXT"}, f"匹配集异常：{matches}"
    assert not explorer._search_bar.property("noMatch")
    assert Path(current_path(explorer)).name in matches, "命中项未被点亮"
    # 点亮不改变范围快照（否则后续输入搜不到快照外的匹配）
    assert explorer._search_scope_dir() == explorer.root_dir

    # 首个命中是根下的 Hello_Upper.TXT；切到深层命中验证懒加载钻取展开
    explorer._step_search_match(1)
    wait_search_settled(explorer)
    pump(200)
    assert Path(current_path(explorer)).name == "hello_target.txt"
    sub_src = explorer.model.index(str(proj / "subdir"))
    assert explorer.tree.isExpanded(explorer.proxy.mapFromSource(sub_src)), \
        "命中项父目录未被展开"
    explorer.deleteLater()


def test_search_scoped_to_focus_dir(qapp):
    proj = fresh_workdir("scoped")
    explorer = make_explorer(proj)
    select_path(explorer, proj / "subdir")
    explorer._toggle_search_bar()

    explorer._search_bar.setText("HELLO")
    wait_search_settled(explorer)
    assert [Path(m).name for m in explorer._search_matches] == ["hello_target.txt"], \
        "范围应限于 subdir，不应命中根下的 Hello_Upper.TXT"
    explorer.deleteLater()


def test_step_cycles_matches(qapp):
    proj = fresh_workdir("cycle")
    explorer = make_explorer(proj)
    explorer._toggle_search_bar()
    explorer._search_bar.setText("hello")
    wait_search_settled(explorer)
    assert len(explorer._search_matches) == 2

    first = current_path(explorer)
    explorer._step_search_match(1)
    wait_search_settled(explorer)
    second = current_path(explorer)
    assert second != first, "Enter 未切到下一个命中"
    explorer._step_search_match(1)
    wait_search_settled(explorer)
    assert current_path(explorer) == first, "循环未回到首个命中"
    explorer._step_search_match(-1)
    wait_search_settled(explorer)
    assert current_path(explorer) == second, "Shift+Enter 未回退上一个命中"
    explorer.deleteLater()


def test_esc_closes_bar_and_nomatch_flag(qapp):
    proj = fresh_workdir("esc")
    explorer = make_explorer(proj)
    explorer._toggle_search_bar()

    explorer._search_bar.setText("zzz_no_such_file")
    pump(200)
    assert explorer._search_bar.property("noMatch"), "无命中未置警示属性"

    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert explorer.eventFilter(explorer._search_bar, esc), "Esc 未被 eventFilter 消费"
    assert not explorer._search_bar.isVisible(), "Esc 未收起搜索栏"
    assert explorer._search_matches == []
    assert not explorer._search_bar.property("noMatch"), "收起后警示属性未复位"
    explorer.deleteLater()


def test_max_results_truncation(qapp):
    proj = WORKDIR / "truncation"
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    for i in range(_SEARCH_MAX_RESULTS + 20):
        (proj / f"match_{i:04d}.txt").write_text("x")
    explorer = make_explorer(proj)
    explorer._toggle_search_bar()
    matches = explorer._collect_matches("match_")
    assert len(matches) == _SEARCH_MAX_RESULTS, "命中未按上限截断"
    explorer.deleteLater()


def test_model_rebuilt_resets_search_state(qapp):
    proj = fresh_workdir("rebuilt")
    explorer = make_explorer(proj)
    explorer._toggle_search_bar()
    explorer._search_bar.setText("hello")
    wait_search_settled(explorer)
    assert explorer._search_matches

    explorer.refresh()
    pump(300)
    assert explorer._search_matches == [], "模型重建后匹配状态未复位"
    assert explorer._search_cursor == -1
    assert explorer._search_pending is None
    assert explorer._search_bar.text() == "hello", "搜索词应保留"
    explorer.deleteLater()
