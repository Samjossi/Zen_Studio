"""ACP 连接层：长驻 agent 子进程 + JSON-RPC（ndjson 帧）收发。

连接构造接收完整 argv（启动命令由各厂商 provider 组装），本模块不拼接
`[binary, "acp"]`，也不含厂商错误文案；错误文案只使用调用方传入的
agent_name 做中性前缀。
"""
import itertools
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque

from ide.core.child_env import with_non_interactive_pagers
from ide.llm.providers.acp.protocol import (
    PermissionHandler,
    PermissionOption,
    PermissionParams,
    TerminalHandler,
    _TurnMessage,
    _parse_terminal_env,
)

logger = logging.getLogger(__name__)

_ACP_TIMEOUT_S = 30  # initialize / session/new / set_config_option 等控制请求超时
_STDERR_MAX_LINES = 40
_STDERR_MAX_BYTES = 8 * 1024
_STDERR_LINE_MAX = 1000

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password)"
    r"\b[\"']?\s*[:=]\s*[\"']?([^\s,;\"'}]+)")
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _redact_diagnostic(text: str) -> str:
    text = _AUTH_SCHEME_RE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = _SENSITIVE_VALUE_RE.sub(lambda match: match.group(0).replace(
        match.group(2), "<redacted>"), text)
    return _JWT_RE.sub("<redacted>", text)


class _StderrRing:
    """有界诊断尾部；先入缓冲前脱敏，避免异常路径再泄漏完整凭证。"""

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._bytes = 0
        self._truncated = False
        self._lock = threading.Lock()

    @staticmethod
    def _size(line: str) -> int:
        return len(line.encode("utf-8")) + 1

    def append(self, line: str) -> None:
        line = _redact_diagnostic(line.rstrip("\r\n"))
        if len(line) > _STDERR_LINE_MAX:
            line = line[:_STDERR_LINE_MAX] + "…[单行截断]"
            self._truncated = True
        size = self._size(line)
        with self._lock:
            if size > _STDERR_MAX_BYTES:
                self._lines.clear()
                self._bytes = 0
                self._truncated = True
                return
            self._lines.append(line)
            self._bytes += size
            while (len(self._lines) > _STDERR_MAX_LINES
                   or self._bytes > _STDERR_MAX_BYTES):
                removed = self._lines.popleft()
                self._bytes -= self._size(removed)
                self._truncated = True

    def tail(self) -> str:
        with self._lock:
            text = "\n".join(self._lines)
            truncated = self._truncated
        if not text:
            return ""
        return ("[stderr 已截断]\n" if truncated else "") + text


#: next_update 拆拍节拍（秒）：timeout=None 时内部以该粒度轮询队列，
#: 使阻塞中的轮次消费能响应本地中断（TurnAborted）；对外阻塞语义不变
_NEXT_UPDATE_TICK_S = 1.0


class TurnAborted(Exception):
    """轮次本地中断信号：cancel 路径置位 _turn_abort 后，阻塞中的
    next_update 拆拍检查发现即抛出。agent 沉默时协议 cancel 无回包，
    本地中断是解封 worker 的唯一路径（2026-1118-1115 计划 D1）。"""


class TurnCancelled(Exception):
    """agent 明确回 `stopReason=cancelled` 的非成功终态。

    与 TurnAborted 分列：TurnAborted 是 IDE 本地中断（用户停止/看门狗），
    无需 agent 回包；本异常只在收到 prompt 响应且 stopReason 为 cancelled
    时抛出，走停止收尾而非失败通道——两者都不显示 [请求失败]。"""

#: terminal/* 反向请求方法集（无 handler 时分发维持 -32601 兜底语义）
_TERMINAL_METHODS = frozenset({
    "terminal/create", "terminal/output", "terminal/wait_for_exit",
    "terminal/kill", "terminal/release",
})


_connection_generations = itertools.count(1)


