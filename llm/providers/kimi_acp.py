"""Kimi ACP provider：长驻 `kimi acp` 子进程 + JSON-RPC（ndjson 帧）对接。

与 kimi_cli（每轮 spawn + stream-json）的差异：长驻单进程、token 级流式
（`agent_message_chunk`）、思维链可见（`agent_thought_chunk`）、会话原生管理
（`session/new`）；历史由 agent 侧会话管理，仅取末条 user 消息作 prompt。
凭证由 CLI 自管（OAuth），代码库零密钥。审批反向请求本期自动允许（allow_once 优先，
等价于 `-p` 的 auto 语义；UI 审批回环见 ACP 计划 C3）。
"""
import atexit
import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Iterator, Literal, TypedDict

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from llm.base import Chunk, LanguageModel, Message
from llm.providers.kimi_cli import _find_bin

_ACP_TIMEOUT_S = 30  # initialize / session/new / set_config_option 等控制请求超时


# ----------------------------------------------------------------------
# ACP 协议层定型（传输边界 dict 合理，入口处定型；键名依协议原文 camelCase）
# ----------------------------------------------------------------------
class PermissionOption(TypedDict, total=False):
    """`session/request_permission` 的单个选项（agent 提供，optionId 回应用原值）。"""
    optionId: str
    name: str
    kind: str  # allow_once / allow_always / reject_once / reject_always


class ToolCallInfo(TypedDict, total=False):
    """审批请求携带的工具调用信息（键全为可选，agent 实发字段随工具而异）。"""
    title: str
    kind: str
    rawInput: dict
    content: list
    locations: list


class PermissionParams(TypedDict, total=False):
    """`session/request_permission` 的 params（ACP 入口定型点）。"""
    sessionId: str
    toolCall: ToolCallInfo
    options: list[PermissionOption]


#: 审批处理器签名：session/request_permission params → optionId（None 视为拒绝）
PermissionHandler = Callable[[PermissionParams], str | None]

#: 轮次内消息：update/response 载荷为 JSON-RPC 帧，dead 载荷为进程退出码
_TurnMessage = tuple[Literal["update", "response"], dict] | tuple[Literal["dead"], int | None]


