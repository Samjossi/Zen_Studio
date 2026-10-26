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

上下文用量以轮末 ACP `usage_update` 为主（source="push"）：该通知在
prompt response 后异步到达，故收尾在同一 ≤3s 窗口内继续消费 ACP 与
wire，避免把两路等待串行叠加。wire 负责轮内尾部轮询及 ACP 缺失兜底
（source="transcript"）；上下文窗口读模型目录 maxContextSize，不使用
llm.request.maxTokens（该字段是输出预算）。wire used 为
inputOther+inputCacheRead+inputCacheCreation+output 四项总和；只有带
turnStep 的请求参与轮次计数与速度配对，超时保留已落盘的有效子集。

轮次输出速度随收尾 usage chunk 携带 TurnSpeed：基线后逐次配对
`llm.request → usage.record`，分子 Σ usage.output（单次调用值，直取
不差分），分母 Σ(record.time − request.time)，仅含模型生成时间，
工具/审批间隙不计。私有 wire 格式假设登记于维护手册 §6。

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
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message, TurnSpeed, UsageStats
# PermissionOption/PermissionParams/ToolCallInfo 仅 re-export（llm/permission_policy 从此处取型），
# 冗余名别名是 ruff 约定的显式 re-export 写法，勿当未用导入删除
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    PermissionOption as PermissionOption,
    PermissionParams as PermissionParams,
    TerminalHandler,
    ToolCallInfo as ToolCallInfo,
    build_client_capabilities,
    build_prompt_blocks,
    map_session_update,
)
from llm.providers.kimi_common import (
    _find_bin,
    context_sizes_from_catalog,
    load_kimi_provider_catalog,
)

logger = logging.getLogger(__name__)

#: wire.jsonl usage.record 异步写盘等待上限（2026-07-31 实测：response 到达
#: 时 llm.request 已写但 usage.record 尚未落盘，约 1~3s 后异步写入）
_WIRE_USAGE_WAIT_S = 3.0
_WIRE_USAGE_POLL_S = 0.2

#: 轮次内轮询（0117 计划 T2）只读文件尾部的字节窗口：wire.jsonl 随会话增长
#: 可达 MB 级，每 2s 全量 read_text 不可接受；64KB 足够覆盖末多条
#: llm.request / usage.record（单行实测数百字节至数千字节）
_WIRE_TAIL_BYTES = 64 * 1024

#: ACP terminal/* 反向能力支持矩阵（2026-0817-1522 五 agent 实测报告）：
#: kimi/reasonix 支持；本 agent 不支持时保持 terminal: false（行为与现状等价）
_TERMINAL_CAPABILITY = True


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
        logger.exception("session_index.jsonl 反查会话目录失败")
        return None
    return None


def _read_wire_records(session_dir: Path) -> list[dict] | None:
    """读 wire.jsonl 全量记录；文件缺失/损坏返回 None（收尾路径一次读法）。"""
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        return [json.loads(line)
                for line in wire.read_text(encoding="utf-8").strip().splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError):
        logger.exception("wire.jsonl 全量读取失败")
        return None


def _is_turn_request(record: dict) -> bool:
    turn_step = record.get("turnStep")
    return (record.get("type") == "llm.request"
            and isinstance(turn_step, str) and bool(turn_step))


