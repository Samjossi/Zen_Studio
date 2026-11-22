"""Kimi 轮内上下文水位：只读会话索引与 wire，不接触 ACP 连接或轮次状态。"""
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ide.llm.base import UsageStats

logger = logging.getLogger(__name__)

WIRE_TAIL_BYTES = 64 * 1024
POLL_INTERVAL_S = 4.0
SESSION_DIR_RETRY_DELAYS_S = (2.0, 5.0, 15.0)


def session_index_path() -> Path:
    return Path.home() / ".kimi-code" / "session_index.jsonl"


def session_dir_of(session_id: str) -> Path | None:
    """从索引末尾反查会话目录；单行损坏不妨碍其他匹配项。"""
    try:
        lines = session_index_path().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        logger.debug("kimi 会话索引读取失败 category=%s", type(exc).__name__)
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("sessionId") != session_id:
            continue
        session_dir = entry.get("sessionDir")
        if isinstance(session_dir, str) and session_dir:
            return Path(session_dir)
    return None


def wire_path(session_dir: Path) -> Path:
    return session_dir / "agents" / "main" / "wire.jsonl"


@dataclass
class WireUsageCursor:
    path: Path
    offset: int = 0
    buffer: bytes = b""
    model_alias: str | None = None
    last_stats: UsageStats | None = None
    measured_seen: bool = False


@dataclass
class _SessionState:
    directory: Path | None = None
    directory_failures: int = 0
    directory_retry_at: float = 0.0
    sampled_at: float | None = None
    cursor: WireUsageCursor | None = None


def _reset_cursor(state: WireUsageCursor) -> None:
    state.offset = 0
    state.buffer = b""
    state.model_alias = None
    state.last_stats = None
    state.measured_seen = False


def _is_turn_request(record: dict) -> bool:
    turn_step = record.get("turnStep")
    return (record.get("type") == "llm.request"
            and isinstance(turn_step, str) and bool(turn_step))


def _record_model_alias(record: dict) -> str | None:
    record_type = record.get("type")
    key = ("model" if record_type == "usage.record" else "modelAlias")
    if record_type not in {"llm.request", "profile.bind", "usage.record"}:
        return None
    if record_type == "llm.request" and not _is_turn_request(record):
        return None
    alias = record.get(key)
    return alias if isinstance(alias, str) and alias else None


