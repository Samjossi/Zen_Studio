"""Kimi wire.jsonl 私有读取与统计：纯文件计算，无连接、无 Qt、无厂商外依赖。

wire.jsonl 是 Kimi agent 的落盘记录，仅为本后台提供用量、输出速度与真实
消耗。本模块只做「按字节读 + 按行解析 + 数值汇总」，不解析 ACP 帧，也不
持有跨实例状态：增量游标由调用方创建并持有，损坏/截断/缺失一律降级为空。

口径约束（与迁移前一致，改动需同步 usage 数值回归）：

- `usage.record` 的 `usage.output` 是单次调用值，直取求和，禁止差分；
- 上下文用量取 `token_counting.measured` 优先，未实测时才用 turn 用量；
- 速度分母只计请求到用量记录的生成耗时，工具/审批间隙不计。
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ide.llm.base import TurnConsumption, TurnSpeed, UsageStats

logger = logging.getLogger(__name__)

#: 首次轮询只以 64KB 尾部建立游标，之后仅读新增字节，避免全量扫描 MB 级文件
WIRE_TAIL_BYTES = 64 * 1024


def session_index_path() -> Path:
    """会话索引路径：每次按当前 HOME 求值，保持旧实现的调用期语义。"""
    return Path.home() / ".kimi-code" / "session_index.jsonl"


def session_dir_of(session_id: str) -> Path | None:
    """session_index.jsonl 按 sessionId 反查会话落盘目录；失败返回 None。

    2026-07-31 实证：ACP `session/new` 返回的 sessionId（`session_<uuid>`）
    与 `~/.kimi-code/session_index.jsonl` 的 sessionId 字段完全一致，索引行
    直接给出 sessionDir（wire.jsonl 在其 agents/main/ 下）。倒序扫描取最后
    匹配（索引按创建追加，同 sessionId 理论上唯一，倒序防御性取新）。
    """
    try:
        lines = session_index_path().read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines):
            entry = json.loads(line)
            if entry.get("sessionId") == session_id and entry.get("sessionDir"):
                return Path(entry["sessionDir"])
    except (OSError, json.JSONDecodeError):
        logger.exception("session_index.jsonl 反查会话目录失败")
        return None
    return None


def wire_path(session_dir: Path) -> Path:
    return session_dir / "agents" / "main" / "wire.jsonl"


@dataclass
class WireLineCursor:
    """wire 尾部增量读取游标：只随调用方（单轮旁路）存活，不跨实例共享。

    `buffer` 保存末段未写完的残行，下一次读取先拼回再切行——写盘半行
    不能当坏行计入熔断，否则正常轮次也会被误判为格式漂移。
    """

    path: Path
    offset: int = 0
    buffer: bytes = b""


def read_new_lines(state: WireLineCursor) -> list[bytes] | None:
    """按字节游标读取新增完整行；文件尚未创建/瞬逝返回 None（下轮重试）。

    只做字节切行，不解析 JSON：旁路需要自行累计坏行熔断，解析失败不能
    在这里被吞掉。首读从文件头开始——旁路目录是本轮新建的，文件不会历史
    累积到 MB 级；用量路径另有 64KB 尾部窗口，不走本入口。
    """
    try:
        with state.path.open("rb") as f:
            f.seek(state.offset)
            state.buffer += f.read()
            state.offset = f.tell()
    except OSError:
        return None
    lines = state.buffer.split(b"\n")
    state.buffer = lines.pop()
    return lines


def read_records(session_dir: Path) -> list[dict] | None:
    """读 wire.jsonl 全量记录；文件缺失/损坏返回 None（收尾路径一次读法）。"""
    wire = wire_path(session_dir)
    try:
        return [json.loads(line)
                for line in wire.read_text(encoding="utf-8").strip().splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError):
        logger.exception("wire.jsonl 全量读取失败")
        return None


def is_turn_request(record: dict) -> bool:
    turn_step = record.get("turnStep")
    return (record.get("type") == "llm.request"
            and isinstance(turn_step, str) and bool(turn_step))


def usage_values(usage: object) -> tuple[int, int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    values: list[int] = []
    for key in ("inputOther", "inputCacheRead", "inputCacheCreation", "output"):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            return None
        values.append(value)
    return values[0], values[1], values[2], values[3]


def context_size(
    request: dict, context_sizes: dict[str, int], model_alias: str | None
) -> int | None:
    alias = request.get("modelAlias") or model_alias
    if not isinstance(alias, str):
        return None
    size = context_sizes.get(alias)
    return size if type(size) is int and size > 0 else None


def usage_stats(record: dict, size: int | None) -> UsageStats | None:
    if size is None:
        return None
    values = usage_values(record.get("usage"))
    if values is None:
        return None
    return UsageStats(
        used=sum(values), size=size, cost=None, source="transcript")


def read_turn_stats(
    session_dir: Path,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> tuple[int, int, UsageStats | None] | None:
    """一次全量读取 → (turn 用量数, turn 请求数, 末条用量定型)；失败 None。"""
    records = read_records(session_dir)
    if records is None:
        return None
    sizes = context_sizes or {}
    usage_records = [r for r in records
                     if r.get("type") == "usage.record" and r.get("usageScope") == "turn"]
    requests = [r for r in records if is_turn_request(r)]
    stats: UsageStats | None = None
    if usage_records and requests:
        size = context_size(requests[-1], sizes, model_alias)
        stats = usage_stats(usage_records[-1], size)
    return len(usage_records), len(requests), stats


def read_turn_metrics(
    session_dir: Path, usage_baseline: int, session_id: str | None = None
) -> tuple[TurnSpeed | None, TurnConsumption | None]:
    """轮末一次全量读：输出速度与真实消耗（2026-1114-1452）同 pass 求和。

    `usage.output` 是单次调用值，直取求和，禁止差分。分母仅计请求到
    用量记录的生成耗时，工具/审批间隙不计。非 turn 请求不得改变配对。
    消耗口径与速度解耦：速度配对失败（缺时间戳等）不影响四项求和；
    turn_id 取 `session_id:轮末累计用量条数`，同轮重复收尾幂等去重。
    """
    records = read_records(session_dir)
    if records is None:
        return None, None
    seen = 0
    pending_req_time: int | None = None
    output_total = 0
    gen_ms_total = 0
    sums = [0, 0, 0, 0]
    new_usage = 0
    for r in records:
        rtype = r.get("type")
        if is_turn_request(r):
            req_time = r.get("time")
            pending_req_time = req_time if isinstance(req_time, int) else None
        elif rtype == "usage.record" and r.get("usageScope") == "turn":
            seen += 1
            if seen <= usage_baseline:
                continue
            values = usage_values(r.get("usage"))
            if values is not None:
                new_usage += 1
                for i, value in enumerate(values):
                    sums[i] += value
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
    speed = (TurnSpeed(output_tokens=output_total, gen_seconds=gen_ms_total / 1000)
             if output_total > 0 and gen_ms_total > 0 else None)
    consumption: TurnConsumption | None = None
    if session_id and new_usage > 0 and any(sums):
        consumption = TurnConsumption(
            input_other=sums[0], input_cache_read=sums[1],
            input_cache_creation=sums[2], output=sums[3],
            turn_id=f"{session_id}:{usage_baseline + new_usage}")
    return speed, consumption


@dataclass
class WireUsageCursor:
    """轮内增量读取游标：只随所属 provider 会话存活，不跨实例共享。"""

    path: Path
    offset: int = 0
    buffer: bytes = b""
    model_alias: str | None = None
    last_stats: UsageStats | None = None
    measured_seen: bool = False


def read_usage_incremental(
    state: WireUsageCursor,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> UsageStats | None:
    """按游标读取新增 wire 记录，并保留最后一次有效上下文水位。"""
    wire = wire_path(state.path)
    try:
        file_size = wire.stat().st_size
        if file_size < state.offset:
            state.offset = 0
            state.buffer = b""
            state.model_alias = None
            state.last_stats = None
            state.measured_seen = False
        if file_size == state.offset:
            return state.last_stats
        with wire.open("rb") as f:
            tail_start = state.offset == 0 and file_size > WIRE_TAIL_BYTES
            f.seek(file_size - WIRE_TAIL_BYTES if tail_start else state.offset)
            data = f.read()
            state.offset = f.tell()
    except OSError:
        return state.last_stats
    if tail_start:
        _partial, separator, data = data.partition(b"\n")
        if not separator:
            return state.last_stats
    state.buffer += data
    lines = state.buffer.split(b"\n")
    state.buffer = lines.pop()
    sizes = context_sizes or {}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if is_turn_request(record):
            alias = record.get("modelAlias")
            if isinstance(alias, str) and alias:
                state.model_alias = alias
            continue
        size = context_size({}, sizes, state.model_alias or model_alias)
        if size is None:
            continue
        if record.get("type") == "token_counting.measured":
            tokens = record.get("tokens")
            if type(tokens) is int and tokens >= 0:
                state.last_stats = UsageStats(
                    used=tokens, size=size, cost=None, source="transcript")
                state.measured_seen = True
        elif (record.get("type") == "usage.record"
                and record.get("usageScope") == "turn"
                and not state.measured_seen):
            stats = usage_stats(record, size)
            if stats is not None:
                state.last_stats = stats
    return state.last_stats