class _AcpConnection:
    """长驻 `kimi acp` 子进程：ndjson 帧收发、请求 id 配对、通知与反向请求分发。

    消息分两条出路：响应按 id 进 `_pending`（同步 `request()` 阻塞等待）；
    轮次内消息（session/update 通知与 prompt 响应）进 `_updates` 由 `chat()` 消费。
    同一时刻仅一个活跃轮次（`KimiAcpLLM._turn_lock` 保证）。
    """

    def __init__(self, bin_path: str) -> None:
        try:
            self._proc = subprocess.Popen(
                [bin_path, "acp"],
                cwd=PROJECT_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # ACP 日志走 stderr/~/.kimi-code/logs，不属协议
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as e:
            raise RuntimeError(f"kimi acp 启动失败：{e}") from e
        self._next = 0
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict]] = {}
        self._updates: queue.Queue[_TurnMessage] = queue.Queue()
        self._turn_id: int | None = None  # 活跃轮次的 prompt 请求 id（其响应改走 _updates）
        self.is_alive = True
        #: 审批处理器（GUI 经 set_permission_handler 注入）；None=自动允许（C2 语义）
        self._permission_handler: PermissionHandler | None = None
        threading.Thread(target=self._reader, daemon=True).start()

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器（None = 自动允许，C2 语义）。"""
        self._permission_handler = handler

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
            self.is_alive = False
            self._updates.put(("dead", self._proc.poll()))
            for pending_queue in self._pending.values():
                pending_queue.put({"error": {"code": -32099, "message": "kimi acp 进程意外退出"}})

    def _dispatch_line(self, line: str) -> None:
        """单帧分发：反向请求 / 轮次内通知与响应（→_updates）/ 控制响应（→_pending）。"""
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"[kimi-acp] 非 JSON 帧: {line[:200]}", file=sys.stderr)
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
        fs/terminal 未声明能力，兜底 methodNotFound。须及时应答，防 agent 阻塞。"""
        if obj["method"] == "session/request_permission":
            params: PermissionParams = obj.get("params") or {}
            options = params.get("options") or []
            option_id: str | None = None
            if self._permission_handler is not None:
                try:
                    option_id = self._permission_handler(params)
                except Exception as e:  # noqa: BLE001 — handler 异常不阻塞 agent，兜底拒绝
                    print(f"[kimi-acp] 审批处理器异常: {e}", file=sys.stderr)
                if option_id is None:  # 用户取消/超时/handler 异常 → 兜底拒绝
                    option_id = self._pick_option(options, "reject_once")
            else:
                option_id = self._pick_option(options, "allow_once")  # C2 语义：等价 -p auto
            with self._write_lock:
                if option_id is not None:
                    self._send({"jsonrpc": "2.0", "id": obj["id"], "result": {
                        "outcome": {"outcome": "selected", "optionId": option_id}}})
                else:  # agent 未给任何选项：按协议回 cancelled
                    self._send({"jsonrpc": "2.0", "id": obj["id"], "result": {
                        "outcome": {"outcome": "cancelled"}}})
        else:
            with self._write_lock:
                self._send({"jsonrpc": "2.0", "id": obj["id"],
                            "error": {"code": -32601, "message": "method not found"}})

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
        with self._write_lock:
            self._next += 1
            request_id = self._next
            pending_queue: queue.Queue[dict] = queue.Queue()
            self._pending[request_id] = pending_queue
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                self._pending.pop(request_id, None)
                raise RuntimeError(f"kimi acp 写入失败：{e}") from e
        try:
            resp = pending_queue.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"kimi acp 请求超时：{method}") from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"kimi acp {method} 错误 {err.get('code')}：{err.get('message')}")
        return resp.get("result") or {}

    def begin_turn(self, method: str, params: dict) -> int:
        """登记轮次请求：其响应与通知同走 `_updates`，由 chat() 逐条消费。"""
        with self._write_lock:
            self._next += 1
            self._turn_id = self._next
            try:
                self._send({"jsonrpc": "2.0", "id": self._turn_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                self._turn_id = None
                raise RuntimeError(f"kimi acp 写入失败：{e}") from e
            return self._turn_id

    def end_turn(self) -> None:
        self._turn_id = None

    def cancel_turn(self, session_id: str) -> bool:
        """取消当前轮次：发 `session/cancel` 通知（agent 收到后结束本轮）。

        无活跃轮次或连接已死时返回 False（无害 no-op）；
        可从任意线程调用（写锁串行化）。连接进程保留，会话不毁。
        """
        if self._turn_id is None or not self.is_alive:
            return False
        try:
            with self._write_lock:
                self._send({"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": session_id}})
        except (OSError, ValueError):
            return False  # 写入失败说明连接将死，chat 路径按 dead 收尾
        return True

    def purge_updates(self) -> None:
        """清空上一轮残留的迟到通知，防串轮。"""
        while True:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                return

    def next_update(self) -> _TurnMessage:
        """取下一条轮次内消息（阻塞；无超时：agent 轮次可长达数分钟）。"""
        return self._updates.get()

    def terminate(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.is_alive = False


class KimiAcpLLM(LanguageModel):
    """Kimi ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None) -> None:
        self._model = model  # None = agent 默认模型（configOptions currentValue）
        self._conn: _AcpConnection | None = None
        self._session_id: str | None = None
        self._turn_lock = threading.Lock()
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型别名（会话存在则即时生效，失败则降级为下次新会话生效）。"""
        self._model = alias
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "model", "value": alias}, timeout=10)
            except RuntimeError:
                self._session_id = None  # 降级：下轮重建会话并应用模型

    def reset_session(self) -> None:
        """清空会话，下次请求 `session/new` 开新会话（进程保留）。"""
        self._session_id = None

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器：params → optionId（None 视为拒绝）；None 恢复自动允许。"""
        self._permission_handler = handler
        if self._conn:
            self._conn.set_permission_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程（atexit 挂钩；Zen Studio 退出时防残留）。"""
        if self._conn:
            self._conn.terminate()
            self._conn = None
            self._session_id = None

    def cancel(self) -> None:
        """取消当前轮次（协议级）：发 `session/cancel`；无轮次时 no-op。

        不持 `_turn_lock`（cancel 从 GUI 线程来，锁由 chat 所在 worker 持有）；
        不终止连接进程——长驻连接是会话资产，仅结束当前轮次。
        """
        conn = self._conn
        if conn is not None and self._session_id:
            conn.cancel_turn(self._session_id)

    def _ensure_session(self) -> _AcpConnection:
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = _AcpConnection(bin_path)
            self._conn.set_permission_handler(self._permission_handler)
            self._session_id = None
            self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": "0.1.0"},
            })
        if self._session_id is None:
            try:
                result = self._conn.request(
                    "session/new", {"cwd": str(PROJECT_ROOT), "mcpServers": []}, timeout=60)
            except RuntimeError as e:
                if "-32000" in str(e):  # authRequired：凭证失效，需用户重新登录
                    raise RuntimeError("kimi 未登录或凭证已过期，请在终端执行 `kimi login` 后重试") from None
                raise
            self._session_id = result["sessionId"]
            if self._model:  # 新会话应用预选模型
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "model",
                        "value": self._model}, timeout=10)
                except RuntimeError:
                    pass  # 保持 agent 默认模型，不阻断对话
        return self._conn

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取最后一条 user 消息作 prompt
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not prompt:
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
            conn.purge_updates()
            conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": prompt}],
            })
            try:
                yield from self._iter_turn_chunks(conn)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(self, conn: _AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。"""
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"kimi acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                return  # stopReason 到达，本轮结束
            chunk = self._map_update(obj)
            if chunk:
                yield chunk

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 时抛错（stopReason 正常结束为静默返回）。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")

    @staticmethod
    def _map_update(obj: dict) -> Chunk | None:
        """session/update 通知 → Chunk；未消费类型返回 None。"""
        update = (obj.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (update.get("content") or {}).get("text")
            return Chunk("text", text) if text else None
        if kind == "agent_thought_chunk":
            text = (update.get("content") or {}).get("text")
            return Chunk("reasoning", text) if text else None
        if kind == "tool_call":
            return Chunk("reasoning", f"\n• 调用工具 {update.get('title') or '?'}\n")
        return None  # tool_call_update / plan / usage_update / available_commands_update 等
