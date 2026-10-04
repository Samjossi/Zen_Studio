"""传输层：stdio ndjson 帧读写 + 反向请求回执经纪。

协议依据（《Dream ACP 接入协议 v1.0》）：
- §1.2/A.6：stdout 只写协议帧，日志一律 stderr。
- §3：反向请求（session/request_permission）用 agent 侧独立字符串 id
  编号空间（"perm-N"），与客户端整数 id 不碰撞；回执须能即时投递给
  阻塞等待的轮次线程。
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from typing import IO, Any, Iterator


# ----------------------------------------------------------------------
# 入站帧的显式结构（AFCP 3.1：模块间禁止裸字典传递，线格式 JSON 只在
# 本层解析一次，出站后全是 dataclass）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class IncomingRequest:
    """客户端请求（有 id，必须应答）。"""
    request_id: int | str
    method: str
    params: dict[str, Any]


@dataclass(frozen=True)
class IncomingNotification:
    """客户端通知（无 id，无需应答，如 session/cancel）。"""
    method: str
    params: dict[str, Any]


@dataclass(frozen=True)
class IncomingResponse:
    """反向请求的客户端回执（有 id 无 method）。"""
    request_id: int | str
    result: dict[str, Any] | None
    error: dict[str, Any] | None


IncomingFrame = IncomingRequest | IncomingNotification | IncomingResponse


class StdioTransport:
    """stdio ndjson JSON-RPC 传输：读帧解析、写锁串行化、stderr 日志。

    写锁存在的理由：轮次在守护线程内流式输出，主线程也可能要发响应，
    无锁交错会把两行 ndjson 拼成不可解析的脏帧（§1.2 每行必须完整）。
    """

    def __init__(
        self,
        agent_name: str,
        input_stream: IO[str] | None = None,
        output_stream: IO[str] | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._input = input_stream if input_stream is not None else sys.stdin
        self._output = output_stream if output_stream is not None else sys.stdout
        self._write_lock = threading.Lock()

    # ---------------- 读 ----------------
    def read_frames(self) -> Iterator[IncomingFrame]:
        """逐行读 stdin 并解析为显式帧结构；EOF 时迭代结束。"""
        for line in self._input:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # 宽容非 JSON 行：客户端实现可能有调试输出混入，丢行比崩连接好
                self.log(f"丢弃非 JSON 行: {line[:200]}")
                continue
            yield self._parse_frame(raw)

    def _parse_frame(self, raw: dict[str, Any]) -> IncomingFrame:
        method = raw.get("method")
        request_id = raw.get("id")
        params = raw.get("params") or {}
        if method is None:
            return IncomingResponse(
                request_id=request_id,
                result=raw.get("result"),
                error=raw.get("error"),
            )
        if request_id is None:
            return IncomingNotification(method=method, params=params)
        return IncomingRequest(request_id=request_id, method=method, params=params)

    # ---------------- 写 ----------------
    def send_frame(self, payload: dict[str, Any]) -> None:
        with self._write_lock:
            self._output.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._output.flush()

    def send_result(self, request_id: int | str, result: dict[str, Any]) -> None:
        self.send_frame({"jsonrpc": "2.0", "id": request_id, "result": result})

    def send_error(self, request_id: int | str, code: int, message: str) -> None:
        self.send_frame({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": code, "message": message}})

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        self.send_frame({"jsonrpc": "2.0", "method": method, "params": params})

    def send_reverse_request(
        self, request_id: str, method: str, params: dict[str, Any]
    ) -> None:
        self.send_frame({"jsonrpc": "2.0", "id": request_id,
                         "method": method, "params": params})

    # ---------------- 日志 ----------------
    def log(self, message: str) -> None:
        """日志一律 stderr（§1.2/A.6：stdout 混入任何非协议输出即污染协议流）。"""
        print(f"[{self._agent_name}] {message}", file=sys.stderr, flush=True)


class ReverseRequestBroker:
    """反向请求经纪：独立 id 编号空间 + 回执阻塞等待 + 主循环投递。

    §3 要点：审批阻塞期间读循环不得被占住——所以本经纪只在轮次线程里
    阻塞（Event.wait），主读循环收到回执后经 resolve() 即时唤醒。
    """

    #: agent 侧反向请求 id 前缀：字符串命名空间与客户端整数 id 不碰撞
    _ID_PREFIX = "perm"

    def __init__(self, transport: StdioTransport) -> None:
        self._transport = transport
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发反向请求并阻塞等回执，返回 result 载荷（无 result 时为空 dict）。

        注意先登记后发送：回执可能在 send 返回前就被主循环读到，
        顺序颠倒会让回执找不到等待者而丢失。
        """
        with self._sequence_lock:
            self._sequence += 1
            request_id = f"{self._ID_PREFIX}-{self._sequence}"
        done_event = threading.Event()
        result_box: dict[str, Any] = {}
        self._pending[request_id] = (done_event, result_box)
        self._transport.send_reverse_request(request_id, method, params)
        done_event.wait()
        self._pending.pop(request_id, None)
        return result_box.get("result") or {}

    def resolve(self, response: IncomingResponse) -> bool:
        """主读循环投递回执；返回 True 表示找到了等待中的请求。"""
        pending = self._pending.get(str(response.request_id))
        if pending is None:
            return False
        done_event, result_box = pending
        if response.result is not None:
            result_box["result"] = response.result
        done_event.set()
        return True
