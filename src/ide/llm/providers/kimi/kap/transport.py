"""KAP 本地服务传输：REST 信封、WebSocket 事件流、实例发现与进程生命周期。

KAP 是 `kimi web` 暴露的本地 REST + WebSocket 服务，`server.token` 既是
REST 的 `Authorization: Bearer` 凭证，也是 WS 子协议
`kimi-code.bearer.<token>` 的凭证。本模块只做传输与生命周期，不解释
会话/轮次语义；事件到 GUI 块的映射归 `events.py`，provider 归 `provider.py`。

隐私纪律：token 只保存在内存与子进程标准输出的首行解析结果中，不写日志、
不进异常文案；HTTP 错误只记录 code/msg/request_id，不记录请求正文。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ide.core.child_env import with_non_interactive_pagers
from ide.llm.providers.kimi.catalog import _find_bin

logger = logging.getLogger(__name__)

#: 本地服务 HTTP 超时（控制请求；prompt 本身由 WS 收款，不占 HTTP 长连接）
_HTTP_TIMEOUT_S = 30.0

#: 实例发现/健康检查的短超时：坏实例必须快速跳过，不能阻塞首个 token
_PROBE_TIMEOUT_S = 2.0

#: WS 单条消息上限：turns 全量帧可能较大，但仍拒绝无界内存占用
_WS_MAX_MESSAGE = 64 * 1024 * 1024

#: WS 握手超时
_WS_HANDSHAKE_TIMEOUT_S = 10.0

#: 服务启动 ready 行等待上限（首次冷启动可能解包 web 资源）
_PROCESS_READY_TIMEOUT_S = 60.0

#: 停止本进程拉起的服务时，先 SIGTERM 再兜底 SIGKILL 的宽限
_PROCESS_STOP_TIMEOUT_S = 10.0

#: WebSocket 关闭后的哨兵（与合法 JSON 对象区分）
_CLOSED = object()

#: RFC6455 握手 GUID
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝跟随重定向：KAP 只应在本机回环上应答，30x 视为契约异常。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


#: 禁用环境代理与重定向：默认 opener 会把 Authorization 带给代理或重定向目标
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect())


class KapError(RuntimeError):
    """KAP REST/WS 的统一错误：保留业务码与 request_id 供上层分流。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.request_id = request_id
        self.http_status = http_status
        super().__init__(message)


class KapConnectionClosed(RuntimeError):
    """WebSocket 已断开；provider 据此标记恢复态并在下一次发送时重连。"""


@dataclass(frozen=True)
class KapEndpoint:
    """一个可用的 KAP 服务端点（token 只存在内存）。"""

    origin: str
    ws_url: str
    #: 凭证不参与 repr：异常/调试打印端点时不能把 token 带出去
    token: str = field(repr=False)
    product: str = ""
    server_version: str = ""
    owned: bool = False


