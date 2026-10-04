"""服务层：GUI 模式的「服务」语义与内核接线。

GUI 模式的「服务」语义选择（实现计划 §4-3.1 的开放式要求）：
stdio 不适合 GUI 常驻，因此本层把协议内核（transport/dispatcher/turn）的
传输端点从 stdio 换成**内存管道**，在守护线程里运行完整 ACP 内核；GUI 侧
再内嵌一个最小 ACP 客户端，走同一套 ndjson JSON-RPC 帧完成
initialize → session/new → prompt 全链路。由此：

- 「启动服务」= 拉起内核线程 + 内嵌客户端握手成功（initialize + session/new）；
- 「连接状态」= 握手协商的真实结果，不是 UI 假状态；
- 「会话/模型别名」= 协议 session/new / set_config_option 协商出的真值；
- 日志面板同时看到内核日志（transport.log 分流）与协议帧摘要。

内核代码零改动、不 import GUI（§3.1 解耦铁律）；跨线程上报一律经 Qt 信号，
禁止任何线程直接触碰 UI 控件。
"""
from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from PySide6.QtCore import QObject, Signal

from local_cli import AGENT_NAME, __version__
from local_cli.acp.dispatcher import RpcDispatcher
from local_cli.acp.session import SessionManager
from local_cli.acp.transport import ReverseRequestBroker, StdioTransport
from local_cli.acp.turn import SessionUpdateType
from local_cli.model.mock import DEMO_MODEL_ALIASES, MockLanguageModel


