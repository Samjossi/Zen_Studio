"""Kimi KAP provider：`kimi web` 常驻服务上的 REST + WebSocket 轮次链路。

与 `KimiAcpLLM` 并列，不替换 ACP。轮次终态以 KAP 的 `turn.ended` 为准：
`outcome=completed` 正常收尾，`interrupted` 走停止/中断通道，`failed` 走
失败通道。通知轮（后台任务完成）在 KAP 中是可见的真实 `agent` turn，
用户追问会由服务端排队；本 provider 只消费 `user_message_id` 命中的
那一轮，绝不把通知轮当作用户轮收尾。

服务发现优先复用已运行的 Desktop/`kimi web`，没有才拉起私有
`kimi web --port 0 --json`；私有进程在 `close()` 时回收，复用的服务不杀。
"""
from __future__ import annotations

import atexit
import itertools
import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterator

from ide.core.paths import PROJECT_ROOT
from ide.llm.base import (
    BackendCapabilities,
    Chunk,
    LanguageModel,
    Message,
    UsageStats,
)
from ide.llm.providers.acp import PermissionRequest, TurnCancelled
from ide.llm.providers.kimi.kap.events import KapEventMapper, _tool_kind
from ide.llm.providers.kimi.kap.transport import (
    KapConnectionClosed,
    KapEndpoint,
    KapError,
    KapHttp,
    KapServer,
    KapWebSocket,
)

logger = logging.getLogger(__name__)

#: 用户停止后等待 `turn.ended` 的宽限；超时按本地取消收尾，防界面卡 busy
_CANCEL_GRACE_S = 5.0

#: 订阅确认等待上限
_SUBSCRIBE_TIMEOUT_S = 15.0

#: 会话恢复时 `:reload` 的等待上限
_RELOAD_TIMEOUT_S = 15.0

#: 提示词内容里图片块允许的本地文件后缀（与 GUI 附件能力一致）
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

#: 事件 pump 的断开哨兵（只在本模块内部流转）
_EVENT_CLOSED = object()


def _friendly_error(exc: KapError) -> str:
    """把 KAP 业务码翻成用户可执行的中文；未知码保留原始 msg。"""
    if exc.code == 40901:
        return "Kimi 会话正忙：请等当前轮次结束或手动停止后再发送"
    if exc.code == 40931:
        return "Kimi 会话被其他客户端占用；请先在原客户端关闭该会话，不自动接管"
    if exc.code == 40932:
        return "Kimi 会话已被其他客户端更新，请重新发送"
    if exc.code == 40401:
        return "Kimi 会话不存在或已过期，下一轮将新建会话"
    if exc.http_status in (401, 403):
        return "Kimi 未登录或凭证已过期，请在终端执行 `kimi login` 后重试"
    return str(exc)


def _image_blocks(images: object) -> list[dict]:
    blocks: list[dict] = []
    if not isinstance(images, list):
        return blocks
    for image in images:
        if not isinstance(image, dict):
            continue
        path = image.get("path")
        if not isinstance(path, str) or not path:
            continue
        if Path(path).suffix.lower() not in _IMAGE_EXTS:
            continue
        blocks.append({
            "type": "image",
            "source": {"kind": "path", "path": path},
            "name": Path(path).name,
        })
    return blocks


