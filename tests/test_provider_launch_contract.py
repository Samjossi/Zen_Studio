"""T1 启动契约：厂商 provider 组装完整 argv，连接/初始化/会话参数不变。

目录迁移后连接层不再拼 `"acp"`；本文件锁定「谁组装启动命令」与
initialize/session/new 的稳定参数，防止 T2 语义拆分时把启动合同改掉。
"""
import os
import queue

import pytest

from ide.llm.providers.acp.connection import AcpConnection
from ide.llm.providers.dsh import provider as dsh_provider
from ide.llm.providers.dsh import session_log as dsh_session_log
from ide.llm.providers.local import provider as local_provider

WORKSPACE = os.path.abspath(".")


class FakeConnection:
    def __init__(self):
        self.requests = []
        self.is_alive = True
        self.generation = 1
        self.failure_reason = None
        self._events = queue.Queue()
        self._events.put(("response", {"id": 1, "result": {"stopReason": "end_turn"}}))

    def set_permission_handler(self, handler):
        pass

    def set_terminal_handler(self, handler):
        pass

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "initialize":
            return {
                "agentInfo": {"name": "fake", "version": "1"},
                "agentCapabilities": {
                    "sessionCapabilities": {},
                    "promptCapabilities": {"image": False},
                },
            }
        if method == "session/new":
            return {"sessionId": "session-launch"}
        return {}

    def begin_turn(self, method, params):
        return 1

    def next_update(self, timeout=None):
        return self._events.get_nowait()

    def end_turn(self):
        pass

    def purge_updates(self):
        pass

    def terminate(self):
        self.is_alive = False

    def log_diagnostics(self, event, method=None):
        pass

    def cancel_turn(self, session_id):
        return True

    def abort_turn(self):
        pass


def test_acp_connection_passes_full_argv_to_popen(monkeypatch):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        raise OSError("fake spawn failure")

    monkeypatch.setattr(
        "ide.llm.providers.acp.connection.subprocess.Popen", fake_popen)

    with pytest.raises(RuntimeError, match="启动失败"):
        AcpConnection(["npx", "@deepseek-ai/dsh", "--profile", "acp"],
                      "/workspace", "dsh acp")

    assert captured["argv"] == [
        "npx", "@deepseek-ai/dsh", "--profile", "acp"]
    assert captured["cwd"] == "/workspace"


@pytest.mark.parametrize(
    "module,cls_name,label,extra_patch",
    [
        (dsh_provider, "DshAcpLLM", "dsh acp", "dsh"),
        (local_provider, "LocalAcpLLM", "local acp", "local"),
    ],
)
def test_provider_builds_own_argv_and_keeps_session_params(
        monkeypatch, module, cls_name, label, extra_patch):
    calls = []
    connections = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(module, "_find_bin", lambda: "/fake/agent")
    monkeypatch.setattr(module, "AcpConnection", factory)
    if extra_patch == "dsh":
        monkeypatch.setattr(dsh_session_log, "turn_consumption", lambda *args: (None, 0))

    provider = getattr(module, cls_name)(workspace_root=WORKSPACE)
    list(provider.chat([{"role": "user", "content": "hello"}]))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (["/fake/agent", "acp"], WORKSPACE, label)
    assert kwargs == {}

    conn = connections[0]
    method, initialize = conn.requests[0]
    assert method == "initialize"
    assert initialize["protocolVersion"] == 1
    assert initialize["clientCapabilities"]["fs"] == {
        "readTextFile": False, "writeTextFile": False}
    assert initialize["clientCapabilities"]["terminal"] is False
    assert initialize["clientInfo"]["name"] == "zen-studio"

    method, session_new = conn.requests[1]
    assert method == "session/new"
    assert session_new == {"cwd": WORKSPACE, "mcpServers": []}

    provider.close()
    assert not conn.is_alive
