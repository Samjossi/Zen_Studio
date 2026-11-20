"""Kimi ACP provider：长驻 `kimi acp` 子进程与 JSON-RPC ndjson 帧对接。

轮次生命周期只遵循 ACP：发送一次 `session/prompt`，转发 `session/update`，
按对应 response 的 `stopReason` 收尾。真实断连保留下一次手动发送时的
`session/resume`；原 prompt 不自动重放。历史由 agent 会话管理，凭证由 CLI 自管。

wire.jsonl 仅提供用量、输出速度、真实消耗和子代理展示数据。轮末用量在
有界窗口内尽力收集，缺失、迟到或格式异常均不改变 ACP 终态。连接帧收发
与反向请求复用 AcpConnection；session/update 由本厂商私有
KimiUpdateMapper 解释。
"""
import atexit
import json
import logging
import queue
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Iterator, cast

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
from ide.llm.providers.kimi import usage as kimi_usage, wire
from ide.llm.providers.kimi.catalog import (
    _find_bin,
    context_sizes_from_catalog,
    load_kimi_provider_catalog,
)
from ide.llm.providers.kimi.permissions import build_permission_request
from ide.llm.providers.kimi.updates import KimiUpdateMapper

logger = logging.getLogger(__name__)

#: 轮次收尾后继续接收迟到用量帧的宽限（Kimi 轮末 push 可能晚于 response）
_POST_TURN_USAGE_GRACE_S = 10.0

#: Kimi 已在 ACP terminal/* 反向能力实测中通过；无 GUI 终端处理器时仍声明 false
_TERMINAL_CAPABILITY = True
_SESSION_RESUME_TIMEOUT_S = 30.0


# ----------------------------------------------------------------------
# 子代理 wire 旁路（0813-1919 计划 T4：kimi 子代理内部活动嵌套显示数据源）
# ----------------------------------------------------------------------
#: 旁路轮询间隔：兼作 conn.next_update 超时（双源汇合节拍）与 wire 增量
#: 读取间隔（轮次内增量写盘实测与 0117 用量轮询同机制，亚秒级延迟可接受）
_SIDECAR_POLL_S = 0.3

#: wire 行 JSON 解析失败熔断阈值（R1 私有格式漂移对策：累计超限静默
#: 关闭旁路回退纯 ACP；写盘半行按残行缓冲重拼，不计失败）
_SIDECAR_PARSE_FAIL_LIMIT = 8

#: wire 工具名 → ACP tool_kind 映射（0813-1919 计划 §3.2：合成帧复用
#: 主流卡片分派约定；未收录名回退 other——AskUserQuestion/TodoList/Agent
#: 等经渲染层工具名二级分派落专卡，与主流同约定）
_WIRE_TOOL_KINDS: dict[str, str] = {
    "Bash": "execute",
    "Edit": "edit",
    "Write": "edit",
    "Read": "read",
    "Glob": "read",
    "Grep": "read",
    "FetchURL": "fetch",
    "WebFetch": "fetch",
    "WebSearch": "fetch",
}


def _wire_tool_kind(name: str) -> str:
    return _WIRE_TOOL_KINDS.get(name, "other")


