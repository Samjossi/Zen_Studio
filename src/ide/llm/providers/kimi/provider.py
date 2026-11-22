"""极简 Kimi ACP provider：一条官方轮次链路，不替上游猜测终态。

按方案 A 的约束，`session/prompt` 响应即本轮终态：`end_turn` 正常收尾，
其余 `stopReason` 走失败或取消通道。本模块不读 Kimi 私有 `wire.jsonl`
判断终态，不做终态确认、drain、同步提示与待发闸门——Kimi CLI 在内部
轮次仍在执行时提前回 `end_turn` 属上游缺陷，客户端如实反映协议，不在
这里补一层私有判定（见 `2026-1122-1846_Kimi提供商极简重写路线选型.md`）。
轮内上下文水位由独立只读轮询器从 wire 获取，不接触 ACP 队列。

真实断连保留会话身份：下一次手动发送先 `session/resume`，原 prompt 不自动
重放。历史由 agent 会话管理，凭证由 CLI 自管。
"""
import atexit
import logging
import threading
from typing import Iterator

from ide.core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from ide.core.version import APP_VERSION
from ide.llm.base import (
    BackendCapabilities,
    Chunk,
    LanguageModel,
    Message,
    UsageStats,
)
# 权限与 terminal 类型来自稳定 ACP 协议层；审批处理器接收厂商分类后的
# 中性 PermissionRequest，连接层只传原始 params（无厂商语义）。
from ide.llm.providers.acp import (
    AcpConnection,
    PermissionParams,
    PermissionRequestHandler,
    TerminalHandler,
    TurnCancelled,
    build_client_capabilities,
    build_prompt_blocks,
)
from ide.llm.providers.kimi.catalog import (
    _find_bin,
    context_sizes_from_catalog,
    load_kimi_provider_catalog,
)
from ide.llm.providers.kimi.permissions import build_permission_request
from ide.llm.providers.kimi.updates import KimiUpdateMapper
from ide.llm.providers.kimi.usage_poll import UsagePoller

logger = logging.getLogger(__name__)

#: Kimi 已在 ACP terminal/* 反向能力实测中通过；无 GUI 终端处理器时仍声明 false
_TERMINAL_CAPABILITY = True
_SESSION_RESUME_TIMEOUT_S = 30.0


