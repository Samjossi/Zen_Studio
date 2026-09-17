"""watcher 失效场景刷新回归测试的子进程运行器（须以 LD_PRELOAD 注入 shim 启动）。

复刻用户现场：watcher 失效（shim 注入 ENOSPC）→ 外部重命名 → 树滞留旧名
→ 真实点击悬浮刷新按钮 → 断言子目录显示新名、展开态回填。
结果打印到 stdout，退出码 0 = 通过，1 = 失败。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui.panels.file_explorer.explorer import FileExplorer


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_loads(model, timeout_ms: int = 5000) -> None:
    done = []
    model.directoryLoaded.connect(lambda p: done.append(p))
    deadline = time.monotonic() + timeout_ms / 1000
    while not done and time.monotonic() < deadline:
        pump(50)


def subdir_names(explorer: FileExplorer, sub: Path) -> list[str]:
    src = explorer.model.index(str(sub))
    return sorted(
        explorer.model.fileName(explorer.model.index(row, 0, src))
        for row in range(explorer.model.rowCount(src))
    )


def main() -> int:
    workdir = Path(sys.argv[1])
    sub = workdir / "subdir"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "old_name.txt").write_text("x")

    app = QApplication(sys.argv)  # noqa: F841 — QApplication 实例须存活，事件循环依赖它
    explorer = FileExplorer(str(workdir))
    explorer.resize(300, 400)
    explorer.show()
    wait_loads(explorer.model)
    pump(300)

    # 展开 subdir 建缓存（用户现场前置状态）
    src_root = explorer.model.index(explorer.root_dir)
    for row in range(explorer.model.rowCount(src_root)):
        idx = explorer.model.index(row, 0, src_root)
        if explorer.model.fileName(idx) == "subdir":
            explorer.tree.expand(explorer.proxy.mapFromSource(idx))
    wait_loads(explorer.model)
    pump(300)

    os.rename(sub / "old_name.txt", sub / "new_name.txt")
    pump(800)
    before = subdir_names(explorer, sub)
    print(f"[runner] 刷新前 subdir = {before}", flush=True)
    if before != ["old_name.txt"]:
        print("[runner] 前置未滞留，复现场景不成立", flush=True)
        return 1

    btn = explorer._refresh_btn
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton, pos=btn.rect().center())
    deadline = time.monotonic() + 5
    while explorer._refresh_restore is not None and time.monotonic() < deadline:
        pump(100)
    pump(500)
    after = subdir_names(explorer, sub)
    print(f"[runner] 刷新后 subdir = {after}", flush=True)
    return 0 if after == ["new_name.txt"] else 1


if __name__ == "__main__":
    sys.exit(main())
