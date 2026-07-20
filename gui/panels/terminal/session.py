"""PTY 会话：ptyprocess 子进程 I/O（纯字节管道，不懂终端语义）。

线程模型：reader 线程阻塞读字节 → `data_received` 信号（Qt 跨线程自动排队到 GUI）；
进程退出 → `process_exited`。terminate 幂等 + atexit 纪律（防残留）。
"""
import atexit
import os
import threading

from ptyprocess import PtyProcess
from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.paths import PROJECT_ROOT


class PtySession(QObject):
    """PTY 子进程生命周期：spawn / read 线程 / write / resize / terminate（幂等）。"""

    #: 字节流到达（reader 线程 → GUI 线程）
    data_received = Signal(bytes)
    #: 进程退出（退出码，未知为 -1）
    process_exited = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: PtyProcess | None = None
        self._is_closing = False  # 应用退出中：reader 线程不再发射信号（防销毁期 UB）
        self._generation = 0  # 进程代次：重开后旧代 reader 的退出信号作废（防竞态污染新会话状态）
        self._cwd = str(PROJECT_ROOT)  # shell 工作目录（start 可改；工作区切换只影响新会话）
        if app := QCoreApplication.instance():
            app.aboutToQuit.connect(self._on_about_to_quit)
        atexit.register(self.terminate)

    def _on_about_to_quit(self) -> None:
        self._is_closing = True

    def _may_emit(self) -> bool:
        return not self._is_closing and QCoreApplication.instance() is not None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, column_count: int = 80, line_count: int = 24, cwd: str | None = None) -> None:
        """spawn $SHELL（TERM=xterm-256color）并启动 reader 线程。

        :param cwd: shell 工作目录；None 沿用上次（重开保持原目录），
                    缺省为项目根（工作区切换时由 TerminalPanel 传入新根）。
        """
        if cwd is not None:
            self._cwd = cwd
        # 先换代再 terminate：terminate 内部 sleep 阶梯期间旧 reader 即完成退出检查，
        # 代次若在其后才递增，旧退出信号（SIGHUP 致死 code 0）会漏过守卫污染新会话
        self._generation += 1
        self.terminate()
        shell = os.environ.get("SHELL", "/bin/bash")
        env = dict(os.environ, TERM="xterm-256color")
        self._proc = PtyProcess.spawn(
            [shell], cwd=self._cwd, env=env, dimensions=(line_count, column_count))
        threading.Thread(
            target=self._read_loop, args=(self._proc, self._generation), daemon=True).start()

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

    def resize(self, row_count: int, column_count: int) -> None:
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.setwinsize(max(1, row_count), max(1, column_count))
            except OSError:
                pass

    # ------------------------------------------------------------------
    # reader 线程
    # ------------------------------------------------------------------
    def _read_loop(self, proc: PtyProcess, generation: int) -> None:
        """阻塞读字节流并转发信号；EOF/异常（进程退出）结束循环。

        generation 为 spawn 时的代次：重开后旧代 reader 的退出信号作废，
        防止 terminate 旧进程产生的晚到退出信号污染新会话状态。
        """
        try:
            while proc.isalive():
                try:
                    data = proc.read(4096)
                except (EOFError, OSError):
                    break
                if not data:
                    break
                if generation == self._generation and self._may_emit():
                    self.data_received.emit(data)
        finally:
            code = -1
            try:
                if not proc.isalive():
                    code = proc.wait()
            except Exception:  # noqa: BLE001 — 退出码不可得时按 -1
                # 显式 None 判断：exitstatus 为 0（正常退出）时不能用 or -1 兜底
                return_code = getattr(proc, "exitstatus", None)
                code = return_code if return_code is not None else -1
            if code is None:
                # ptyprocess：被信号杀死时 wait() 返回 None（exitstatus=None）；
                # 按 shell 惯例以 128+signo 回报（SIGHUP=129 / SIGINT=130 / SIGKILL=137）
                sig = getattr(proc, "signalstatus", None)
                code = 128 + sig if isinstance(sig, int) else -1
            if generation == self._generation and self._may_emit():
                self.process_exited.emit(code)
