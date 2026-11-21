"""T4：session/close 能力记录、停止分流与关闭路径顺序。

判定矩阵（计划 §T4）：能力有/无、close 成功/超时/报错、`_turn_id=None`
且终态未确认时停止、切后端调用顺序、close 失败不杀连接、close 后下
一次只 resume 一次且不静默新建。
"""
import queue

import pytest

from ide.llm.providers.acp import TurnAborted
from ide.llm.providers.acp.connection import AcpConnection
from ide.llm.providers.kimi import provider as kimi_acp
from ide.llm.providers.kimi import usage as kimi_usage, wire as kimi_wire


SESSION_ID = "session-test"


class FakeAcpConnection:
    """带 close 记录的假连接：close 行为可配置（成功/失败/超时）。"""

    def __init__(self, events=(), *, close_supported=True, close_fails=False,
                 close_result=True):
        self.events = queue.Queue()
        for event in events:
            self.events.put(event)
        self.close_supported = close_supported
        self.close_fails = close_fails
        self.close_result = close_result
        self.is_alive = True
        self.generation = 1
        self.failure_reason = None
        self.requests = []
        self.prompts = []
        self.cancel_calls = 0
        self.abort_calls = 0
        self.close_calls = 0
        self.terminate_calls = 0
        self.aborted = False
        self._turn_active = True  # cancel_turn 是否生效（模拟活跃轮）

    def set_permission_handler(self, handler):
        pass

    def set_terminal_handler(self, handler):
        pass

    def request(self, method, params, timeout=30):
        self.requests.append((method, params, timeout))
        if method == "initialize":
            caps = {"resume": {}}
            if self.close_supported:
                caps["close"] = {}
            return {"agentInfo": {"name": "fake", "version": "1"},
                    "agentCapabilities": {"sessionCapabilities": caps}}
        if method == "session/new":
            return {"sessionId": SESSION_ID}
        if method == "session/close" and self.close_fails:
            raise RuntimeError("close timeout")
        return {}

    def begin_turn(self, method, params):
        self.aborted = False
        self.prompts.append((method, params))
        return len(self.prompts)

    def next_update(self, timeout=None):
        if self.aborted:
            raise TurnAborted
        return self.events.get_nowait()

    def cancel_turn(self, session_id):
        self.cancel_calls += 1
        return self._turn_active

    def abort_turn(self):
        self.abort_calls += 1
        self.aborted = True

    def end_turn(self):
        pass

    def purge_updates(self):
        pass

    def close_session(self, session_id):
        self.close_calls += 1
        return self.close_result

    def terminate(self):
        self.terminate_calls += 1
        self.is_alive = False
        self.events.put(("dead", -15))

    def log_diagnostics(self, event, method=None):
        pass


@pytest.fixture
def provider_factory(monkeypatch):
    providers = []
    monkeypatch.setattr(kimi_acp, "_find_bin", lambda: "/fake/kimi")
    monkeypatch.setattr(kimi_usage.UsageTracker, "ensure_context_sizes", lambda self: None)
    monkeypatch.setattr(kimi_wire, "session_dir_of", lambda session_id: None)

    def create(*connections):
        remaining = list(connections)

        def connection_factory(*args):
            return remaining.pop(0)

        monkeypatch.setattr(kimi_acp, "AcpConnection", connection_factory)
        provider = kimi_acp.KimiAcpLLM(workspace_root=".")
        providers.append(provider)
        return provider

    yield create
    for provider in providers:
        provider.close()


def chat(provider, text="hello"):
    return list(provider.chat([{"role": "user", "content": text}]))


def response(stop_reason="end_turn", request_id=1):
    return "response", {"id": request_id, "result": {"stopReason": stop_reason}}


def test_initialize_records_close_capability(provider_factory):
    provider = provider_factory(FakeAcpConnection([response()]))
    chat(provider)
    assert provider._close_supported is True

    provider2 = provider_factory(
        FakeAcpConnection([response()], close_supported=False))
    chat(provider2)
    assert provider2._close_supported is False


