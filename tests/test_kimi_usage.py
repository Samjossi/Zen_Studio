"""Kimi 上下文用量：ACP 轮末推送与 wire 兜底的回归测试。"""
import json
import queue

import pytest

from llm.providers import kimi_acp
from llm.providers.kimi_acp import (
    KimiAcpLLM,
    _read_wire_turn_stats,
    _read_wire_usage_tail,
)

ALIAS = "kimi-code/k3"
CONTEXT_SIZES = {ALIAS: 262144}


class _FakeConn:
    def __init__(self, messages):
        self.messages = queue.Queue()
        for message in messages:
            self.messages.put(message)
        self.calls = 0

    def next_update(self, timeout=None):
        self.calls += 1
        return self.messages.get(timeout=timeout)


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


def _write_wire(tmp_path, records):
    session_dir = tmp_path / "session_x"
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


def test_tail_ignores_non_turn_request_and_max_tokens(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(),
        _record(),
        {"type": "llm.request", "time": 7000, "maxTokens": 999999},
    ])
    stats = _read_wire_usage_tail(
        session_dir, context_sizes=CONTEXT_SIZES, model_alias=ALIAS)
    assert stats is not None
    assert stats.used == 370
    assert stats.size == 262144


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