class KimiKapLLM(LanguageModel):
    """Kimi KAP 后端（常驻服务多会话，REST 提交 + WS 收款）。"""

    def __init__(
        self,
        model: str | None = None,
        workspace_root: str | None = None,
        *,
        server: KapServer | None = None,
    ) -> None:
        self._model = model
        self._effort: str | None = None
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._server = server or KapServer(self._cwd)
        self._http: KapHttp | None = None
        self._ws: KapWebSocket | None = None
        self._endpoint: KapEndpoint | None = None
        self._session_id: str | None = None
        self._subscribed_session: str | None = None
        self._workspace_id: str | None = None
        self._model_sizes: dict[str, int] = {}
        self._mapper = KapEventMapper()
        self._last_usage: UsageStats | None = None
        self._prompt_seq = itertools.count(1)
        self._turn_lock = threading.Lock()
        self._cancel = threading.Event()
        self._cancel_sent = threading.Event()
        self._closed = False
        #: 订阅/恢复期间先到的帧（turns 已丢弃）；chat 开轮前统一消费
        self._backlog: deque[dict] = deque()
        #: pump 线程把 WS 帧转成队列；chat 只消费这里，空闲时也持续更新用量
        self._events: queue.Queue = queue.Queue()
        self._chat_active = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._permission_handler = None
        self._needs_recovery = False
        self._last_error: str | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        self._model = alias
        self._apply_profile()

    def set_effort(self, value: str) -> None:
        self._effort = value
        self._apply_profile()

    def set_permission_handler(self, handler) -> None:
        self._permission_handler = handler

    def apply_vendor_settings(self, settings) -> None:
        """KAP 暂无厂商设置键；保留鸭子类型入口，GUI 声明为空时不会调用。"""
        return None

    def reset_session(self) -> None:
        self._session_id = None
        self._subscribed_session = None
        self._workspace_id = None
        self._mapper.reset()
        self._last_usage = None
        self._backlog.clear()
        self._needs_recovery = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        atexit.unregister(self.close)
        self._close_ws()
        self._server.close()
        self._http = None
        self._endpoint = None
        self._session_id = None
        self._subscribed_session = None
        self._mapper.reset()

    def _close_ws(self) -> None:
        self._stop_pump()
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()
        self._drain_events()
        self._subscribed_session = None

    def _drain_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def _start_pump(self) -> None:
        if self._pump_thread is not None and self._pump_thread.is_alive():
            return
        self._pump_stop.clear()
        thread = threading.Thread(
            target=self._pump_loop, name="kimi-kap-pump", daemon=True)
        self._pump_thread = thread
        thread.start()

    def _stop_pump(self) -> None:
        self._pump_stop.set()
        thread, self._pump_thread = self._pump_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)

    def _pump_loop(self) -> None:
        """唯一 WS 消费者：轮内转发给 chat，空闲时更新用量/应答交互。

        WS reader 线程只做帧解析与心跳，本线程可以安全地在审批处理器上
        阻塞（GUI 弹窗），心跳不会因此停摆。
        """
        ws = self._ws
        if ws is None:
            return
        stopped = False
        try:
            while not self._pump_stop.is_set():
                try:
                    message = ws.next_message(timeout=0.5)
                except queue.Empty:
                    continue
                except KapConnectionClosed as exc:
                    self._last_error = str(exc)
                    self._needs_recovery = True
                    break
                kind = message.get("type")
                if kind in ("turns", "turns.removed", "turns.failed",
                            "response", "ping", "pong"):
                    continue
                if kind == "context":
                    chunk = self._mapper.map_context(message, self._context_size())
                    if chunk is not None:
                        self._last_usage = chunk.usage
                    if self._chat_active.is_set():
                        self._events.put(message)
                    continue
                if kind == "interaction":
                    if self._chat_active.is_set():
                        self._events.put(message)
                    else:
                        self._answer_interaction(message)
                    continue
                if kind == "notice":
                    logger.warning(
                        "[kimi kap] 通知 level=%s code=%s title=%s",
                        message.get("level"), message.get("code"),
                        message.get("title"))
                    continue
                if self._chat_active.is_set():
                    self._events.put(message)
                # 空闲期轮次帧没有消费者，直接丢弃；否则队列会随通知轮增长
        except Exception as exc:  # noqa: BLE001 — pump 必须自愈，不能静默死掉
            self._last_error = str(exc)
            self._needs_recovery = True
        finally:
            stopped = self._pump_stop.is_set()
            if not stopped and self._chat_active.is_set():
                self._events.put(_EVENT_CLOSED)

    def _next_event(self, timeout: float | None = None) -> dict:
        item = self._events.get(timeout=timeout)
        if item is _EVENT_CLOSED:
            raise KapConnectionClosed(self._last_error or "KAP 事件流已断开")
        return item

    @property
    def recovery_pending(self) -> bool:
        if not self._session_id:
            return False
        return self._needs_recovery or self._ws is None or not self._ws.is_alive

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            image_input=True,
            post_turn_usage_grace_s=1.0,
            stall_watchdog=False,
            recovery_pending=self.recovery_pending,
        )

    def poll_usage(self) -> UsageStats | None:
        """返回最近一次 `context` 事件快照；线程安全（只读引用）。"""
        return self._last_usage

    # ------------------------------------------------------------------
    # 连接与会话
    # ------------------------------------------------------------------
    def _ensure_connected(self) -> None:
        if self._closed:
            raise RuntimeError("kimi kap 后端已关闭（标签已销毁）")
        try:
            endpoint = self._server.ensure()
        except KapError as exc:
            raise RuntimeError(f"kimi kap 服务不可用：{_friendly_error(exc)}") from exc
        if self._endpoint is not None and endpoint.origin != self._endpoint.origin:
            # 服务换了：旧 WS 必须丢弃，否则订阅在旧端口上等不到本轮。
            # session id 保留：同一 KIMI_CODE_HOME 下服务重启后仍可 :reload；
            # 真恢复不了由 _recover_session 走新建，不在这里提前丢上下文。
            self._close_ws()
            self._http = None
            self._subscribed_session = None
            self._workspace_id = None
            self._mapper.reset()
        self._endpoint = endpoint
        if self._http is None:
            self._http = KapHttp(endpoint)
        ws_new = self._ws is None or not self._ws.is_alive
        if ws_new:
            self._close_ws()
            ws = KapWebSocket(endpoint.ws_url, endpoint.token)
            try:
                ws.connect()
            except (KapError, OSError) as exc:
                self._last_error = str(exc)
                raise RuntimeError(f"kimi kap 事件流连接失败：{exc}") from exc
            self._ws = ws
            self._needs_recovery = False
        if not self._model_sizes:
            self._load_model_sizes()
        if self._session_id is None:
            self._create_session()
        elif ws_new:
            self._recover_session()
        # 订阅是会话级长连接：已订阅且 WS 未重连时不重复发 subscribe，
        # 否则每轮都会多推一份 turns 全量帧并叠加服务端订阅。
        if self._subscribed_session != self._session_id:
            self._subscribe()
        if self._pump_thread is None or not self._pump_thread.is_alive():
            self._start_pump()

    def _load_model_sizes(self) -> None:
        assert self._http is not None
        try:
            data = self._http.get("/api/v1/models", timeout=10)
        except KapError:
            return
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            model = item.get("model")
            size = item.get("max_context_size")
            if isinstance(model, str) and isinstance(size, int) and size > 0:
                self._model_sizes[model] = size

    def _resolve_workspace(self) -> str | None:
        if self._workspace_id is not None:
            return self._workspace_id or None
        assert self._http is not None
        try:
            data = self._http.get("/api/v1/workspaces", timeout=10)
        except KapError:
            return None
        target = os.path.realpath(self._cwd)
        candidates: list[dict] = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            root = item.get("root")
            if isinstance(root, str) and os.path.realpath(root) == target:
                candidates.append(item)
        candidates.sort(key=lambda item: 0 if item.get("kind") == "managed" else 1)
        workspace_id = candidates[0].get("id") if candidates else None
        self._workspace_id = workspace_id if isinstance(workspace_id, str) else ""
        return self._workspace_id or None

    def _create_session(self) -> None:
        assert self._http is not None
        workspace_id = self._resolve_workspace()
        body: dict = {"title": "Zen Studio", "agent_config": {
            "permission_mode": "manual"}}
        if workspace_id:
            body["workspace_id"] = workspace_id
        else:
            body["cwd"] = self._cwd
        if self._model:
            body["agent_config"]["model"] = self._model
        if self._effort:
            body["agent_config"]["thinking"] = self._effort
        try:
            data = self._http.post("/api/v1/sessions", body, timeout=60)
        except KapError as exc:
            raise RuntimeError(f"kimi kap 建会话失败：{_friendly_error(exc)}") from exc
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("kimi kap 建会话响应缺少 session id")
        self._session_id = session_id
        self._subscribed_session = None
        self._mapper.reset()
        self._last_usage = None
        logger.info("[kimi kap] 新会话 sessionId=%s", session_id)

    def _recover_session(self) -> None:
        """服务重启/订阅断开后保留原 session id；`GET` 失败再 `:reload`。"""
        assert self._http is not None and self._session_id is not None
        try:
            self._http.get(f"/api/v1/sessions/{self._session_id}", timeout=10)
            return
        except KapError as exc:
            if exc.code != 40401:
                raise RuntimeError(f"kimi kap 会话检查失败：{_friendly_error(exc)}") from exc
        try:
            self._http.post(
                f"/api/v1/sessions/{self._session_id}:reload", {},
                timeout=_RELOAD_TIMEOUT_S)
            logger.info("[kimi kap] 会话已从磁盘恢复 sessionId=%s", self._session_id)
        except KapError:
            logger.warning("[kimi kap] 原会话不可恢复，改为新建会话")
            self._session_id = None
            self._subscribed_session = None
            self._mapper.reset()
            self._create_session()

    def _subscribe(self) -> None:
        assert self._ws is not None and self._session_id is not None
        # 订阅确认必须独占 WS 读：先停 pump，确认后再恢复；断线/换会话
        # 期间残留的旧事件没有本轮价值，直接清掉。
        self._stop_pump()
        self._drain_events()
        self._backlog.clear()
        request_id = f"sub-{next(self._prompt_seq)}"
        self._ws.send_json({
            "type": "subscribe", "request_id": request_id,
            "session_id": self._session_id})
        deadline = time.monotonic() + _SUBSCRIBE_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("kimi kap 订阅确认超时")
            try:
                message = self._ws.next_message(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError("kimi kap 订阅确认超时") from exc
            if message.get("type") == "response" \
                    and message.get("request_id") == request_id:
                code = message.get("code")
                if code != 0:
                    raise RuntimeError(
                        f"kimi kap 订阅失败 code={code} msg={message.get('msg')}")
                self._subscribed_session = self._session_id
                self._start_pump()
                return
            self._stash(message)

    def _stash(self, message: dict) -> None:
        """订阅确认前先到的帧：turns 全量快照不参与实时聊天，直接丢弃。"""
        kind = message.get("type")
        if kind in ("turns", "turns.removed", "turns.failed", "pong", "ping"):
            return
        self._backlog.append(message)

    def _drain_backlog(self) -> None:
        """开轮前消费旧帧：只保留用量/待答交互，轮次残留不串进下一轮。"""
        while self._backlog:
            self._handle_message(self._backlog.popleft(), streaming=False)
        while True:
            try:
                message = self._next_event(timeout=0)
            except queue.Empty:
                return
            except KapConnectionClosed as exc:
                self._needs_recovery = True
                raise RuntimeError(
                    f"kimi kap 事件流已断开：{exc}；原请求不会自动重发") from exc
            self._handle_message(message, streaming=False)

    def _handle_message(self, message: dict, *, streaming: bool) -> Chunk | None:
        """非轮次流消息的轻量处理：context 更新、interaction 应答、notice 日志。"""
        kind = message.get("type")
        if kind == "context":
            chunk = self._mapper.map_context(message, self._context_size())
            if chunk is not None:
                self._last_usage = chunk.usage
                return chunk if streaming else None
            return None
        if kind == "interaction":
            self._answer_interaction(message)
            return None
        if kind == "notice":
            logger.warning("[kimi kap] 通知 level=%s code=%s title=%s",
                           message.get("level"), message.get("code"),
                           message.get("title"))
            return None
        if kind == "session":
            session = message.get("session") or {}
            if session.get("id") == self._session_id:
                activity = session.get("activity")
                if activity == "idle":
                    self._needs_recovery = False
            return None
        return None

    def _context_size(self) -> int | None:
        if not self._model:
            return None
        return self._model_sizes.get(self._model)

    def _apply_profile(self) -> None:
        """模型/强度切换：会话存在时走 profile；失败保留旧会话。"""
        if self._closed or self._http is None or self._session_id is None:
            return
        config: dict = {"permission_mode": "manual"}
        if self._model:
            config["model"] = self._model
        if self._effort:
            config["thinking"] = self._effort
        try:
            self._http.post(
                f"/api/v1/sessions/{self._session_id}/profile",
                {"agent_config": config}, timeout=10)
        except KapError as exc:
            logger.warning("[kimi kap] 即时切换配置失败 code=%s", exc.code)

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        message = next((item for item in reversed(messages) if item["role"] == "user"), None)
        if message is None or (not message["content"] and not message.get("images")):
            return
        with self._turn_lock:
            self._chat_active.set()
            try:
                self._cancel.clear()
                self._cancel_sent.clear()
                self._ensure_connected()
                self._drain_backlog()
                prompt_id = f"zen-{os.getpid()}-{next(self._prompt_seq)}"
                content: list[dict] = []
                if message["content"]:
                    content.append({"type": "text", "text": message["content"]})
                content.extend(_image_blocks(message.get("images")))
                try:
                    data = self._http.post(
                        f"/api/v1/sessions/{self._session_id}/prompts",
                        {"prompt_id": prompt_id, "content": content})
                except KapError as exc:
                    if exc.code != 40401:
                        raise RuntimeError(
                            f"kimi kap 发送失败：{_friendly_error(exc)}") from exc
                    # 会话已失效：换新会话必须换订阅，旧 WS 的 pump 先停；
                    # 重建后同一轮 prompt 重试一次，失败不再自动重放。
                    self._session_id = None
                    self._close_ws()
                    self._create_session()
                    self._subscribe()
                    data = self._http.post(
                        f"/api/v1/sessions/{self._session_id}/prompts",
                        {"prompt_id": prompt_id, "content": content})
                user_message_id = data.get("user_message_id")
                status = data.get("status")
                if status in ("failed", "blocked"):
                    raise RuntimeError(f"kimi kap 未接受本轮请求（status={status}）")
                if status == "queued":
                    logger.info("[kimi kap] prompt 已排队 promptId=%s sessionId=%s",
                                prompt_id, self._session_id)
                yield from self._stream_turn(prompt_id, user_message_id)
            finally:
                self._chat_active.clear()

    def _stream_turn(self, prompt_id: str,
                     user_message_id: object) -> Iterator[Chunk]:
        deadline = time.monotonic() + _CANCEL_GRACE_S
        turn_id: str | None = None
        turn_ours: bool | None = None
        last_ordinal: int = -1
        try:
            while True:
                if self._cancel.is_set():
                    if not self._cancel_sent.is_set():
                        self._cancel_sent.set()
                        self._send_cancel()
                        deadline = time.monotonic() + _CANCEL_GRACE_S
                    if time.monotonic() > deadline:
                        raise TurnCancelled("本轮已停止")
                if self._closed:
                    raise RuntimeError("kimi kap 后端已关闭（标签已销毁）")
                try:
                    message = self._next_event(timeout=1.0)
                except queue.Empty:
                    continue
                except KapConnectionClosed as exc:
                    self._needs_recovery = True
                    self._last_error = str(exc)
                    raise RuntimeError(
                        f"kimi kap 事件流已断开：{exc}；原请求不会自动重发") from exc
                kind = message.get("type")
                if kind == "turn.started":
                    turn = message.get("turn") or {}
                    ordinal = turn.get("ordinal")
                    if isinstance(ordinal, int) and ordinal < last_ordinal:
                        # 事件可重复：旧轮的 started 迟到不能把当前轮顶掉
                        logger.debug(
                            "[kimi kap] 忽略迟到 turn.started id=%s ordinal=%s last=%s",
                            turn.get("turn_id"), ordinal, last_ordinal)
                        continue
                    turn_id = turn.get("turn_id")
                    turn_ours = None
                    if isinstance(ordinal, int):
                        last_ordinal = max(last_ordinal, ordinal)
                    logger.debug(
                        "[kimi kap] 事件 turn.started id=%s ordinal=%s",
                        turn_id, ordinal)
                    continue
                if kind in ("item.started", "item.ended"):
                    item = message.get("item") or {}
                    item_turn = message.get("turn_id")
                    if turn_id is None and isinstance(item_turn, str):
                        turn_id = item_turn
                    if turn_id is not None and isinstance(item_turn, str) \
                            and item_turn != turn_id:
                        continue
                    item_type = item.get("type")
                    if item_type == "user_message":
                        prompt_ids = item.get("prompt_ids") or []
                        item_id = item.get("item_id")
                        if (isinstance(user_message_id, str) and item_id == user_message_id) \
                                or prompt_id in prompt_ids:
                            turn_ours = True
                        elif turn_ours is None:
                            turn_ours = False
                        if not turn_ours:
                            continue
                    elif item_type == "notification":
                        turn_ours = False
                        continue
                    elif turn_ours is None:
                        # 未绑定本轮前不消费正文/工具：通知轮的 item 可能先到，
                        # 误认领会把后台通知写进用户轮（真实 C2 冒烟已踩中）。
                        # 只认 user_message 的 prompt_id/item_id 作为绑定锚点。
                        continue
                    if not turn_ours:
                        continue
                    if kind == "item.started":
                        for chunk in self._mapper.map_item_started(
                                item, self._context_size()):
                            yield chunk
                    else:
                        for chunk in self._mapper.map_item_ended(
                                item, self._context_size()):
                            yield chunk
                    continue
                if kind == "item.delta":
                    if turn_ours is True:
                        for chunk in self._mapper.map_delta(message):
                            yield chunk
                    continue
                if kind == "item.updated":
                    if turn_ours is True:
                        for chunk in self._mapper.map_item_updated(message):
                            yield chunk
                    continue
                if kind == "item.removed":
                    if turn_ours is True:
                        for chunk in self._mapper.map_item_removed(message):
                            yield chunk
                    continue
                if kind == "todo":
                    chunk = self._mapper.map_todo(message)
                    if chunk is not None:
                        yield chunk
                    continue
                if kind == "context":
                    chunk = self._mapper.map_context(message, self._context_size())
                    if chunk is not None:
                        self._last_usage = chunk.usage
                        yield chunk
                    continue
                if kind == "interaction":
                    self._answer_interaction(message)
                    continue
                if kind == "notice":
                    logger.warning("[kimi kap] 通知 level=%s code=%s title=%s",
                                   message.get("level"), message.get("code"),
                                   message.get("title"))
                    continue
                if kind == "turn.ended":
                    ended = message.get("turn") or {}
                    ended_id = ended.get("turn_id")
                    logger.debug(
                        "[kimi kap] 事件 turn.ended id=%s ordinal=%s ours=%s outcome=%s current=%s",
                        ended_id, ended.get("ordinal"), turn_ours,
                        ended.get("outcome"), turn_id)
                    if turn_id is None:
                        # 本轮开始前就在跑的外来通知轮收尾；没有绑定关系，
                        # 不能据它的 outcome 结束用户轮。
                        continue
                    if ended_id != turn_id:
                        continue
                    if turn_ours is not True:
                        # 通知轮/未绑定轮收尾：不是用户轮，不能据它的 outcome
                        # 结束本轮；通知轮的 started 在空闲期被 pump 丢弃时，
                        # 这里就是「只看到 ended」的兜底。
                        logger.debug(
                            "[kimi kap] 忽略非本轮 turn.ended id=%s ours=%s",
                            ended_id, turn_ours)
                        self._mapper.end_turn()
                        turn_id = None
                        turn_ours = None
                        ordinal = ended.get("ordinal")
                        if isinstance(ordinal, int):
                            last_ordinal = max(last_ordinal, ordinal)
                        continue
                    consumption = self._mapper.turn_consumption(
                        ended, self._session_id or "")
                    usage = self._last_usage
                    if consumption is not None or usage is not None:
                        yield Chunk("usage", "", usage=usage,
                                    consumption=consumption)
                    ordinal = ended.get("ordinal")
                    if isinstance(ordinal, int):
                        last_ordinal = max(last_ordinal, ordinal)
                    outcome = ended.get("outcome")
                    if outcome == "completed":
                        return
                    if outcome == "interrupted":
                        if self._cancel.is_set() or ended.get("end_reason") == "user_cancelled":
                            raise TurnCancelled("本轮已停止")
                        raise RuntimeError(
                            f"kimi kap 本轮被中断（reason={ended.get('end_reason')}）")
                    error = ended.get("error") or {}
                    detail = error.get("message") if isinstance(error, dict) else None
                    raise RuntimeError(
                        "kimi kap 本轮失败"
                        + (f"：{detail}" if detail else f"（outcome={outcome}）"))
                continue
        finally:
            self._mapper.end_turn()

    def _send_cancel(self) -> None:
        if self._http is None or self._session_id is None:
            return
        try:
            self._http.post(
                f"/api/v1/sessions/{self._session_id}:cancel",
                {"include_background": False}, timeout=10)
            logger.info("[kimi kap] 已发送取消 sessionId=%s", self._session_id)
        except KapError as exc:
            logger.warning("[kimi kap] 取消失败 code=%s msg=%s", exc.code, str(exc))

    def cancel(self) -> None:
        self._cancel.set()
        if not self._cancel_sent.is_set():
            self._cancel_sent.set()
            threading.Thread(
                target=self._send_cancel, name="kimi-kap-cancel", daemon=True).start()

    # ------------------------------------------------------------------
    # 审批/提问交互
    # ------------------------------------------------------------------
    def _answer_interaction(self, message: dict) -> None:
        if message.get("status") != "pending":
            return
        if message.get("session_id") not in (None, self._session_id):
            # 旧会话的迟到交互：不能把应答打到当前会话
            return
        interaction_id = message.get("interaction_id")
        kind = message.get("kind")
        request = message.get("request") or {}
        tool_call_id = message.get("tool_call_id")
        if not isinstance(interaction_id, str) or not interaction_id:
            return
        if kind == "approval":
            body = self._approval_body(request, tool_call_id)
        elif kind == "question":
            body = self._question_body(request, tool_call_id)
        else:
            return
        if self._http is None:
            return
        try:
            self._http.post(
                f"/api/v1/sessions/{self._session_id}/interactions/{interaction_id}",
                body, timeout=30)
            logger.info("[kimi kap] 已应答交互 kind=%s interactionId=%s",
                        kind, interaction_id)
        except KapError as exc:
            # 40404/40902 属正常竞态：交互已被用户/agent 结束，不打断聊天
            logger.info("[kimi kap] 交互应答已失效 code=%s interactionId=%s",
                        exc.code, interaction_id)

    def _approval_body(self, request: dict, tool_call_id: object) -> dict:
        tool_name = str(request.get("tool_name") or "?")
        display = request.get("tool_input_display")
        raw_input = display if isinstance(display, dict) else {}
        action = request.get("action")
        content = [{"content": {"type": "text", "text": action}}] if action else []
        options = [
            {"optionId": "approved_session", "name": "本会话允许",
             "kind": "allow_always"},
            {"optionId": "approved_once", "name": "允许一次",
             "kind": "allow_once"},
            {"optionId": "rejected", "name": "拒绝", "kind": "reject_once"},
        ]
        handler = self._permission_handler
        selected = None
        if handler is not None:
            selected = handler(PermissionRequest(
                params={
                    "sessionId": self._session_id or "",
                    "toolCall": {
                        "toolCallId": str(tool_call_id or ""),
                        "title": tool_name,
                        "kind": _tool_kind({"name": tool_name, "display": raw_input}),
                        "rawInput": raw_input,
                        "content": content,
                    },
                    "options": options,
                },
                kind="permission"))
        if selected == "approved_session":
            return {"kind": "approval", "decision": "approved", "scope": "session"}
        if selected in ("approved", "approved_once"):
            return {"kind": "approval", "decision": "approved"}
        return {"kind": "approval", "decision": "rejected"}

    def _question_body(self, request: dict, tool_call_id: object) -> dict:
        questions = request.get("questions")
        if not isinstance(questions, list) or not questions:
            return {"kind": "question", "dismissed": True}
        handler = self._permission_handler
        if handler is None:
            return {"kind": "question", "dismissed": True}
        answers: dict[str, dict] = {}
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = question.get("id")
            if not isinstance(qid, str) or not qid:
                continue
            options = [
                {"optionId": str(opt.get("id") or ""), "name": str(opt.get("label") or "?"),
                 "kind": "allow_once"}
                for opt in question.get("options") or [] if isinstance(opt, dict)
                and opt.get("id") and opt.get("label")
            ]
            if not options:
                answers[qid] = {"kind": "skipped"}
                continue
            selected = handler(PermissionRequest(
                params={
                    "sessionId": self._session_id or "",
                    "toolCall": {
                        "toolCallId": str(tool_call_id or ""),
                        "title": "AskUserQuestion",
                        "kind": "other",
                        "rawInput": {"questions": [question]},
                    },
                    "options": options,
                },
                kind="question",
                questions=(str(question.get("question") or ""),),
            ))
            valid = {option["optionId"] for option in options}
            if selected in valid:
                if question.get("multi_select"):
                    answers[qid] = {"kind": "multi", "option_ids": [selected]}
                else:
                    answers[qid] = {"kind": "single", "option_id": selected}
            else:
                answers[qid] = {"kind": "skipped"}
        if not answers:
            return {"kind": "question", "dismissed": True}
        return {"kind": "question", "answers": answers}
