"""Kimi 上下文用量：ACP 轮末推送与 wire 兜底的回归测试。"""
import json
import queue

import pytest

from ide.llm.providers import kimi_acp
from ide.llm.providers.kimi_acp import KimiAcpLLM, _read_wire_turn_stats

ALIAS = "kimi-code/k3"
CONTEXT_SIZES = {ALIAS: 262144}


class _FakeConn:
    def __init__(self, messages):
        self.messages = queue.Queue()
        for message in messages:
            self.messages.put(message)
        self.calls = 0
        self.purge_calls = 0
        self.begin_calls = 0
        self.end_calls = 0

    def next_update(self, timeout=None):
        self.calls += 1
        return self.messages.get(timeout=timeout)

    def purge_updates(self):
        self.purge_calls += 1
        while True:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                return

    def begin_turn(self, _method, _params):
        self.begin_calls += 1
        self.messages.put(_response())

    def end_turn(self):
        self.end_calls += 1

    def terminate(self):
        pass


def _response():
    return "response", {"result": {"stopReason": "end_turn"}}


def _update(update):
    return "update", {"params": {"sessionId": "session_x", "update": update}}


def _usage_update(used=999, size=262144):
    return _update({"sessionUpdate": "usage_update", "used": used, "size": size})


def _text_update(text="tail"):
    return _update({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    })


def _request(t=1000, turn_step="0.0", alias=ALIAS):
    return {
        "type": "llm.request",
        "time": t,
        "turnStep": turn_step,
        "modelAlias": alias,
        "maxTokens": 1,
    }


def _record(t=6000, output=40):
    return {
        "type": "usage.record",
        "usageScope": "turn",
        "usage": {
            "inputOther": 100,
            "inputCacheRead": 200,
            "inputCacheCreation": 30,
            "output": output,
        },
        "time": t,
    }


def _measured(tokens):
    return {"type": "token_counting.measured", "tokens": tokens}


def _write_wire(tmp_path, records, session_id="session_x"):
    session_dir = tmp_path / session_id
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return session_dir


def _run_turn(monkeypatch, messages, session_dir=None, sidecar_enabled=False):
    llm = KimiAcpLLM(model=ALIAS)
    llm._sidecar_enabled = sidecar_enabled
    llm._context_sizes = dict(CONTEXT_SIZES)
    if session_dir is not None:
        llm._session_id = "session_x"
        monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: session_dir)
        read_stats = kimi_acp._read_wire_turn_stats
        baseline = True

        def empty_baseline_then_real(path, *args, **kwargs):
            nonlocal baseline
            if baseline:
                baseline = False
                return 0, 0, None
            return read_stats(path, *args, **kwargs)

        monkeypatch.setattr(kimi_acp, "_read_wire_turn_stats", empty_baseline_then_real)
        turn_metadata = [
            {"type": "turn.prompt", "origin": {"kind": "user"},
             "promptId": "msg_usage", "turnId": 0, "trigger": "normal"},
            {"type": "context.append_loop_event", "event": {
                "type": "step.end", "turnId": "0", "finishReason": "end_turn"}},
            {"type": "turn.ended", "turnId": 0, "reason": "completed"},
            {"type": "prompt.completed", "promptId": "msg_usage",
             "reason": "completed"},
        ]
        with (session_dir / "agents" / "main" / "wire.jsonl").open(
                "a", encoding="utf-8") as stream:
            for record in turn_metadata:
                stream.write(json.dumps(record) + "\n")
    conn = _FakeConn(messages)
    try:
        chunks = list(llm._iter_turn_chunks(conn))
    finally:
        llm.close()
    return chunks, conn


def test_late_acp_usage_update_is_consumed(monkeypatch):
    chunks, conn = _run_turn(monkeypatch, [_response(), _usage_update()])
    usage = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert len(usage) == 1
    assert usage[0].usage is not None
    assert usage[0].usage.used == 999
    assert usage[0].usage.size == 262144
    assert usage[0].usage.source == "push"
    assert conn.calls == 2


def test_acp_takes_priority_over_wire(monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _record()])
    chunks, _ = _run_turn(
        monkeypatch, [_response(), _usage_update()], session_dir)
    usage = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert len(usage) == 1
    assert usage[0].usage is not None
    assert usage[0].usage.used == 999
    assert usage[0].usage.source == "push"
    assert usage[0].speed is not None
    assert usage[0].speed.output_tokens == 40


