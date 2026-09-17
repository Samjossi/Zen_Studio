"""文件树手动刷新（方案 A：重建模型实例）回归测试。

根因背景：QFileSystemModel watcher 失效时深层节点缓存滞留，
脱根重挂只能重列根层；修复为整体重建模型实例物理消灭缓存。

- test_refresh_rebuilds_model_and_restores_state：常规场景验证
  模型实例更换、model_rebuilt 信号发射、展开/选中回填、树显示新结构。
- test_refresh_recovers_with_dead_watcher：LD_PRELOAD 注入
  inotify_add_watch 恒失败的子进程里复现用户现场（watcher 失效 +
  子目录 + 真实点击按钮）；无 gcc 的环境自动 skip。

临时数据按 AGENTS.md 纪律落项目内 .temp/，不用系统临时目录。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from gui.panels.file_explorer.explorer import FileExplorer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "tests_file_tree_refresh"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
    (path / "subdir" / "old_name.txt").write_text("x")
    return path


def wait_restore_done(explorer: FileExplorer, timeout_ms: int = 5000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while explorer._refresh_restore is not None and time.monotonic() < deadline:
        pump(100)


def subdir_names(explorer: FileExplorer, sub: Path) -> list[str]:
    src = explorer.model.index(str(sub))
    return sorted(
        explorer.model.fileName(explorer.model.index(row, 0, src))
        for row in range(explorer.model.rowCount(src))
    )


def test_refresh_rebuilds_model_and_restores_state(qapp):
    proj = fresh_workdir("rebuild")
    explorer = FileExplorer(str(proj))
    explorer.resize(300, 400)
    explorer.show()
    deadline = time.monotonic() + 5
    while explorer.model.rowCount(explorer.model.index(explorer.root_dir)) == 0 \
            and time.monotonic() < deadline:
        pump(50)
    pump(300)

    # 展开 subdir 并选中其中的文件（复刻用户现场前置状态）
    src_root = explorer.model.index(explorer.root_dir)
    sub_src = None
    for row in range(explorer.model.rowCount(src_root)):
        idx = explorer.model.index(row, 0, src_root)
        if explorer.model.fileName(idx) == "subdir":
            sub_src = idx
    assert sub_src is not None
    explorer.tree.expand(explorer.proxy.mapFromSource(sub_src))
    deadline = time.monotonic() + 5
    while explorer.model.rowCount(sub_src) == 0 and time.monotonic() < deadline:
        pump(50)
    pump(200)

    rebuilt_spy = []
    explorer.model_rebuilt.connect(lambda: rebuilt_spy.append(True))
    old_model = explorer.model

    os.rename(proj / "subdir" / "old_name.txt", proj / "subdir" / "new_name.txt")
    explorer.refresh()
    wait_restore_done(explorer)
    pump(500)

    assert explorer.model is not old_model, "refresh 必须更换模型实例（物理消灭缓存）"
    assert rebuilt_spy, "model_rebuilt 信号未发射（controllers 依赖它重接线）"

    sub = proj / "subdir"
    assert subdir_names(explorer, sub) == ["new_name.txt"]
    sub_idx_new = explorer.model.index(str(sub))
    assert explorer.tree.isExpanded(explorer.proxy.mapFromSource(sub_idx_new)), \
        "展开态未回填"
    explorer.deleteLater()


def test_refresh_recovers_with_dead_watcher():
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("无 gcc，无法编译 inotify 故障注入 shim")

    shim_so = WORKDIR / "no_inotify_shim.so"
    shim_so.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [gcc, "-shared", "-fPIC", "-o", str(shim_so),
         str(FIXTURES / "no_inotify_shim.c")],
        check=True,
    )

    proj = fresh_workdir("dead_watcher")
    env = os.environ.copy()
    env["LD_PRELOAD"] = str(shim_so)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, str(FIXTURES / "refresh_dead_watcher_runner.py"), str(proj)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, (
        "watcher 失效 + 子目录场景下刷新未恢复新结构：\n"
        f"{result.stdout}\n{result.stderr}"
    )