class AcpConnection:
    """长驻 agent 子进程：ndjson 帧收发、请求 id 配对、通知与反向请求分发。

    消息分两条出路：响应按 id 进 `_pending`（同步 `request()` 阻塞等待）；
    轮次内消息（session/update 通知与 prompt 响应）进 `_updates` 由 `chat()` 消费。
    同一时刻仅一个活跃轮次（各 provider 的 `_turn_lock` 保证）。
    """

    def __init__(self, argv: list[str], cwd: str, agent_name: str) -> None:
        """
        :param argv: 完整启动命令（由各厂商 provider 组装，如
            `[bin_path, "acp"]`；连接层不再拼 `"acp"` 子命令）
        :param cwd: agent 子进程工作目录（多开模式由启动参数注入）
        :param agent_name: agent 显示名（错误文案用，如 "kimi acp"）
        """
        self._agent_name = agent_name
        self.generation = next(_connection_generations)
        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=with_non_interactive_pagers(os.environ),
            )
        except OSError as e:
            raise RuntimeError(f"{agent_name} 启动失败：{e}") from e
        self._next = 0
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict]] = {}
        self._updates: queue.Queue[_TurnMessage] = queue.Queue()
        self._turn_id: int | None = None  # 活跃轮次的 prompt 请求 id（其响应改走 _updates）
        #: 轮次本地中断（abort_turn 置位，begin_turn 清除）：agent 沉默时
        #: 协议 cancel 无回包，本信号让阻塞中的 next_update 拆拍逃逸
        self._turn_abort = threading.Event()
        self.is_alive = True
        self._terminated = False
        self._expected_close = False
        self._failure_reason: str | None = None
        self._failure_method: str | None = None
        self._last_request_id: int | str | None = None
        self._stderr_ring = _StderrRing()
        #: 审批处理器（GUI 经 set_permission_handler 注入）；None=自动允许（C2 语义）
        self._permission_handler: PermissionHandler | None = None
        #: 终端处理器（GUI 经 set_terminal_handler 注入）；None=terminal/*
        #: 反向请求维持 -32601 兜底（未声明能力时 agent 本不该发起）
        self._terminal_handler: TerminalHandler | None = None
        #: 挂起的 terminal/wait_for_exit 响应 id 集（异步应答簿记；
        #: 访问均持 _write_lock）：进程退出回调到达时补发响应包
        self._pending_waits: set[int | str] = set()
        threading.Thread(target=self._reader, daemon=True).start()
        if getattr(self._proc, "stderr", None) is not None:
            threading.Thread(target=self._stderr_reader, daemon=True).start()
        logger.info(
            "[%s] 连接启动 generation=%s pid=%s",
            self._agent_name, self.generation, getattr(self._proc, "pid", None))

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器（None = 自动允许，C2 语义）。"""
        self._permission_handler = handler

    def set_terminal_handler(self, handler: TerminalHandler | None) -> None:
        """注入终端处理器（None = terminal/* 反向请求回 -32601）。"""
        self._terminal_handler = handler

    # ------------------------------------------------------------------
    # 帧收发
    # ------------------------------------------------------------------
    def _send(self, msg: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _reader(self) -> None:
        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self._dispatch_line(line)
        finally:
            self._mark_dead("eof", "stdout_reader")

    def _stderr_reader(self) -> None:
        try:
            stream = self._proc.stderr
            if stream is not None:
                for line in stream:
                    self._stderr_ring.append(line)
        except (OSError, ValueError):
            if not self._expected_close:
                logger.exception(
                    "[%s] stderr 读取失败 generation=%s",
                    self._agent_name, self.generation)

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    def log_diagnostics(self, event: str, method: str | None = None) -> None:
        tail = self._stderr_ring.tail()
        logger.warning(
            "[%s] 诊断 event=%s generation=%s pid=%s method=%s requestId=%s "
            "reason=%s exitCode=%s",
            self._agent_name, event, self.generation,
            getattr(self._proc, "pid", None), method or self._failure_method,
            self._last_request_id, self._failure_reason, self._proc.poll())
        if tail:
            logger.warning(
                "[%s] stderr 尾部 generation=%s（有界脱敏）\n%s",
                self._agent_name, self.generation, tail)

    def _mark_dead(
        self,
        reason: str,
        method: str | None = None,
        request_id: int | str | None = None,
    ) -> None:
        """失效单点收口；先释放写锁再调用，所有等待方由同一死讯唤醒。"""
        with self._state_lock:
            if self._terminated:
                return
            self._terminated = True
            self.is_alive = False
            self._failure_reason = reason
            self._failure_method = method
            if request_id is not None:
                self._last_request_id = request_id
            expected = self._expected_close
        exit_code = self._proc.poll()
        self._updates.put(("dead", exit_code))
        message = (f"{self._agent_name} 连接已关闭" if expected
                   else f"{self._agent_name} 连接失效（{reason}）")
        for pending_queue in list(self._pending.values()):
            pending_queue.put({"error": {"code": -32099, "message": message}})
        handler = self._terminal_handler
        if handler is not None:
            try:
                handler.on_connection_dead()
            except Exception as e:  # noqa: BLE001 — 死讯路径异常不外溢
                logger.exception(f"[{self._agent_name}] 终端处理器死讯清理异常: {e}")
        with self._write_lock:
            self._pending_waits.clear()
        if expected:
            logger.info(
                "[%s] 连接关闭 generation=%s reason=%s exitCode=%s",
                self._agent_name, self.generation, reason, exit_code)
        else:
            self.log_diagnostics("connection_dead", method)

    def _write_reverse(self, msg: dict, method: str, request_id: int | str) -> bool:
        error: OSError | ValueError | None = None
        with self._write_lock:
            if not self.is_alive:
                return False
            try:
                self._send(msg)
            except (OSError, ValueError) as e:
                error = e
        if error is not None:
            self._last_request_id = request_id
            self._mark_dead("write_error", method, request_id)
            return False
        return True

    def _dispatch_line(self, line: str) -> None:
        """单帧分发：反向请求 / 轮次内通知与响应（→_updates）/ 控制响应（→_pending）。"""
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.exception(f"[{self._agent_name}] 非 JSON 帧: {line[:200]}")
            return
        if "method" in obj and "id" in obj:
            self._handle_reverse(obj)  # 反向请求须及时应答，防 agent 阻塞
            return
        if "method" in obj:
            if obj["method"] == "session/update":
                self._updates.put(("update", obj))
            return
        if "id" not in obj:
            return
        if obj["id"] == self._turn_id:
            self._updates.put(("response", obj))
            return
        if pending_queue := self._pending.get(obj["id"]):
            pending_queue.put(obj)

    def _handle_reverse(self, obj: dict) -> None:
        """反向请求（agent→client）：审批经 handler 路由（无 handler 自动允许）；
        terminal/* 经 handler 路由（无 handler 兜底 methodNotFound，与未声明
        能力的现状等价）；fs 未声明能力，兜底 methodNotFound。须及时应答，防 agent 阻塞。"""
        if obj["method"] == "session/request_permission":
            params: PermissionParams = obj.get("params") or {}
            options = params.get("options") or []
            option_id: str | None = None
            if self._permission_handler is not None:
                try:
                    option_id = self._permission_handler(params)
                except Exception as e:  # noqa: BLE001 — handler 异常不阻塞 agent，兜底拒绝
                    logger.exception(f"[{self._agent_name}] 审批处理器异常: {e}")
                if option_id is None:  # 用户取消/超时/handler 异常 → 兜底拒绝
                    option_id = self._pick_option(options, "reject_once")
            else:
                option_id = self._pick_option(options, "allow_once")  # C2 语义：等价 -p auto
            if option_id is not None:
                self._write_reverse({"jsonrpc": "2.0", "id": obj["id"], "result": {
                    "outcome": {"outcome": "selected", "optionId": option_id}}},
                    "session/request_permission", obj["id"])
            else:  # agent 未给任何选项：按协议回 cancelled
                self._write_reverse({"jsonrpc": "2.0", "id": obj["id"], "result": {
                    "outcome": {"outcome": "cancelled"}}},
                    "session/request_permission", obj["id"])
        elif obj["method"] in _TERMINAL_METHODS and self._terminal_handler is not None:
            self._handle_terminal(obj)
        else:
            self._write_reverse({"jsonrpc": "2.0", "id": obj["id"],
                                 "error": {"code": -32601, "message": "method not found"}},
                                obj["method"], obj["id"])

    # ------------------------------------------------------------------
    # terminal/* 反向请求（T1，2026-0817-1554 计划；无 handler 时维持 -32601 现状）
    # ------------------------------------------------------------------
    def _handle_terminal(self, obj: dict) -> None:
        """terminal/* 分发：create/output/kill/release 同步应答；
        wait_for_exit 长阻塞语义改异步应答（登记 response id 挂起，进程退出
        回调到达时 _complete_wait 补发响应包——严禁占 reader 线程等待）。"""
        method = obj["method"]
        params = obj.get("params") or {}
        request_id = obj["id"]
        handler = self._terminal_handler
        assert handler is not None  # 分发处已判空
        if method == "terminal/wait_for_exit":
            with self._write_lock:
                self._pending_waits.add(request_id)
            try:
                handler.wait_for_exit(
                    params.get("terminalId") or "",
                    lambda result, rid=request_id: self._complete_wait(rid, result))
            except Exception as e:  # noqa: BLE001 — handler 异常不阻塞 agent
                logger.exception(f"[{self._agent_name}] wait_for_exit 登记异常: {e}")
                with self._write_lock:
                    self._pending_waits.discard(request_id)
                self._reply_error(request_id, -32603, f"wait_for_exit 失败：{e}")
            return
        try:
            if method == "terminal/create":
                terminal_id = handler.create(
                    params.get("command") or "", list(params.get("args") or []),
                    params.get("cwd"), _parse_terminal_env(params.get("env")))
                result: dict = {"terminalId": terminal_id}
            elif method == "terminal/output":
                result = handler.output(params.get("terminalId") or "")
            elif method == "terminal/kill":
                handler.kill(params.get("terminalId") or "")
                result = {}
            else:  # terminal/release
                handler.release(params.get("terminalId") or "")
                result = {}
        except Exception as e:  # noqa: BLE001 — handler 异常回 -32603，防 agent 永久阻塞
            logger.exception(f"[{self._agent_name}] {method} 处理器异常: {e}")
            self._reply_error(request_id, -32603, f"{method} 失败：{e}")
            return
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id, "result": result}, method, request_id)

    def _complete_wait(self, request_id: int, result: dict) -> None:
        """wait_for_exit 异步应答：进程退出回调（GUI 线程）补发响应包。

        回调可能晚于死讯注入（id 已清）——登记处查无此项即丢弃；
        _write_lock 串行化跨线程写帧（与 reader 线程应答互斥）。
        """
        with self._write_lock:
            if request_id not in self._pending_waits:
                return
            self._pending_waits.discard(request_id)
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            "terminal/wait_for_exit", request_id)

    def _reply_error(self, request_id: int, code: int, message: str) -> None:
        """反向请求错误应答；写失败与其他路径共享连接失效收口。"""
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id,
             "error": {"code": code, "message": message}},
            "reverse_error", request_id)

    @staticmethod
    def _pick_option(options: list[PermissionOption], kind: str) -> str | None:
        """按 kind 选 optionId（回应用 agent 提供的 optionId 原值，不臆造）。"""
        option = next((o for o in options if o.get("kind") == kind), None)
        return option["optionId"] if option else None

    # ------------------------------------------------------------------
    # 请求原语
    # ------------------------------------------------------------------
    def request(self, method: str, params: dict, timeout: float = _ACP_TIMEOUT_S) -> dict:
        """同步请求（轮次外使用）：阻塞至响应/超时/进程死亡。"""
        error: OSError | ValueError | None = None
        with self._write_lock:
            self._next += 1
            request_id = self._next
            self._last_request_id = request_id
            pending_queue: queue.Queue[dict] = queue.Queue()
            self._pending[request_id] = pending_queue
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                error = e
                self._pending.pop(request_id, None)
        if error is not None:
            self._mark_dead("write_error", method, request_id)
            raise RuntimeError(f"{self._agent_name} 写入失败：{method}：{error}") from error
        try:
            resp = pending_queue.get(timeout=timeout)
        except queue.Empty:
            self.log_diagnostics("request_timeout", method)
            raise RuntimeError(f"{self._agent_name} 请求超时：{method}") from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"{self._agent_name} {method} 错误 {err.get('code')}：{err.get('message')}")
        return resp.get("result") or {}

    def begin_turn(self, method: str, params: dict) -> int:
        """登记轮次请求：其响应与通知同走 `_updates`，由 chat() 逐条消费。"""
        error: OSError | ValueError | None = None
        with self._write_lock:
            self._turn_abort.clear()  # 新一轮消费前清中断：上轮 cancel 残留不得误伤
            self._next += 1
            self._turn_id = self._next
            request_id = self._turn_id
            self._last_request_id = request_id
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                error = e
                self._turn_id = None
        if error is not None:
            self._mark_dead("write_error", method, request_id)
            raise RuntimeError(f"{self._agent_name} 写入失败：{method}：{error}") from error
        return request_id

    def abort_turn(self) -> None:
        """本地中断当前轮次：置 _turn_abort，阻塞中的 next_update 拆拍
        检查发现后以 TurnAborted 解封（可从任意线程调用，幂等）。

        与 cancel_turn 的协议通知互补——协议通知给 agent 优雅收场的机会，
        本地中断保证 agent 沉默（丢失任务完成事件等）时 worker 也能在
        秒级醒来。无活跃轮次时置位无害：begin_turn 统一清除。
        """
        self._turn_abort.set()
        logger.info(
            "[%s] 轮次本地中断 generation=%s requestId=%s",
            self._agent_name, self.generation, self._turn_id)

    def end_turn(self) -> None:
        self._turn_id = None

    def cancel_turn(self, session_id: str) -> bool:
        """取消当前轮次：发 `session/cancel` 通知（agent 收到后结束本轮）。

        无活跃轮次或连接已死时返回 False（无害 no-op）；
        可从任意线程调用（写锁串行化）。连接进程保留，会话不毁。
        """
        if self._turn_id is None or not self.is_alive:
            return False
        error: OSError | ValueError | None = None
        with self._write_lock:
            try:
                self._send({"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": session_id}})
            except (OSError, ValueError) as e:
                error = e
        if error is not None:
            self._mark_dead("write_error", "session/cancel", self._turn_id)
            return False
        logger.info(
            "[%s] 轮次取消 generation=%s sessionId=%s requestId=%s",
            self._agent_name, self.generation, session_id, self._turn_id)
        return True

    def purge_updates(self) -> None:
        """清空上一轮残留的迟到通知，防串轮。"""
        while True:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                return

    def next_update(self, timeout: float | None = None) -> _TurnMessage:
        """取下一条轮次内消息（默认无超时阻塞：agent 轮次可长达数分钟）。

        timeout 秒数超时抛 queue.Empty（0813-1919 计划 T4：kimi wire
        旁路与 conn 双源汇合的轮询消费用；其余后端不传超时保持原语义）。

        2026-1118-1115 计划 D1 拆拍：内部以 ≤_NEXT_UPDATE_TICK_S 的队列
        等待逼近调用方语义，每拍检查 _turn_abort——置位即抛 TurnAborted。
        对外语义不变（None=无限等帧），但 agent 永久沉默时阻塞中的轮次
        消费可在秒级被本地中断解封（停止按钮不失效的前提）。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._turn_abort.is_set():
                raise TurnAborted()
            if deadline is None:
                slice_timeout = _NEXT_UPDATE_TICK_S
            else:
                slice_timeout = min(_NEXT_UPDATE_TICK_S,
                                    max(0.0, deadline - time.monotonic()))
            try:
                return self._updates.get(timeout=slice_timeout)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise

    def terminate(self) -> None:
        """终止子进程并主动注入死讯：consumer 立即醒来，不必等 reader EOF。

        标签关闭路径（2026-0722-1117 计划 T5）：阻塞在 next_update()/
        request() 的 worker 依赖死讯/错误帧收尾；主动注入把解封延迟
        从"reader 反应过来"压到毫秒级（幂等失效收口见 _mark_dead）。
        """
        self._expected_close = True
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.exception("agent 进程 terminate 超时，强制 kill")
                self._proc.kill()
        self._mark_dead("terminate")