class ServiceState:
    """服务生命周期状态令牌（界面显示文案由 MainWindow 映射，语义与展示分离）。"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"


class MemoryPipe:
    """单方向内存文本管道：一端 write、另一端按行迭代，close 后迭代结束。

    StdioTransport 只依赖读端的迭代协议与写端的 write/flush（鸭子类型），
    因此本管道可直接顶替 stdio 成为内核传输端点，内核侧零改动。
    """

    #: 关闭哨兵：读端取到它即等价 stdin EOF（read_frames 迭代终止）
    _EOF_SENTINEL: object = object()

    def __init__(self) -> None:
        self._queue: queue.Queue[str | object] = queue.Queue()
        self._is_closed = False
        self._write_lock = threading.Lock()

    # ---------------- 读端（模拟 IO[str] 迭代协议） ----------------
    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        item = self._queue.get()
        if item is self._EOF_SENTINEL:
            raise StopIteration
        return item  # type: ignore[return-value]

    # ---------------- 写端（模拟 IO[str]） ----------------
    def write(self, text: str) -> int:
        with self._write_lock:
            if self._is_closed:
                # 关闭后静默丢弃：停止服务时轮次线程可能仍在流式输出，
                # 丢行比抛异常炸线程好（与 transport 宽容非 JSON 行同理）
                return 0
            self._queue.put(text)
            return len(text)

    def flush(self) -> None:
        """队列即写即达，无需缓冲冲刷；存在仅为满足 IO 鸭子类型。"""

    def close(self) -> None:
        """幂等关闭：哨兵排在已排队内容之后，残留帧仍能被读完再 EOF。"""
        with self._write_lock:
            if self._is_closed:
                return
            self._is_closed = True
            self._queue.put(self._EOF_SENTINEL)


class GuiStdioTransport(StdioTransport):
    """把内核日志同时分流到 GUI 回调；stderr 原通道保留（排障习惯一致，A.6）。

    继承仅一层（AFCP 2.4）：只覆盖 log 这一个扩展点，帧读写全部复用。
    """

    def __init__(
        self,
        input_stream: MemoryPipe,
        output_stream: MemoryPipe,
        log_sink: Callable[[str], None],
    ) -> None:
        super().__init__(agent_name=AGENT_NAME,
                         input_stream=input_stream, output_stream=output_stream)
        self._log_sink = log_sink

    def log(self, message: str) -> None:
        super().log(message)
        self._log_sink(message)


@dataclass
class _PendingCall:
    """客户端侧等待回执的一次调用（AFCP 3.1：显式结构，禁止裸元组流转）。"""
    done_event: threading.Event = field(default_factory=threading.Event)
    result_box: dict[str, Any] = field(default_factory=dict)
    #: 非 None 时走异步回调（prompt 轮次长，不能阻塞 GUI 线程）；
    #: None 时由调用方阻塞等 done_event（握手短促，阻塞可接受）
    on_result: Callable[[dict[str, Any]], None] | None = None


class EmbeddedAcpClient:
    """内嵌最小 ACP 客户端：以真实协议帧与内核对话，供 GUI 演示与状态同步。

    读循环在独立守护线程：内核的响应/通知/反向请求都在这里分发；
    审批反向请求按演示策略自动「允许一次」并记日志（真实审批 UI 留待后续）。
    """

    #: initialize 的 clientInfo（§1.3：客户端自报家门，内核记诊断日志）
    _CLIENT_INFO = {"name": "local-gui-embedded", "version": __version__}
    #: 握手回执等待上限：内核在本进程内，超时即视为内核异常
    _HANDSHAKE_TIMEOUT_SECONDS = 5.0
    #: 演示审批策略：固定选「允许一次」（§3 三态 options 的第一项 optionId）
    _AUTO_PERMISSION_OPTION_ID = "allow_once"

    def __init__(
        self,
        incoming: MemoryPipe,
        outgoing: MemoryPipe,
        on_session_update: Callable[[dict[str, Any]], None],
        on_log: Callable[[str], None],
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing
        self._on_session_update = on_session_update
        self._on_log = on_log
        self._next_request_id = 0
        self._id_lock = threading.Lock()
        self._pending: dict[int, _PendingCall] = {}
        self._reader_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start_reader(self) -> None:
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="gui-acp-client-reader")
        self._reader_thread.start()

    def join_reader(self, timeout_seconds: float) -> None:
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout_seconds)

    # ------------------------------------------------------------------
    # 出站：握手（阻塞）与轮次（异步）
    # ------------------------------------------------------------------
    def handshake(self, cwd: str) -> tuple[str, str]:
        """initialize + session/new，返回 (agent 描述, sessionId)；失败抛异常。"""
        init_result = self._request_blocking("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": dict(self._CLIENT_INFO),
        })
        agent_info = init_result.get("agentInfo") or {}
        agent_label = f"{agent_info.get('name', '?')} {agent_info.get('version', '?')}"
        new_result = self._request_blocking(
            "session/new", {"cwd": cwd, "mcpServers": []})
        session_id = str(new_result.get("sessionId") or "")
        self._on_log(f"握手完成：{agent_label}，会话 {session_id}")
        return agent_label, session_id

    def send_prompt_async(
        self,
        session_id: str,
        text: str,
        on_turn_result: Callable[[dict[str, Any]], None],
    ) -> None:
        """prompt 轮次异步化：流式 update 走 on_session_update，
        终态 stopReason 经 on_turn_result 回调（不阻塞调用线程）。"""
        request_id = self._register_pending(_PendingCall(on_result=on_turn_result))
        self._send_frame({
            "jsonrpc": "2.0", "id": request_id, "method": "session/prompt",
            "params": {"sessionId": session_id,
                       "prompt": [{"type": "text", "text": text}]},
        })

    def send_model_alias_async(
        self,
        session_id: str,
        model_alias: str,
        on_result: Callable[[dict[str, Any]], None],
    ) -> None:
        """set_config_option 异步化（§1.5：别名是不透明字符串，原样传递）。"""
        request_id = self._register_pending(_PendingCall(on_result=on_result))
        self._send_frame({
            "jsonrpc": "2.0", "id": request_id,
            "method": "session/set_config_option",
            "params": {"sessionId": session_id,
                       "configId": "model", "value": model_alias},
        })

    def send_cancel(self, session_id: str) -> None:
        # §1.6：cancel 是通知无需响应
        self._send_frame({
            "jsonrpc": "2.0", "method": "session/cancel",
            "params": {"sessionId": session_id},
        })

    # ------------------------------------------------------------------
    # 入站读循环
    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        for line in self._incoming:
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                # 与 transport 同样的宽容策略：丢行比崩连接好
                self._on_log(f"客户端丢弃非 JSON 行: {line[:200]}")
                continue
            self._route_frame(frame)

    def _route_frame(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        if method == "session/update":
            self._on_session_update((frame.get("params") or {}).get("update") or {})
            return
        if method is not None and frame.get("id") is not None:
            self._reply_reverse_request(frame)
            return
        if frame.get("id") is not None:
            self._resolve_pending(frame)

    def _reply_reverse_request(self, frame: dict[str, Any]) -> None:
        """审批反向请求自动「允许一次」（演示策略）；未知反向请求同样放行并记日志。"""
        self._on_log(f"自动批准反向请求: {frame.get('method')}"
                     f"（{self._AUTO_PERMISSION_OPTION_ID}）")
        self._send_frame({
            "jsonrpc": "2.0", "id": frame.get("id"),
            "result": {"outcome": {"outcome": "selected",
                                   "optionId": self._AUTO_PERMISSION_OPTION_ID}},
        })

    def _resolve_pending(self, frame: dict[str, Any]) -> None:
        pending = self._pending.pop(frame.get("id"), None)
        if pending is None:
            self._on_log(f"无等待者的回执，忽略: id={frame.get('id')}")
            return
        if pending.on_result is not None:
            pending.on_result(frame)
            return
        pending.result_box.update(frame)
        pending.done_event.set()

    # ------------------------------------------------------------------
    # 帧收发助手
    # ------------------------------------------------------------------
    def _request_blocking(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        pending = _PendingCall()
        request_id = self._register_pending(pending)
        self._send_frame({"jsonrpc": "2.0", "id": request_id,
                          "method": method, "params": params})
        if not pending.done_event.wait(self._HANDSHAKE_TIMEOUT_SECONDS):
            self._pending.pop(request_id, None)
            raise TimeoutError(f"握手超时：{method} 无回执")
        frame = pending.result_box
        if frame.get("error") is not None:
            error = frame["error"]
            raise RuntimeError(f"{method} 被拒: {error.get('code')} {error.get('message')}")
        return frame.get("result") or {}

    def _register_pending(self, pending: _PendingCall) -> int:
        with self._id_lock:
            self._next_request_id += 1
            request_id = self._next_request_id
        # 先登记后发送：回执可能在 send 返回前就被读循环处理，顺序颠倒会丢回执
        self._pending[request_id] = pending
        return request_id

    def _send_frame(self, payload: dict[str, Any]) -> None:
        # MemoryPipe.write 自带写锁串行化，多线程发送不会拼出脏帧
        self._outgoing.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._outgoing.flush()


class GuiAcpService(QObject):
    """GUI 侧服务门面：生命周期管理 + Qt 信号上报（主线程与内核线程的唯一桥梁）。

    所有信号都从工作线程发射，由 Qt 队列连接投递到 GUI 线程——
    本类与 MainWindow 都不允许跨线程直接操作控件。
    """

    log_received = Signal(str)
    state_changed = Signal(str)            # ServiceState 令牌
    session_changed = Signal(str, str)     # session_id, model_alias
    chunk_received = Signal(str, bool)     # text, is_thought
    turn_finished = Signal(str)            # stopReason

    #: 停线程等待上限：内核在 EOF 后即刻退出，2 秒足够；卡死也不拖住关窗
    _THREAD_JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._state = ServiceState.STOPPED
        self._session_id = ""
        self._model_alias = DEMO_MODEL_ALIASES[0]
        self._client: EmbeddedAcpClient | None = None
        self._kernel_thread: threading.Thread | None = None
        self._kernel_in: MemoryPipe | None = None
        self._kernel_out: MemoryPipe | None = None
        self._sessions: SessionManager | None = None

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        with self._lock:
            return self._state == ServiceState.RUNNING

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """异步启动：握手在引导线程内完成，避免任何内核异常拖住 GUI 线程。"""
        with self._lock:
            if self._state != ServiceState.STOPPED:
                return
            self._state = ServiceState.STARTING
        self.state_changed.emit(ServiceState.STARTING)
        threading.Thread(
            target=self._bootstrap, args=(os.getcwd(),),
            daemon=True, name="gui-acp-bootstrap",
        ).start()

    def stop(self) -> None:
        """幂等停止（关机方案 §4：退出路径可被触发多次，清理逻辑须幂等）。

        关闭管道即给内核读循环送 EOF，serve() 自然返回——
        与 CLI 模式客户端关管道退出进程是同一条收尾路径。
        """
        with self._lock:
            if self._state == ServiceState.STOPPED:
                return
            self._state = ServiceState.STOPPED
            client, kernel_thread = self._client, self._kernel_thread
            kernel_in, kernel_out = self._kernel_in, self._kernel_out
            sessions, session_id = self._sessions, self._session_id
            self._client = self._kernel_thread = None
            self._kernel_in = self._kernel_out = None
            self._sessions = None
        # 先置取消标志：在跑的轮次于下一个 chunk 间即停，线程回收更快
        if sessions is not None and session_id:
            session = sessions.find(session_id)
            if session is not None:
                session.cancel_event.set()
        if kernel_in is not None:
            kernel_in.close()
        if kernel_out is not None:
            kernel_out.close()
        if kernel_thread is not None and kernel_thread.is_alive():
            kernel_thread.join(self._THREAD_JOIN_TIMEOUT_SECONDS)
        if client is not None:
            client.join_reader(self._THREAD_JOIN_TIMEOUT_SECONDS)
        self.log_received.emit("服务已停止")
        self.state_changed.emit(ServiceState.STOPPED)

    def _bootstrap(self, cwd: str) -> None:
        """引导线程：组装内核 → 起线程 → 握手；任一环节失败回到 STOPPED。"""
        kernel_in, kernel_out = MemoryPipe(), MemoryPipe()
        transport = GuiStdioTransport(kernel_in, kernel_out, self._emit_kernel_log)
        sessions = SessionManager(default_model_alias=self._model_alias)
        dispatcher = RpcDispatcher(
            transport=transport,
            broker=ReverseRequestBroker(transport),
            sessions=sessions,
            model=MockLanguageModel(),
            agent_name=AGENT_NAME,
        )
        client = EmbeddedAcpClient(
            incoming=kernel_out, outgoing=kernel_in,
            on_session_update=self._handle_session_update,
            on_log=self._emit_client_log,
        )
        kernel_thread = threading.Thread(
            target=dispatcher.serve, daemon=True, name="gui-acp-kernel")
        with self._lock:
            if self._state != ServiceState.STARTING:
                # stop() 在组装期间已介入：线程尚未启动，无资源可泄
                return
            self._client = client
            self._kernel_thread = kernel_thread
            self._kernel_in, self._kernel_out = kernel_in, kernel_out
            self._sessions = sessions
        kernel_thread.start()
        client.start_reader()
        try:
            _agent_label, session_id = client.handshake(cwd)
        except (TimeoutError, RuntimeError) as handshake_error:
            self._emit_client_log(f"服务启动失败：{handshake_error}")
            self.stop()
            return
        with self._lock:
            if self._state != ServiceState.STARTING:
                # 握手期间 stop() 已介入，状态以 stop 为准
                return
            self._state = ServiceState.RUNNING
            self._session_id = session_id
        self.session_changed.emit(session_id, self._model_alias)
        self.state_changed.emit(ServiceState.RUNNING)

    # ------------------------------------------------------------------
    # 业务动作（仅 RUNNING 态可用；内核校验兜底，GUI 只做轻量前置判断）
    # ------------------------------------------------------------------
    def send_prompt(self, text: str) -> bool:
        with self._lock:
            if self._state != ServiceState.RUNNING or self._client is None:
                return False
            client, session_id = self._client, self._session_id
        client.send_prompt_async(session_id, text, self._handle_turn_result)
        return True

    def send_model_alias(self, model_alias: str) -> bool:
        with self._lock:
            if self._state != ServiceState.RUNNING or self._client is None:
                return False
            client, session_id = self._client, self._session_id
        # 别名不做乐观更新：等内核确认（回执到达）后才记入服务状态，
        # 避免未知别名被拒时 GUI 状态与内核状态背离
        client.send_model_alias_async(
            session_id, model_alias,
            lambda frame: self._handle_model_change_result(frame, model_alias))
        return True

    def send_cancel(self) -> None:
        with self._lock:
            if self._state != ServiceState.RUNNING or self._client is None:
                return
            client, session_id = self._client, self._session_id
        client.send_cancel(session_id)
        self._emit_client_log("已发送 session/cancel")

    # ------------------------------------------------------------------
    # 客户端回调 → Qt 信号（全部发射自工作线程，Qt 队列连接保证 UI 安全）
    # ------------------------------------------------------------------
    def _emit_kernel_log(self, message: str) -> None:
        self.log_received.emit(f"[{AGENT_NAME}] {message}")

    def _emit_client_log(self, message: str) -> None:
        self.log_received.emit(f"[gui] {message}")

    def _handle_session_update(self, update: dict[str, Any]) -> None:
        update_type = update.get("sessionUpdate")
        if update_type in (SessionUpdateType.AGENT_MESSAGE_CHUNK,
                           SessionUpdateType.AGENT_THOUGHT_CHUNK):
            text = (update.get("content") or {}).get("text") or ""
            self.chunk_received.emit(
                text, update_type == SessionUpdateType.AGENT_THOUGHT_CHUNK)
            return
        if update_type == SessionUpdateType.USAGE_UPDATE:
            self._emit_client_log(
                f"usage_update: used={update.get('used')} size={update.get('size')}")
            return
        if update_type in (SessionUpdateType.TOOL_CALL,
                           SessionUpdateType.TOOL_CALL_UPDATE):
            self._emit_client_log(
                f"{update_type}: {update.get('title') or update.get('status') or ''}")
            return
        self._emit_client_log(f"session/update: {update_type}")

    def _handle_turn_result(self, frame: dict[str, Any]) -> None:
        if frame.get("error") is not None:
            error = frame["error"]
            self._emit_client_log(f"轮次出错: {error.get('message')}")
            self.turn_finished.emit("error")
            return
        stop_reason = ((frame.get("result") or {}).get("stopReason")) or "unknown"
        self._emit_client_log(f"轮次结束: stopReason={stop_reason}")
        self.turn_finished.emit(stop_reason)

    def _handle_model_change_result(self, frame: dict[str, Any], model_alias: str) -> None:
        if frame.get("error") is not None:
            self._emit_client_log(f"模型切换被拒: {frame['error'].get('message')}")
            return
        with self._lock:
            self._model_alias = model_alias
            session_id = self._session_id
        self._emit_client_log(f"模型别名已切换: {model_alias}")
        self.session_changed.emit(session_id, model_alias)
