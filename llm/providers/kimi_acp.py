"""Kimi ACP provider：长驻 `kimi acp` 子进程 + JSON-RPC（ndjson 帧）对接。

kimi 唯一传输层（CLI `-p` 模式已移除，见 2026-0731-0036 计划）：长驻单进程、
token 级流式（`agent_message_chunk`）、思维链可见（`agent_thought_chunk`）、
会话原生管理（`session/new`）；历史由 agent 侧会话管理，仅取末条 user 消息作
prompt。凭证由 CLI 自管（OAuth），代码库零密钥。

连接层已泛化抽出（计划 2026-0730-0150 §4-D5）：帧收发/反向请求/死讯注入
见 llm/providers/acp.py 的 AcpConnection（agent_name 参数化）；本文件仅留
kimi 专有装配（initialize 载荷、-32000 authRequired 文案映射），二进制路径
解析复用 llm/providers/kimi_common.py 的 _find_bin。
行为与抽离前逐行等价。
"""
import atexit
import threading
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message
# PermissionOption/ToolCallInfo 仅 re-export（llm/permission_policy 从此处取型）
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    PermissionOption,
    PermissionParams,
    ToolCallInfo,
)
from llm.providers.kimi_common import _find_bin


class KimiAcpLLM(LanguageModel):
    """Kimi ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（None = agent 默认模型 configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）
        """
        self._model = model
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
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

    def set_workspace_root(self, root: str) -> None:
        """切换 agent 工作目录：丢弃旧 session（长驻进程保留，下次 session/new 用新 cwd）。

        当前无调用方（多开模型下工作区根进程级固定），按计划 2026-0722-0756 预留。
        """
        self._cwd = root
        self.reset_session()

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器：params → optionId（None 视为拒绝）；None 恢复自动允许。"""
        self._permission_handler = handler
        if self._conn:
            self._conn.set_permission_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程并注销 atexit 钩（多标签：实例随标签关闭销毁，
        不注销则绑定方法把已死实例钉在 atexit 注册表至进程退出）。

        `_closed` 先置位：`_ensure_session` 在 spawn 前后双检该标志，
        "close 之后新建的连接"语义上不可能存活（评审 CRITICAL#1：
        清理线程 terminate 与 worker spawn 竞态曾致 acp 进程孤儿）。
        """
        self._closed = True
        atexit.unregister(self.close)
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

    def _ensure_session(self) -> AcpConnection:
        if self._closed:  # 标签已销毁：拒绝新建连接（评审 CRITICAL#1）
            raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "kimi acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
            self._conn.set_permission_handler(self._permission_handler)
            self._session_id = None
            self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": APP_VERSION},
            })
        if self._session_id is None:
            try:
                result = self._conn.request(
                    "session/new", {"cwd": self._cwd, "mcpServers": []}, timeout=60)
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

    def _iter_turn_chunks(self, conn: AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。"""
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"kimi acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                return
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