def _synthesize_wire_call(event: dict, parent_tid: str) -> dict | None:
    """wire `tool.call` 事件 → ACP 同构 tool_call update dict（层级 tid）。

    合成即真实数据的 ACP 化重表达（不臆造）：title/kind/rawInput/locations
    均出自 wire 实记；Edit 形态 wire 不给 ACP diff 项，args 的
    old_string/new_string 即真实增删数据，合成 content diff 项喂协议层
    `_extract_diff` 管线（与 kimi 主流 0919 计划 T2 content diff 项同构；
    0803 计划 T2 write 合成 diff 先例同纪律）。Write 形态 rawInput.content
    由 `_extract_write_diff` 天然承接，无需特判。
    """
    name = event.get("name")
    wire_tid = event.get("toolCallId")
    if not isinstance(name, str) or not name \
            or not isinstance(wire_tid, str) or not wire_tid:
        return None
    kind = _wire_tool_kind(name)
    args = event.get("args")
    args = args if isinstance(args, dict) else {}
    update: dict = {
        "sessionUpdate": "tool_call",
        "toolCallId": f"{parent_tid}/{wire_tid}",
        "title": name,
        "kind": kind,
        "status": "pending",
    }
    if args:
        update["rawInput"] = args
    if isinstance(args.get("path"), str):
        update["locations"] = [{"path": args["path"]}]
    if kind == "edit" and (isinstance(args.get("old_string"), str)
                           or isinstance(args.get("new_string"), str)):
        update["content"] = [{
            "type": "diff",
            "path": args.get("path"),
            "oldText": args.get("old_string") or "",
            "newText": args.get("new_string") or "",
        }]
    return update


def _synthesize_wire_result(event: dict, parent_tid: str, kind: str) -> dict | None:
    """wire `tool.result` 事件 → ACP 同构 tool_call_update（completed）dict。

    wire 侧无 in_progress 概念（v1 不合成尾滚帧，起止两帧尽力而为）；
    result.output 映射 rawOutput.output 进既有输出提取管线；错误以
    output 文本形态落盘（wire 无独立 error 字段，实测格式假设见维护手册）。
    """
    wire_tid = event.get("toolCallId")
    if not isinstance(wire_tid, str) or not wire_tid:
        return None
    update: dict = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": f"{parent_tid}/{wire_tid}",
        "status": "completed",
        "kind": kind,
    }
    result = event.get("result")
    if isinstance(result, dict) and isinstance(result.get("output"), str):
        update["rawOutput"] = {"output": result["output"]}
    return update


def _dir_ctime(path: Path) -> float:
    """目录创建时序（关联排序键）；stat 失败排末位（瞬逝目录防御）。"""
    try:
        return path.stat().st_ctime
    except OSError:
        logger.exception("目录 stat 失败，排序排末位")
        return float("inf")