class KimiAcpLLM(LanguageModel):
    """极简 Kimi ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（None = agent 默认模型 configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）
        """
        self._model = model
        #: 推理强度预选值（None = 未定制，agent 默认强度生效）
        self._effort: str | None = None
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        #: 本 provider 实例私有的 update 映射器；账本不跨标签/会话共享。
        self._updates = KimiUpdateMapper()
        self._usage_poller = UsagePoller(
            context_sizes_source=lambda: context_sizes_from_catalog(
                load_kimi_provider_catalog()))
        self._session_cwd: str | None = None
        self._session_mcp_servers: list[dict] = []
        #: 断连后待恢复：下一次手动发送重连并 resume，原 prompt 不自动重放
        self._needs_recovery = False
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：中性 PermissionRequest → optionId | None
        self._permission_handler: PermissionRequestHandler | None = None
        #: 终端处理器（GUI 桥经 set_terminal_handler 注入）；None = 不声明
        #: terminal 能力（无 GUI 的 headless 场景自动退化 false）
        self._terminal_handler: TerminalHandler | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型别名；失败保留原会话，普通协议错误不触发连接恢复。"""
        self._model = alias
        self._usage_poller.invalidate_model()
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "model", "value": alias}, timeout=10)
            except RuntimeError:
                logger.exception("即时切换模型失败")

    def set_effort(self, value: str) -> None:
        """切换推理强度（会话存在则即时生效）。

        值域随模型而异（k3-256k 为 low/high/max），强度值原样透传不解析，
        不支持的档位由 agent 侧拒绝；失败不丢弃会话——强度是辅助控制轴，
        拒绝不该陪葬会话上下文。
        """
        self._effort = value
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "thinking",
                    "value": value}, timeout=10)
            except RuntimeError:
                logger.exception("即时切换推理强度失败；连接失效时下轮恢复会重试")

    def reset_session(self) -> None:
        """显式清空恢复身份，下次请求 `session/new` 开新会话（进程保留）。"""
        self._session_id = None
        self._session_cwd = None
        self._session_mcp_servers = []
        self._needs_recovery = False
        self._updates.reset()
        self._usage_poller.reset()

    def set_permission_handler(self, handler: PermissionRequestHandler | None) -> None:
        """注入审批处理器：中性 PermissionRequest → optionId（None 视为拒绝）。

        连接层只认原始 params；本厂商先把参数分类成 permission/question，
        再交给 GUI，公共策略与连接层都不解析 Kimi 工具名。
        """
        self._permission_handler = handler
        if self._conn:
            self._conn.set_permission_handler(
                self._handle_permission_request if handler is not None else None)

    def _handle_permission_request(self, params: PermissionParams) -> str | None:
        """连接层回调：按 Kimi 规则分类后转交中性处理器。"""
        handler = self._permission_handler
        if handler is None:
            return None
        return handler(build_permission_request(params))

    def set_terminal_handler(self, handler: TerminalHandler | None) -> None:
        """注入终端处理器（terminal/* 反向请求路由）；None 恢复 terminal: false。"""
        self._terminal_handler = handler
        if self._conn:
            self._conn.set_terminal_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程并注销 atexit 钩。

        不先发 `session/close`：进程终止本身即结束隐藏通知轮，少一条分流
        判定就少一处状态耦合（方案 A）。`_closed` 先置位，让 spawn 与
        close 竞态窗口内迟到的连接即建即杀，避免 acp 进程孤儿。
        """
        self._closed = True
        atexit.unregister(self.close)
        if self._conn:
            self._conn.terminate()
            self._conn = None
        self.reset_session()

    @property
    def recovery_pending(self) -> bool:
        return bool(self._session_id) and (
            self._needs_recovery or self._conn is None or not self._conn.is_alive)

    def poll_usage(self) -> UsageStats | None:
        session_id = self._session_id
        if session_id is None:
            return None
        return self._usage_poller.poll(session_id, self._model)

    def capabilities(self) -> BackendCapabilities:
        """当前能力快照：图片可用、无看门狗、无轮末用量宽限。

        每次按现状重建：恢复态随连接/会话变化，缓存会在 GUI 上显示滞后。
        """
        return BackendCapabilities(
            image_input=True,
            post_turn_usage_grace_s=0.0,
            stall_watchdog=False,
            recovery_pending=self.recovery_pending,
        )

    def cancel(self) -> None:
        """取消当前轮次：协议 `session/cancel` + 本地 abort_turn 解封。

        不回落 `session/close`：cancel 覆盖不到隐藏通知轮是上游行为，客户端
        不自建第二条停止通道（方案 A）。abort_turn 保证 agent 沉默时阻塞中的
        轮次消费秒级醒来，停止按钮不至于完全无响应。
        """
        conn = self._conn
        if conn is None:
            return
        if self._session_id:
            conn.cancel_turn(self._session_id)
        conn.abort_turn()

    def _ensure_session(self) -> AcpConnection:
        if self._closed:  # 标签已销毁：拒绝新建连接
            raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        self._usage_poller.ensure_context_sizes()
        if self._conn is not None and (not self._conn.is_alive or self._needs_recovery):
            self._conn.terminate()
            self._conn = None
        if self._conn is None:
            conn = AcpConnection([bin_path, "acp"], self._cwd, "kimi acp")
            self._conn = conn
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                conn.terminate()
                self._conn = None
                raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
            conn.set_permission_handler(
                self._handle_permission_request
                if self._permission_handler is not None else None)
            conn.set_terminal_handler(self._terminal_handler)
            try:
                init_result = conn.request("initialize", {
                    "protocolVersion": 1,
                    "clientCapabilities": build_client_capabilities(
                        terminal=_TERMINAL_CAPABILITY and self._terminal_handler is not None),
                    "clientInfo": {
                        "name": "zen-studio", "title": "Zen Studio", "version": APP_VERSION},
                })
            except RuntimeError as e:
                conn.log_diagnostics("initialize_failed", "initialize")
                if self._session_id:
                    self._raise_recovery_failed(e)
                raise
            agent = init_result.get("agentInfo") or {}
            capabilities = init_result.get("agentCapabilities") or {}
            resume_supported = "resume" in (capabilities.get("sessionCapabilities") or {})
            logger.info(
                "[kimi acp min] 初始化 agent=%s version=%s generation=%s resume=%s",
                agent.get("name", "?"), agent.get("version", "?"),
                conn.generation, resume_supported)
            if self._session_id:
                self._resume_session(conn, init_result)
        if self._session_id is None:
            try:
                result = self._conn.request(
                    "session/new", {"cwd": self._cwd, "mcpServers": []}, timeout=60)
            except RuntimeError as e:
                if "-32000" in str(e):  # authRequired：凭证失效，需用户重新登录
                    raise RuntimeError("kimi 未登录或凭证已过期，请在终端执行 `kimi login` 后重试") from None
                raise
            self._session_id = result["sessionId"]
            self._session_cwd = self._cwd
            self._session_mcp_servers = []
            # 新会话没有旧会话的调用账本可承接；清空可防止无界增长，并让
            # 相同 toolCallId 在新会话中重新开始。
            self._updates.reset()
            self._usage_poller.reset()
            self._apply_session_config(strict=False)
            self._needs_recovery = False
        return self._conn

    def _apply_session_config(self, strict: bool) -> None:
        assert self._conn is not None and self._session_id is not None
        for config_id, value in (("model", self._model), ("thinking", self._effort)):
            if not value:
                continue
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": config_id,
                    "value": value}, timeout=10)
            except RuntimeError:
                if strict:
                    raise
                logger.exception("新会话应用预选配置失败 configId=%s", config_id)

    def _resume_session(self, conn: AcpConnection, init_result: dict) -> None:
        assert self._session_id is not None
        capabilities = init_result.get("agentCapabilities") or {}
        if "resume" not in (capabilities.get("sessionCapabilities") or {}):
            self._raise_recovery_failed(RuntimeError(
                "当前 Kimi CLI 不支持 session/resume，请升级 CLI"))
        logger.info(
            "[kimi acp min] 恢复开始 sessionId=%s generation=%s",
            self._session_id, conn.generation)
        try:
            conn.request("session/resume", {
                "sessionId": self._session_id,
                "cwd": self._session_cwd or self._cwd,
                "mcpServers": list(self._session_mcp_servers),
            }, timeout=_SESSION_RESUME_TIMEOUT_S)
            self._apply_session_config(strict=True)
        except RuntimeError as e:
            self._raise_recovery_failed(e)
        self._needs_recovery = False
        self._usage_poller.reset()
        logger.info(
            "[kimi acp min] 恢复成功 sessionId=%s generation=%s",
            self._session_id, conn.generation)

    def _raise_recovery_failed(self, error: RuntimeError) -> None:
        self._needs_recovery = True
        if self._conn is not None:
            self._conn.log_diagnostics("recovery_failed", "session/resume")
        logger.warning(
            "[kimi acp min] 恢复失败 sessionId=%s generation=%s error=%s",
            self._session_id, self._conn.generation if self._conn else None, error)
        raise RuntimeError(
            "原会话恢复失败，未发送消息；可重试恢复，或明确新建空会话。"
            f"原因：{error}") from error

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件）
        message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if message is None or (not message["content"] and not message.get("images")):
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
            # 开轮前清队列：上一轮响应后的迟到帧不清会串进下一轮
            conn.purge_updates()
            request_id = conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": build_prompt_blocks(message, workspace_root=self._cwd),
            })
            try:
                yield from self._iter_turn_chunks(conn, request_id)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(
        self,
        conn: AcpConnection,
        request_id: int | None = None,
    ) -> Iterator[Chunk]:
        """消费 ACP 帧直到 prompt response 或真实断连。

        响应即终态：不核对私有记录，不等待「可能仍在跑」的内部轮次。
        """
        while True:
            kind, obj = conn.next_update(timeout=None)
            if kind == "dead":
                self._needs_recovery = True
                raise RuntimeError(
                    f"kimi acp 连接已断开（{conn.failure_reason or '未知原因'}，"
                    f"退出码 {obj}）；原请求不会自动重发")
            if kind == "response":
                self._raise_on_turn_error(obj)
                error = self._classify_stop_reason(obj, request_id)
                if error is not None:
                    raise error
                return
            chunk = self._updates.map_session_update(obj)
            if chunk:
                yield chunk

    def _classify_stop_reason(
        self, response: dict, request_id: int | None = None
    ) -> Exception | None:
        """把 prompt 响应的 `stopReason` 映射为待抛异常；正常终态返回 None。

        ACP 只以响应里的 StopReason 表示轮次终态：`cancelled` 是 agent 对
        cancel 的确认，走 TurnCancelled 停止通道；其余非 end_turn 终态走失败
        通道（保留屏幕输出、回滚 history）。未知值 fail-open：枚举可能扩展，
        fail-closed 会让旧 IDE 永久卡 busy；原始值进日志便于上游契约变化时定位。
        """
        stop_reason = (response.get("result") or {}).get("stopReason")
        logger.info(
            "[kimi acp min] 轮次响应 stopReason=%r requestId=%s responseId=%s "
            "sessionId=%s generation=%s",
            stop_reason, request_id, response.get("id"), self._session_id,
            self._conn.generation if self._conn is not None else None)
        if stop_reason in (None, "end_turn"):
            return None
        if stop_reason == "cancelled":
            return TurnCancelled("本轮已被取消")
        if stop_reason == "max_tokens":
            return RuntimeError(
                "kimi acp 本轮未正常完成：输出达到模型长度上限"
                "（stopReason=max_tokens）")
        if stop_reason == "max_turn_requests":
            return RuntimeError(
                "kimi acp 本轮未正常完成：单轮模型请求次数达到上限"
                "（stopReason=max_turn_requests）")
        if stop_reason == "refusal":
            return RuntimeError(
                "kimi acp agent 拒绝继续本轮（stopReason=refusal）")
        logger.warning(
            "[kimi acp min] 未知 stopReason=%r sessionId=%s，按正常完成收尾",
            stop_reason, self._session_id)
        return None

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 帧时抛错；优先级高于 stopReason 判定。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
