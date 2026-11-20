"""Kimi ACP response 与主 wire 轮次一致性、通知排空及恢复守卫。"""
import json
import queue
from pathlib import Path

import pytest

from ide.llm.providers import acp
from ide.llm.providers import kimi_acp as ka
from ide.llm.providers.kimi_turn_consistency import (
    KimiTurnTracker,
    TurnDesynchronized,
)


FIXTURE = (Path(__file__).parent / "fixtures" /
           "kimi_turn_desynchronized.jsonl")


def _normal_records(prompt_id="msg_user", turn_id=0):
    return [
        {"type": "turn.prompt", "origin": {"kind": "user"},
         "promptId": prompt_id, "turnId": turn_id, "trigger": "normal"},
        {"type": "context.append_loop_event", "event": {
            "type": "step.end", "turnId": str(turn_id),
            "finishReason": "end_turn"}},
        {"type": "turn.ended", "turnId": turn_id, "reason": "completed"},
        {"type": "prompt.completed", "promptId": prompt_id,
         "reason": "completed"},
    ]


def _notification_records(notification_id="task:bash-1:completed", turn_id=1):
    return [
        {"type": "turn.prompt", "origin": {
            "kind": "task", "notificationId": notification_id},
         "promptId": notification_id, "turnId": turn_id, "trigger": "notify"},
        {"type": "context.append_loop_event", "event": {
            "type": "step.end", "turnId": str(turn_id),
            "finishReason": "end_turn"}},
        {"type": "turn.ended", "turnId": turn_id, "reason": "completed"},
        {"type": "prompt.completed", "promptId": notification_id,
         "reason": "completed"},
    ]


def _write_records(tmp_path, records, name="session"):
    session_dir = tmp_path / name
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text("".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8")
    return session_dir, wire


def _append(wire, records):
    with wire.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")


def _tracker(tmp_path, records):
    session_dir, wire = _write_records(tmp_path, records)
    tracker = KimiTurnTracker(session_dir, start_at_end=False)
    tracker.poll()
    return tracker, wire


def test_normal_user_turn_is_consistent(tmp_path):
    tracker, _wire = _tracker(tmp_path, _normal_records())
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "normal"
    assert not snapshot.agent_active
    assert snapshot.user_prompt_id == "msg_user"
    assert snapshot.last_finish_reason == "end_turn"


def test_incident_fixture_is_desynchronized_and_notification_active(tmp_path):
    records = [json.loads(line) for line in
               FIXTURE.read_text(encoding="utf-8").splitlines()]
    tracker, _wire = _tracker(tmp_path, records)
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "desynchronized"
    assert snapshot.agent_active
    assert snapshot.last_finish_reason == "tool_use"
    assert snapshot.notification_ids == ("task:bash-1:completed",)


def test_valid_user_turn_drains_active_notification(tmp_path):
    records = _normal_records() + _notification_records()[:1]
    tracker, wire = _tracker(tmp_path, records)
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "draining" and snapshot.agent_active
    _append(wire, _notification_records()[1:])
    tracker.poll()
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "normal" and not snapshot.agent_active


def test_continuous_notifications_wait_for_every_notification(tmp_path):
    first = "task:bash-1:completed"
    second = "task:bash-2:killed"
    records = (_normal_records() + _notification_records(first, 1) + [
        {"type": "prompt.steered", "activePromptId": first,
         "promptIds": [second], "messageId": second},
    ])
    tracker, wire = _tracker(tmp_path, records)
    assert tracker.snapshot(response_received=True).state == "draining"
    _append(wire, _notification_records(second, 2))
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "normal"


def test_partial_line_waits_and_truncation_fails_closed(tmp_path):
    tracker, wire = _tracker(tmp_path, [])
    payload = json.dumps(_normal_records()[-1])
    with wire.open("a", encoding="utf-8") as stream:
        for record in _normal_records()[:-1]:
            stream.write(json.dumps(record) + "\n")
        stream.write(payload)
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "draining"
    with wire.open("a", encoding="utf-8") as stream:
        stream.write("\n")
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "normal"
    wire.write_text("", encoding="utf-8")
    tracker.poll()
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "unknown" and not snapshot.agent_active


def test_corrupt_and_unknown_records_have_distinct_effects(tmp_path):
    session_dir, wire = _write_records(tmp_path, _normal_records())
    with wire.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"future.record","newField":1}\n')
    tracker = KimiTurnTracker(session_dir, start_at_end=False)
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "normal"
    with wire.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "unknown"


def test_missing_wire_without_risk_keeps_normal_protocol_path(tmp_path):
    tracker = KimiTurnTracker(tmp_path / "missing")
    tracker.poll()
    assert tracker.snapshot(response_received=True).state == "normal"


def test_wire_loss_during_notification_is_unknown_not_success(tmp_path):
    tracker, wire = _tracker(
        tmp_path, _normal_records() + _notification_records()[:1])
    wire.unlink()
    tracker.poll()
    snapshot = tracker.snapshot(response_received=True)
    assert snapshot.state == "unknown" and snapshot.agent_active


