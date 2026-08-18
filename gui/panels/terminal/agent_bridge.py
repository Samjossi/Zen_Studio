"""ACP terminal/* 反向能力 GUI 桥（2026-0817-1554 计划 T3）。

agent 的 Bash 命令经 ACP terminal/* 反向请求落到终端面板的用户可见
AI tab 中执行（真 PTY，所有权在用户侧，AI 远程"驾驶"）：用户实时看
输出、可 Ctrl+C 干预、可手关 tab（等价 kill+release）。

线程模型：连接层 reader 线程调用本桥 → QTimer.singleShot(receiver, callable)
封送 GUI 线程同步执行（同 PERMISSION_QUEUE.ask 已验证的阻塞封送模式）；
wait_for_exit 为回调式长阻塞语义，不占 reader 线程（连接层异步应答）。

所有权：bridge 实例全窗口唯一（main_window 装配）；每 provider 经
handle() 取独立句柄视图——terminalId 全局唯一（ai-term-N 递增），
死讯清理按句柄归属集隔离，多 chat 标签 × 多 provider 并发互不串扰。
"""
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, QTimer

from gui.panels.terminal.panel import TerminalPanel, _Session

#: 输出尾部缓冲上限（字节）：超出保留尾部并置 truncated（T1 契约的截断策略）
_TAIL_BYTES = 64 * 1024

#: reader 线程 → GUI 线程封送超时（create/kill/release 均为快操作）
_GUI_MARSHAL_TIMEOUT_S = 30


@dataclass
class _TermRecord:
    """单 AI 终端簿记：面板会话句柄 + 尾部缓冲 + 退出码 + 挂起 wait 回调。

    entry 在 spawn 完成前瞬时为 None（回调先接线防丢首帧输出，见
    AgentTerminalBridge._create_gui）；create 返回后恒非 None。
    """
    entry: _Session | None = None
    buf: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    exit_code: int | None = None
    wait_cbs: list[Callable[[dict], None]] = field(default_factory=list)


class _BridgeHandler:
    """单 provider 的终端处理器视图（TerminalHandler 契约实现）。"""

    def __init__(self, bridge: "AgentTerminalBridge") -> None:
        self._bridge = bridge
        #: 本连接名下 terminalId 集（死讯清理按归属隔离，不串其他 agent）
        self._owned: set[str] = set()

    # ------------------------------------------------------------------
    # TerminalHandler 契约（连接层 reader 线程调用）
    # ------------------------------------------------------------------
    def create(self, command: str, args: list[str], cwd: str | None,
               env: dict[str, str] | None) -> str:
        terminal_id = self._bridge._create(command, args, cwd, env)
        with self._bridge._lock:
            self._owned.add(terminal_id)
        return terminal_id

    def output(self, terminal_id: str) -> dict:
        return self._bridge._output(terminal_id)

    def wait_for_exit(self, terminal_id: str, callback: Callable[[dict], None]) -> None:
        self._bridge._wait_for_exit(terminal_id, callback)

    def kill(self, terminal_id: str) -> None:
        self._bridge._kill(terminal_id)

    def release(self, terminal_id: str) -> None:
        with self._bridge._lock:
            self._owned.discard(terminal_id)
        self._bridge._release(terminal_id)

    def on_connection_dead(self) -> None:
        """agent 死讯注入（连接层调用，任意线程）：本连接挂起 wait 兜底 -1。"""
        with self._bridge._lock:
            owned, self._owned = self._owned, set()
            recs = [self._bridge._terms[tid] for tid in owned if tid in self._bridge._terms]
        for rec in recs:
            self._bridge._flush_waits(rec, -1)


