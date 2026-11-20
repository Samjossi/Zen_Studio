"""ACP terminal/* 反向请求分发与 env 解析。

terminal/* 是 ACP 标准反向能力：agent 请求 IDE 建终端、读输出、等待退出、
终止与释放。本模块只做协议分发与异步应答簿记，实际执行由注入的
`TerminalHandler` 承担（GUI 桥）；不 import 连接层，也不新增 shell 执行路径。
"""
import logging
import threading
from collections.abc import Callable

from ide.llm.providers.acp.protocol import TerminalHandler

logger = logging.getLogger(__name__)

#: terminal/* 反向请求方法集（无 handler 时分发维持 -32601 兜底语义）
TERMINAL_METHODS = frozenset({
    "terminal/create", "terminal/output", "terminal/wait_for_exit",
    "terminal/kill", "terminal/release",
})

#: 反向应答原语：连接层提供的写帧入口（返回 False 表示连接已失效/写失败）
WriteReverse = Callable[[dict, str, int | str], bool]


def parse_terminal_env(env: object) -> dict[str, str] | None:
    """terminal/create 的 env 载荷 → dict（协议为 [{name, value}] 数组，dict 容错直通）。"""
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    if isinstance(env, list):
        parsed = {str(item["name"]): str(item.get("value", ""))
                  for item in env if isinstance(item, dict) and "name" in item}
        return parsed or None
    return None


class TerminalDispatcher:
    """单个连接的 terminal/* 分发器。

    连接层注入写帧原语后，本类持有处理器与挂起的 wait_for_exit 响应 id；
    不反向持有连接对象，连接死讯由 on_connection_dead() 显式通知。
    """

    def __init__(self, agent_name: str, write_reverse: WriteReverse) -> None:
        self._agent_name = agent_name
        self._write_reverse = write_reverse
        self._handler: TerminalHandler | None = None
        self._pending_waits: set[int | str] = set()
        self._lock = threading.Lock()

    def set_handler(self, handler: TerminalHandler | None) -> None:
        """注入终端处理器（None = 不声明 terminal 能力，分发回 -32601）。"""
        self._handler = handler

    @property
    def handler(self) -> TerminalHandler | None:
        return self._handler

    def dispatch(self, obj: dict) -> bool:
        """处理一个 terminal/* 反向请求。

        :return: True 表示已由本分发器接管；False 表示无 handler 或非
            terminal 方法，连接层按原样回 -32601。
        """
        method = obj["method"]
        handler = self._handler
        if method not in TERMINAL_METHODS or handler is None:
            return False
        params = obj.get("params") or {}
        request_id = obj["id"]
        if method == "terminal/wait_for_exit":
            with self._lock:
                self._pending_waits.add(request_id)
            try:
                handler.wait_for_exit(
                    params.get("terminalId") or "",
                    lambda result, rid=request_id: self._complete_wait(rid, result))
            except Exception as e:  # noqa: BLE001 — handler 异常不阻塞 agent
                logger.exception(f"[{self._agent_name}] wait_for_exit 登记异常: {e}")
                with self._lock:
                    self._pending_waits.discard(request_id)
                self._reply_error(request_id, -32603, f"wait_for_exit 失败：{e}")
            return True
        try:
            if method == "terminal/create":
                terminal_id = handler.create(
                    params.get("command") or "", list(params.get("args") or []),
                    params.get("cwd"), parse_terminal_env(params.get("env")))
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
            return True
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id, "result": result}, method, request_id)
        return True

    def _complete_wait(self, request_id: int | str, result: dict) -> None:
        """wait_for_exit 异步应答：进程退出回调（GUI 线程）补发响应包。

        回调可能晚于死讯注入（id 已清）——登记处查无此项即丢弃；
        写帧由连接层原语串行化（与 reader 线程应答互斥）。
        """
        with self._lock:
            if request_id not in self._pending_waits:
                return
            self._pending_waits.discard(request_id)
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            "terminal/wait_for_exit", request_id)

    def _reply_error(self, request_id: int | str, code: int, message: str) -> None:
        """反向请求错误应答；写失败与其他路径共享连接失效收口。"""
        self._write_reverse(
            {"jsonrpc": "2.0", "id": request_id,
             "error": {"code": code, "message": message}},
            "reverse_error", request_id)

    def on_connection_dead(self) -> None:
        """连接死讯：先让 handler 清理并触发挂起回调，再清空本连接登记。"""
        handler = self._handler
        if handler is not None:
            try:
                handler.on_connection_dead()
            except Exception as e:  # noqa: BLE001 — 死讯路径异常不外溢
                logger.exception(f"[{self._agent_name}] 终端处理器死讯清理异常: {e}")
        with self._lock:
            self._pending_waits.clear()