class _WireSidecar(threading.Thread):
    """子代理 wire.jsonl 旁路线程（0813-1919 计划 T4）。

    生命周期 = 单个轮次：构造时快照 `agents/` 目录基线；轮次内主流
    Agent 调用在途期间（`note_agent_call` 登记父 tid 后）发现新增
    `agent-N` 子目录即建立关联（单在途假设：同一时刻一个子代理，
    两级封顶下成立；多在途按目录创建时序先到先得，关联不上则该路
    静默降级）；关联后按字节偏移增量解析 wire 尾部，合成 update dict
    注入 out 队列由轮次消费循环排干。目录发现失败、wire 缺失、JSON
    解析失败累计超阈值（格式漂移）→ 置 _broken 静默收束，行为回退
    纯 ACP（仅起止 + 成果摘要）。
    """

    def __init__(self, session_dir: Path, out: "queue.Queue[dict]") -> None:
        super().__init__(daemon=True)
        self._agents_dir = session_dir / "agents"
        self._out = out
        self._stop_flag = threading.Event()
        self._broken = False
        #: 在途 Agent 调用的 toolCallId（worker 线程单值写入，GIL 原子；
        #: 关联建立后锁定不再更新——首在途优先，防多在途错配，R2）
        self._parent_tid: str | None = None
        #: 目录发现基线（构造时已在场的子目录不属本轮新增）
        try:
            self._known_dirs = {
                p.name for p in self._agents_dir.iterdir() if p.is_dir()}
        except OSError:
            logger.exception("旁路目录基线快照失败，按空基线降级")
            self._known_dirs = set()
        self._wire_path: Path | None = None
        self._wire_offset = 0
        self._wire_buffer = b""
        #: wire tid → kind（tool.result 帧自身不带工具名，call 帧簿记回补）
        self._kinds: dict[str, str] = {}
        self._parse_failures = 0

    def note_agent_call(self, tid: str) -> None:
        """主流 Agent 调用在途登记（首在途优先；关联后忽略后续登记）。"""
        if self._wire_path is None:
            self._parent_tid = tid

    def stop(self) -> None:
        self._stop_flag.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_flag.is_set() and not self._broken:
            try:
                self._poll()
            except Exception:  # noqa: BLE001 — 旁路任何异常静默降级，不连累主轮次
                logger.exception("kimi 子代理 wire 旁路异常，旁路关闭")
                return
            self._stop_flag.wait(_SIDECAR_POLL_S)

    def _poll(self) -> None:
        if self._wire_path is None:
            self._discover()
        if self._wire_path is not None:
            self._read_wire()

    def _discover(self) -> None:
        """新增 agent-N 目录发现 + 在途 Agent 关联（父 tid 未登记不发现）。"""
        if self._parent_tid is None:
            return
        try:
            current = {p.name: p for p in self._agents_dir.iterdir()
                       if p.is_dir() and p.name.startswith("agent-")}
        except OSError:
            logger.exception("子代理目录发现失败，本轮跳过")
            return
        new = [p for name, p in current.items() if name not in self._known_dirs]
        if not new:
            return
        self._known_dirs |= set(current)
        new.sort(key=_dir_ctime)  # 多在途先到先得（R2 假设外的尽力而为）
        self._wire_path = new[0] / "wire.jsonl"

    def _read_wire(self) -> None:
        """按字节偏移增量读 wire 尾部；残行留缓冲下轮重拼（写盘半行防御）。"""
        try:
            with self._wire_path.open("rb") as f:
                f.seek(self._wire_offset)
                self._wire_buffer += f.read()
                self._wire_offset = f.tell()
        except OSError:
            return  # 文件尚未创建/瞬逝：下轮再试
        lines = self._wire_buffer.split(b"\n")
        self._wire_buffer = lines.pop()  # 末段残行（可能正在写）留下轮
        for raw in lines:
            self._consume_line(raw)

    def _consume_line(self, raw: bytes) -> None:
        """单条 wire 记录分发：只消费 context.append_loop_event 的
        tool.call/tool.result（content.part 思维链 v1 跳过，计划 R4）。"""
        if not raw.strip():
            return
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("wire 行 JSON 解析失败（累计计入熔断阈值）")
            self._parse_failures += 1
            self._broken = self._parse_failures >= _SIDECAR_PARSE_FAIL_LIMIT
            return
        if record.get("type") != "context.append_loop_event":
            return
        event = record.get("event") or {}
        parent = self._parent_tid
        if parent is None:
            return
        etype = event.get("type")
        if etype == "tool.call":
            if update := _synthesize_wire_call(event, parent):
                self._kinds[event["toolCallId"]] = update["kind"]
                self._out.put(update)
        elif etype == "tool.result":
            kind = self._kinds.get(event.get("toolCallId") or "", "other")
            if update := _synthesize_wire_result(event, parent, kind):
                self._out.put(update)


def _sidecar_observe(sidecar: _WireSidecar, chunk: Chunk) -> None:
    """ACP 主流帧反哺旁路：Agent 调用在途登记（目录发现关联锚点）。

    title 归一化小写命中 agent/task 即登记在途 toolCallId（kimi 实证
    title="Agent"，0813-1919 探针；task 防御收录）。只看不改 Chunk。
    """
    if chunk.kind != "tool_call":
        return
    payload = chunk.payload or {}
    title = payload.get("title")
    tid = payload.get("tool_call_id")
    if isinstance(title, str) and isinstance(tid, str) \
            and title.strip().lower() in ("agent", "task"):
        sidecar.note_agent_call(tid)


