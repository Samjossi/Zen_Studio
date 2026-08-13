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

上下文用量（1454 计划 T5，2026-07-31 实证落地）：kimi ACP 出口不推
usage_update（1412 T5 实测），轮次收尾改读 agent 会话落盘记录
`~/.kimi-code/session_index.jsonl` → sessionDir → agents/main/wire.jsonl
的末条 usage.record（API 真值，source="transcript"）；size 取末条
llm.request.maxTokens。usage.record 于 response 后约 1~3s 异步写盘，
轮次开始记基线条数、收尾短轮询（≤3s）只接受新记录，超时/残缺静默降级。

session/update 映射（1602 计划 T3）：私有 _map_update 已删除，统一改调
llm/providers/acp.py 的公共实现 map_session_update（D4 上收——原四份
逐行一致副本同一改动改四处必然漂移）。

子代理 wire 旁路（0813-1919 计划 T4，客户端私有行为，无协议契约）：
kimi ACP 通道对子代理（Agent 工具）仅报起止，但会话落盘
`agents/agent-N/wire.jsonl` 完整记录子代理 tool.call/tool.result 且
轮次内增量写盘（与 0117 轮次内用量轮询同机制实证）。旁路线程在轮次内
发现新增 agent-N 目录、按字节偏移增量解析 wire，合成与 reasonix 层级
ID 帧同构的 update dict（tid 拼 `父/子` 全串），经队列注入轮次消费
循环与 conn 双源汇合——GUI 只认 parent_tool_call_id 一种形态，来源
差异封闭在本文件。目录发现失败/格式漂移 → 静默关闭旁路回退纯 ACP。
"""
import atexit
import json
import queue
import threading
import time
from pathlib import Path
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message, UsageStats
# PermissionOption/ToolCallInfo 仅 re-export（llm/permission_policy 从此处取型）
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    PermissionOption,
    PermissionParams,
    ToolCallInfo,
    build_prompt_blocks,
    map_session_update,
)
from llm.providers.kimi_common import _find_bin

#: wire.jsonl usage.record 异步写盘等待上限（2026-07-31 实测：response 到达
#: 时 llm.request 已写但 usage.record 尚未落盘，约 1~3s 后异步写入）
_WIRE_USAGE_WAIT_S = 3.0
_WIRE_USAGE_POLL_S = 0.2

#: 轮次内轮询（0117 计划 T2）只读文件尾部的字节窗口：wire.jsonl 随会话增长
#: 可达 MB 级，每 2s 全量 read_text 不可接受；64KB 足够覆盖末多条
#: llm.request / usage.record（单行实测数百字节至数千字节）
_WIRE_TAIL_BYTES = 64 * 1024


def _session_dir_of(session_id: str) -> Path | None:
    """session_index.jsonl 按 sessionId 反查会话落盘目录；失败返回 None。

    2026-07-31 实证：ACP `session/new` 返回的 sessionId（`session_<uuid>`）
    与 `~/.kimi-code/session_index.jsonl` 的 sessionId 字段完全一致，索引行
    直接给出 sessionDir（wire.jsonl 在其 agents/main/ 下）。倒序扫描取最后
    匹配（索引按创建追加，同 sessionId 理论上唯一，倒序防御性取新）。
    """
    index = Path.home() / ".kimi-code" / "session_index.jsonl"
    try:
        lines = index.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines):
            entry = json.loads(line)
            if entry.get("sessionId") == session_id and entry.get("sessionDir"):
                return Path(entry["sessionDir"])
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _read_wire_usage(session_dir: Path) -> tuple[int, UsageStats | None]:
    """读 wire.jsonl → (usage.record 条数, 末条用量定型)；文件缺失/损坏返回 (0, None)。

    末条口径（1454 计划 T5，E2 实证字段结构）：
    - used = inputOther + inputCacheRead（对齐 kilocode 推送口径 input+cache.read，
      不含 output 与 inputCacheCreation）；
    - size = 末条 `llm.request.maxTokens`（kimi-code 以模型窗口为请求上限，
      实测 262144 与 kilocode 推送的 size 交叉验证一致）；
    - source="transcript"（agent 落盘的 API 真值，非协议推送）。
    """
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        records = [json.loads(line)
                   for line in wire.read_text(encoding="utf-8").strip().splitlines()
                   if line.strip()]
    except (OSError, json.JSONDecodeError):
        return 0, None
    usage_records = [r for r in records
                     if r.get("type") == "usage.record" and r.get("usageScope") == "turn"]
    if not usage_records:
        return 0, None
    usage = usage_records[-1].get("usage") or {}
    used_other = usage.get("inputOther")
    used_cache = usage.get("inputCacheRead")
    requests = [r for r in records if r.get("type") == "llm.request"]
    max_tokens = requests[-1].get("maxTokens") if requests else None
    if (not isinstance(used_other, int) or used_other < 0
            or not isinstance(used_cache, int) or used_cache < 0
            or not isinstance(max_tokens, int) or max_tokens <= 0):
        return len(usage_records), None
    return len(usage_records), UsageStats(
        used=used_other + used_cache, size=max_tokens, cost=None, source="transcript")


def _read_wire_usage_tail(session_dir: Path) -> UsageStats | None:
    """轮次内轮询（0117 计划 T2/D2）：只读 wire.jsonl 尾部窗口，取末条用量定型。

    与收尾 `_poll_wire_usage` 语义相反——不卡基线条数，就要最新一条（轮次内
    任何子步骤的新记录皆可接受）；与 `_read_wire_usage` 全量读法区分——尾部
    seek 回溯 `_WIRE_TAIL_BYTES`，防 MB 级文件每 2s 全量读（红线）。

    尾部窗口内须同时找到末条 usage.record（used）与末条 llm.request
    （maxTokens 作 size）；窗口未覆盖 llm.request（理论罕见）或字段残缺、
    文件缺失/正被写半行（json 损坏行跳过）→ None，静默降级不抛出。
    """
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        with wire.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _WIRE_TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    lines = tail.split(b"\n")
    if size > _WIRE_TAIL_BYTES and lines:
        lines = lines[1:]  # 窗口起点落在行中：丢弃首条残行
    used_other: int | None = None
    used_cache: int | None = None
    max_tokens: int | None = None
    for raw in reversed(lines):  # 倒序取末条：usage.record 与 llm.request 各取一
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue  # 写盘半行/损坏行跳过
        rtype = r.get("type")
        if used_other is None and rtype == "usage.record" and r.get("usageScope") == "turn":
            usage = r.get("usage") or {}
            used_other = usage.get("inputOther")
            used_cache = usage.get("inputCacheRead")
        elif max_tokens is None and rtype == "llm.request":
            max_tokens = r.get("maxTokens")
        if used_other is not None and max_tokens is not None:
            break
    if (not isinstance(used_other, int) or used_other < 0
            or not isinstance(used_cache, int) or used_cache < 0
            or not isinstance(max_tokens, int) or max_tokens <= 0):
        return None
    return UsageStats(
        used=used_other + used_cache, size=max_tokens, cost=None, source="transcript")


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
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
        #: 子代理 wire 旁路开关（0813-1919 计划 T4 设置项，GUI 经
        #: set_subagent_sidecar 注入；False 回退纯 ACP 行为——子代理
        #: 仅起止 + 成果摘要）
        self._sidecar_enabled = True
        #: poll_usage 的会话目录缓存（0117 计划 T2）：session_index.jsonl 随会话
        #: 增长，每 2s 轮询重读全索引不可接受；按 session_id 缓存反查结果，
        #: 会话切换（reset/重建）时随 _session_id 变化失效重查
        self._poll_dir_sid: str | None = None
        self._poll_dir: Path | None = None
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

    def set_effort(self, value: str) -> None:
        """切换推理强度（2026-0806 计划；会话存在则即时生效）。

        生效机制同 set_model（D6 红线 3）：session/set_config_option
        （configId="thinking"——kimi configOptions 的 thinking 选择器，
        2026-0718-1555 实测），强度值原样透传不解析不校验（D6 红线 2 同款
        不透明字符串语义）；值域随模型而异（k3-256k 为 low/high/max，
        2026-0725-0205 实测），当前模型不支持的档位由 agent 侧拒绝。
        失败不丢弃会话（与 set_model 的差异）：强度是辅助控制轴，拒绝
        不该陪葬会话上下文——`_effort` 已记，下个新会话生效。
        """
        self._effort = value
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "thinking",
                    "value": value}, timeout=10)
            except RuntimeError:
                pass  # 降级：保持会话与当前强度，新会话时应用 _effort

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
            if self._effort:  # 新会话应用预选推理强度（2026-0806 计划）
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "thinking",
                        "value": self._effort}, timeout=10)
                except RuntimeError:
                    pass  # 保持 agent 默认强度，不阻断对话
        return self._conn

    def poll_usage(self) -> UsageStats | None:
        """轮次内主动取用量快照（0117 计划 T2）：读 wire.jsonl 尾部末条记录。

        T0 实证：轮次内每次 API 调用后 usage.record(scope=turn) 增量写盘，
        数值单调爬升，可作为轮次内徽章数据源。语义见 D2——不卡基线条数，
        就要最新一条；收尾真值仍由 `_poll_wire_usage` 基线轮询兜底。

        线程安全：从 GUI QTimer 调用，与 chat 所在 worker 并发——`_session_id`
        只读单值、目录缓存读写均为原子赋值（GIL），读文件失败静默 None。
        """
        session_id = self._session_id
        if not session_id:
            return None
        if session_id != self._poll_dir_sid:
            self._poll_dir = _session_dir_of(session_id)
            self._poll_dir_sid = session_id
        if self._poll_dir is None:
            return None
        return _read_wire_usage_tail(self._poll_dir)

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
            conn.purge_updates()
            conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": build_prompt_blocks(message, workspace_root=self._cwd),
            })
            try:
                yield from self._iter_turn_chunks(conn)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(self, conn: AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。

        0813-1919 计划 T4 双源汇合：旁路启用时 conn.next_update 改带
        超时轮询，超时间隙排干 wire 旁路合成队列（子代理内部活动帧与
        ACP 帧同一 map_session_update 真实映射链，层级 tid 自动拆父
        指针）；旁路关闭/不可用时保持原阻塞语义零开销。
        """
        # 用量基线（1454 T5）：kimi 无 usage_update（1412 T5 实证），轮次收尾
        # 改读会话落盘 wire.jsonl。usage.record 于 response 后约 1~3s 异步写盘，
        # 且须防误读上一轮记录——轮次开始时记下 (session_dir, 已有条数) 基线，
        # 收尾只接受条数增长后的新记录。
        session_dir = _session_dir_of(self._session_id) if self._session_id else None
        baseline_count = _read_wire_usage(session_dir)[0] if session_dir else 0
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
                    pass  # 无 ACP 帧间隙：落入下方旁路排干（子代理活动主力通道）
                else:
                    if kind == "dead":
                        self._session_id = None
                        raise RuntimeError(f"kimi acp 进程意外退出（退出码 {obj}）")
                    if kind == "response":
                        self._raise_on_turn_error(obj)
                        if sidecar is not None:
                            # 尾部宽限：response 到达时 wire 末段可能尚未
                            # 落盘，再给一个轮询周期后末次排干（worker
                            # 线程内短等待，不阻塞 GUI）
                            time.sleep(_SIDECAR_POLL_S)
                            yield from self._drain_sidecar(sidecar_queue)
                        stats = self._poll_wire_usage(session_dir, baseline_count)
                        if stats is not None:
                            yield Chunk("usage", "", usage=stats)
                        return
                    chunk = map_session_update(obj)
                    if chunk:
                        if sidecar is not None:
                            _sidecar_observe(sidecar, chunk)
                        yield chunk
                if sidecar is not None:
                    yield from self._drain_sidecar(sidecar_queue)
        finally:
            if sidecar is not None:
                sidecar.stop()

    def _drain_sidecar(self, sidecar_queue: "queue.Queue[dict]") -> Iterator[Chunk]:
        """排干旁路合成队列：update dict 经 map_session_update 真实映射
        产出 Chunk（层级 tid 由公共映射拆出父指针，0813-1919 计划 T1）。"""
        while True:
            try:
                update = sidecar_queue.get_nowait()
            except queue.Empty:
                return
            chunk = map_session_update({
                "params": {"update": update, "sessionId": self._session_id or ""}})
            if chunk:
                yield chunk

    @staticmethod
    def _poll_wire_usage(
        session_dir: Path | None, baseline_count: int
    ) -> UsageStats | None:
        """轮询 wire.jsonl 直至出现本轮新 usage.record（条数 > 基线）或超时。

        在 worker 线程内短轮询（最长 _WIRE_USAGE_WAIT_S），不阻塞 GUI；
        超时/数据残缺 → None（徽章隐藏降级，D4 语义）。session_dir 为 None
        （索引未收录）时直接放弃，不轮询。
        """
        if session_dir is None:
            return None
        deadline = time.monotonic() + _WIRE_USAGE_WAIT_S
        while True:
            count, stats = _read_wire_usage(session_dir)
            if count > baseline_count:
                return stats  # 新记录已落盘（数据残缺则 stats=None，同样收尾）
            if time.monotonic() >= deadline:
                return None
            time.sleep(_WIRE_USAGE_POLL_S)

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 时抛错（stopReason 正常结束为静默返回）。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
