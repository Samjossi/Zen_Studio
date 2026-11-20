"""Kimi 私有 wire 与用量模块单测：文件容错、增量游标、轮末配对与并发。

临时目录统一落在项目 `.temp/` 下（AGENTS 约束），用后清理。
"""
import json
import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from ide.llm.base import Chunk, UsageStats
from ide.llm.providers.acp import TurnAborted
from ide.llm.providers.kimi import usage as kimi_usage, wire as kimi_wire


@pytest.fixture
def wire_dir():
    base = Path(".temp") / f"s3_wire_{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def request(*, model="k3", at=1000, turn=True, **extra):
    record = {"type": "llm.request", "time": at, "modelAlias": model}
    if turn:
        record["turnStep"] = "step"
    record.update(extra)
    return record


def usage(*, other=1, cache_read=2, cache_creation=3, output=4, at=1500, scope="turn"):
    return {
        "type": "usage.record", "usageScope": scope, "time": at,
        "usage": {
            "inputOther": other, "inputCacheRead": cache_read,
            "inputCacheCreation": cache_creation, "output": output,
        },
    }


def measured(tokens, *, at=1500, model="k3"):
    return {"type": "token_counting.measured", "tokens": tokens,
            "time": at, "modelAlias": model}


def write_records(session_dir: Path, records, *, append=False):
    path = kimi_wire.wire_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
             for r in records]
    text = "\n".join(lines) + "\n"
    with path.open("a" if append else "w", encoding="utf-8") as f:
        f.write(text)
    return path


class FakeConn:
    """最小 ACP 连接桩：按序发帧；TurnAborted 作为异常帧抛出。"""

    def __init__(self, *events):
        self._queue = queue.Queue()
        for event in events:
            self._queue.put(event)
        self._turn_abort = threading.Event()

    def next_update(self, timeout=None):
        if timeout == 0:
            item = self._queue.get_nowait()
        else:
            item = self._queue.get(timeout=timeout)
        if isinstance(item, BaseException):
            raise item
        return item


def map_usage(obj):
    return Chunk("usage", "", usage=UsageStats(
        used=obj["used"], size=100, cost=None, source="push"))


# ----------------------------------------------------------------------
# wire：会话索引与全量读取
# ----------------------------------------------------------------------
def test_session_dir_of_last_match_and_missing(wire_dir, monkeypatch):
    index = wire_dir / "session_index.jsonl"
    monkeypatch.setattr(kimi_wire, "session_index_path", lambda: index)
    index.write_text("\n".join([
        json.dumps({"sessionId": "s1", "sessionDir": "/old"}),
        json.dumps({"sessionId": "s1", "sessionDir": "/new"}),
        json.dumps({"sessionId": "s2", "sessionDir": "/other"}),
    ]) + "\n", encoding="utf-8")

    assert kimi_wire.session_dir_of("s1") == Path("/new")
    assert kimi_wire.session_dir_of("missing") is None


def test_session_dir_of_corrupt_index_degrades(wire_dir, monkeypatch):
    index = wire_dir / "session_index.jsonl"
    monkeypatch.setattr(kimi_wire, "session_index_path", lambda: index)
    index.write_text('{"sessionId": "s1", "sessionDir": "/ok"}\nnot-json\n',
                     encoding="utf-8")

    assert kimi_wire.session_dir_of("s1") is None


