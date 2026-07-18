"""PTY 会话：ptyprocess 子进程 I/O（纯字节管道，不懂终端语义）。

线程模型：reader 线程阻塞读字节 → `data_received` 信号（Qt 跨线程自动排队到 GUI）；
进程退出 → `process_exited`。terminate 幂等 + atexit 纪律（防残留）。
"""
import atexit
import os
import threading
from pathlib import Path

from ptyprocess import PtyProcess
from PySide6.QtCore import QCoreApplication, QObject, Signal

#: 项目根（本文件位于 gui/panels/terminal/，上三级为项目根）；shell 工作目录限定于此
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PtySession(QObject):
    """PTY 子进程生命周期：spawn / read 线程 / write / resize / terminate（幂等）。"""

    #: 字节流到达（reader 线程 → GUI 线程）
    data_received = Signal(bytes)
    #: 进程退出（退出码，未知为 -1）
    process_exited = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: PtyProcess | None = None
        self._closing = False  # 应用退出中：reader 线程不再发射信号（防销毁期 UB）
        if app := QCoreApplication.instance():
            app.aboutToQuit.connect(self._on_about_to_quit)
        atexit.register(self.terminate)

    def _on_about_to_quit(self) -> None:
        self._closing = True

    def _may_emit(self) -> bool:
        return not self._closing and QCoreApplication.instance() is not None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, columns: int = 80, lines: int = 24) -> None:
        """spawn $SHELL（cwd=项目根，TERM=xterm-256color）并启动 reader 线程。"""
        self.terminate()
        shell = os.environ.get("SHELL", "/bin/bash")
        env = dict(os.environ, TERM="xterm-256color")
        self._proc = PtyProcess.spawn(
            [shell], cwd=str(PROJECT_ROOT), env=env, dimensions=(lines, columns))
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True).start()

    def terminate(self) -> None:
        """终止子进程（幂等；应用退出经 atexit 兜底）。"""
        proc, self._proc = self._proc, None
        if proc is not None and proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:  # noqa: BLE001 — 退出期异常无需上屏
                pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.write(data)
            except OSError:
                pass

    def resize(self, rows: int, cols: int) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.setwinsize(max(1, rows), max(1, cols))
            except OSError:
                pass

    # ------------------------------------------------------------------
    # reader 线程
    # ------------------------------------------------------------------
    def _read_loop(self, proc: PtyProcess) -> None:
        """阻塞读字节流并转发信号；EOF/异常（进程退出）结束循环。"""
        try:
            while proc.isalive():
                try:
                    data = proc.read(4096)
                except (EOFError, OSError):
                    break
                if not data:
                    break
                if self._may_emit():
                    self.data_received.emit(data)
        finally:
            code = -1
            try:
                if not proc.isalive():
                    code = proc.wait()
            except Exception:  # noqa: BLE001 — 退出码不可得时按 -1
                code = getattr(proc, "exitstatus", -1) or -1
            if self._may_emit():
                self.process_exited.emit(code)
