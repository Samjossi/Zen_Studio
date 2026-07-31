"""ACP 连接层泛化：长驻 `<agent> acp` 子进程 + JSON-RPC（ndjson 帧）收发。

由 kimi_acp._AcpConnection 抽出泛化（计划 2026-0730-0150 §4-D5）：帧收发、
请求 id 配对、通知与反向请求分发、死讯注入均已协议无关；仅 agent 名
（错误文案）参数化，argv 恒为 `[bin_path, "acp"]`（`kimi acp` /
`reasonix acp` 同构）。initialize 载荷（clientInfo）不在连接层——
归各 provider 的 `_ensure_session`；kimi 专有的 `-32000 authRequired →
"请 kimi login"` 文案映射亦不搬，由各 provider 钩子自行翻译。

协议定型类型（PermissionOption/ToolCallInfo/PermissionParams/
PermissionHandler/_TurnMessage）是 ACP 协议层产物，不属 kimi 专有，
随连接层同居本模块。
"""
import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Literal, TypedDict

from llm.base import UsageStats

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


def map_usage_update(update: dict) -> UsageStats | None:
    """`usage_update` 通知载荷 → UsageStats；无有效上限数据返回 None。

    协议载荷：`{"sessionUpdate": "usage_update", "used": N, "size": M,
    "cost": {"amount": f, "currency": "USD"}}`——used/size 同帧送达，
    IDE 侧无需自维护模型上下文上限表。size 缺失或为 0 的通知无意义
    （算不出百分比），返回 None 不上屏（UI 须容忍收不到用量的后端，
    保持隐藏而非显示 0%）。
    """
    size = update.get("size")
    used = update.get("used")
    if not isinstance(size, int) or size <= 0 or not isinstance(used, int) or used < 0:
        return None
    cost_amount = (update.get("cost") or {}).get("amount")
    return UsageStats(
        used=used,
        size=size,
        cost=cost_amount if isinstance(cost_amount, (int, float)) else None,
    )


class AcpConnection:
    """长驻 `<agent> acp` 子进程：ndjson 帧收发、请求 id 配对、通知与反向请求分发。

    消息分两条出路：响应按 id 进 `_pending`（同步 `request()` 阻塞等待）；
    轮次内消息（session/update 通知与 prompt 响应）进 `_updates` 由 `chat()` 消费。
    同一时刻仅一个活跃轮次（各 provider 的 `_turn_lock` 保证）。
    """

    def __init__(self, bin_path: str, cwd: str, agent_name: str) -> None:
        """
        :param bin_path: agent CLI 二进制路径（argv 恒为 `[bin_path, "acp"]`）
        :param cwd: agent 子进程工作目录（多开模式由启动参数注入）
        :param agent_name: agent 显示名（错误文案用，如 "kimi acp"）
        """
        self._agent_name = agent_name
        try:
            self._proc = subprocess.Popen(
                [bin_path, "acp"],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # ACP 日志走 stderr/agent 自有日志，不属协议
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as e:
            raise RuntimeError(f"{agent_name} 启动失败：{e}") from e
        self._next = 0
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict]] = {}
        self._updates: queue.Queue[_TurnMessage] = queue.Queue()
        self._turn_id: int | None = None  # 活跃轮次的 prompt 请求 id（其响应改走 _updates）
        self.is_alive = True
        self._terminated = False  # 死讯注入幂等标志（reader EOF 与 terminate 谁先谁注入）
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
            self._inject_dead()

    def _inject_dead(self) -> None:
        """向 `_updates` 注入死讯、向残余 `_pending` 注入错误（幂等）。

        reader 线程 EOF 与 terminate() 两路径共用，先到者注入；
        check-then-set 竞态下的重复注入无害（多余死讯由 purge_updates
        清理，已 pop 的 pending 不在表中）。
        """
        if self._terminated:
            return
        self._terminated = True
        self._updates.put(("dead", self._proc.poll()))
        for pending_queue in list(self._pending.values()):
            pending_queue.put({"error": {"code": -32099,
                                         "message": f"{self._agent_name} 进程意外退出"}})

    def _dispatch_line(self, line: str) -> None:
        """单帧分发：反向请求 / 轮次内通知与响应（→_updates）/ 控制响应（→_pending）。"""
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"[{self._agent_name}] 非 JSON 帧: {line[:200]}", file=sys.stderr)
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
                    print(f"[{self._agent_name}] 审批处理器异常: {e}", file=sys.stderr)
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
                raise RuntimeError(f"{self._agent_name} 写入失败：{e}") from e
        try:
            resp = pending_queue.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"{self._agent_name} 请求超时：{method}") from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"{self._agent_name} {method} 错误 {err.get('code')}：{err.get('message')}")
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
                raise RuntimeError(f"{self._agent_name} 写入失败：{e}") from e
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
        """终止子进程并主动注入死讯：consumer 立即醒来，不必等 reader EOF。

        标签关闭路径（2026-0722-1117 计划 T5）：阻塞在 next_update()/
        request() 的 worker 依赖死讯/错误帧收尾；主动注入把解封延迟
        从"reader 反应过来"压到毫秒级（check-then-set 幂等，见 _inject_dead）。
        """
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.is_alive = False
        self._inject_dead()


__all__ = [
    "AcpConnection",
    "PermissionOption",
    "ToolCallInfo",
    "PermissionParams",
    "PermissionHandler",
    "map_usage_update",
]
