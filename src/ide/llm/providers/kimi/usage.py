"""Kimi 用量状态：轮内增量轮询、轮末宽限收集与请求配对。

职责边界：

- `UsageTracker` 按 provider 实例持有会话目录定位、上下文窗口目录、增量
  游标与迟到 push 缓存；模块级无状态，不跨会话/实例共享。
- `TurnUsage` 记录单轮基线（用量条数、请求数）并在轮末汇总速度与真实
  消耗，数值口径与迁移前逐项一致。
- 本模块不解析 ACP 帧：帧到 `Chunk` 的映射由 provider 传入回调，tracker
  只认 `llm.base` 的用量载荷，不反向 import provider。

并发约束：`poll()` 由 GUI 线程调用，chat 在 worker 线程。只有空闲时
（非阻塞拿到轮次锁）才排空 ACP 迟到帧；所有共享状态由实例锁保护，持锁
期间不做子进程调用或等待 ACP。
"""
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ide.llm.base import Chunk, TurnConsumption, TurnSpeed, UsageStats
# 仅用于空闲排空时解封上一轮 abort；不含任何厂商语义。
from ide.llm.providers.acp import TurnAborted
from ide.llm.providers.kimi import wire

logger = logging.getLogger(__name__)

#: wire.jsonl usage.record 异步写盘等待上限（2026-07-31 实测：response 到达
#: 时 llm.request 已写但 usage.record 尚未落盘，约 1~3s 后异步写入）
WIRE_USAGE_WAIT_S = 3.0
WIRE_USAGE_POLL_S = 0.2

#: 会话目录定位失败后的短退避；索引未就绪时避免每次轮询都读全量索引
SESSION_DIR_RETRY_DELAYS_S = (2.0, 5.0, 15.0)

MapUpdate = Callable[[dict], Chunk | None]


@dataclass(frozen=True)
class TurnUsageResult:
    """一轮收尾的用量载荷；三者均可缺，任一存在即需向上游发 usage 帧。"""

    stats: UsageStats | None
    speed: TurnSpeed | None
    consumption: TurnConsumption | None

    @property
    def has_payload(self) -> bool:
        return (self.stats is not None or self.speed is not None
                or self.consumption is not None)


