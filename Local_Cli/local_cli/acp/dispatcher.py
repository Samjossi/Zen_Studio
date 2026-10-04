"""分发层：JSON-RPC 方法表、错误码表、主读循环。

主循环持续读帧（§1.6/§3：cancel 通知与审批回执必须即时到达），
prompt 轮次派发给守护线程执行。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable

from local_cli import __version__
from local_cli.acp.session import AcpSession, SessionManager
from local_cli.acp.transport import (
    IncomingFrame,
    IncomingNotification,
    IncomingRequest,
    IncomingResponse,
    ReverseRequestBroker,
    StdioTransport,
)
from local_cli.acp.turn import TurnRunner, extract_prompt_text
from local_cli.model.base import LanguageModel


class JsonRpcErrorCode:
    """协议 §5 错误码表（本项目只用这三个；-32099 由客户端注入，agent 不发）。"""
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


class RpcMethod:
    """支持的 JSON-RPC 方法名（魔法字符串命名常量）。"""
    INITIALIZE = "initialize"
    SESSION_NEW = "session/new"
    SESSION_SET_CONFIG_OPTION = "session/set_config_option"
    SESSION_PROMPT = "session/prompt"
    SESSION_CANCEL = "session/cancel"


#: set_config_option 目前唯一支持的配置项（§1.5：会话内即时切换模型）
CONFIG_ID_MODEL = "model"

#: ACP 线协议版本（§1.3 initialize 协商值，与协议文档版本是两个层面）
ACP_PROTOCOL_VERSION = 1


class RpcMethodError(Exception):
    """handler 内抛出的可转错误帧异常：message 写可读原因与建议动作（A.5）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RpcDispatcher:
    """方法分发与主读循环：协作者全部经构造函数注入（AFCP 2.3/2.4）。"""

    def __init__(
        self,
        transport: StdioTransport,
        broker: ReverseRequestBroker,
        sessions: SessionManager,
        model: LanguageModel,
        agent_name: str,
    ) -> None:
        self._transport = transport
        self._broker = broker
        self._sessions = sessions
        self._model = model
        self._agent_name = agent_name
        self._handlers: dict[str, Callable[[IncomingRequest], dict[str, Any]]] = {
            RpcMethod.INITIALIZE: self._handle_initialize,
            RpcMethod.SESSION_NEW: self._handle_session_new,
            RpcMethod.SESSION_SET_CONFIG_OPTION: self._handle_set_config_option,
            # SESSION_PROMPT 不入表：它需要起守护线程，由 _dispatch_request 特殊分支处理
        }

    # ------------------------------------------------------------------
    # 主读循环
    # ------------------------------------------------------------------
    def serve(self) -> int:
        """读帧 → 分发，直到 stdin EOF（客户端关闭管道即进程退出）。"""
        self._transport.log(f"{self._agent_name} {__version__} 进入 ACP 模式")
        for frame in self._transport.read_frames():
            self.dispatch(frame)
        self._transport.log("stdin EOF，退出")
        return 0

    def dispatch(self, frame: IncomingFrame) -> None:
        if isinstance(frame, IncomingResponse):
            # 反向请求回执：投递给阻塞等待的轮次线程（§3）
            if not self._broker.resolve(frame):
                self._transport.log(f"无等待者的回执，忽略: id={frame.request_id}")
            return
        if isinstance(frame, IncomingNotification):
            self._dispatch_notification(frame)
            return
        self._dispatch_request(frame)

    def _dispatch_notification(self, notification: IncomingNotification) -> None:
        if notification.method == RpcMethod.SESSION_CANCEL:
            # §1.6：cancel 是通知无需响应；置位会话取消标志即可
            session = self._require_session(notification.params)
            if session is not None:
                session.cancel_event.set()
                self._transport.log("session/cancel 收到，轮次即停")
            return
        self._transport.log(f"未知通知，忽略: {notification.method}")

    def _dispatch_request(self, request: IncomingRequest) -> None:
        # prompt 优先于方法表判断：它需要起守护线程，不走同步 handler 路径
        if request.method == RpcMethod.SESSION_PROMPT:
            self._start_turn_thread(request)
            return
        handler = self._handlers.get(request.method)
        if handler is None:
            self._transport.send_error(
                request.request_id, JsonRpcErrorCode.METHOD_NOT_FOUND,
                f"method not found: {request.method}")
            self._transport.log(f"未知方法: {request.method}")
            return
        try:
            result = handler(request)
        except RpcMethodError as method_error:
            self._transport.send_error(
                request.request_id, method_error.code, method_error.message)
            return
        self._transport.send_result(request.request_id, result)

    def _start_turn_thread(self, request: IncomingRequest) -> None:
        """prompt 轮次在守护线程执行：主循环继续读帧（cancel/审批回执及时到达）。"""
        session = self._require_session(request.params)
        if session is None:
            self._transport.send_error(
                request.request_id, JsonRpcErrorCode.INVALID_PARAMS,
                "invalid params: 未知 sessionId")
            return
        prompt_text = extract_prompt_text(request.params.get("prompt") or [])
        runner = TurnRunner(
            transport=self._transport,
            broker=self._broker,
            model=self._model,
            session=session,
            prompt_text=prompt_text,
        )
        threading.Thread(
            target=runner.execute, args=(request.request_id,), daemon=True,
        ).start()

    def _require_session(self, params: dict[str, Any]) -> AcpSession | None:
        return self._sessions.find(params.get("sessionId") or "")

    # ------------------------------------------------------------------
    # 方法 handler（返回 result 载荷；校验失败抛 RpcMethodError）
    # ------------------------------------------------------------------
    def _handle_initialize(self, _request: IncomingRequest) -> dict[str, Any]:
        # A.1：authMethods 是静态能力声明，不当「未配置」信号——这里不返回它
        self._transport.log(f"initialize ok ({self._agent_name} {__version__})")
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentCapabilities": {},
            "agentInfo": {"name": self._agent_name, "version": __version__},
        }

    def _handle_session_new(self, request: IncomingRequest) -> dict[str, Any]:
        cwd = request.params.get("cwd") or ""
        # §1.4/A.2：cwd 硬校验绝对路径，相对路径 -32602 硬拒
        if not os.path.isabs(cwd):
            raise RpcMethodError(
                JsonRpcErrorCode.INVALID_PARAMS,
                f"invalid params: cwd 必须是绝对路径，收到 {cwd!r}")
        # A.1/A.5：模型未配置信号归 session/new 的 -32603 + 可读引导文案。
        # Mock 恒可用不触发；GGUF 后端在 llama-server 缺失或模型目录为空时触发。
        if not self._model.is_available():
            raise RpcMethodError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                "模型未加载：请先在 Local CLI 配置本地模型文件后重试")
        session = self._sessions.create(cwd)
        self._transport.log(
            f"session/new ok: {session.session_id} "
            f"(cwd={cwd}, model={session.model_alias})")
        return {"sessionId": session.session_id}

    def _handle_set_config_option(self, request: IncomingRequest) -> dict[str, Any]:
        session = self._require_session(request.params)
        if session is None:
            raise RpcMethodError(
                JsonRpcErrorCode.INVALID_PARAMS, "invalid params: 未知 sessionId")
        config_id = request.params.get("configId")
        if config_id != CONFIG_ID_MODEL:
            raise RpcMethodError(
                JsonRpcErrorCode.INVALID_PARAMS,
                f"invalid params: 不支持的 configId {config_id!r}")
        # §1.5：value 是不透明字符串，原样接受不解析；未知别名报错不崩
        value = request.params.get("value")
        if value not in self._model.known_aliases():
            raise RpcMethodError(
                JsonRpcErrorCode.INVALID_PARAMS,
                f"invalid params: 未知模型别名 {value!r}"
                f"（可选：{self._model.known_aliases()}）")
        session.model_alias = value
        self._transport.log(f"set_config_option ok: model={value}")
        return {}