def test_wire_fallback_and_four_field_total(monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0)
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_POLL_S", 0)
    session_dir = _write_wire(tmp_path, [_request(), _record()])
    chunks, _ = _run_turn(monkeypatch, [_response()], session_dir)
    usage = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert len(usage) == 1
    assert usage[0].usage is not None
    assert usage[0].usage.used == 370
    assert usage[0].usage.size == 262144
    assert usage[0].usage.source == "transcript"


def test_partial_wire_survives_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0)
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_POLL_S", 0)
    session_dir = _write_wire(tmp_path, [
        _request(1000, "0.0"),
        _record(6000),
        _request(7000, "0.1"),
    ])
    chunks, _ = _run_turn(monkeypatch, [_response()], session_dir)
    usage = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert len(usage) == 1
    assert usage[0].usage is not None
    assert usage[0].usage.used == 370


def test_no_usage_data_stays_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0)
    session_dir = _write_wire(tmp_path, [])
    chunks, _ = _run_turn(monkeypatch, [_response()], session_dir)
    assert chunks == []


def test_no_new_wire_usage_survives_timeout_as_none(monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0)
    session_dir = _write_wire(tmp_path, [_request(), _record()])
    stats = KimiAcpLLM._poll_wire_usage(
        session_dir, 1, 1, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert stats is None


def test_late_non_usage_updates_are_preserved(monkeypatch):
    chunks, _ = _run_turn(
        monkeypatch, [_response(), _text_update(), _usage_update()])
    assert [chunk.kind for chunk in chunks] == ["text", "usage"]
    assert chunks[0].text == "tail"


def test_sidecar_tail_is_drained_before_final_usage(monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _record()])

    class _FakeSidecar:
        def __init__(self, _session_dir, out):
            out.put(_text_update("sidecar tail")[1]["params"]["update"])

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(kimi_acp, "_WireSidecar", _FakeSidecar)
    monkeypatch.setattr(kimi_acp, "_SIDECAR_POLL_S", 0)
    chunks, _ = _run_turn(
        monkeypatch, [_response(), _usage_update()], session_dir,
        sidecar_enabled=True)
    assert [chunk.kind for chunk in chunks] == ["text", "usage"]
    assert chunks[0].text == "sidecar tail"


def test_turn_stats_uses_catalog_size_not_max_tokens(tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _record()])
    parsed = _read_wire_turn_stats(
        session_dir, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert parsed is not None
    assert parsed[:2] == (1, 1)
    assert parsed[2] is not None
    assert parsed[2].used == 370
    assert parsed[2].size == 262144


def test_turn_stats_counts_only_turn_requests(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(),
        {"type": "llm.request", "time": 2000, "maxTokens": 999999},
        _record(),
    ])
    parsed = _read_wire_turn_stats(
        session_dir, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert parsed is not None
    assert parsed[:2] == (1, 1)


def test_incremental_read_ignores_non_turn_request_and_max_tokens(
        monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(),
        _record(),
        {"type": "llm.request", "time": 7000, "maxTokens": 999999},
    ])
    llm = _polling_llm(monkeypatch, session_dir)
    try:
        stats = llm.poll_usage()
        assert stats is not None
        assert stats.used == 370
        assert stats.size == 262144
    finally:
        llm.close()


@pytest.mark.parametrize("missing", [
    "inputOther", "inputCacheRead", "inputCacheCreation", "output",
])
def test_wire_usage_requires_all_four_fields(tmp_path, missing):
    record = _record()
    del record["usage"][missing]
    session_dir = _write_wire(tmp_path, [_request(), record])
    parsed = _read_wire_turn_stats(
        session_dir, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert parsed is not None
    assert parsed[2] is None


@pytest.mark.parametrize("field,value", [
    ("inputOther", -1),
    ("inputCacheRead", "200"),
    ("inputCacheCreation", 1.5),
    ("output", True),
])
def test_wire_usage_rejects_invalid_field_values(tmp_path, field, value):
    record = _record()
    record["usage"][field] = value
    session_dir = _write_wire(tmp_path, [_request(), record])
    parsed = _read_wire_turn_stats(
        session_dir, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert parsed is not None
    assert parsed[2] is None


# ----------------------------------------------------------------------
# 轮次真实消耗（2026-1114-1452 计划：TurnConsumption 随轮末 usage chunk）
# ----------------------------------------------------------------------
def test_turn_end_chunk_carries_consumption(monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _record()])
    chunks, _ = _run_turn(
        monkeypatch, [_response(), _usage_update()], session_dir)
    usage = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert len(usage) == 1
    consumption = usage[0].consumption
    assert consumption is not None
    assert (consumption.input_other, consumption.input_cache_read,
            consumption.input_cache_creation, consumption.output) == (100, 200, 30, 40)
    assert consumption.turn_id == "session_x:1"


def test_consumption_sums_multiple_calls(monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(1000, "0.0"), _record(6000),
        _request(7000, "0.1"), _record(9000),
    ])
    chunks, _ = _run_turn(monkeypatch, [_response(), _usage_update()], session_dir)
    consumption = [c for c in chunks if c.kind == "usage"][0].consumption
    assert consumption is not None
    assert consumption.input_other == 200
    assert consumption.output == 80
    assert consumption.turn_id == "session_x:2"