class UsageTracker:
    """一个 provider 实例的用量状态；随会话重置，不跨实例共享。"""

    def __init__(
        self,
        *,
        context_sizes_source: Callable[[], dict[str, int]],
        session_dir_lookup: Callable[[str], Path | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_dir_lookup = session_dir_lookup
        self._context_sizes_source = context_sizes_source
        self._clock = clock
        self._lock = threading.Lock()
        #: 只缓存成功的会话目录定位；失败按短退避重试，避免索引未就绪永久失联
        self._poll_dir_sid: str | None = None
        self._poll_dir: Path | None = None
        self._poll_dir_failures = 0
        self._poll_dir_retry_at = 0.0
        self._cursors: dict[str, wire.WireUsageCursor] = {}
        self._context_sizes: dict[str, int] = {}
        self._context_sizes_loaded = False
        self._context_catalog_attempts = 0
        self._idle_push_stats: UsageStats | None = None
        self._usage_warning_active = False

    # ------------------------------------------------------------------
    # 上下文窗口目录
    # ------------------------------------------------------------------
    @property
    def context_sizes(self) -> dict[str, int]:
        return self._context_sizes

    def ensure_context_sizes(self) -> None:
        if self._context_sizes_loaded:
            return
        self._context_catalog_attempts += 1
        sizes = self._context_sizes_source()
        self._context_sizes = sizes
        self._context_sizes_loaded = bool(sizes)
        if not sizes:
            logger.warning(
                "kimi 模型上下文目录为空，下轮重试 attempt=%s",
                self._context_catalog_attempts)
        elif self._context_catalog_attempts > 1:
            logger.info(
                "kimi 模型上下文目录恢复 attempt=%s", self._context_catalog_attempts)

    def now(self) -> float:
        return self._clock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """会话重置/关闭：清空游标、配对与目录缓存，不保留跨会话数据。"""
        with self._lock:
            self._poll_dir_sid = None
            self._poll_dir = None
            self._poll_dir_failures = 0
            self._poll_dir_retry_at = 0.0
            self._cursors.clear()
            self._idle_push_stats = None

    def invalidate_model(self) -> None:
        """模型别名切换：旧游标的窗口口径失效，丢弃增量缓存与迟到 push。"""
        with self._lock:
            self._cursors.clear()
            self._idle_push_stats = None

    # ------------------------------------------------------------------
    # 轮内轮询
    # ------------------------------------------------------------------
    def session_dir(self, session_id: str) -> Path | None:
        """带失败退避的会话目录定位；成功结果缓存，索引未就绪不打死循环。"""
        now = self.now()
        with self._lock:
            if session_id != self._poll_dir_sid:
                self._poll_dir_sid = session_id
                self._poll_dir = None
                self._poll_dir_failures = 0
                self._poll_dir_retry_at = 0.0
            if self._poll_dir is not None or now < self._poll_dir_retry_at:
                return self._poll_dir
        lookup = self._session_dir_lookup or wire.session_dir_of
        session_dir = lookup(session_id)
        with self._lock:
            if session_id != self._poll_dir_sid:
                return None
            if session_dir is not None:
                recovered = self._poll_dir_failures > 0
                self._poll_dir = session_dir
                self._poll_dir_failures = 0
                self._poll_dir_retry_at = 0.0
                if recovered:
                    logger.info("kimi 会话目录定位恢复")
                return session_dir
            self._poll_dir_failures += 1
            delay = SESSION_DIR_RETRY_DELAYS_S[
                min(self._poll_dir_failures - 1,
                    len(SESSION_DIR_RETRY_DELAYS_S) - 1)]
            self._poll_dir_retry_at = now + delay
            logger.warning(
                "kimi 会话目录暂未定位，退避后重试 attempt=%s delay=%.0fs",
                self._poll_dir_failures, delay)
            return None

    def poll(
        self,
        session_id: str,
        model_alias: str | None,
        conn: object | None,
        map_update: MapUpdate,
        turn_lock: threading.Lock,
    ) -> UsageStats | None:
        """读取增量 wire；仅空闲时回收迟到 ACP usage，避免与 worker 抢帧。"""
        push_stats: UsageStats | None = None
        if turn_lock.acquire(blocking=False):
            try:
                if conn is not None:
                    push_stats = self._drain_idle_updates(conn, map_update)
                with self._lock:
                    if push_stats is not None:
                        self._idle_push_stats = push_stats
                    push_stats = push_stats or self._idle_push_stats
            finally:
                turn_lock.release()
        session_dir = self.session_dir(session_id)
        wire_stats: UsageStats | None = None
        if session_dir is not None:
            with self._lock:
                cursor = self._cursors.get(session_id)
                if cursor is None or cursor.path != session_dir:
                    cursor = wire.WireUsageCursor(session_dir)
                    self._cursors[session_id] = cursor
                wire_stats = wire.read_usage_incremental(
                    cursor, self._context_sizes, model_alias)
        return push_stats or wire_stats

    @staticmethod
    def _drain_idle_updates(conn: object, map_update: MapUpdate) -> UsageStats | None:
        """排空空闲队列，仅保留有效 usage；其他迟到帧维持原清理语义。"""
        latest: UsageStats | None = None
        next_update = getattr(conn, "next_update")
        while True:
            try:
                kind, obj = next_update(timeout=0)
            except queue.Empty:
                return latest
            except TurnAborted:
                # 轮次锁已证明 worker 退出；残留 abort 只用于解封上一轮等待
                getattr(conn, "_turn_abort").clear()
                continue
            if kind != "update":
                continue
            chunk = map_update(obj)
            if chunk is not None and chunk.kind == "usage" \
                    and chunk.usage is not None:
                latest = chunk.usage

    # ------------------------------------------------------------------
    # 轮末收集
    # ------------------------------------------------------------------
    def begin_turn(
        self,
        session_dir: Path | None,
        model_alias: str | None,
        session_id: str | None,
    ) -> "TurnUsage":
        """进入一轮：先读基线，后续只统计基线之后的增量。"""
        return TurnUsage(self, session_dir, model_alias, session_id)

    def note_idle_push(self, stats: UsageStats) -> None:
        with self._lock:
            self._idle_push_stats = stats

    def mark_usage_available(self) -> None:
        self._usage_warning_active = False

    @property
    def usage_warning_active(self) -> bool:
        return self._usage_warning_active

    def mark_usage_warning(self) -> None:
        self._usage_warning_active = True


class TurnUsage:
    """单轮用量收集状态：基线、wire 完成度、ACP push 与轮末汇总。

    宽限截止不在构造时开始，而由 `start_collection()` 在响应到达、进入
    收尾循环时开启；与迁移前「response 到达后最多再等 3s」口径一致。
    """

    def __init__(
        self,
        tracker: UsageTracker,
        session_dir: Path | None,
        model_alias: str | None,
        session_id: str | None,
    ) -> None:
        self._tracker = tracker
        self._session_dir = session_dir
        self._model_alias = model_alias
        self._session_id = session_id
        parsed = (wire.read_turn_stats(
            session_dir, tracker.context_sizes, model_alias)
            if session_dir is not None else None)
        self.usage_baseline = parsed[0] if parsed else 0
        self.request_baseline = parsed[1] if parsed else 0
        self._push_stats: UsageStats | None = None
        self._wire_stats: UsageStats | None = None
        self._wire_complete = session_dir is None
        self._deadline = 0.0

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def poll_interval(self) -> float:
        return WIRE_USAGE_POLL_S

    def start_collection(self) -> None:
        self._deadline = self._tracker.now() + WIRE_USAGE_WAIT_S

    def accept_push(self, stats: UsageStats | None) -> None:
        if stats is not None:
            self._push_stats = stats

    def poll_wire(self) -> None:
        """重读 wire 并更新本轮完成度；全量读失败时保留上一次结论。"""
        if self._session_dir is None:
            self._wire_complete = True
            return
        parsed = wire.read_turn_stats(
            self._session_dir, self._tracker.context_sizes, self._model_alias)
        if parsed is None:
            return
        usage_count, request_count, stats = parsed
        new_usage = usage_count - self.usage_baseline
        new_requests = request_count - self.request_baseline
        if new_usage > 0 and stats is not None:
            self._wire_stats = stats
        self._wire_complete = (new_usage >= new_requests if new_requests > 0
                               else new_usage > 0)

    def ready(self, now: float, sidecar_ready_at: float) -> bool:
        """ACP 已送达、wire 已补齐且旁路首拍已到，才提前结束宽限。"""
        return (self._push_stats is not None
                and self._wire_complete
                and now >= sidecar_ready_at)

    def finish(self) -> TurnUsageResult:
        """汇总速度/消耗与上下文用量；缺失只记一次告警，不改变 ACP 终态。"""
        if self._session_dir is not None:
            speed, consumption = wire.read_turn_metrics(
                self._session_dir, self.usage_baseline, self._session_id)
        else:
            speed, consumption = None, None
        stats = self._push_stats or self._wire_stats
        if self._push_stats is not None:
            self._tracker.note_idle_push(self._push_stats)
        if stats is not None:
            self._tracker.mark_usage_available()
        elif not self._tracker.usage_warning_active:
            reasons = []
            if self._session_dir is None:
                reasons.append("会话目录未定位")
            if not self._tracker.context_sizes:
                reasons.append("模型窗口目录为空")
            reasons.append("ACP 未送达")
            if self._wire_stats is None:
                reasons.append("wire 无本轮有效用量")
            logger.warning("kimi 轮末上下文用量不可用（%s）", "、".join(reasons))
            self._tracker.mark_usage_warning()
        return TurnUsageResult(stats=stats, speed=speed, consumption=consumption)
