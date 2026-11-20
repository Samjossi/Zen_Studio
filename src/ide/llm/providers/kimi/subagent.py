"""Kimi 子代理 wire 旁路：目录发现、增量解析、事件合成与线程生命周期。

wire.jsonl 是 Kimi agent 的私有落盘记录。旁路只在轮次内工作：主流 ACP
帧出现 Agent/Task 调用后登记父 toolCallId，再发现本轮新增的
`agents/agent-N/` 子目录，增量读取其 wire.jsonl，把 tool.call/tool.result
重表达为 ACP 同构 update dict 交给 provider 映射上屏。

本模块不 import provider、不 import Qt：合成是纯函数，线程只在调用方显式
`start()` 后才创建，`stop()` 幂等并做有界 join；模块 import 无 I/O 与线程
副作用。旁路状态（父指针、已见目录、kind 簿记、坏行计数）全部属于实例，
随轮次结束丢弃，不能放模块级。
"""
import json
import logging
import queue
import threading
from pathlib import Path

from ide.llm.base import Chunk
from ide.llm.providers.kimi import wire

logger = logging.getLogger(__name__)

#: 旁路轮询间隔：兼作 conn.next_update 超时（双源汇合节拍）与 wire 增量
#: 读取间隔（轮次内增量写盘实测与 0117 用量轮询同机制，亚秒级延迟可接受）
SIDECAR_POLL_S = 0.3

#: wire 行 JSON 解析失败熔断阈值（R1 私有格式漂移对策：累计超限静默
#: 关闭旁路回退纯 ACP；写盘半行按残行缓冲重拼，不计失败）
SIDECAR_PARSE_FAIL_LIMIT = 8

#: 停止时有界等待线程退出的上限；轮询间隔 0.3s，正常远低于此值
STOP_JOIN_TIMEOUT_S = 1.0

#: wire 工具名 → ACP tool_kind 映射（0813-1919 计划 §3.2：合成帧复用
#: 主流卡片分派约定；未收录名回退 other——AskUserQuestion/TodoList/Agent
#: 等经渲染层工具名二级分派落专卡，与主流同约定）
WIRE_TOOL_KINDS: dict[str, str] = {
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


def wire_tool_kind(name: str) -> str:
    return WIRE_TOOL_KINDS.get(name, "other")


def synthesize_wire_call(event: dict, parent_tid: str) -> dict | None:
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
    kind = wire_tool_kind(name)
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


def synthesize_wire_result(event: dict, parent_tid: str, kind: str) -> dict | None:
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


class WireSidecar:
    """子代理 wire.jsonl 旁路线程（0813-1919 计划 T4）。

    生命周期 = 单个轮次：构造时快照 `agents/` 目录基线；轮次内主流
    Agent 调用在途期间（`note_agent_call` 登记父 tid 后）发现新增
    `agent-N` 子目录即建立关联（单在途假设：同一时刻一个子代理，
    两级封顶下成立；多在途按目录创建时序先到先得，关联不上则该路
    静默降级）；关联后按字节偏移增量解析 wire 尾部，合成 update dict
    注入 out 队列由轮次消费循环排干。目录发现失败、wire 缺失、JSON
    解析失败累计超阈值（格式漂移）→ 置 _broken 静默收束，行为回退
    纯 ACP（仅起止 + 成果摘要）。

    线程用组合而非继承：`start()` 重复调用只启动一次，`stop()` 幂等并
    有界 join，异常退出后 `is_alive()` 为 False，调用方无需区分路径。
    """

    def __init__(self, session_dir: Path, out: "queue.Queue[dict]") -> None:
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
        self._cursor: wire.WireLineCursor | None = None
        #: wire tid → kind（tool.result 帧自身不带工具名，call 帧簿记回补）
        self._kinds: dict[str, str] = {}
        self._parse_failures = 0
        self._thread: threading.Thread | None = None
        self._started = False

    # ------------------------------------------------------------------
    # 显式生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动轮询线程；重复调用无副作用（单轮只允许一个线程）。"""
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run, name="kimi-wire-sidecar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止并做有界 join；幂等，异常退出/未启动时同样安全。"""
        self._stop_flag.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(STOP_JOIN_TIMEOUT_S)

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # 旁路观察
    # ------------------------------------------------------------------
    def note_agent_call(self, tid: str) -> None:
        """主流 Agent 调用在途登记（首在途优先；关联后忽略后续登记）。"""
        if self._wire_path is None:
            self._parent_tid = tid

    def _run(self) -> None:
        while not self._stop_flag.is_set() and not self._broken:
            try:
                self._poll()
            except Exception:  # noqa: BLE001 — 旁路任何异常静默降级，不连累主轮次
                logger.exception("kimi 子代理 wire 旁路异常，旁路关闭")
                return
            self._stop_flag.wait(SIDECAR_POLL_S)

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
        self._cursor = wire.WireLineCursor(self._wire_path)

    def _read_wire(self) -> None:
        """按字节偏移增量读 wire 尾部；残行留缓冲下轮重拼（写盘半行防御）。"""
        lines = wire.read_new_lines(self._cursor)
        if lines is None:
            return  # 文件尚未创建/瞬逝：下轮再试
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
            self._broken = self._parse_failures >= SIDECAR_PARSE_FAIL_LIMIT
            return
        if record.get("type") != "context.append_loop_event":
            return
        event = record.get("event") or {}
        parent = self._parent_tid
        if parent is None:
            return
        etype = event.get("type")
        if etype == "tool.call":
            if update := synthesize_wire_call(event, parent):
                self._kinds[event["toolCallId"]] = update["kind"]
                self._out.put(update)
        elif etype == "tool.result":
            kind = self._kinds.get(event.get("toolCallId") or "", "other")
            if update := synthesize_wire_result(event, parent, kind):
                self._out.put(update)


def observe_chunk(sidecar: WireSidecar, chunk: Chunk) -> None:
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