def test_read_records_missing_empty_blank_and_corrupt(wire_dir):
    assert kimi_wire.read_records(wire_dir) is None

    path = kimi_wire.wire_path(wire_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert kimi_wire.read_records(wire_dir) == []

    path.write_text('\n  \n{"type": "llm.request"}\n\n', encoding="utf-8")
    assert kimi_wire.read_records(wire_dir) == [{"type": "llm.request"}]

    path.write_text('{"type": "llm.request"', encoding="utf-8")
    assert kimi_wire.read_records(wire_dir) is None

    path.write_text('{"type": "ok"}\nnot-json\n', encoding="utf-8")
    assert kimi_wire.read_records(wire_dir) is None


def test_read_turn_stats_counts_and_last_usage(wire_dir):
    write_records(wire_dir, [
        request(at=1000),
        usage(other=1, cache_read=2, cache_creation=3, output=4, at=1500),
        {"type": "usage.record", "usageScope": "session", "usage": {}},
        request(at=2000, turn=False),
        request(at=3000, model="k3-256k"),
        usage(other=5, cache_read=6, cache_creation=7, output=8, at=3500),
    ])

    parsed = kimi_wire.read_turn_stats(
        wire_dir, {"k3": 1000, "k3-256k": 2000}, "fallback")

    assert parsed is not None
    usage_count, request_count, stats = parsed
    assert (usage_count, request_count) == (2, 2)
    assert stats == UsageStats(used=26, size=2000, cost=None, source="transcript")


def test_read_turn_stats_without_usage_is_none_stats(wire_dir):
    write_records(wire_dir, [request()])
    assert kimi_wire.read_turn_stats(wire_dir) == (0, 1, None)


def test_read_turn_stats_missing_file_is_none(wire_dir):
    assert kimi_wire.read_turn_stats(wire_dir) is None


# ----------------------------------------------------------------------
# wire：轮末速度与消耗
# ----------------------------------------------------------------------
def test_read_turn_metrics_pairs_requests_and_sums(wire_dir):
    write_records(wire_dir, [
        request(at=1000),
        usage(other=1, cache_read=2, cache_creation=3, output=10, at=1500),
        request(at=2000, turn=False),  # 非 turn 请求不得改变配对
        usage(other=1, cache_read=2, cache_creation=3, output=20, at=2600),
    ])

    speed, consumption = kimi_wire.read_turn_metrics(wire_dir, 0, "sid")

    # 第二次用量仍与最后一次 turn 请求（1000）配对：500 + 1600 ms
    assert speed is not None
    assert (speed.output_tokens, speed.gen_seconds) == (30, 2.1)
    assert consumption is not None
    assert (consumption.input_other, consumption.input_cache_read,
            consumption.input_cache_creation, consumption.output) == (2, 4, 6, 30)
    assert consumption.turn_id == "sid:2"


def test_read_turn_metrics_baseline_skips_prior_usage(wire_dir):
    write_records(wire_dir, [
        request(at=1000),
        usage(output=10, at=1500),
        usage(output=20, at=2600),
    ])

    speed, consumption = kimi_wire.read_turn_metrics(wire_dir, 1, "sid")

    assert speed is not None and speed.output_tokens == 20
    assert consumption is not None and consumption.output == 20
    assert consumption.turn_id == "sid:2"

    assert kimi_wire.read_turn_metrics(wire_dir, 2, "sid") == (None, None)


def test_read_turn_metrics_missing_file_and_bad_fields(wire_dir):
    assert kimi_wire.read_turn_metrics(wire_dir, 0, "sid") == (None, None)

    write_records(wire_dir, [
        request(at=1000),
        usage(other=-1, at=1500),
        usage(other=1, cache_read=2, cache_creation=3, output=4, at=1600),
    ])
    speed, consumption = kimi_wire.read_turn_metrics(wire_dir, 0, "sid")

    # 坏用量不计入四项求和，但单次 output 独立有效，仍参与速度配对
    assert speed is not None and speed.output_tokens == 8
    assert consumption is not None and consumption.total == 10


# ----------------------------------------------------------------------
# wire：增量游标
# ----------------------------------------------------------------------
def test_read_usage_incremental_appends_and_preserves_watermark(wire_dir):
    write_records(wire_dir, [request(), measured(500)])
    cursor = kimi_wire.WireUsageCursor(wire_dir)

    first = kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3")
    assert first is not None and first.used == 500
    assert kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3") is first

    write_records(wire_dir, [usage(output=9)], append=True)
    assert kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3").used == 500

    write_records(wire_dir, [measured(777)], append=True)
    assert kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3").used == 777


def test_read_usage_incremental_truncation_resets_state(wire_dir):
    # 先写长文件，再重写为更短文件，才能触发「游标越过文件尾」回退
    write_records(wire_dir, [request(pad="x" * 300), measured(500)])
    cursor = kimi_wire.WireUsageCursor(wire_dir)
    assert kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3").used == 500
    assert cursor.measured_seen

    write_records(wire_dir, [request(), usage(other=1, output=9, at=1600)])
    stats = kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3")

    assert stats is not None and stats.used == 15
    assert not cursor.measured_seen


def test_read_usage_incremental_skips_bad_lines_and_tail_partial(wire_dir, monkeypatch):
    write_records(wire_dir, [
        request(),
        '{"type": "usage.record", "usageScope": "turn", "bad": ',
        usage(output=9),
    ])
    cursor = kimi_wire.WireUsageCursor(wire_dir)
    assert kimi_wire.read_usage_incremental(cursor, {"k3": 1000}, "k3").used == 15

    # 首读只取尾部 100B：首行被拦腰截断应丢弃，完整 measured 行仍生效
    tail_dir = wire_dir / "tail"
    write_records(tail_dir, [
        request(pad="x" * 300),
        measured(777),
    ])
    monkeypatch.setattr(kimi_wire, "WIRE_TAIL_BYTES", 100)
    tail_cursor = kimi_wire.WireUsageCursor(tail_dir)
    stats = kimi_wire.read_usage_incremental(tail_cursor, {"k3": 1000}, "k3")
    assert stats is not None and stats.used == 777


def test_read_usage_incremental_missing_file_returns_last(wire_dir):
    cursor = kimi_wire.WireUsageCursor(wire_dir)
    assert kimi_wire.read_usage_incremental(cursor) is None


# ----------------------------------------------------------------------
# usage tracker：目录退避、窗口目录与轮询
# ----------------------------------------------------------------------
def test_usage_tracker_session_dir_backoff_and_cache(wire_dir):
    clock = [0.0]
    calls = []

    def lookup(session_id):
        calls.append(session_id)
        return wire_dir if len(calls) >= 3 else None

    tracker = kimi_usage.UsageTracker(
        context_sizes_source=lambda: {},
        session_dir_lookup=lookup, clock=lambda: clock[0])

    assert tracker.session_dir("sid") is None
    clock[0] = 1.0
    assert tracker.session_dir("sid") is None
    assert len(calls) == 1  # 退避中不重复读索引

    clock[0] = 2.0
    assert tracker.session_dir("sid") is None
    assert len(calls) == 2

    clock[0] = 7.0
    assert tracker.session_dir("sid") == wire_dir
    assert tracker.session_dir("sid") == wire_dir
    assert len(calls) == 3  # 成功后缓存

    tracker.reset()
    clock[0] = 8.0
    assert tracker.session_dir("sid") == wire_dir
    assert len(calls) == 4


def test_usage_tracker_context_sizes_retry_until_nonempty(caplog):
    catalog = [{}, {"k3": 1000}]
    calls = []

    def source():
        calls.append(1)
        return catalog.pop(0)

    tracker = kimi_usage.UsageTracker(context_sizes_source=source)

    with caplog.at_level(logging.WARNING):
        tracker.ensure_context_sizes()
    assert tracker.context_sizes == {}
    assert len(calls) == 1

    tracker.ensure_context_sizes()
    assert tracker.context_sizes == {"k3": 1000}
    tracker.ensure_context_sizes()
    assert len(calls) == 2


def test_tracker_poll_reads_incremental_wire(wire_dir):
    write_records(wire_dir, [request(), usage(output=9)])
    tracker = kimi_usage.UsageTracker(
        session_dir_lookup=lambda sid: wire_dir,
        context_sizes_source=lambda: {"k3": 1000})
    tracker.ensure_context_sizes()

    stats = tracker.poll("sid", "k3", None, map_usage, threading.Lock())
    assert stats is not None and stats.used == 15
    # 重复轮询幂等：文件未增长时返回同一水位
    assert tracker.poll("sid", "k3", None, map_usage, threading.Lock()) == stats

    tracker.reset()
    assert tracker.poll("sid", "k3", None, map_usage, threading.Lock()).used == 15


def test_tracker_poll_does_not_steal_frames_while_turn_lock_held():
    conn = FakeConn(TurnAborted(), ("update", {"used": 7}))
    conn._turn_abort.set()
    tracker = kimi_usage.UsageTracker(
        context_sizes_source=lambda: {}, session_dir_lookup=lambda sid: None)
    lock = threading.Lock()
    lock.acquire()
    try:
        assert tracker.poll("sid", None, conn, map_usage, lock) is None
        assert conn._queue.qsize() == 2  # abort + usage 均未被抢
    finally:
        lock.release()

    stats = tracker.poll("sid", None, conn, map_usage, lock)
    assert stats is not None and stats.used == 7
    # 迟到的 abort 被清位，不阻塞下一轮
    assert not conn._turn_abort.is_set()
    # 缓存使空闲轮询在无新帧时仍返回上次 push
    assert tracker.poll("sid", None, None, map_usage, lock) == stats


# ----------------------------------------------------------------------
# usage：轮末基线与汇总
# ----------------------------------------------------------------------
def _turn_tracker(wire_dir):
    tracker = kimi_usage.UsageTracker(
        session_dir_lookup=lambda sid: wire_dir,
        context_sizes_source=lambda: {"k3": 1000})
    tracker.ensure_context_sizes()
    return tracker


def test_turn_usage_collects_baseline_pairing_and_push_priority(wire_dir):
    write_records(wire_dir, [
        request(at=1000),
        usage(other=1, output=1, at=1500),
        request(at=2000),
        usage(other=1, output=2, at=2400),
        request(at=3000),
        usage(other=1, cache_read=2, cache_creation=3, output=10, at=3400),
    ])
    tracker = _turn_tracker(wire_dir)
    turn = tracker.begin_turn(wire_dir, "k3", "sid")
    write_records(wire_dir, [
        request(at=4000),
        usage(other=1, cache_read=2, cache_creation=3, output=10, at=4400),
    ], append=True)

    assert (turn.usage_baseline, turn.request_baseline) == (3, 3)
    turn.poll_wire()
    assert not turn.ready(time.monotonic(), 0.0)  # 没有 ACP push 不提前收尾

    push = UsageStats(used=42, size=1000, cost=None, source="push")
    turn.accept_push(push)
    turn.start_collection()
    assert turn.ready(time.monotonic(), 0.0)

    result = turn.finish()

    assert result.stats is push  # ACP 优先于 wire
    assert result.speed is not None
    assert (result.speed.output_tokens, result.speed.gen_seconds) == (10, 0.4)
    assert result.consumption is not None
    assert (result.consumption.input_other, result.consumption.input_cache_read,
            result.consumption.input_cache_creation, result.consumption.output) == (1, 2, 3, 10)
    assert result.consumption.turn_id == "sid:4"
    assert tracker.poll("sid", "k3", None, map_usage, threading.Lock()) is push


def test_turn_usage_wire_only_and_missing_degrades_once(wire_dir, caplog):
    write_records(wire_dir, [request()])
    tracker = _turn_tracker(wire_dir)
    turn = tracker.begin_turn(wire_dir, "k3", "sid")

    turn.start_collection()
    with caplog.at_level(logging.WARNING):
        result = turn.finish()
    assert result.stats is None and result.speed is None and result.consumption is None
    warnings = [r for r in caplog.records if "轮末上下文用量不可用" in r.getMessage()]
    assert len(warnings) == 1
    assert "wire 无本轮有效用量" in warnings[0].getMessage()

    with caplog.at_level(logging.WARNING):
        again = turn.finish()
    assert again == result
    assert len([r for r in caplog.records if "轮末上下文用量不可用" in r.getMessage()]) == 1


def test_turn_usage_wire_stats_without_push(wire_dir):
    write_records(wire_dir, [request()])
    tracker = _turn_tracker(wire_dir)
    turn = tracker.begin_turn(wire_dir, "k3", "sid")
    write_records(wire_dir, [usage(output=9)], append=True)

    turn.start_collection()
    turn.poll_wire()
    result = turn.finish()

    assert result.stats is not None and result.stats.used == 15
    assert not turn.ready(time.monotonic(), 0.0)


# ----------------------------------------------------------------------
# 并发 smoke：worker 追加 wire，GUI 线程持续轮询
# ----------------------------------------------------------------------
def test_gui_poll_concurrent_with_worker_writes(wire_dir):
    write_records(wire_dir, [request()])
    tracker = _turn_tracker(wire_dir)
    stop = threading.Event()
    errors = []

    def gui_loop():
        try:
            while not stop.is_set():
                stats = tracker.poll(
                    "sid", "k3", None, map_usage, threading.Lock())
                if stats is not None:
                    assert stats.size == 1000
        except Exception as exc:  # noqa: BLE001 - 线程内异常须带回主线程断言
            errors.append(exc)

    thread = threading.Thread(target=gui_loop)
    thread.start()
    try:
        for i in range(100):
            write_records(
                wire_dir,
                [usage(other=1, cache_read=2, cache_creation=3, output=i,
                       at=1500 + i)],
                append=True)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    final = tracker.poll("sid", "k3", None, map_usage, threading.Lock())
    assert final is not None and final.used == 1 + 2 + 3 + 99