def test_consumption_survives_speed_pairing_failure(monkeypatch, tmp_path):
    # 用量时间戳早于请求（速度配对失败）不影响消耗求和
    session_dir = _write_wire(tmp_path, [_request(1000), _record(500)])
    chunks, _ = _run_turn(monkeypatch, [_response(), _usage_update()], session_dir)
    usage = [chunk for chunk in chunks if chunk.kind == "usage"][0]
    assert usage.speed is None
    assert usage.consumption is not None
    assert usage.consumption.output == 40


# ----------------------------------------------------------------------
# 轮内增量采样、可恢复缓存与轮后迟到 push
# ----------------------------------------------------------------------
def _polling_llm(monkeypatch, session_dir, session_id="session_x"):
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = session_id
    llm._context_sizes = dict(CONTEXT_SIZES)
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: session_dir)
    return llm


def test_session_directory_failure_retries_then_success_is_cached(
        monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _measured(123)])
    lookups = iter([None, session_dir])
    calls = 0

    def lookup(_sid):
        nonlocal calls
        calls += 1
        return next(lookups, session_dir)

    clock = [0.0]
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lookup)
    monkeypatch.setattr(kimi_acp.time, "monotonic", lambda: clock[0])
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = "session_x"
    llm._context_sizes = dict(CONTEXT_SIZES)
    try:
        assert llm.poll_usage() is None
        assert llm.poll_usage() is None
        assert calls == 1
        clock[0] = 2.1
        stats = llm.poll_usage()
        assert stats is not None and stats.used == 123
        assert llm.poll_usage() is not None
        assert calls == 2
    finally:
        llm.close()


def test_session_directory_retry_backoff_is_bounded(monkeypatch):
    calls = 0

    def lookup(_sid):
        nonlocal calls
        calls += 1
        return None

    clock = [0.0]
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lookup)
    monkeypatch.setattr(kimi_acp.time, "monotonic", lambda: clock[0])
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = "session_x"
    try:
        for tick, expected_calls in (
            (0, 1), (1.9, 1), (2, 2), (6.9, 2), (7, 3),
            (21.9, 3), (22, 4), (36.9, 4), (37, 5),
        ):
            clock[0] = tick
            assert llm.poll_usage() is None
            assert calls == expected_calls
    finally:
        llm.close()


def test_empty_context_catalog_is_retried_and_gui_poll_never_loads_it(
        monkeypatch, tmp_path):
    catalogs = iter([{}, {"models": {ALIAS: {"maxContextSize": 262144}}}])
    calls = 0

    def load_catalog():
        nonlocal calls
        calls += 1
        return next(catalogs)

    monkeypatch.setattr(kimi_acp, "load_kimi_provider_catalog", load_catalog)
    llm = KimiAcpLLM(model=ALIAS)
    try:
        llm._ensure_context_sizes()
        assert not llm._context_sizes_loaded
        llm._ensure_context_sizes()
        assert llm._context_sizes_loaded
        assert llm._context_sizes == CONTEXT_SIZES
        assert calls == 2

        session_dir = _write_wire(tmp_path, [_request(), _measured(123)])
        llm._session_id = "session_x"
        monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: session_dir)
        monkeypatch.setattr(
            kimi_acp, "load_kimi_provider_catalog",
            lambda: pytest.fail("GUI 轮询不得启动模型目录加载"))
        assert llm.poll_usage().used == 123
        assert calls == 2
    finally:
        llm.close()


def test_incremental_poll_prefers_measured_and_keeps_record_fallback(
        monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _measured(456)])
    llm = _polling_llm(monkeypatch, session_dir)
    try:
        stats = llm.poll_usage()
        assert stats is not None
        assert (stats.used, stats.size, stats.source) == (456, 262144, "transcript")
    finally:
        llm.close()

    fallback_dir = _write_wire(tmp_path, [_request(), _record()], "fallback")
    llm = _polling_llm(monkeypatch, fallback_dir, "fallback")
    try:
        stats = llm.poll_usage()
        assert stats is not None and stats.used == 370
    finally:
        llm.close()