class KimiAcpLLM(LanguageModel):
    """Kimi ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（None = agent 默认模型 configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）
        """
        self._model = model
        #: 推理强度预选值（2026-0806 计划；None = 未定制，agent 默认强度生效）
        self._effort: str | None = None
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        #: 本 provider 实例私有的 update 映射器；账本不跨标签/会话共享。
        self._updates = KimiUpdateMapper()
        self._session_cwd: str | None = None
        self._session_mcp_servers: list[dict] = []
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
        #: 子代理 wire 旁路开关（0813-1919 计划 T4 设置项，GUI 经
        #: set_subagent_sidecar 注入；False 回退纯 ACP 行为——子代理
        #: 仅起止 + 成果摘要）
        self._sidecar_enabled = True
        #: 用量状态按 provider 实例持有：会话目录定位、上下文窗口、增量游标
        #: 与轮末汇总全在 tracker 内，模块级不共享（跨标签/会话互不污染）。
        self._usage = kimi_usage.UsageTracker(
            context_sizes_source=lambda: context_sizes_from_catalog(
                load_kimi_provider_catalog()))
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型别名；失败保留原会话，普通协议错误不触发连接恢复。"""
        self._model = alias
        self._usage.invalidate_model()
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "model", "value": alias}, timeout=10)
            except RuntimeError:
                logger.exception("即时切换模型失败")

    def set_effort(self, value: str) -> None:
        """切换推理强度（2026-0806 计划；会话存在则即时生效）。

        生效机制同 set_model（D6 红线 3）：session/set_config_option
        （configId="thinking"——kimi configOptions 的 thinking 选择器，
        2026-0718-1555 实测），强度值原样透传不解析不校验（D6 红线 2 同款
        不透明字符串语义）；值域随模型而异（k3-256k 为 low/high/max，
        2026-0725-0205 实测），当前模型不支持的档位由 agent 侧拒绝。
        失败不丢弃会话：强度是辅助控制轴，拒绝不该陪葬会话上下文；
        若失败已令连接失效，下轮恢复时会重新应用 `_effort`。
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
        self._usage.reset()

    def set_workspace_root(self, root: str) -> None:
        """切换 agent 工作目录：丢弃旧 session（长驻进程保留，下次 session/new 用新 cwd）。

        当前无调用方（多开模型下工作区根进程级固定），按计划 2026-0722-0756 预留。
        """
        self._cwd = root
        self.reset_session()

    def set_permission_handler(self, handler: PermissionRequestHandler | None) -> None:
        """注入审批处理器：中性 PermissionRequest → optionId（None 视为拒绝）。

        连接层只认原始 params；本厂商先把参数分类成 permission/question，
        再交给 GUI，公共策略与连接层都不解析 Kimi 工具名。None 恢复自动允许。
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

    def set_subagent_sidecar(self, enabled: bool) -> None:
        """子代理 wire 旁路开关注入（0813-1919 计划 T4；GUI 鸭子类型接线，
        无本方法的 provider 静默跳过）。"""
        self._sidecar_enabled = enabled

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
        self.reset_session()

    @property
    def recovery_pending(self) -> bool:
        return bool(self._session_id) and (
            self._needs_recovery or self._conn is None or not self._conn.is_alive)

    def capabilities(self) -> BackendCapabilities:
        """当前能力快照：图片可用、无看门狗；正常轮末 10s 宽限，待恢复态 0。

        每次按现状重建：恢复态随连接/会话变化，缓存会在 GUI 上显示滞后。
        """
        recovery_pending = self.recovery_pending
        return BackendCapabilities(
            image_input=True,
            post_turn_usage_grace_s=(
                0.0 if recovery_pending else _POST_TURN_USAGE_GRACE_S),
            stall_watchdog=False,
            recovery_pending=recovery_pending,
        )

    def apply_vendor_settings(self, settings: Mapping[str, object]) -> None:
        """解释本后台声明的厂商设置；未声明键静默忽略。

        子代理旁路开关沿用 set_subagent_sidecar 的既有语义（原值直传，
        不做类型归一——持久化值的历史读取口径不变）。
        """
        if "kimi_wire_sidecar" in settings:
            self.set_subagent_sidecar(cast(bool, settings["kimi_wire_sidecar"]))

    def cancel(self) -> None:
        """取消当前轮次：协议 `session/cancel` + 本地 abort_turn；无轮次时 no-op。

        不持 `_turn_lock`（cancel 从 GUI 线程来，锁由 chat 所在 worker 持有）；
        不终止连接进程——长驻连接是会话资产，仅结束当前轮次。
        本地中断（2026-1118-1115 计划 D1）：agent 沉默时协议通知无回包，
        abort_turn 保证阻塞中的轮次消费秒级解封，停止按钮不失效。
        """
        conn = self._conn
        if conn is not None and self._session_id:
            conn.cancel_turn(self._session_id)
            conn.abort_turn()

    def _ensure_session(self) -> AcpConnection:
        if self._closed:  # 标签已销毁：拒绝新建连接（评审 CRITICAL#1）
            raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
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
            resume_supported = "resume" in (
                capabilities.get("sessionCapabilities") or {})
            logger.info(
                "[kimi acp] 初始化 agent=%s version=%s generation=%s resume=%s",
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
            self._apply_session_config(strict=False)
            self._needs_recovery = False
            self._usage.reset()
        self._usage.ensure_context_sizes()
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
            "[kimi acp] 恢复开始 sessionId=%s generation=%s",
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
        self._usage.reset()
        logger.info(
            "[kimi acp] 恢复成功 sessionId=%s generation=%s",
            self._session_id, conn.generation)

    def _raise_recovery_failed(self, error: RuntimeError) -> None:
        self._needs_recovery = True
        if self._conn is not None:
            self._conn.log_diagnostics("recovery_failed", "session/resume")
        logger.warning(
            "[kimi acp] 恢复失败 sessionId=%s generation=%s error=%s",
            self._session_id, self._conn.generation if self._conn else None, error)
        raise RuntimeError(
            "原会话恢复失败，未发送消息；可重试恢复，或明确新建空会话。"
            f"原因：{error}") from error

    def poll_usage(self) -> UsageStats | None:
        """读取增量 wire；仅空闲时回收迟到 ACP usage，避免与 worker 抢帧。"""
        session_id = self._session_id
        if not session_id:
            return None
        return self._usage.poll(
            session_id, self._model, self._conn,
            self._updates.map_session_update, self._turn_lock)

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件，
        # 0340 方案 B：text+image 多块经 build_prompt_blocks 构造）
        message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if message is None or (not message["content"] and not message.get("images")):
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
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
        """消费 ACP 与展示 sidecar，直到 prompt response 或真实断连。"""
        session_dir = wire.session_dir_of(self._session_id) if self._session_id else None
        turn_usage = self._usage.begin_turn(session_dir, self._model, self._session_id)
        turn_push_stats: UsageStats | None = None
        sidecar: _WireSidecar | None = None
        sidecar_queue: queue.Queue[dict] = queue.Queue()
        if self._sidecar_enabled and session_dir is not None:
            sidecar = _WireSidecar(session_dir, sidecar_queue)
            sidecar.start()
        try:
            while True:
                timeout = _SIDECAR_POLL_S if sidecar is not None else None
                try:
                    kind, obj = conn.next_update(timeout=timeout)
                except queue.Empty:
                    pass
                else:
                    if kind == "dead":
                        self._needs_recovery = True
                        raise RuntimeError(
                            f"kimi acp 连接已断开（{conn.failure_reason or '未知原因'}，"
                            f"退出码 {obj}）；原请求不会自动重发")
                    if kind == "response":
                        self._raise_on_turn_error(obj)
                        pending_error = self._classify_stop_reason(obj, request_id)
                        yield from self._collect_turn_end_usage(
                            conn, turn_usage, sidecar, sidecar_queue,
                            turn_push_stats)
                        if pending_error is not None:
                            raise pending_error
                        return
                    chunk = self._updates.map_session_update(obj)
                    if chunk:
                        if chunk.kind == "usage" and chunk.usage is not None:
                            turn_push_stats = chunk.usage
                        if sidecar is not None:
                            _sidecar_observe(sidecar, chunk)
                        yield chunk
                if sidecar is not None:
                    yield from self._drain_sidecar(sidecar_queue)
        finally:
            if sidecar is not None:
                sidecar.stop()

    def _collect_turn_end_usage(
        self,
        conn: AcpConnection,
        turn: kimi_usage.TurnUsage,
        sidecar: _WireSidecar | None,
        sidecar_queue: "queue.Queue[dict]",
        initial_push: UsageStats | None,
    ) -> Iterator[Chunk]:
        """在同一截止时间内收集 ACP 与 wire；ACP 优先，wire 保留部分结果。"""
        turn.start_collection()
        turn.accept_push(initial_push)
        sidecar_ready_at = (time.monotonic() + _SIDECAR_POLL_S
                            if sidecar is not None else 0.0)
        while True:
            turn.poll_wire()
            if sidecar is not None:
                yield from self._drain_sidecar(sidecar_queue)
            if turn.ready(time.monotonic(), sidecar_ready_at):
                break
            remaining = turn.deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                kind, obj = conn.next_update(
                    timeout=min(turn.poll_interval, remaining))
            except queue.Empty:
                continue
            if kind == "dead":
                self._needs_recovery = True
                logger.warning(
                    "kimi acp 在轮末用量收尾时退出 generation=%s sessionId=%s",
                    conn.generation, self._session_id)
                break
            if kind == "response":
                continue
            chunk = self._updates.map_session_update(obj)
            if chunk:
                if chunk.kind == "usage":
                    turn.accept_push(chunk.usage)
                else:
                    if sidecar is not None:
                        _sidecar_observe(sidecar, chunk)
                    yield chunk
            if sidecar is not None:
                yield from self._drain_sidecar(sidecar_queue)
        if sidecar is not None:
            yield from self._drain_sidecar(sidecar_queue)
        result = turn.finish()
        if result.has_payload:
            yield Chunk("usage", "", usage=result.stats, speed=result.speed,
                        consumption=result.consumption)

    def _drain_sidecar(self, sidecar_queue: "queue.Queue[dict]") -> Iterator[Chunk]:
        """排干旁路合成队列：update dict 经本厂商 mapper 真实映射产出
        Chunk，层级 toolCallId 由 Kimi mapper 拆出父指针。"""
        while True:
            try:
                update = sidecar_queue.get_nowait()
            except queue.Empty:
                return
            chunk = self._updates.map_session_update({
                "params": {"update": update, "sessionId": self._session_id or ""}})
            if chunk:
                yield chunk

    def _classify_stop_reason(
        self, response: dict, request_id: int | None = None
    ) -> Exception | None:
        """把 prompt 响应的 `stopReason` 映射为待抛异常；正常终态返回 None。

        ACP 只以 `session/prompt` 响应的 StopReason 表示轮次终态，`cancelled`
        是 agent 对 cancel 的确认、不属于失败，走 TurnCancelled 停止通道；
        其余非 end_turn 终态走现有失败通道（保留屏幕输出、回滚 history）。
        未知值 fail-open：StopReason 枚举可能扩展，fail-closed 会让旧 IDE
        永久卡 busy；原始值必须进日志，便于上游契约变化时定位。
        """
        stop_reason = (response.get("result") or {}).get("stopReason")
        logger.info(
            "[kimi acp] 轮次响应 stopReason=%r requestId=%s responseId=%s "
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
            "[kimi acp] 未知 stopReason=%r sessionId=%s，按正常完成收尾",
            stop_reason, self._session_id)
        return None

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 帧时抛错；优先级高于 stopReason 判定。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
