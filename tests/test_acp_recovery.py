"""ACP 连接失效、Kimi 原会话恢复与有界 stderr 诊断回归。"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

import ide.llm.providers.acp as acp_mod
import ide.llm.providers.kimi_acp as ka

_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_acp_recovery"
_SESSION_ID = "session_recovery_test"


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _FakeAgentProc:
    def __init__(self, scenario: dict) -> None:
        self.scenario = scenario
        self.pid = 1000 + scenario.get("index", 0)
        self.requests: list[tuple[str, dict]] = []
        self.prompts: list[str] = []
        self.prompt_seen = threading.Event()
        self.cancel_seen = threading.Event()
        self.stdin_read, self.stdin_write = os.pipe()
        self.stdout_read, self.stdout_write = os.pipe()
        self.stderr_read, self.stderr_write = os.pipe()
        self.stdin = os.fdopen(self.stdin_write, "w", encoding="utf-8", buffering=1)
        self.stdout = os.fdopen(self.stdout_read, encoding="utf-8")
        self.stderr = os.fdopen(self.stderr_read, encoding="utf-8")
        self._stdout_sink = os.fdopen(self.stdout_write, "w", encoding="utf-8", buffering=1)
        self._stderr_sink = os.fdopen(self.stderr_write, "w", encoding="utf-8", buffering=1)
        self._reader_f = os.fdopen(self.stdin_read, encoding="utf-8")
        self._dead = threading.Event()
        self._responder = threading.Thread(target=self._run_responder, daemon=True)
        self._responder.start()

    def _run_responder(self):
        try:
            for line in self._reader_f:
                if self._dead.is_set():
                    return
                try:
                    self._handle(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except (OSError, ValueError):
            return

    def _handle(self, obj: dict) -> None:
        method, req_id = obj.get("method"), obj.get("id")
        params = obj.get("params") or {}
        if method:
            self.requests.append((method, params))
        if method == "initialize":
            if self.scenario.get("stderr_burst"):
                for index in range(200):
                    self._stderr_sink.write(
                        f"diagnostic-{index} Authorization: Bearer secret-value\n")
                self._stderr_sink.flush()
            capabilities = ({"sessionCapabilities": {"resume": {}}}
                            if self.scenario.get("resume_supported", True) else {})
            self._reply(req_id, {
                "protocolVersion": 1,
                "agentCapabilities": capabilities,
                "agentInfo": {"name": "fake", "version": "1"},
            })
        elif method == "session/new":
            self._reply(req_id, {"sessionId": _SESSION_ID, "configOptions": []})
            if self.scenario.get("break_after_new"):
                self._reader_f.close()
        elif method == "session/resume":
            if self.scenario.get("resume_timeout"):
                return
            if self.scenario.get("resume_error"):
                self._send({"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32603, "message": "resume failed"}})
            else:
                self._reply(req_id, {"configOptions": [], "modes": {}})
        elif method == "session/set_config_option":
            if self.scenario.get("config_error"):
                self._send({"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32602, "message": "bad config"}})
            else:
                self._reply(req_id, {})
        elif method == "session/prompt":
            self.prompts.append(params["prompt"][0]["text"])
            self.prompt_seen.set()
            behavior = self.scenario.get("prompt", "normal")
            if behavior == "eof":
                self._dead.set()
                self._stdout_sink.close()
            elif behavior == "reverse_wait":
                self._send({"jsonrpc": "2.0", "id": 900,
                            "method": "terminal/wait_for_exit",
                            "params": {"terminalId": "t1"}})
                self._reader_f.close()
            elif behavior == "stuck":
                return
            elif behavior == "stuck_once" and len(self.prompts) == 1:
                return
            elif "prompt_error" in self.scenario:
                self._send({"jsonrpc": "2.0", "id": req_id,
                            "error": self.scenario["prompt_error"]})
            elif "prompt_result" in self.scenario:
                self._reply(req_id, self.scenario["prompt_result"])
            else:
                self._reply(req_id, {"stopReason": "end_turn"})
        elif method == "session/cancel":
            self.cancel_seen.set()

    def _reply(self, req_id, result: dict) -> None:
        if req_id is not None:
            self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send(self, msg: dict) -> None:
        try:
            self._stdout_sink.write(json.dumps(msg) + "\n")
            self._stdout_sink.flush()
        except (OSError, ValueError):
            pass

    def methods(self) -> list[str]:
        return [method for method, _params in self.requests]

    def poll(self):
        return 0 if self._dead.is_set() else None

    def terminate(self) -> None:
        self._close()

    def kill(self) -> None:
        self._close()

    def wait(self, timeout=None):
        return 0

    def _close(self) -> None:
        self._dead.set()
        for stream in (self.stdin, self._stdout_sink, self._stderr_sink):
            try:
                stream.close()
            except OSError:
                pass
        self._responder.join(timeout=2)
        try:
            self._reader_f.close()
        except OSError:
            pass


@pytest.fixture()
def fake_agent(monkeypatch):
    class _Ctl:
        def __init__(self) -> None:
            self.scenarios: list[dict] = []
            self.procs: list[_FakeAgentProc] = []
            self.popen_kwargs: list[dict] = []

    ctl = _Ctl()

    def _popen(*_args, **kwargs):
        ctl.popen_kwargs.append(kwargs)
        index = len(ctl.procs)
        scenario = dict(ctl.scenarios[min(index, len(ctl.scenarios) - 1)]
                        if ctl.scenarios else {})
        scenario["index"] = index
        proc = _FakeAgentProc(scenario)
        ctl.procs.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", _popen)
    return ctl


@pytest.fixture()
def kimi_provider(fake_agent, monkeypatch):
    monkeypatch.setattr(ka, "_find_bin", lambda: "/fake/kimi")
    monkeypatch.setattr(ka, "_session_dir_of", lambda _sid: None)
    monkeypatch.setattr(ka, "load_kimi_provider_catalog", lambda: {})
    monkeypatch.setattr(ka, "_WIRE_USAGE_WAIT_S", 0)
    provider = ka.KimiAcpLLM(workspace_root=str(_TEMP_DIR))
    yield provider
    provider.close()


def _chat(provider, text: str):
    return list(provider.chat([{"role": "user", "content": text}]))


def _chat_async(provider, text: str):
    errors: list[Exception] = []

    def run():
        try:
            _chat(provider, text)
        except Exception as error:  # noqa: BLE001 — 测试线程转交主线程断言
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def test_acp_process_gets_non_interactive_pagers_without_mutating_parent(
        fake_agent, monkeypatch):
    monkeypatch.setenv("GIT_PAGER", "less")
    monkeypatch.setenv("PAGER", "more")
    monkeypatch.setenv("ACP_INHERITED_SENTINEL", "kept")
    parent_before = dict(os.environ)

    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    try:
        env = fake_agent.popen_kwargs[0]["env"]
        assert env["GIT_PAGER"] == "cat"
        assert env["PAGER"] == "cat"
        assert env["ACP_INHERITED_SENTINEL"] == "kept"
        assert env is not os.environ
        assert dict(os.environ) == parent_before
    finally:
        conn.terminate()


def test_eof_recovers_same_session_without_replay(fake_agent, kimi_provider):
    fake_agent.scenarios = [{"prompt": "eof"}, {"prompt": "normal"}]
    with pytest.raises(RuntimeError, match="原请求不会自动重发"):
        _chat(kimi_provider, "original")
    assert kimi_provider._session_id == _SESSION_ID
    assert kimi_provider.recovery_pending

    _chat(kimi_provider, "new instruction")

    first, second = fake_agent.procs
    assert first.prompts == ["original"]
    assert second.prompts == ["new instruction"]
    assert "session/new" not in second.methods()
    resume_params = next(params for method, params in second.requests
                         if method == "session/resume")
    assert resume_params["sessionId"] == _SESSION_ID
    assert resume_params["cwd"] == str(_TEMP_DIR)
    assert resume_params["mcpServers"] == []
    assert not kimi_provider.recovery_pending


def test_begin_turn_broken_pipe_recovers_on_next_manual_send(fake_agent, kimi_provider):
    fake_agent.scenarios = [{"break_after_new": True}, {"prompt": "normal"}]
    with pytest.raises(RuntimeError, match="session/prompt"):
        _chat(kimi_provider, "original")
    assert kimi_provider._session_id == _SESSION_ID

    _chat(kimi_provider, "new instruction")

    first, second = fake_agent.procs
    assert first.prompts == []
    assert second.prompts == ["new instruction"]
    assert second.methods().count("session/resume") == 1


def test_stall_marks_recovery_but_normal_stop_reuses_connection(fake_agent, kimi_provider):
    fake_agent.scenarios = [{"prompt": "stuck"}, {"prompt": "normal"}]
    thread, _errors = _chat_async(kimi_provider, "stalled")
    assert _wait_for(lambda: bool(fake_agent.procs)
                     and fake_agent.procs[0].prompt_seen.is_set())
    kimi_provider.mark_stalled()
    kimi_provider.cancel()
    thread.join(timeout=5)
    assert not thread.is_alive()

    _chat(kimi_provider, "after stall")
    assert len(fake_agent.procs) == 2
    assert "session/resume" in fake_agent.procs[1].methods()

    kimi_provider.close()
    fake_agent.scenarios = [{"prompt": "stuck_once"}]
    provider = ka.KimiAcpLLM(workspace_root=str(_TEMP_DIR))
    try:
        previous_count = len(fake_agent.procs)
        thread, _errors = _chat_async(provider, "manual stop")
        assert _wait_for(lambda: len(fake_agent.procs) > previous_count
                         and fake_agent.procs[-1].prompt_seen.is_set())
        provider.cancel()
        thread.join(timeout=5)
        assert not thread.is_alive()
        _chat(provider, "after manual stop")
        assert len(fake_agent.procs) == 3
        assert "session/resume" not in fake_agent.procs[-1].methods()
    finally:
        provider.close()


def test_unsupported_resume_fails_closed_and_can_retry(fake_agent, kimi_provider):
    fake_agent.scenarios = [
        {"prompt": "eof"},
        {"resume_supported": False},
        {"prompt": "normal"},
    ]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    with pytest.raises(RuntimeError, match="原会话恢复失败"):
        _chat(kimi_provider, "must not send")

    unsupported = fake_agent.procs[1]
    assert unsupported.prompts == []
    assert "session/new" not in unsupported.methods()
    assert kimi_provider._session_id == _SESSION_ID

    _chat(kimi_provider, "retry")
    assert fake_agent.procs[2].prompts == ["retry"]


@pytest.mark.parametrize("failure", ["resume_error", "resume_timeout"])
def test_resume_failure_never_sends_prompt_or_creates_session(
        fake_agent, kimi_provider, monkeypatch, failure):
    monkeypatch.setattr(ka, "_SESSION_RESUME_TIMEOUT_S", 0.1)
    fake_agent.scenarios = [{"prompt": "eof"}, {failure: True}]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    with pytest.raises(RuntimeError, match="未发送消息"):
        _chat(kimi_provider, "must not send")

    failed = fake_agent.procs[1]
    assert failed.prompts == []
    assert "session/new" not in failed.methods()
    assert kimi_provider._session_id == _SESSION_ID
    assert kimi_provider.recovery_pending


def test_resume_restores_model_and_effort(fake_agent, kimi_provider):
    kimi_provider._model = "model-x"
    kimi_provider._effort = "high"
    fake_agent.scenarios = [{"prompt": "eof"}, {"prompt": "normal"}]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    _chat(kimi_provider, "new")
    configs = [params for method, params in fake_agent.procs[1].requests
               if method == "session/set_config_option"]
    assert [(params["configId"], params["value"]) for params in configs] == [
        ("model", "model-x"), ("thinking", "high")]


def test_resume_config_failure_is_fail_closed(fake_agent, kimi_provider):
    kimi_provider._model = "model-x"
    kimi_provider._effort = "high"
    fake_agent.scenarios = [{"prompt": "eof"}, {"config_error": True}]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    with pytest.raises(RuntimeError, match="原会话恢复失败"):
        _chat(kimi_provider, "must not send")
    assert fake_agent.procs[1].prompts == []
    assert "session/new" not in fake_agent.procs[1].methods()


def test_close_after_failure_does_not_spawn_recovery(fake_agent, kimi_provider):
    fake_agent.scenarios = [{"prompt": "eof"}, {"prompt": "normal"}]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    kimi_provider.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        _chat(kimi_provider, "too late")
    assert len(fake_agent.procs) == 1


def test_explicit_reset_is_the_only_new_session_path(fake_agent, kimi_provider):
    fake_agent.scenarios = [{"prompt": "normal"}]
    _chat(kimi_provider, "first")
    conn = kimi_provider._conn
    kimi_provider.reset_session()
    _chat(kimi_provider, "second")
    assert len(fake_agent.procs) == 1
    assert kimi_provider._conn is conn
    assert fake_agent.procs[0].methods().count("session/new") == 2
    assert "session/resume" not in fake_agent.procs[0].methods()


def test_late_old_connection_death_does_not_replace_recovered_connection(
        fake_agent, kimi_provider):
    fake_agent.scenarios = [{"prompt": "eof"}, {"prompt": "normal"}]
    with pytest.raises(RuntimeError):
        _chat(kimi_provider, "original")
    old = kimi_provider._conn
    _chat(kimi_provider, "new")
    recovered = kimi_provider._conn
    assert old is not None and recovered is not None and old is not recovered
    old._mark_dead("late_eof")
    assert kimi_provider._conn is recovered
    assert recovered.is_alive
    assert kimi_provider._session_id == _SESSION_ID


class _TerminalHandler:
    def __init__(self) -> None:
        self.callback = None
        self.registered = threading.Event()
        self.dead = threading.Event()

    def wait_for_exit(self, _terminal_id, callback):
        self.callback = callback
        self.registered.set()

    def on_connection_dead(self):
        self.dead.set()


def test_wait_for_exit_write_failure_uses_common_dead_state(fake_agent):
    fake_agent.scenarios = [{"prompt": "reverse_wait"}]
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    handler = _TerminalHandler()
    conn.set_terminal_handler(handler)
    try:
        conn.begin_turn("session/prompt", {
            "sessionId": _SESSION_ID, "prompt": [{"type": "text", "text": "x"}]})
        assert _wait_for(handler.registered.is_set)
        handler.callback({"exitCode": 0, "signal": None})
        assert _wait_for(lambda: not conn.is_alive)
        assert conn.failure_reason == "write_error"
        assert handler.dead.is_set()
        kind, _obj = conn.next_update(timeout=1)
        assert kind == "dead"
    finally:
        conn.terminate()


def test_stderr_is_drained_bounded_and_redacted(fake_agent):
    fake_agent.scenarios = [{"stderr_burst": True}]
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    try:
        conn.request("initialize", {}, timeout=2)
        assert _wait_for(lambda: "diagnostic-199" in conn._stderr_ring.tail())
        tail = conn._stderr_ring.tail()
        assert conn.is_alive
        assert "secret-value" not in tail
        assert "<redacted>" in tail
        assert "已截断" in tail
        assert len(tail.encode("utf-8")) <= acp_mod._STDERR_MAX_BYTES + 100
        assert len(tail.splitlines()) <= acp_mod._STDERR_MAX_LINES + 1
    finally:
        conn.terminate()


def test_expected_terminate_wakes_pending_without_unexpected_error(fake_agent):
    fake_agent.scenarios = [{"resume_timeout": True}]
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    errors: list[RuntimeError] = []

    def run():
        try:
            conn.request("session/resume", {}, timeout=5)
        except RuntimeError as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert _wait_for(lambda: bool(conn._pending))
    conn.terminate()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors and "连接已关闭" in str(errors[0])
    assert "意外" not in str(errors[0])


# ----------------------------------------------------------------------
# stopReason 语义（2026-1120-0946 计划 D1/D5）
# ----------------------------------------------------------------------
def _install_usage_spy(provider, monkeypatch) -> list[bool]:
    """替换轮末用量收尾：记录是否执行，并产出一条 usage chunk 作为
    「收尾先于抛错」的可观察证据（真实 wire 读取在假后端下为空）。"""
    collected: list[bool] = []

    def spy(*_args, **_kwargs):
        collected.append(True)
        yield ka.Chunk("usage", "")

    monkeypatch.setattr(provider, "_collect_turn_end_usage", spy)
    return collected


@pytest.mark.parametrize("prompt_result", [
    {"stopReason": "end_turn"},
    {},  # 缺失 stopReason：旧 CLI 兼容，按正常完成
    {"stopReason": "future_reason"},  # 未知枚举：fail-open 不卡 busy
])
def test_non_error_stop_reasons_complete_with_usage(
        fake_agent, kimi_provider, monkeypatch, prompt_result):
    fake_agent.scenarios = [{"prompt_result": prompt_result}]
    collected = _install_usage_spy(kimi_provider, monkeypatch)
    chunks = _chat(kimi_provider, "hello")
    assert collected == [True]
    assert any(chunk.kind == "usage" for chunk in chunks)


def test_cancelled_raises_after_usage_is_collected(
        fake_agent, kimi_provider, monkeypatch):
    fake_agent.scenarios = [{"prompt_result": {"stopReason": "cancelled"}}]
    collected = _install_usage_spy(kimi_provider, monkeypatch)
    generator = kimi_provider.chat([{"role": "user", "content": "hello"}])
    chunks = []
    with pytest.raises(acp_mod.TurnCancelled):
        while True:
            chunks.append(next(generator))
    assert collected == [True]
    assert any(chunk.kind == "usage" for chunk in chunks)


@pytest.mark.parametrize(
    "stop_reason", ["max_tokens", "max_turn_requests", "refusal"])
def test_non_end_turn_failure_keeps_usage_and_names_reason(
        fake_agent, kimi_provider, monkeypatch, stop_reason):
    fake_agent.scenarios = [{"prompt_result": {"stopReason": stop_reason}}]
    collected = _install_usage_spy(kimi_provider, monkeypatch)
    generator = kimi_provider.chat([{"role": "user", "content": "hello"}])
    chunks = []
    with pytest.raises(RuntimeError) as raised:
        while True:
            chunks.append(next(generator))
    assert stop_reason in str(raised.value)
    assert collected == [True]
    assert any(chunk.kind == "usage" for chunk in chunks)


def test_error_frame_takes_priority_over_stop_reason(
        fake_agent, kimi_provider, monkeypatch):
    fake_agent.scenarios = [{
        "prompt_error": {"code": -32000, "message": "boom"},
        "prompt_result": {"stopReason": "cancelled"},
    }]
    collected = _install_usage_spy(kimi_provider, monkeypatch)
    with pytest.raises(RuntimeError, match="对话失败") as raised:
        _chat(kimi_provider, "hello")
    assert not isinstance(raised.value, acp_mod.TurnCancelled)
    assert collected == []  # error 帧先抛，不进入收尾分类
