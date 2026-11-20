import queue
import threading
from pathlib import Path

import pytest

from ide.llm.base import UsageStats
from ide.llm.providers.acp import TurnAborted, TurnCancelled
from ide.llm.providers.kimi import KimiAcpLLM
from ide.llm.providers.kimi import provider as kimi_acp
from ide.llm.providers.kimi import usage as kimi_usage, wire as kimi_wire


SESSION_ID = "session-test"


class FakeAcpConnection:
    def __init__(self, events=(), *, resume_mode="ok", write_error=False):
        self.events = queue.Queue()
        for event in events:
            self.events.put(event)
        self.resume_mode = resume_mode
        self.write_error = write_error
        self.is_alive = True
        self.generation = 1
        self.failure_reason = "write_error" if write_error else "eof"
        self.requests = []
        self.prompts = []
        self.cancel_calls = 0
        self.abort_calls = 0
        self.terminate_calls = 0
        self.purge_calls = 0
        self.turn_ids = []
        self.aborted = False

    def set_permission_handler(self, handler):
        pass

    def set_terminal_handler(self, handler):
        pass

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "initialize":
            capabilities = {} if self.resume_mode == "unsupported" else {"resume": {}}
            return {
                "agentInfo": {"name": "fake", "version": "1"},
                "agentCapabilities": {"sessionCapabilities": capabilities},
            }
        if method == "session/new":
            return {"sessionId": SESSION_ID}
        if method == "session/resume" and self.resume_mode != "ok":
            raise RuntimeError(f"resume {self.resume_mode}")
        return {}

    def begin_turn(self, method, params):
        self.aborted = False
        self.prompts.append((method, params))
        request_id = len(self.prompts)
        self.turn_ids.append(request_id)
        if self.write_error:
            self.is_alive = False
            raise RuntimeError("write failed")
        return request_id

    def next_update(self, timeout=None):
        if self.aborted:
            raise TurnAborted
        return self.events.get_nowait()

    def cancel_turn(self, session_id):
        self.cancel_calls += 1
        return True

    def abort_turn(self):
        self.abort_calls += 1
        self.aborted = True

    def end_turn(self):
        self.turn_ids.append(None)

    def purge_updates(self):
        self.purge_calls += 1

    def terminate(self):
        self.terminate_calls += 1
        self.is_alive = False
        self.events.put(("dead", -15))

    def log_diagnostics(self, event, method=None):
        pass


def response(stop_reason="end_turn", request_id=1):
    return "response", {"id": request_id, "result": {"stopReason": stop_reason}}