def _usage_values(usage: object) -> tuple[int, int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    values: list[int] = []
    for key in ("inputOther", "inputCacheRead", "inputCacheCreation", "output"):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            return None
        values.append(value)
    return values[0], values[1], values[2], values[3]


def _context_size(
    request: dict, context_sizes: dict[str, int], model_alias: str | None
) -> int | None:
    alias = request.get("modelAlias") or model_alias
    if not isinstance(alias, str):
        return None
    size = context_sizes.get(alias)
    return size if type(size) is int and size > 0 else None


def _usage_stats(record: dict, size: int | None) -> UsageStats | None:
    if size is None:
        return None
    values = _usage_values(record.get("usage"))
    if values is None:
        return None
    return UsageStats(
        used=sum(values), size=size, cost=None, source="transcript")


def _read_wire_turn_stats(
    session_dir: Path,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> tuple[int, int, UsageStats | None] | None:
    """一次全量读取 → (turn 用量数, turn 请求数, 末条用量定型)；失败 None。"""
    records = _read_wire_records(session_dir)
    if records is None:
        return None
    sizes = context_sizes or {}
    usage_records = [r for r in records
                     if r.get("type") == "usage.record" and r.get("usageScope") == "turn"]
    requests = [r for r in records if _is_turn_request(r)]
    stats: UsageStats | None = None
    if usage_records and requests:
        size = _context_size(requests[-1], sizes, model_alias)
        stats = _usage_stats(usage_records[-1], size)
    return len(usage_records), len(requests), stats


def _read_wire_usage(
    session_dir: Path,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> tuple[int, UsageStats | None]:
    """读 wire.jsonl → (turn 用量数, 末条用量定型)；失败返回 (0, None)。"""
    parsed = _read_wire_turn_stats(session_dir, context_sizes, model_alias)
    if parsed is None:
        return 0, None
    return parsed[0], parsed[2]


def _read_wire_turn_speed(
    session_dir: Path, usage_baseline: int
) -> TurnSpeed | None:
    """轮次输出速度：基线后逐次 turn 请求与用量记录配对求和。

    `usage.output` 是单次调用值，直取求和，禁止差分。分母仅计请求到
    用量记录的生成耗时，工具/审批间隙不计。非 turn 请求不得改变配对。
    """
    records = _read_wire_records(session_dir)
    if records is None:
        return None
    seen = 0
    pending_req_time: int | None = None
    output_total = 0
    gen_ms_total = 0
    for r in records:
        rtype = r.get("type")
        if _is_turn_request(r):
            req_time = r.get("time")
            pending_req_time = req_time if isinstance(req_time, int) else None
        elif rtype == "usage.record" and r.get("usageScope") == "turn":
            seen += 1
            if seen <= usage_baseline:
                continue
            usage = r.get("usage") or {}
            output = usage.get("output")
            rec_time = r.get("time")
            if (pending_req_time is None
                    or type(output) is not int or output < 0
                    or not isinstance(rec_time, int)
                    or rec_time <= pending_req_time):
                continue
            output_total += output
            gen_ms_total += rec_time - pending_req_time
    if output_total <= 0 or gen_ms_total <= 0:
        return None
    return TurnSpeed(
        output_tokens=output_total, gen_seconds=gen_ms_total / 1000)


def _read_wire_usage_tail(
    session_dir: Path,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> UsageStats | None:
    """轮次内只读 wire 尾部，取末条有效 turn 用量与对应目录窗口。

    seek 回溯 `_WIRE_TAIL_BYTES`，避免 GUI 每 2 秒全量读取 MB 级文件；
    文件缺失、半行、字段残缺或目录窗口未知均返回 None。
    """
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        with wire.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _WIRE_TAIL_BYTES))
            tail = f.read()
    except OSError:
        logger.exception("wire.jsonl 尾部窗口读取失败")
        return None
    lines = tail.split(b"\n")
    if size > _WIRE_TAIL_BYTES and lines:
        lines = lines[1:]
    sizes = context_sizes or {}
    usage_record: dict | None = None
    request: dict | None = None
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (usage_record is None and record.get("type") == "usage.record"
                and record.get("usageScope") == "turn"
                and _usage_values(record.get("usage")) is not None):
            usage_record = record
        elif request is None and _is_turn_request(record):
            request = record
        if usage_record is not None and request is not None:
            break
    if usage_record is None or request is None:
        return None
    size = _context_size(request, sizes, model_alias)
    return _usage_stats(usage_record, size)


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
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
        #: 终端处理器（GUI 桥经 set_terminal_handler 注入）；None = 不声明
        #: terminal 能力（无 GUI 的 headless 场景自动退化 false）
        self._terminal_handler: TerminalHandler | None = None
        #: 子代理 wire 旁路开关（0813-1919 计划 T4 设置项，GUI 经
        #: set_subagent_sidecar 注入；False 回退纯 ACP 行为——子代理
        #: 仅起止 + 成果摘要）
        self._sidecar_enabled = True
        #: poll_usage 的会话目录缓存（0117 计划 T2）：session_index.jsonl 随会话
        #: 增长，每 2s 轮询重读全索引不可接受；按 session_id 缓存反查结果，
        #: 会话切换（reset/重建）时随 _session_id 变化失效重查
        self._poll_dir_sid: str | None = None
        self._poll_dir: Path | None = None
        self._context_sizes: dict[str, int] = {}
        self._context_sizes_loaded = False
        self._usage_warning_active = False
        atexit.register(self.close)

    def _ensure_context_sizes(self) -> None:
        if self._context_sizes_loaded:
            return
        self._context_sizes = context_sizes_from_catalog(load_kimi_provider_catalog())
        self._context_sizes_loaded = True
        if not self._context_sizes:
            logger.warning("kimi 模型上下文目录为空，wire 用量兜底不可用")

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
                logger.exception("即时切换模型失败，降级为下次新会话生效")
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
                logger.exception("即时切换推理强度失败，降级为新会话时应用")
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
            self._conn.set_terminal_handler(self._terminal_handler)
            self._session_id = None
            self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": build_client_capabilities(
                    terminal=_TERMINAL_CAPABILITY and self._terminal_handler is not None),
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
                    logger.exception("新会话应用预选模型失败，保持 agent 默认模型")
                    pass  # 保持 agent 默认模型，不阻断对话
            if self._effort:  # 新会话应用预选推理强度（2026-0806 计划）
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "thinking",
                        "value": self._effort}, timeout=10)
                except RuntimeError:
                    logger.exception("新会话应用预选强度失败，保持 agent 默认强度")
                    pass  # 保持 agent 默认强度，不阻断对话
        self._ensure_context_sizes()
        return self._conn

    def poll_usage(self) -> UsageStats | None:
        """轮次内主动取用量快照：读 wire 尾部末条有效 turn 用量。

        只读单值缓存，不在 GUI 轮询中拉取模型目录或全量 wire；失败返回 None。
        """
        session_id = self._session_id
        if not session_id:
            return None
        if session_id != self._poll_dir_sid:
            self._poll_dir = _session_dir_of(session_id)
            self._poll_dir_sid = session_id
        if self._poll_dir is None:
            return None
        return _read_wire_usage_tail(self._poll_dir, self._context_sizes, self._model)

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
        """轮次内消费 ACP 与 sidecar；response 后继续收集迟到用量。"""
        session_dir = _session_dir_of(self._session_id) if self._session_id else None
        turn_stats0 = (_read_wire_turn_stats(
            session_dir, self._context_sizes, self._model) if session_dir else None)
        usage_baseline = turn_stats0[0] if turn_stats0 else 0
        request_baseline = turn_stats0[1] if turn_stats0 else 0
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
                        self._session_id = None
                        raise RuntimeError(f"kimi acp 进程意外退出（退出码 {obj}）")
                    if kind == "response":
                        self._raise_on_turn_error(obj)
                        yield from self._collect_turn_end_usage(
                            conn, session_dir, usage_baseline, request_baseline,
                            sidecar, sidecar_queue, turn_push_stats)
                        return
                    chunk = map_session_update(obj)
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
        session_dir: Path | None,
        usage_baseline: int,
        request_baseline: int,
        sidecar: _WireSidecar | None,
        sidecar_queue: "queue.Queue[dict]",
        initial_push: UsageStats | None,
    ) -> Iterator[Chunk]:
        """在同一截止时间内收集 ACP 与 wire；ACP 优先，wire 保留部分结果。"""
        deadline = time.monotonic() + _WIRE_USAGE_WAIT_S
        sidecar_ready_at = (time.monotonic() + _SIDECAR_POLL_S
                            if sidecar is not None else 0.0)
        push_stats = initial_push
        wire_stats: UsageStats | None = None
        wire_complete = session_dir is None
        while True:
            if session_dir is not None:
                parsed = _read_wire_turn_stats(
                    session_dir, self._context_sizes, self._model)
                if parsed is not None:
                    usage_count, request_count, stats = parsed
                    new_usage = usage_count - usage_baseline
                    new_requests = request_count - request_baseline
                    if new_usage > 0 and stats is not None:
                        wire_stats = stats
                    wire_complete = (new_usage >= new_requests if new_requests > 0
                                     else new_usage > 0)
            if sidecar is not None:
                yield from self._drain_sidecar(sidecar_queue)
            if (push_stats is not None and wire_complete
                    and time.monotonic() >= sidecar_ready_at):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                kind, obj = conn.next_update(
                    timeout=min(_WIRE_USAGE_POLL_S, remaining))
            except queue.Empty:
                continue
            if kind == "dead":
                self._session_id = None
                logger.warning("kimi acp 在轮末用量收尾时退出")
                break
            if kind == "response":
                continue
            chunk = map_session_update(obj)
            if chunk:
                if chunk.kind == "usage":
                    if chunk.usage is not None:
                        push_stats = chunk.usage
                else:
                    if sidecar is not None:
                        _sidecar_observe(sidecar, chunk)
                    yield chunk
            if sidecar is not None:
                yield from self._drain_sidecar(sidecar_queue)
        if sidecar is not None:
            yield from self._drain_sidecar(sidecar_queue)
        speed = (_read_wire_turn_speed(session_dir, usage_baseline)
                 if session_dir is not None else None)
        stats = push_stats or wire_stats
        if stats is not None:
            self._usage_warning_active = False
        elif not self._usage_warning_active:
            reasons = []
            if session_dir is None:
                reasons.append("会话目录未定位")
            if not self._context_sizes:
                reasons.append("模型窗口目录为空")
            reasons.append("ACP 未送达")
            if wire_stats is None:
                reasons.append("wire 无本轮有效用量")
            logger.warning("kimi 轮末上下文用量不可用（%s）", "、".join(reasons))
            self._usage_warning_active = True
        if stats is not None or speed is not None:
            yield Chunk("usage", "", usage=stats, speed=speed)

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
        session_dir: Path | None,
        usage_baseline: int,
        request_baseline: int,
        context_sizes: dict[str, int] | None = None,
        model_alias: str | None = None,
    ) -> UsageStats | None:
        """等待本轮 turn 用量凑齐；超时仍返回已落盘的有效子集。"""
        if session_dir is None:
            return None
        deadline = time.monotonic() + _WIRE_USAGE_WAIT_S
        latest_stats: UsageStats | None = None
        while True:
            parsed = _read_wire_turn_stats(session_dir, context_sizes, model_alias)
            if parsed is not None:
                usage_count, request_count, stats = parsed
                new_usage = usage_count - usage_baseline
                new_requests = request_count - request_baseline
                if new_usage > 0 and stats is not None:
                    latest_stats = stats
                complete = (new_usage >= new_requests if new_requests > 0
                            else new_usage > 0)
                if complete:
                    return stats if stats is not None else latest_stats
            if time.monotonic() >= deadline:
                return latest_stats
            time.sleep(_WIRE_USAGE_POLL_S)

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 时抛错（stopReason 正常结束为静默返回）。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