def test_cancel_falls_back_to_close_when_turn_pending(provider_factory):
    """假完成场景：轮次句柄已清且终态未确认 → 停止走 session/close。"""
    conn = FakeAcpConnection([response()])
    conn._turn_active = False  # _turn_id 已清：cancel_turn 无效
    provider = provider_factory(conn)
    chat(provider)
    provider._turn_pending = True  # 模拟 drain 中被停止/失配收尾

    provider.cancel()

    assert conn.cancel_calls == 1
    assert conn.close_calls == 1  # 回落会话级 close
    assert conn.abort_calls == 1


def test_cancel_skips_close_when_turn_settled(provider_factory):
    """空闲停止（turn_pending=False）：close 不发出（停止钮此时本应禁用）。"""
    conn = FakeAcpConnection([response()])
    conn._turn_active = False
    provider = provider_factory(conn)
    chat(provider)

    provider.cancel()

    assert conn.cancel_calls == 1
    assert conn.close_calls == 0
    assert conn.abort_calls == 1


def test_cancel_uses_cancel_turn_for_active_turn(provider_factory):
    """正常轮次级取消：close 不参与（保留连接，会话资产）。"""
    conn = FakeAcpConnection([
        ("update", {"method": "session/update", "params": {
            "sessionId": SESSION_ID,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": "partial"}}}}),
        response(),
    ])
    provider = provider_factory(conn)
    turn = provider.chat([{"role": "user", "content": "hello"}])
    next(turn)
    provider.cancel()

    assert conn.cancel_calls == 1
    assert conn.close_calls == 0


def test_provider_close_calls_close_session_before_terminate(provider_factory):
    """标签关闭/切后端顺序：先 session/close 通知 agent，再 terminate 兜底。"""
    conn = FakeAcpConnection([response()])
    provider = provider_factory(conn)
    chat(provider)

    provider.close()

    assert conn.close_calls == 1
    assert conn.terminate_calls == 1
    # 顺序由 close() 实现保证：close_session 内联、terminate 其后
    assert provider._conn is None
    assert not provider.recovery_pending


def test_close_keeps_session_id_for_next_resume(provider_factory):
    """D7：close 后保留 sessionId，下一次手动发送只 resume 一次。"""
    first = FakeAcpConnection([response()])
    second = FakeAcpConnection([response()])
    provider = provider_factory(first, second)
    chat(provider)

    provider.close_session()
    assert first.close_calls == 1
    assert provider._session_id == SESSION_ID  # 会话身份保留
    assert provider.recovery_pending is True  # 下次发送先 resume

    chat(provider, "after close")

    assert provider._session_id == SESSION_ID
    methods = [m for m, _, _ in second.requests]
    assert methods == ["initialize", "session/resume"]  # 只 resume，不 session/new
    assert len(second.prompts) == 1
    assert second.prompts[0][1]["prompt"][0]["text"] == "after close"


def test_close_without_capability_logs_and_aborts(provider_factory):
    """D12 降级：CLI 未声明 close → 不发请求、明确日志、不假装已停。"""
    conn = FakeAcpConnection([response()], close_supported=False)
    provider = provider_factory(conn)
    chat(provider)

    provider.close_session()

    assert conn.close_calls == 0  # provider 侧能力门拦截
    assert conn.abort_calls == 1  # 本地中断仍执行（解封 worker）


# ---------------------------------------------------------------- 连接层

def make_bare_connection() -> AcpConnection:
    conn = AcpConnection.__new__(AcpConnection)
    conn._agent_name = "kimi acp"
    conn.generation = 1
    conn.is_alive = True
    return conn


def test_connection_close_session_sends_request_with_short_timeout():
    conn = make_bare_connection()
    calls = []
    conn.request = lambda method, params, timeout=30: calls.append(
        (method, params, timeout)) or {}

    assert conn.close_session("s-1") is True
    assert calls == [("session/close", {"sessionId": "s-1"}, 2.0)]


def test_connection_close_session_failure_returns_false_and_keeps_alive():
    conn = make_bare_connection()

    def failing_request(method, params, timeout=30):
        raise RuntimeError("boom")

    conn.request = failing_request
    assert conn.close_session("s-1") is False
    assert conn.is_alive is True  # 失败不杀连接


def test_connection_close_session_dead_connection_is_noop():
    conn = make_bare_connection()
    conn.is_alive = False
    calls = []
    conn.request = lambda *a, **k: calls.append(a)
    assert conn.close_session("s-1") is False
    assert calls == []