def text_update(text):
    return "update", {
        "method": "session/update",
        "params": {
            "sessionId": SESSION_ID,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    }


def usage_update(used=10):
    return "update", {
        "method": "session/update",
        "params": {
            "sessionId": SESSION_ID,
            "update": {"sessionUpdate": "usage_update", "used": used, "size": 100},
        },
    }


@pytest.fixture
def provider_factory(monkeypatch):
    providers = []
    monkeypatch.setattr(kimi_acp, "_find_bin", lambda: "/fake/kimi")
    monkeypatch.setattr(kimi_usage.UsageTracker, "ensure_context_sizes", lambda self: None)
    monkeypatch.setattr(kimi_usage, "WIRE_USAGE_WAIT_S", 0.0)
    monkeypatch.setattr(kimi_wire, "session_dir_of", lambda session_id: None)

    def create(*connections):
        remaining = list(connections)

        def connection_factory(*args):
            return remaining.pop(0)

        monkeypatch.setattr(kimi_acp, "AcpConnection", connection_factory)
        provider = KimiAcpLLM(workspace_root=".")
        providers.append(provider)
        return provider

    yield create
    for provider in providers:
        provider.close()


def chat(provider, text="hello"):
    return list(provider.chat([{"role": "user", "content": text}]))


def test_i1_end_turn_is_the_only_turn_boundary(provider_factory):
    conn = FakeAcpConnection([text_update("done"), response()])
    provider = provider_factory(conn)

    chunks = chat(provider)

    assert [(chunk.kind, chunk.text) for chunk in chunks] == [("text", "done")]
    assert len(conn.prompts) == 1
    assert conn.purge_calls == 0
    assert conn.terminate_calls == 0
    assert not provider.recovery_pending


@pytest.mark.parametrize("stop_reason", ["max_tokens", "max_turn_requests", "refusal"])
def test_i2_non_success_stop_reason_is_failure(provider_factory, stop_reason):
    conn = FakeAcpConnection([response(stop_reason)])
    provider = provider_factory(conn)

    with pytest.raises(RuntimeError, match=stop_reason):
        chat(provider)

    assert len(conn.prompts) == 1
    assert not provider.recovery_pending


def test_i2_cancelled_uses_stop_channel(provider_factory):
    provider = provider_factory(FakeAcpConnection([response("cancelled")]))

    with pytest.raises(TurnCancelled):
        chat(provider)


def test_i2_protocol_error_is_failure_without_recovery(provider_factory):
    conn = FakeAcpConnection([
        ("response", {"id": 1, "error": {"code": -32603, "message": "boom"}}),
    ])
    provider = provider_factory(conn)

    with pytest.raises(RuntimeError, match="-32603"):
        chat(provider)

    assert not provider.recovery_pending


@pytest.mark.parametrize("wire_state", ["missing", "broken", "active"])
def test_i3_wire_cannot_override_end_turn(provider_factory, monkeypatch, wire_state):
    conn = FakeAcpConnection([text_update("ok"), response()])
    provider = provider_factory(conn)
    provider.set_subagent_sidecar(False)
    if wire_state != "missing":
        monkeypatch.setattr(
            kimi_wire, "session_dir_of", lambda session_id: Path(".temp/wire-test"))
        parsed = None if wire_state == "broken" else (
            1, 1, UsageStats(used=10, size=100, cost=None, source="transcript"))
        monkeypatch.setattr(kimi_wire, "read_turn_stats", lambda *args: parsed)

    chunks = chat(provider)

    assert any(chunk.kind == "text" and chunk.text == "ok" for chunk in chunks)
    assert not provider.recovery_pending
    assert conn.terminate_calls == 0


def test_gui_poll_while_worker_turn_holds_lock_does_not_steal(provider_factory):
    """GUI 轮询在 worker 持锁期间只做 wire，不排空 ACP 帧。"""
    class BlockingConnection(FakeAcpConnection):
        def __init__(self, events):
            super().__init__(events)
            self.waiting = threading.Event()
            self.release = threading.Event()

        def next_update(self, timeout=None):
            if self.events.qsize() == 1:
                self.waiting.set()
                assert self.release.wait(timeout=5)
            return super().next_update(timeout=timeout)

    conn = BlockingConnection([text_update("streamed"), response()])
    provider = provider_factory(conn)
    provider.set_subagent_sidecar(False)
    chunks = []

    worker = threading.Thread(
        target=lambda: chunks.extend(provider.chat(
            [{"role": "user", "content": "hello"}])))
    worker.start()
    try:
        assert conn.waiting.wait(2)
        assert provider.poll_usage() is None
        assert conn.events.qsize() == 1  # response 未被 GUI 抢走
    finally:
        conn.release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert [chunk.text for chunk in chunks if chunk.kind == "text"] == ["streamed"]
    assert not provider.recovery_pending


def test_i4_missing_usage_does_not_change_success(provider_factory):
    provider = provider_factory(FakeAcpConnection([response()]))

    chunks = chat(provider)

    assert chunks == []


def test_i4_delayed_usage_is_display_only(provider_factory):
    conn = FakeAcpConnection([response(), usage_update(42)])
    provider = provider_factory(conn)

    assert chat(provider) == []
    stats = provider.poll_usage()

    assert stats is not None
    assert stats.used == 42
    assert not provider.recovery_pending


def test_i5_manual_stop_cancels_without_killing_connection(provider_factory):
    conn = FakeAcpConnection([text_update("partial")])
    provider = provider_factory(conn)
    turn = provider.chat([{"role": "user", "content": "hello"}])

    assert next(turn).text == "partial"
    provider.cancel()
    with pytest.raises(TurnAborted):
        next(turn)

    assert conn.cancel_calls == 1
    assert conn.abort_calls == 1
    assert conn.terminate_calls == 0
    assert conn.is_alive
    assert not provider.recovery_pending


def test_i6_next_prompt_after_stop_has_no_old_drain(provider_factory):
    conn = FakeAcpConnection([text_update("partial")])
    provider = provider_factory(conn)
    turn = provider.chat([{"role": "user", "content": "first"}])
    next(turn)
    provider.cancel()
    with pytest.raises(TurnAborted):
        next(turn)
    conn.events.put(response(request_id=2))

    chunks = chat(provider, "second")

    assert chunks == []
    assert len(conn.prompts) == 2
    assert conn.prompts[1][1]["prompt"][0]["text"] == "second"
    assert conn.turn_ids == [1, None, 2, None]
    assert conn.purge_calls == 0


def test_i9_eof_fails_current_turn_without_replay(provider_factory):
    conn = FakeAcpConnection([("dead", 1)])
    provider = provider_factory(conn)

    with pytest.raises(RuntimeError, match="不会自动重发"):
        chat(provider)

    assert provider.recovery_pending
    assert provider._session_id == SESSION_ID
    assert len(conn.prompts) == 1


def test_i10_write_failure_uses_eof_recovery_boundary(provider_factory):
    conn = FakeAcpConnection(write_error=True)
    provider = provider_factory(conn)

    with pytest.raises(RuntimeError, match="write failed"):
        chat(provider)

    assert provider.recovery_pending
    assert provider._session_id == SESSION_ID
    assert len(conn.prompts) == 1


def test_i11_next_manual_send_resumes_and_sends_only_new_prompt(provider_factory):
    first = FakeAcpConnection([("dead", 1)])
    second = FakeAcpConnection([response()])
    provider = provider_factory(first, second)
    with pytest.raises(RuntimeError):
        chat(provider, "old")

    assert chat(provider, "new") == []

    assert provider._session_id == SESSION_ID
    assert [method for method, _ in second.requests] == ["initialize", "session/resume"]
    assert len(second.prompts) == 1
    assert second.prompts[0][1]["prompt"][0]["text"] == "new"
    assert first.terminate_calls == 1


@pytest.mark.parametrize("resume_mode", ["unsupported", "timeout", "failed"])
def test_i12_resume_failure_never_sends_or_creates_session(provider_factory, resume_mode):
    first = FakeAcpConnection([("dead", 1)])
    second = FakeAcpConnection(resume_mode=resume_mode)
    provider = provider_factory(first, second)
    with pytest.raises(RuntimeError):
        chat(provider, "old")

    with pytest.raises(RuntimeError, match="原会话恢复失败"):
        chat(provider, "new")

    assert second.prompts == []
    assert [method for method, _ in second.requests] == ["initialize"] + (
        [] if resume_mode == "unsupported" else ["session/resume"])
    assert provider._session_id == SESSION_ID


def test_i14_close_while_waiting_does_not_rebuild_or_restore(provider_factory):
    conn = FakeAcpConnection([text_update("partial")])
    provider = provider_factory(conn)
    turn = provider.chat([{"role": "user", "content": "hello"}])
    assert next(turn).text == "partial"

    provider.close()
    with pytest.raises(RuntimeError):
        next(turn)

    assert len(conn.prompts) == 1
    assert conn.terminate_calls == 1
    assert not provider.recovery_pending