def kimi_home() -> Path:
    """Kimi Code 的配置/服务注册根：遵守 `KIMI_CODE_HOME` 隔离变量。"""
    configured = os.environ.get("KIMI_CODE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".kimi-code"


def _loopback_host(host: str) -> str | None:
    """只允许回环地址：token 不能发往局域网/公网。"""
    normalized = (host or "").strip().lower()
    if normalized == "0.0.0.0":
        return "127.0.0.1"
    if normalized in ("::", "::0"):
        return "[::1]"
    if normalized in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return normalized if normalized.startswith("[") else (
            "[::1]" if normalized == "::1" else normalized)
    return None


def _endpoint_from_origin(origin: str, token: str, product: str = "",
                          server_version: str = "") -> KapEndpoint:
    """origin → 端点；只接受回环 http(s)，ws 地址由同一 origin 派生。"""
    parts = urllib.parse.urlsplit(origin)
    if parts.scheme not in ("http", "https"):
        raise KapError(f"KAP origin 协议不支持：{parts.scheme!r}")
    host = _loopback_host(parts.hostname or "")
    if host is None:
        raise KapError("KAP origin 不是回环地址，拒绝发送 token")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    netloc = f"{host}:{port}"
    ws_scheme = "wss" if parts.scheme == "https" else "ws"
    return KapEndpoint(
        origin=f"{parts.scheme}://{netloc}",
        ws_url=f"{ws_scheme}://{netloc}/api/v1/ws",
        token=token,
        product=product,
        server_version=server_version,
    )


def _read_token(home: Path) -> str | None:
    path = home / "server.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _started_key(value: object) -> float:
    """实例排序键：优先数字 epoch，其次 ISO 时间串；坏值排最后。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return 0.0
    return 0.0


def _iter_instances(home: Path) -> list[dict]:
    """读取 `server/instances/*.json`：desktop 优先，其次 started_at 新→旧。"""
    directory = home / "server" / "instances"
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        return []
    instances: list[dict] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            instances.append(data)
    # desktop 优先（app 内会话实时可见），同产品按启动时间倒序
    instances.sort(
        key=lambda item: (
            0 if item.get("product") == "kimi-code-desktop" else 1,
            -_started_key(item.get("started_at")),
        ))
    return instances


def _probe(origin: str, token: str) -> KapEndpoint | None:
    """健康检查 + 鉴权探测；失败返回 None（坏实例不阻断发现链）。"""
    try:
        endpoint = _endpoint_from_origin(origin, token)
    except KapError:
        return None
    # `/healthz` 是无信封的裸对象，统一信封客户端不适用；`/meta` 同时
    # 验证鉴权与信封契约，坏 token/端口复用都会在这里被排除。
    try:
        meta = _http_json(endpoint, "GET", "/api/v1/meta",
                          timeout=_PROBE_TIMEOUT_S)
    except KapError:
        return None
    return KapEndpoint(
        origin=endpoint.origin,
        ws_url=endpoint.ws_url,
        token=token,
        product=str(meta.get("product") or ""),
        server_version=str(meta.get("server_version") or ""),
    )


def discover_endpoint(home: Path | None = None) -> KapEndpoint | None:
    """发现本机正在运行的 Desktop/`kimi web` 实例；没有则返回 None。

    只做发现，不启动进程。坏实例（进程已退出、端口复用、token 失效）
    在探测阶段被跳过；调用方随后按需走 `KapServer.ensure()` 的启动兜底。
    """
    home = home or kimi_home()
    token = _read_token(home)
    if not token:
        return None
    for item in _iter_instances(home):
        pid = item.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)  # 只探活，不发信号
        except OSError:
            continue
        host = _loopback_host(str(item.get("host") or ""))
        port = item.get("port")
        if host is None or not isinstance(port, int) or port <= 0:
            continue
        endpoint = _probe(f"http://{host}:{port}", token)
        if endpoint is not None:
            return endpoint
    return None


class KapHttp:
    """KAP REST 客户端：统一信封解析，业务码非 0 一律抛 `KapError`。"""

    def __init__(self, endpoint: KapEndpoint) -> None:
        self._endpoint = endpoint

    def request(self, method: str, path: str, body: dict | None = None,
                timeout: float = _HTTP_TIMEOUT_S) -> dict:
        return _http_json(self._endpoint, method, path, body, timeout=timeout)

    def get(self, path: str, timeout: float = _HTTP_TIMEOUT_S) -> dict:
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict | None = None,
             timeout: float = _HTTP_TIMEOUT_S) -> dict:
        return self.request("POST", path, body, timeout=timeout)


def _http_json(endpoint: KapEndpoint, method: str, path: str,
               body: dict | None = None, *, auth: bool = True,
               timeout: float = _HTTP_TIMEOUT_S) -> dict:
    """发一次 JSON 请求并拆信封；HTTP 非 2xx 也尝试解析错误信封。"""
    data = None if body is None else json.dumps(
        body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {endpoint.token}"
    request = urllib.request.Request(
        endpoint.origin + path, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code, msg, request_id = _parse_envelope_error(raw)
        raise KapError(msg or f"KAP HTTP {exc.code}", code=code,
                       request_id=request_id, http_status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise KapError(f"KAP 服务不可达：{exc.reason}") from exc
    except TimeoutError as exc:
        raise KapError("KAP 请求超时") from exc
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KapError(f"KAP 响应不是 JSON（HTTP {getattr(response, 'status', '?')}）") from exc
    if not isinstance(envelope, dict):
        raise KapError("KAP 响应信封不是对象")
    code = envelope.get("code")
    if code != 0:
        raise KapError(
            str(envelope.get("msg") or f"KAP code={code}"),
            code=code if isinstance(code, int) else None,
            request_id=envelope.get("request_id"))
    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


def _parse_envelope_error(raw: bytes) -> tuple[int | None, str | None, str | None]:
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None, None
    if not isinstance(envelope, dict):
        return None, None, None
    code = envelope.get("code")
    return (code if isinstance(code, int) else None,
            str(envelope.get("msg") or "") or None,
            envelope.get("request_id"))


class KapWebSocket:
    """最小 RFC6455 客户端：文本 JSON 帧、应用层 ping/pong、分片重组。

    KAP 的心跳是**应用层** `{"type":"ping"}`，不是 WS 控制帧 ping；
    收到后必须立即回 `{"type":"pong"}`，否则服务端会在无客户端消息后
    主动断开订阅。控制帧 ping 也照常回 pong，属于协议层礼貌。
    """

    def __init__(self, ws_url: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(ws_url)
        if parsed.scheme not in ("ws", "wss"):
            raise KapError(f"KAP WS 协议不支持：{parsed.scheme!r}")
        host = _loopback_host(parsed.hostname or "")
        if host is None:
            raise KapError("KAP WS 不是回环地址，拒绝发送 token")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        # socket.create_connection 不接受方括号形式；HTTP URI/Host 头才需要
        self._host = host.strip("[]")
        self._port = port
        self._path = parsed.path or "/api/v1/ws"
        if parsed.query:
            self._path += "?" + parsed.query
        self._token = token
        self._tls = parsed.scheme == "wss"
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._closed = threading.Event()
        self._messages: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._buffer = b""
        self._frag_opcode = 0
        self._frag: list[bytes] = []
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def is_alive(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    def connect(self, timeout: float = _WS_HANDSHAKE_TIMEOUT_S) -> None:
        if self._sock is not None:
            return
        raw = socket.create_connection((self._host, self._port), timeout=timeout)
        if self._tls:
            import ssl
            raw = ssl.create_default_context().wrap_socket(
                raw, server_hostname=self._host)
        self._sock = raw
        key = base64.b64encode(os.urandom(16)).decode()
        host_header = (f"[{self._host}]:{self._port}" if ":" in self._host
                       else f"{self._host}:{self._port}")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: kimi-code.bearer.{self._token}\r\n"
            "\r\n"
        )
        try:
            raw.sendall(request.encode("ascii"))
            head, _, rest = self._read_handshake(timeout)
            self._buffer = rest
            lines = head.split(b"\r\n")
            status = lines[0].decode("latin-1", "replace")
            if " 101 " not in status:
                raise KapError(f"KAP WS 握手失败：{status}")
            headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(b":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
            expected = base64.b64encode(
                hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode()
            actual = headers.get(b"sec-websocket-accept", b"").decode(
                "ascii", "replace")
            if actual != expected:
                raise KapError("KAP WS 握手校验失败（Sec-WebSocket-Accept 不匹配）")
        except Exception:
            # 握手失败后必须清 socket：否则 is_alive 仍为真、重连被 _sock
            # 非空短路，客户端会永久卡在坏连接上。
            self._sock = None
            try:
                raw.close()
            except OSError:
                pass
            raise
        raw.settimeout(None)
        self._reader = threading.Thread(
            target=self._read_loop, name="kimi-kap-ws", daemon=True)
        self._reader.start()

    def _read_handshake(self, timeout: float) -> tuple[bytes, bytes, bytes]:
        assert self._sock is not None
        self._sock.settimeout(timeout)
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise KapError("KAP WS 握手连接被关闭")
            buffer += chunk
            if len(buffer) > 65536:
                raise KapError("KAP WS 握手响应过大")
        head, _, rest = buffer.partition(b"\r\n\r\n")
        return head, b"", rest

    def send_json(self, payload: dict) -> None:
        if not self.is_alive:
            raise KapConnectionClosed("KAP WS 已断开")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_frame(0x1, data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise KapConnectionClosed("KAP WS 未连接")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(value ^ mask[index % 4]
                       for index, value in enumerate(payload))
        with self._send_lock:
            sock.sendall(bytes(header) + masked)

    def _recv_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            if self._sock is None:
                raise KapConnectionClosed("KAP WS 已关闭")
            chunk = self._sock.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise KapConnectionClosed("KAP WS 连接已断开")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                first, second = self._recv_exact(2)
                fin = bool(first & 0x80)
                opcode = first & 0x0F
                masked = bool(second & 0x80)
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack("!H", self._recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._recv_exact(8))[0]
                if length > _WS_MAX_MESSAGE:
                    raise KapError(f"KAP WS 消息过大：{length} 字节")
                mask = self._recv_exact(4) if masked else b""
                payload = self._recv_exact(length)
                if masked:
                    payload = bytes(value ^ mask[index % 4]
                                    for index, value in enumerate(payload))
                if opcode == 0x9:  # ping
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:  # pong
                    continue
                if opcode == 0x8:  # close
                    break
                if opcode in (0x1, 0x2):
                    self._frag_opcode = opcode
                    self._frag = [payload]
                elif opcode == 0x0:
                    self._frag.append(payload)
                else:
                    continue
                if not fin:
                    continue
                data = b"".join(self._frag)
                self._frag = []
                if self._frag_opcode != 0x1:
                    continue
                try:
                    message = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "ping":
                    self.send_json({"type": "pong"})
                    continue
                if message.get("type") == "pong":
                    continue
                self._messages.put(message)
        except KapConnectionClosed as exc:
            self._last_error = str(exc)
        except (OSError, KapError, KapConnectionClosed) as exc:
            self._last_error = str(exc)
        finally:
            self._closed.set()
            self._messages.put(_CLOSED)

    def next_message(self, timeout: float | None = None) -> dict:
        """取一条非心跳消息；超时抛 `queue.Empty`，断开抛 `KapConnectionClosed`。"""
        item = self._messages.get(timeout=timeout)
        if item is _CLOSED:
            raise KapConnectionClosed(self._last_error or "KAP WS 已断开")
        return item

    def close(self) -> None:
        if self._closed.is_set() and self._sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except (OSError, KapConnectionClosed):
            pass
        self._closed.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


class KapServer:
    """KAP 服务生命周期：发现已有实例优先，没有才拉起私有 `kimi web`。

    发现到的 Desktop/`kimi web` 实例不归本对象所有，关闭时只断开 WS，
    不终止用户的服务；只有自己拉起的进程才在 `close()` 时回收。这样
    多标签共享桌面服务，也不会在退出时误杀用户正在用的 Kimi。
    """

    def __init__(self, cwd: str, bin_path: str | None = None) -> None:
        self._cwd = cwd
        self._bin_path = bin_path
        self._proc: subprocess.Popen | None = None
        self._endpoint: KapEndpoint | None = None
        #: 关闭标签与 ensure 可能并发：锁覆盖发现/启动/回收整个生命周期
        self._lock = threading.RLock()

    @property
    def endpoint(self) -> KapEndpoint | None:
        return self._endpoint

    def ensure(self, timeout: float = _PROCESS_READY_TIMEOUT_S) -> KapEndpoint:
        with self._lock:
            if self._endpoint is not None and self.is_alive():
                return self._endpoint
            if self._endpoint is not None:
                # 旧私有服务已失活：先回收再发现/重启，防旧进程被新 _proc
                # 覆盖后变成无主进程；发现的用户服务 close 不杀，只清引用。
                self.close()
            discovered = discover_endpoint()
            if discovered is not None:
                self._endpoint = discovered
                logger.info("[kimi kap] 复用已有服务 product=%s version=%s",
                            discovered.product or "?",
                            discovered.server_version or "?")
                return discovered
            return self._spawn(timeout)

    def is_alive(self) -> bool:
        if self._endpoint is None:
            return False
        if self._proc is not None and self._endpoint.owned:
            return self._proc.poll() is None
        try:
            _http_json(self._endpoint, "GET", "/api/v1/meta",
                       timeout=_PROBE_TIMEOUT_S)
            return True
        except KapError:
            return False

    def _spawn(self, timeout: float) -> KapEndpoint:
        bin_path = self._bin_path or _find_bin()
        if not bin_path:
            raise KapError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        env = with_non_interactive_pagers(os.environ)
        try:
            proc = subprocess.Popen(
                [bin_path, "web", "--port", "0", "--json", "--no-open"],
                cwd=self._cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise KapError(f"kimi web 启动失败：{exc}") from exc
        line = _read_first_line(proc, timeout)
        if line is None:
            _stop_process(proc)
            raise KapError(
                "kimi web 未在限定时间内返回 ready 行"
                f"（exit={proc.poll()}）")
        try:
            info = json.loads(line)
            origin = str(info["origin"])
            token = str(info["token"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            _stop_process(proc)
            raise KapError("kimi web ready 行格式不符合 KAP 契约") from exc
        try:
            # ready 行同样必须走回环校验：PATH 劫持/输出被篡改时不能把
            # Bearer token 发到局域网或公网；ws_url 由本地 origin 重新派生。
            endpoint = _endpoint_from_origin(
                origin, token, product="kimi-code-cli")
        except KapError:
            _stop_process(proc)
            raise
        endpoint = KapEndpoint(
            origin=endpoint.origin,
            ws_url=endpoint.ws_url,
            token=token,
            product="kimi-code-cli",
            server_version="",
            owned=True,
        )
        self._endpoint = endpoint
        self._proc = proc
        if not self._wait_healthy(endpoint, timeout=5.0):
            _stop_process(proc)
            self._proc = None
            self._endpoint = None
            raise KapError("kimi web 已启动但健康检查失败")
        logger.info("[kimi kap] 已启动私有服务 origin=%s", endpoint.origin)
        return endpoint

    @staticmethod
    def _wait_healthy(endpoint: KapEndpoint, timeout: float) -> bool:
        """ready 行之后等 /meta 真正可鉴权访问；冷启动有短暂窗口。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _http_json(endpoint, "GET", "/api/v1/meta", timeout=0.5)
                return True
            except KapError:
                time.sleep(0.1)
        return False

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            endpoint = self._endpoint
            self._endpoint = None
            if proc is None or not (endpoint and endpoint.owned):
                return
            _stop_process(proc)


def _stop_process(proc: subprocess.Popen) -> None:
    """统一 TERM→wait→KILL：kimi web 有活动轮时可能忽略 TERM。"""
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=_PROCESS_STOP_TIMEOUT_S)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _read_first_line(proc: subprocess.Popen, timeout: float) -> str | None:
    """读子进程 stdout 首行（ready 行）；超时/EOF 返回 None。"""
    if proc.stdout is None:
        return None
    result: queue.Queue = queue.Queue()

    def _read() -> None:
        try:
            result.put(proc.stdout.readline())
        except (OSError, ValueError):
            result.put(b"")

    threading.Thread(target=_read, name="kimi-kap-ready", daemon=True).start()
    try:
        line = result.get(timeout=timeout)
    except queue.Empty:
        return None
    text = line.decode("utf-8", "replace").strip() if isinstance(line, bytes) else str(line).strip()
    return text or None
