"""极简 Kimi provider 的轮次契约：响应即终态，不产出同步提示。

这些用例锁死方案 A 的行为边界：如果日后有人把终态确认、drain 或 status
提示加回来，这里会直接变红。
"""
import queue

import pytest

from ide.llm.providers.acp import TurnCancelled
from ide.llm.providers.kimi.provider import KimiAcpLLM

SESSION_ID = "session-min-test"


def _text_frame(text: str) -> tuple[str, dict]:
    return ("update", {
        "method": "session/update",
        "params": {
            "sessionId": SESSION_ID,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    })


def _response(stop_reason: str) -> tuple[str, dict]:
    return ("response", {"id": 1, "result": {"stopReason": stop_reason}})


class FakeConn:
    """最小 ACP 连接替身：只回放给定帧序列。"""

    def __init__(self, events):
        self.events = queue.Queue()
        for event in events:
            self.events.put(event)
        self.is_alive = True
        self.generation = 1
        self.failure_reason = "eof"
        self.requests = []
        self.prompts = []
        self.purged = 0

    def set_permission_handler(self, handler):
        pass

    def set_terminal_handler(self, handler):
        pass

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "initialize":
            return {"agentInfo": {"name": "fake"}, "agentCapabilities": {}}
        if method == "session/new":
            return {"sessionId": SESSION_ID}
        return {}

    def begin_turn(self, method, params):
        self.prompts.append((method, params))
        return 1

    def end_turn(self):
        pass

    def purge_updates(self):
        self.purged += 1

    def cancel_turn(self, session_id):
        return True

    def abort_turn(self):
        pass

    def terminate(self):
        self.is_alive = False

    def log_diagnostics(self, event, method=None):
        pass

    def next_update(self, timeout=None):
        return self.events.get_nowait()


def _provider(conn: FakeConn) -> KimiAcpLLM:
    llm = KimiAcpLLM()
    llm._conn = conn
    llm._session_id = SESSION_ID
    return llm


def test_turn_ends_on_response_and_ignores_trailing_frames():
    """响应即终态：响应后的迟到帧不再消费，也不产生 status 块。"""
    conn = FakeConn([
        _text_frame("MIN-"),
        _text_frame("OK"),
        _response("end_turn"),
        _text_frame("LATE"),
    ])
    chunks = list(_provider(conn).chat([{"role": "user", "content": "hi"}]))
    assert [c.text for c in chunks] == ["MIN-", "OK"]
    assert all(c.kind == "text" for c in chunks)


def test_cancelled_maps_to_turn_cancelled():
    conn = FakeConn([_response("cancelled")])
    with pytest.raises(TurnCancelled):
        list(_provider(conn).chat([{"role": "user", "content": "hi"}]))


def test_max_tokens_maps_to_failure():
    conn = FakeConn([_response("max_tokens")])
    with pytest.raises(RuntimeError, match="max_tokens"):
        list(_provider(conn).chat([{"role": "user", "content": "hi"}]))


def test_dead_connection_reports_and_marks_recovery():
    conn = FakeConn([_text_frame("半截"),
                     ("dead", -15)])
    llm = _provider(conn)
    with pytest.raises(RuntimeError, match="连接已断开"):
        list(llm.chat([{"role": "user", "content": "hi"}]))
    assert llm.recovery_pending is True


def test_response_error_takes_priority_over_stop_reason():
    conn = FakeConn([("response", {"id": 1, "error": {"code": -32603, "message": "boom"}})])
    with pytest.raises(RuntimeError, match="boom"):
        list(_provider(conn).chat([{"role": "user", "content": "hi"}]))


def test_capabilities_keep_minimal_contract():
    caps = _provider(FakeConn([])).capabilities()
    assert caps.stall_watchdog is False
    assert caps.post_turn_usage_grace_s == 0.0
    assert caps.recovery_pending is False


def test_empty_user_message_is_noop():
    conn = FakeConn([])
    assert list(_provider(conn).chat([{"role": "assistant", "content": "x"}])) == []
    assert conn.prompts == []