class _FakeConn:
    generation = 1
    failure_reason = "test"
    is_alive = True

    def __init__(self, messages, on_empty=None, on_begin=None):
        self.messages = queue.Queue()
        for message in messages:
            self.messages.put(message)
        self.on_empty = on_empty
        self.on_begin = on_begin
        self.begin_calls = 0
        self.end_calls = 0
        self.terminate_calls = 0

    def next_update(self, timeout=None):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.on_empty is not None:
                callback, self.on_empty = self.on_empty, None
                callback()
            raise
        if isinstance(message, Exception):
            raise message
        return message

    def begin_turn(self, _method, _params):
        self.begin_calls += 1
        if self.on_begin is not None:
            self.on_begin()
        self.messages.put(("response", {"id": 9, "result": {"stopReason": "end_turn"}}))
        return 9

    def end_turn(self):
        self.end_calls += 1

    def purge_updates(self):
        while True:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                return

    def terminate(self):
        self.terminate_calls += 1


def _response():
    return ("response", {"id": 9, "result": {"stopReason": "end_turn"}})


def _edit_update():
    return ("update", {"params": {"sessionId": "session_x", "update": {
        "sessionUpdate": "tool_call", "toolCallId": "edit-1", "title": "Edit",
        "kind": "edit", "status": "completed", "rawInput": {"path": "x.txt"},
    }}})


def _provider(monkeypatch, session_dir):
    monkeypatch.setattr(ka, "_session_dir_of", lambda _sid: session_dir)
    monkeypatch.setattr(ka, "_WIRE_USAGE_WAIT_S", 0)
    monkeypatch.setattr(ka, "_WIRE_USAGE_POLL_S", 0.005)
    monkeypatch.setattr(ka, "_TURN_CONSISTENCY_WAIT_S", 0.01)
    llm = ka.KimiAcpLLM()
    llm._session_id = "session_x"
    llm._sidecar_enabled = False
    return llm


def test_provider_never_succeeds_on_incident_and_keeps_late_edit_visible(
        monkeypatch, tmp_path):
    records = [json.loads(line) for line in
               FIXTURE.read_text(encoding="utf-8").splitlines()]
    session_dir, wire = _write_records(tmp_path, records)
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn(
        [_response(), _edit_update()],
        on_empty=lambda: _append(wire, _notification_records()[1:]),
    )
    try:
        generator = llm._iter_turn_chunks(conn, request_id=9)
        chunks = []
        with pytest.raises(TurnDesynchronized, match="tool_use"):
            while True:
                chunks.append(next(generator))
        assert [chunk.kind for chunk in chunks] == ["turn_sync", "tool_call"]
        assert llm.recovery_pending
        assert conn.terminate_calls == 0
    finally:
        llm.close()


def test_provider_drains_valid_notification_before_success(monkeypatch, tmp_path):
    session_dir, wire = _write_records(
        tmp_path, _normal_records() + _notification_records()[:1])
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn(
        [_response(), _edit_update()],
        on_empty=lambda: _append(wire, _notification_records()[1:]),
    )
    try:
        chunks = list(llm._iter_turn_chunks(conn, request_id=9))
        assert [chunk.kind for chunk in chunks] == ["turn_sync", "tool_call"]
        assert not llm._needs_recovery
    finally:
        llm.close()


def test_provider_rejects_response_without_user_prompt(monkeypatch, tmp_path):
    session_dir, _wire = _write_records(tmp_path, [])
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn([_response()])
    try:
        with pytest.raises(TurnDesynchronized, match="未出现用户"):
            list(llm._iter_turn_chunks(conn, request_id=9))
        assert llm.recovery_pending
    finally:
        llm.close()


def test_provider_normal_turn_does_not_emit_sync(monkeypatch, tmp_path):
    session_dir, _wire = _write_records(tmp_path, _normal_records())
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn([_response()])
    try:
        assert list(llm._iter_turn_chunks(conn, request_id=9)) == []
        assert not llm._needs_recovery
    finally:
        llm.close()


def test_connection_death_during_drain_uses_existing_recovery(monkeypatch, tmp_path):
    records = [json.loads(line) for line in
               FIXTURE.read_text(encoding="utf-8").splitlines()]
    session_dir, _wire = _write_records(tmp_path, records)
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn([_response(), ("dead", -1)])
    try:
        with pytest.raises(RuntimeError, match="连接已断开"):
            list(llm._iter_turn_chunks(conn, request_id=9))
        assert llm.recovery_pending
    finally:
        llm.close()


def test_local_abort_during_drain_preserves_stop_priority(monkeypatch, tmp_path):
    records = [json.loads(line) for line in
               FIXTURE.read_text(encoding="utf-8").splitlines()]
    session_dir, _wire = _write_records(tmp_path, records)
    llm = _provider(monkeypatch, session_dir)
    conn = _FakeConn([_response(), acp.TurnAborted()])
    try:
        with pytest.raises(acp.TurnAborted):
            list(llm._iter_turn_chunks(conn, request_id=9))
    finally:
        llm.close()


def test_next_prompt_waits_for_preexisting_notification(monkeypatch, tmp_path):
    session_dir, wire = _write_records(
        tmp_path, _normal_records() + _notification_records()[:1])
    llm = _provider(monkeypatch, session_dir)
    tracker = KimiTurnTracker(session_dir, start_at_end=False)
    llm._turn_tracker = tracker

    def finish_notification():
        _append(wire, _notification_records()[1:])

    def begin_user_turn():
        tracker.poll()
        assert tracker.snapshot(response_received=False).state == "normal"
        _append(wire, _normal_records("msg_next", 2))

    conn = _FakeConn([], on_empty=finish_notification, on_begin=begin_user_turn)
    monkeypatch.setattr(llm, "_ensure_session", lambda: conn)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "new instruction"}]))
        assert conn.begin_calls == 1
        assert conn.end_calls == 1
        assert any(chunk.kind == "turn_sync" for chunk in chunks)
        assert not llm._needs_recovery
    finally:
        llm.close()