def _usage_values(usage: object) -> tuple[int, int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    values = []
    for key in ("inputOther", "inputCacheRead", "inputCacheCreation", "output"):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            return None
        values.append(value)
    return values[0], values[1], values[2], values[3]


def _context_size(
    context_sizes: dict[str, int], model_alias: str | None
) -> int | None:
    if not isinstance(model_alias, str):
        return None
    size = context_sizes.get(model_alias)
    return size if type(size) is int and size > 0 else None


def read_usage_incremental(
    state: WireUsageCursor,
    context_sizes: dict[str, int] | None = None,
    model_alias: str | None = None,
) -> UsageStats | None:
    """读取新增完整行；任何无效记录都保留最后一次有效水位。"""
    wire = wire_path(state.path)
    try:
        file_size = wire.stat().st_size
        if file_size < state.offset:
            _reset_cursor(state)
        if file_size == state.offset:
            return state.last_stats
        tail_start = state.offset == 0 and file_size > WIRE_TAIL_BYTES
        with wire.open("rb") as stream:
            stream.seek(file_size - WIRE_TAIL_BYTES if tail_start else state.offset)
            data = stream.read()
            state.offset = stream.tell()
    except OSError as exc:
        logger.debug("kimi wire 读取失败 category=%s", type(exc).__name__)
        return state.last_stats

    if tail_start:
        _partial, separator, data = data.partition(b"\n")
        if not separator:
            data = b""
    state.buffer += data
    lines = state.buffer.split(b"\n")
    state.buffer = lines.pop()
    sizes = context_sizes or {}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        alias = _record_model_alias(record)
        if alias is not None:
            state.model_alias = alias
        if _is_turn_request(record):
            continue
        size = _context_size(sizes, state.model_alias or model_alias)
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
            values = _usage_values(record.get("usage"))
            if values is not None:
                state.last_stats = UsageStats(
                    used=sum(values), size=size, cost=None, source="transcript")
    return state.last_stats


class UsagePoller:
    """provider 私有的只读采样器；目录子进程只能由 worker 显式准备。"""

    def __init__(
        self,
        *,
        context_sizes_source: Callable[[], dict[str, int]],
        session_dir_lookup: Callable[[str], Path | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._context_sizes_source = context_sizes_source
        self._session_dir_lookup = session_dir_lookup or session_dir_of
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionState] = {}
        self._context_sizes: dict[str, int] = {}
        self._context_sizes_loaded = False
        self._context_catalog_attempts = 0

    @property
    def context_sizes(self) -> dict[str, int]:
        with self._lock:
            return dict(self._context_sizes)

    def ensure_context_sizes(self) -> None:
        with self._lock:
            if self._context_sizes_loaded:
                return
        try:
            sizes = self._context_sizes_source()
        except Exception as exc:  # noqa: BLE001 - 目录失败只影响徽章
            logger.warning("kimi 模型上下文目录加载失败 category=%s", type(exc).__name__)
            sizes = {}
        if not isinstance(sizes, dict):
            sizes = {}
        with self._lock:
            if self._context_sizes_loaded:
                return
            self._context_catalog_attempts += 1
            self._context_sizes = dict(sizes)
            self._context_sizes_loaded = bool(sizes)
            if not sizes:
                logger.warning(
                    "kimi 模型上下文目录为空，下轮重试 attempt=%s",
                    self._context_catalog_attempts)

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()

    def invalidate_model(self) -> None:
        with self._lock:
            for state in self._sessions.values():
                state.sampled_at = None
                state.cursor = None

    def _session_dir(self, session_id: str, now: float) -> Path | None:
        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionState())
            if state.directory is not None or now < state.directory_retry_at:
                return state.directory
        try:
            directory = self._session_dir_lookup(session_id)
        except Exception as exc:  # noqa: BLE001 - 私有索引失败不得影响聊天
            logger.debug("kimi 会话目录定位异常 category=%s", type(exc).__name__)
            directory = None
        with self._lock:
            if self._sessions.get(session_id) is not state:
                return None
            if directory is not None:
                state.directory = directory
                state.directory_failures = 0
                state.directory_retry_at = 0.0
                return directory
            state.directory_failures += 1
            delay = SESSION_DIR_RETRY_DELAYS_S[
                min(state.directory_failures - 1,
                    len(SESSION_DIR_RETRY_DELAYS_S) - 1)]
            state.directory_retry_at = now + delay
            logger.warning(
                "kimi 会话目录暂未定位，退避后重试 attempt=%s delay=%.0fs",
                state.directory_failures, delay)
            return None

    def poll(self, session_id: str, model_alias: str | None) -> UsageStats | None:
        """每会话最多四秒读盘一次；接口没有连接、锁或帧回调参数。"""
        try:
            now = self._clock()
            with self._lock:
                state = self._sessions.setdefault(session_id, _SessionState())
                cursor = state.cursor
                if (state.sampled_at is not None
                        and 0 <= now - state.sampled_at < POLL_INTERVAL_S):
                    return cursor.last_stats if cursor is not None else None
                state.sampled_at = now
                sizes = dict(self._context_sizes)

            directory = self._session_dir(session_id, now)
            with self._lock:
                if self._sessions.get(session_id) is not state:
                    return None
                if directory is None:
                    return state.cursor.last_stats if state.cursor is not None else None
                if state.cursor is None or state.cursor.path != directory:
                    state.cursor = WireUsageCursor(directory)
                return read_usage_incremental(state.cursor, sizes, model_alias)
        except Exception as exc:  # noqa: BLE001 - GUI 轮询必须 fail-soft
            logger.debug("kimi 轮内用量采样失败 category=%s", type(exc).__name__)
            return None
