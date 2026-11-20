"""S6：ACP terminal/* 分发、env 解析、异步 wait 应答与死讯清理。

连接层只保留总入口与写帧原语；这些用例直接锁定 TerminalDispatcher 的
同步应答、-32603 错误兜底、原请求 ID 回传与 pending wait 清理。
"""
import logging
import queue
import threading

import pytest

from ide.llm.providers.acp.connection import AcpConnection
from ide.llm.providers.acp.diagnostics import StderrRing
from ide.llm.providers.acp.terminal import (
    TERMINAL_METHODS,
    TerminalDispatcher,
    parse_terminal_env,
)


class Sender:
    """记录连接层写帧调用（payload, method, request_id）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[dict, str, int | str]] = []

    def __call__(self, msg: dict, method: str, request_id: int | str) -> bool:
        self.calls.append((msg, method, request_id))
        return True


class FakeHandler:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.wait_callbacks: list = []
        self.dead_calls = 0
        self.output_result = {"output": "hello", "truncated": False}

    def create(self, command, args, cwd, env):
        self.calls.append(("create", command, args, cwd, env))
        return "term-1"

    def output(self, terminal_id):
        self.calls.append(("output", terminal_id))
        return dict(self.output_result)

    def wait_for_exit(self, terminal_id, callback):
        self.calls.append(("wait_for_exit", terminal_id))
        self.wait_callbacks.append(callback)

    def kill(self, terminal_id):
        self.calls.append(("kill", terminal_id))

    def release(self, terminal_id):
        self.calls.append(("release", terminal_id))

    def on_connection_dead(self):
        self.dead_calls += 1
        self.wait_callbacks.clear()


class CallbackOnDeadHandler(FakeHandler):
    """死讯时同步触发挂起回调（模拟 GUI 桥的兜底 exitCode=-1）。"""

    def on_connection_dead(self):
        self.dead_calls += 1
        callbacks, self.wait_callbacks = self.wait_callbacks, []
        for callback in callbacks:
            callback({"exitCode": -1, "signal": None})


class ExplodingHandler(FakeHandler):
    def create(self, *args):
        raise RuntimeError("create boom")

    def output(self, *args):
        raise RuntimeError("output boom")

    def wait_for_exit(self, *args):
        raise RuntimeError("wait boom")

    def kill(self, *args):
        raise RuntimeError("kill boom")

    def release(self, *args):
        raise RuntimeError("release boom")


class SyncWaitHandler(FakeHandler):
    def wait_for_exit(self, terminal_id, callback):
        self.calls.append(("wait_for_exit", terminal_id))
        callback({"exitCode": 0, "signal": None})


def _dispatcher(handler=None):
    sender = Sender()
    dispatcher = TerminalDispatcher("test agent", sender)
    if handler is not None:
        dispatcher.set_handler(handler)
    return dispatcher, sender


def _terminal_request(method: str, request_id, **params) -> dict:
    return {"jsonrpc": "2.0", "method": method, "id": request_id,
            "params": params}


# ----------------------------------------------------------------------
# 分发边界
# ----------------------------------------------------------------------
def test_terminal_method_set_matches_protocol():
    assert TERMINAL_METHODS == {
        "terminal/create", "terminal/output", "terminal/wait_for_exit",
        "terminal/kill", "terminal/release",
    }


def test_dispatch_rejects_non_terminal_method_or_missing_handler():
    dispatcher, sender = _dispatcher(FakeHandler())
    assert dispatcher.dispatch(_terminal_request("fs/read_text_file", 1)) is False
    assert sender.calls == []

    dispatcher, sender = _dispatcher()
    assert dispatcher.dispatch(_terminal_request("terminal/create", 2)) is False
    assert sender.calls == []


def test_create_parses_list_env_and_returns_terminal_id():
    handler = FakeHandler()
    dispatcher, sender = _dispatcher(handler)
    request = _terminal_request(
        "terminal/create", 7, command="bash", args=["-lc", "echo hi"],
        cwd="/workspace",
        env=[{"name": "A", "value": "1"}, {"name": "B"}, {"bad": 1}])

    assert dispatcher.dispatch(request) is True

    assert handler.calls == [
        ("create", "bash", ["-lc", "echo hi"], "/workspace",
         {"A": "1", "B": ""}),
    ]
    payload, method, request_id = sender.calls[0]
    assert payload == {"jsonrpc": "2.0", "id": 7,
                       "result": {"terminalId": "term-1"}}
    assert method == "terminal/create"
    assert request_id == 7


def test_create_accepts_dict_env_and_defaults_missing_fields():
    handler = FakeHandler()
    dispatcher, _sender = _dispatcher(handler)
    dispatcher.dispatch(_terminal_request(
        "terminal/create", 8, command="echo", env={"A": 1, "B": None}))
    assert handler.calls[0][4] == {"A": "1", "B": "None"}

    handler.calls.clear()
    dispatcher.dispatch(_terminal_request("terminal/create", 9))
    assert handler.calls[0] == ("create", "", [], None, None)


@pytest.mark.parametrize("method,expected_call,expected_result", [
    ("terminal/output", ("output", "term-9"),
     {"output": "hello", "truncated": False}),
    ("terminal/kill", ("kill", "term-9"), {}),
    ("terminal/release", ("release", "term-9"), {}),
])
def test_sync_terminal_methods_reply_with_original_id(
        method, expected_call, expected_result):
    handler = FakeHandler()
    dispatcher, sender = _dispatcher(handler)

    assert dispatcher.dispatch(
        _terminal_request(method, "req-9", terminalId="term-9")) is True

    assert handler.calls == [expected_call]
    payload, sent_method, request_id = sender.calls[0]
    assert payload == {"jsonrpc": "2.0", "id": "req-9",
                       "result": expected_result}
    assert sent_method == method
    assert request_id == "req-9"


@pytest.mark.parametrize("method", [
    "terminal/create", "terminal/output", "terminal/kill", "terminal/release",
])
def test_handler_exception_replies_minus_32603(method):
    dispatcher, sender = _dispatcher(ExplodingHandler())

    assert dispatcher.dispatch(_terminal_request(method, 11)) is True

    payload, sent_method, request_id = sender.calls[0]
    assert sent_method == "reverse_error"
    assert request_id == 11
    assert payload["error"]["code"] == -32603
    assert method in payload["error"]["message"]


# ----------------------------------------------------------------------
# wait_for_exit 异步应答
# ----------------------------------------------------------------------
def test_wait_for_exit_sync_callback_replies_immediately():
    dispatcher, sender = _dispatcher(SyncWaitHandler())

    assert dispatcher.dispatch(
        _terminal_request("terminal/wait_for_exit", 42, terminalId="term-2")) is True

    payload, method, request_id = sender.calls[0]
    assert payload == {"jsonrpc": "2.0", "id": 42,
                       "result": {"exitCode": 0, "signal": None}}
    assert method == "terminal/wait_for_exit"
    assert request_id == 42


def test_wait_for_exit_defers_and_drops_duplicate_callback():
    handler = FakeHandler()
    dispatcher, sender = _dispatcher(handler)

    dispatcher.dispatch(
        _terminal_request("terminal/wait_for_exit", 43, terminalId="term-3"))
    assert sender.calls == []
    assert handler.wait_callbacks

    handler.wait_callbacks[0]({"exitCode": 3, "signal": None})
    handler.wait_callbacks[0]({"exitCode": 4, "signal": None})

    assert len(sender.calls) == 1
    payload, method, request_id = sender.calls[0]
    assert payload["id"] == 43
    assert payload["result"] == {"exitCode": 3, "signal": None}
    assert method == "terminal/wait_for_exit"


def test_wait_for_exit_registration_failure_replies_and_clears_pending():
    dispatcher, sender = _dispatcher(ExplodingHandler())

    dispatcher.dispatch(
        _terminal_request("terminal/wait_for_exit", 44, terminalId="term-4"))

    payload, method, request_id = sender.calls[0]
    assert method == "reverse_error"
    assert request_id == 44
    assert payload["error"]["code"] == -32603
    assert "wait_for_exit 失败" in payload["error"]["message"]
    assert dispatcher._pending_waits == set()


# ----------------------------------------------------------------------
# 死讯清理与实例隔离
# ----------------------------------------------------------------------
def test_connection_dead_clears_pending_and_ignores_late_callback():
    handler = FakeHandler()
    dispatcher, sender = _dispatcher(handler)
    dispatcher.dispatch(
        _terminal_request("terminal/wait_for_exit", 45, terminalId="term-5"))
    late_callback = handler.wait_callbacks[0]

    dispatcher.on_connection_dead()
    late_callback({"exitCode": -1, "signal": None})

    assert handler.dead_calls == 1
    assert dispatcher._pending_waits == set()
    assert sender.calls == []


def test_connection_dead_handler_exception_does_not_escape():
    class DeadExplodingHandler(FakeHandler):
        def on_connection_dead(self):
            raise RuntimeError("dead boom")

    dispatcher, _sender = _dispatcher(DeadExplodingHandler())
    dispatcher.dispatch(
        _terminal_request("terminal/wait_for_exit", 46, terminalId="term-6"))

    dispatcher.on_connection_dead()

    assert dispatcher._pending_waits == set()


def test_dead_cleanup_is_per_dispatcher_instance():
    first_handler = FakeHandler()
    first, first_sender = _dispatcher(first_handler)
    second_handler = FakeHandler()
    second, second_sender = _dispatcher(second_handler)
    first.dispatch(
        _terminal_request("terminal/wait_for_exit", 1, terminalId="a"))
    second.dispatch(
        _terminal_request("terminal/wait_for_exit", 2, terminalId="b"))

    first.on_connection_dead()
    second_handler.wait_callbacks[0]({"exitCode": 0, "signal": None})

    assert first_sender.calls == []
    assert len(second_sender.calls) == 1
    assert second_sender.calls[0][0]["id"] == 2


# ----------------------------------------------------------------------
# env 解析
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    (None, None),
    ("not-a-list", None),
    ({}, {}),
    ({"A": 1}, {"A": "1"}),
    ([], None),
    ([{"name": "A", "value": "1"}, {"name": "B"}], {"A": "1", "B": ""}),
    ([{"value": "1"}, "junk"], None),
    ([{"name": "A"}, {"bad": 1}, {"name": 2}], {"A": "", "2": ""}),
])
def test_parse_terminal_env(raw, expected):
    assert parse_terminal_env(raw) == expected


def test_connection_and_protocol_no_longer_own_terminal_implementation():
    from ide.llm.providers.acp import protocol

    assert not hasattr(AcpConnection, "_handle_terminal")
    assert not hasattr(AcpConnection, "_complete_wait")
    assert not hasattr(AcpConnection, "_reply_error")
    assert not hasattr(protocol, "_parse_terminal_env")


# ----------------------------------------------------------------------
# 连接层组合
# ----------------------------------------------------------------------
class _FakeProc:
    pid = 4321

    def poll(self):
        return 1


def _bare_connection(sender: Sender) -> AcpConnection:
    """跳过 Popen/线程，只装配 _handle_reverse/_mark_dead 所需的连接状态。"""
    conn = object.__new__(AcpConnection)
    conn._agent_name = "test"
    conn.generation = 1
    conn._proc = _FakeProc()
    conn._next = 0
    conn._write_lock = threading.Lock()
    conn._state_lock = threading.Lock()
    conn._pending = {}
    conn._updates = queue.Queue()
    conn._turn_id = None
    conn._turn_abort = threading.Event()
    conn.is_alive = True
    conn._terminated = False
    conn._expected_close = False
    conn._failure_reason = None
    conn._failure_method = None
    conn._last_request_id = None
    conn._stderr_ring = StderrRing()
    conn._permission_handler = None

    def write_reverse(msg, method, request_id):
        if not conn.is_alive:
            return False
        return sender(msg, method, request_id)

    conn._write_reverse = write_reverse
    conn._terminal = TerminalDispatcher("test", write_reverse)
    return conn


def test_connection_routes_terminal_request_through_dispatcher():
    sender = Sender()
    conn = _bare_connection(sender)
    handler = FakeHandler()
    conn.set_terminal_handler(handler)

    conn._handle_reverse(
        _terminal_request("terminal/create", 5, command="echo", args=["hi"]))

    assert sender.calls[0][0]["result"] == {"terminalId": "term-1"}
    assert sender.calls[0][1] == "terminal/create"


def test_connection_falls_back_to_method_not_found_without_handler():
    sender = Sender()
    conn = _bare_connection(sender)
    conn.set_terminal_handler(None)

    conn._handle_reverse(_terminal_request("terminal/output", 6))

    payload, method, request_id = sender.calls[0]
    assert payload["error"] == {"code": -32601, "message": "method not found"}
    assert method == "terminal/output"
    assert request_id == 6


def test_connection_log_diagnostics_uses_failure_method_and_redacted_tail(caplog):
    conn = _bare_connection(Sender())
    conn.generation = 3
    conn._failure_method = "session/resume"
    conn._failure_reason = "eof"
    conn._last_request_id = 9
    conn._stderr_ring.append("Authorization: Bearer sk-hidden")

    with caplog.at_level(
            logging.WARNING, logger="ide.llm.providers.acp.diagnostics"):
        conn.log_diagnostics("connection_dead")

    assert "method=session/resume" in caplog.records[0].getMessage()
    assert "reason=eof" in caplog.records[0].getMessage()
    assert "requestId=9" in caplog.records[0].getMessage()
    assert "pid=4321" in caplog.records[0].getMessage()
    assert "sk-hidden" not in caplog.records[1].getMessage()

    caplog.clear()
    with caplog.at_level(
            logging.WARNING, logger="ide.llm.providers.acp.diagnostics"):
        conn.log_diagnostics("request_timeout", "session/prompt")
    assert "method=session/prompt" in caplog.records[0].getMessage()


def test_connection_mark_dead_cleans_terminal_waits_without_writing():
    sender = Sender()
    conn = _bare_connection(sender)
    handler = CallbackOnDeadHandler()
    conn.set_terminal_handler(handler)
    conn._handle_reverse(
        _terminal_request("terminal/wait_for_exit", 12, terminalId="term-12"))

    conn._mark_dead("eof")

    assert conn.failure_reason == "eof"
    assert conn.is_alive is False
    assert handler.dead_calls == 1
    assert conn._terminal._pending_waits == set()
    assert sender.calls == []