class AgentTerminalBridge:
    """terminal/* 反向请求 → 终端面板 AI tab（仅经面板公开方法操作）。"""

    def __init__(self, panel: TerminalPanel) -> None:
        self._panel = panel
        self._lock = threading.Lock()
        self._serial = 0
        self._terms: dict[str, _TermRecord] = {}
        # 用户手关 AI tab（等价 kill+release）：桥侧清理挂起 wait 与簿记
        panel.session_closed.connect(self._on_tab_closed)

    def handle(self) -> _BridgeHandler:
        """取独立处理器视图（每 provider 一个，注入 set_terminal_handler）。"""
        return _BridgeHandler(self)

    # ------------------------------------------------------------------
    # 线程封送（reader 线程 → GUI 线程，同 PERMISSION_QUEUE.ask 模式）
    # ------------------------------------------------------------------
    def _invoke_gui(self, fn: Callable[[], object]) -> object:
        if QThread.currentThread() is self._panel.thread():
            return fn()
        done = threading.Event()
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = fn()
            except Exception as e:  # noqa: BLE001 — 跨线程回传，由调用侧按失败处理
                box["error"] = e
            finally:
                done.set()

        # receiver=面板：callable 在 GUI 线程执行；面板销毁则不发（调用侧超时兜底）
        QTimer.singleShot(0, self._panel, run)
        if not done.wait(timeout=_GUI_MARSHAL_TIMEOUT_S):
            raise RuntimeError("终端面板响应超时（窗口可能正在关闭）")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ------------------------------------------------------------------
    # 契约实现（_* 由 _BridgeHandler 透传）
    # ------------------------------------------------------------------
    def _create(self, command: str, args: list[str], cwd: str | None,
                env: dict[str, str] | None) -> str:
        return self._invoke_gui(lambda: self._create_gui(command, args, cwd, env))  # type: ignore[return-value]

    def _create_gui(self, command: str, args: list[str], cwd: str | None,
                    env: dict[str, str] | None) -> str:
        """GUI 线程：建 AI tab 会话。create 载荷两形态兼容（实测报告 §3-2）：
        args 非空直起 argv；args 为空则 $SHELL -c <command 整串>。"""
        if args:
            argv = [command, *args]
            title_word = os.path.basename(command) or command
        else:
            shell = os.environ.get("SHELL", "/bin/bash")
            argv = [shell, "-c", command]
            words = command.split()
            title_word = os.path.basename(words[0]) if words else "sh"
        with self._lock:
            self._serial += 1
            terminal_id = f"ai-term-{self._serial}"
        rec = _TermRecord()
        # data_received 信号在 GUI 线程到达（Qt 跨线程排队）：分一路进尾部
        # 缓冲，屏幕渲染路径不动；reader 线程 output() 经锁读取。
        # 回调经 spawn_agent_session 在 start 之前接线——快命令首帧数据
        # 可能先于 spawn 返回到达，事后接线会丢输出（offscreen 冒烟实证）
        entry = self._panel.spawn_agent_session(
            argv, cwd, f"🤖 {title_word}",
            on_data=lambda data, rec=rec: self._on_data(rec, data),
            on_exited=lambda code, rec=rec: self._on_exited(rec, code))
        rec.entry = entry
        with self._lock:
            self._terms[terminal_id] = rec
        return terminal_id

    def _output(self, terminal_id: str) -> dict:
        """reader 线程直读（锁保护，不封送）：{output, truncated, exitStatus?}。"""
        with self._lock:
            rec = self._terms.get(terminal_id)
            if rec is None:
                return {"output": "", "truncated": False}
            result: dict = {
                "output": bytes(rec.buf).decode("utf-8", errors="replace"),
                "truncated": rec.truncated,
            }
            if rec.exit_code is not None:
                result["exitStatus"] = {"exitCode": rec.exit_code, "signal": None}
            return result

    def _wait_for_exit(self, terminal_id: str, callback: Callable[[dict], None]) -> None:
        """reader 线程：挂一次性回调（进程退出时 _on_exited 触发）；已退出立即回调。"""
        with self._lock:
            rec = self._terms.get(terminal_id)
            if rec is not None and rec.exit_code is None:
                rec.wait_cbs.append(callback)
                return
            exit_code = rec.exit_code if rec is not None else -1
        callback({"exitCode": exit_code, "signal": None})

    def _kill(self, terminal_id: str) -> None:
        with self._lock:
            rec = self._terms.get(terminal_id)
        if rec is not None and rec.entry is not None:
            self._invoke_gui(rec.entry.session.terminate)  # PtySession.terminate 幂等

    def _release(self, terminal_id: str) -> None:
        """解除协议侧跟踪：tab 保留供用户查看，标题不变
        （退出状态由头部状态行「进程已退出 code N」承载，不占标题）。"""
        with self._lock:
            rec = self._terms.pop(terminal_id, None)
        if rec is None:
            return
        self._flush_waits(rec, rec.exit_code if rec.exit_code is not None else -1)

    # ------------------------------------------------------------------
    # 会话事件（GUI 线程，信号槽）
    # ------------------------------------------------------------------
    def _on_data(self, rec: _TermRecord, data: bytes) -> None:
        with self._lock:
            rec.buf += data
            if len(rec.buf) > _TAIL_BYTES:
                del rec.buf[:len(rec.buf) - _TAIL_BYTES]
                rec.truncated = True

    def _on_exited(self, rec: _TermRecord, code: int) -> None:
        with self._lock:
            rec.exit_code = code
        self._flush_waits(rec, code)

    def _on_tab_closed(self, session_entry: _Session) -> None:
        """用户手关 tab（GUI 线程）：等价 kill+release——进程已由面板
        terminate，此处清理簿记并以既有退出码（未知 -1）兜底挂起 wait。"""
        with self._lock:
            terminal_id = next(
                (tid for tid, rec in self._terms.items() if rec.entry is session_entry), None)
            rec = self._terms.pop(terminal_id, None) if terminal_id else None
        if rec is not None:
            self._flush_waits(rec, rec.exit_code if rec.exit_code is not None else -1)

    def _flush_waits(self, rec: _TermRecord, exit_code: int) -> None:
        """清账并触发该会话全部挂起 wait 回调（回调连接层 _complete_wait 补发响应）。"""
        with self._lock:
            cbs, rec.wait_cbs = rec.wait_cbs, []
        for cb in cbs:
            try:
                cb({"exitCode": exit_code, "signal": None})
            except Exception:  # noqa: BLE001 — 单回调异常不波及其余
                pass