def test_incremental_poll_survives_large_tool_output_and_new_measurement(
        monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _measured(100)])
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    llm = _polling_llm(monkeypatch, session_dir)
    try:
        assert llm.poll_usage().used == 100
        with wire.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "tool.result", "output": "x" * 70000}) + "\n")
        assert llm.poll_usage().used == 100
        with wire.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_measured(200)) + "\n")
        assert llm.poll_usage().used == 200
    finally:
        llm.close()


def test_incremental_poll_handles_partial_line_and_unchanged_file(
        monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _measured(100)])
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    llm = _polling_llm(monkeypatch, session_dir)
    real_loads = kimi_acp.json.loads
    parse_calls = 0

    def counting_loads(value):
        nonlocal parse_calls
        parse_calls += 1
        return real_loads(value)

    monkeypatch.setattr(kimi_acp.json, "loads", counting_loads)
    try:
        assert llm.poll_usage().used == 100
        assert parse_calls == 2
        assert llm.poll_usage().used == 100
        assert parse_calls == 2
        with wire.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_measured(200)))
        assert llm.poll_usage().used == 100
        with wire.open("a", encoding="utf-8") as f:
            f.write("\n")
        assert llm.poll_usage().used == 200
    finally:
        llm.close()


def test_incremental_poll_resets_on_truncation_and_isolates_sessions(
        monkeypatch, tmp_path):
    first = _write_wire(tmp_path, [_request(), _measured(100)])
    second = _write_wire(tmp_path, [_request(), _measured(200)], "session_y")
    dirs = {"session_x": first, "session_y": second}
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda sid: dirs[sid])
    llm = KimiAcpLLM(model=ALIAS)
    llm._context_sizes = dict(CONTEXT_SIZES)
    try:
        llm._session_id = "session_x"
        assert llm.poll_usage().used == 100
        llm._session_id = "session_y"
        assert llm.poll_usage().used == 200
        llm._session_id = "session_x"
        wire = first / "agents" / "main" / "wire.jsonl"
        wire.write_text("", encoding="utf-8")
        assert llm.poll_usage() is None
        wire.write_text(
            json.dumps(_request()) + "\n" + json.dumps(_measured(300)) + "\n",
            encoding="utf-8")
        assert llm.poll_usage().used == 300
    finally:
        llm.close()


@pytest.mark.parametrize("tokens", [-1, True, 1.5, "100"])
def test_measured_rejects_invalid_token_counts(monkeypatch, tmp_path, tokens):
    session_dir = _write_wire(tmp_path, [_request(), _measured(tokens)])
    llm = _polling_llm(monkeypatch, session_dir)
    try:
        assert llm.poll_usage() is None
    finally:
        llm.close()


def test_idle_poll_recovers_late_push_and_keeps_it_final(monkeypatch, tmp_path):
    session_dir = _write_wire(tmp_path, [_request(), _measured(500)])
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = "session_x"
    llm._context_sizes = dict(CONTEXT_SIZES)
    conn = _FakeConn([_text_update("stale"), _usage_update(1200)])
    llm._conn = conn
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: session_dir)
    try:
        stats = llm.poll_usage()
        assert stats is not None
        assert (stats.used, stats.source) == (1200, "push")
        assert conn.messages.empty()
        assert llm.poll_usage().source == "push"
        assert llm.poll_usage().used == 1200
    finally:
        llm.close()


def test_active_turn_poll_never_reads_acp_queue(monkeypatch):
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = "session_x"
    conn = _FakeConn([_usage_update()])
    llm._conn = conn
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: None)
    llm._turn_lock.acquire()
    try:
        assert llm.poll_usage() is None
        assert conn.calls == 0
        assert not conn.messages.empty()
    finally:
        llm._turn_lock.release()
        llm.close()


def test_next_turn_recovers_usage_before_purge(monkeypatch):
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0)
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda _sid: None)
    llm = KimiAcpLLM(model=ALIAS)
    llm._session_id = "session_x"
    llm._context_sizes = dict(CONTEXT_SIZES)
    conn = _FakeConn([_text_update("stale"), _usage_update(1300)])
    monkeypatch.setattr(llm, "_ensure_session", lambda: conn)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "next"}]))
        usage = [chunk for chunk in chunks if chunk.kind == "usage"]
        assert len(usage) == 1
        assert usage[0].usage is not None
        assert (usage[0].usage.used, usage[0].usage.source) == (1300, "push")
        assert all(chunk.kind != "text" for chunk in chunks)
        assert conn.purge_calls == conn.begin_calls == conn.end_calls == 1
    finally:
        llm.close()
