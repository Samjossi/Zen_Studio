"""三家 provider 的轮次终态与 mapper 集成基线。

T0 基线按厂商归属改写后保留的 provider 级用例：正文/usage 经各自
mapper 上屏，错误与取消仍走各厂商原有通道。
"""
import queue

import pytest

from ide.llm.providers.acp import TurnAborted, TurnCancelled
from ide.llm.providers.dsh import provider as dsh_acp
from ide.llm.providers.kimi import provider as kimi_acp
from ide.llm.providers.kimi import usage as kimi_usage, wire as kimi_wire
from ide.llm.providers.local import provider as local_acp

SESSION_ID = "session-baseline"


def frame(update: dict, session_id: str = SESSION_ID) -> dict:
    return {
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


def text_frame(text: str, *, thought: bool = False) -> dict:
    return frame({
        "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
        "content": {"type": "text", "text": text},
    })


def usage_frame(used: int = 10, size: int = 100) -> dict:
    return frame({"sessionUpdate": "usage_update", "used": used, "size": size})


def update_event(update_frame: dict) -> tuple[str, dict]:
    return "update", update_frame


class FakeConnection:
    def __init__(self, events=(), *, session_error=None, init_result=None):
        self.events = queue.Queue()
        for event in events:
            self.events.put(event)
        self.session_error = session_error
        self.init_result = init_result or {
            "agentInfo": {"name": "fake", "version": "1"},
            "agentCapabilities": {"promptCapabilities": {"image": False}},
        }
        self.is_alive = True
        self.generation = 1
        self.requests = []
        self.prompts = []
        self.cancel_calls = 0
        self.abort_calls = 0
        self.terminate_calls = 0
        self.aborted = False

    def set_permission_handler(self, handler):
        pass

    def set_terminal_handler(self, handler):
        pass

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "initialize":
            return self.init_result
        if method == "session/new":
            if self.session_error:
                raise RuntimeError(self.session_error)
            return {"sessionId": SESSION_ID}
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
        return True

    def abort_turn(self):
        self.abort_calls += 1
        self.aborted = True

    def end_turn(self):
        pass

    def purge_updates(self):
        pass

    def terminate(self):
        self.terminate_calls += 1
        self.is_alive = False

    def log_diagnostics(self, event, method=None):
        pass


def _install_connection(monkeypatch, module, connection):
    monkeypatch.setattr(module, "_find_bin", lambda: "/fake/agent")
    monkeypatch.setattr(module, "AcpConnection", lambda *args, **kwargs: connection)


def _response(stop_reason="end_turn"):
    return "response", {"id": 1, "result": {"stopReason": stop_reason}}


def test_dsh_provider_text_usage_stop_reason_and_config_error(monkeypatch):
    conn = FakeConnection([
        update_event(text_frame("dsh 正文")), update_event(usage_frame(5, 50)),
        _response(),
    ])
    _install_connection(monkeypatch, dsh_acp, conn)
    monkeypatch.setattr(dsh_acp, "_turn_consumption", lambda *args: (None, 0))
    provider = dsh_acp.DshAcpLLM(workspace_root=".")
    chunks = list(provider.chat([{"role": "user", "content": "hello"}]))
    assert [(chunk.kind, chunk.text) for chunk in chunks] == [
        ("text", "dsh 正文"), ("usage", "")]
    assert chunks[1].usage.used == 5
    assert conn.prompts[0][1]["prompt"][0]["text"] == "hello"
    provider.close()
    assert conn.terminate_calls == 1

    failed_conn = FakeConnection([_response("error")])
    _install_connection(monkeypatch, dsh_acp, failed_conn)
    failed = dsh_acp.DshAcpLLM(workspace_root=".")
    with pytest.raises(RuntimeError, match="stopReason=error"):
        list(failed.chat([{"role": "user", "content": "hello"}]))
    failed.close()

    auth_conn = FakeConnection(session_error="401 unauthorized")
    _install_connection(monkeypatch, dsh_acp, auth_conn)
    auth = dsh_acp.DshAcpLLM(workspace_root=".")
    with pytest.raises(RuntimeError, match="dsh 凭据缺失"):
        list(auth.chat([{"role": "user", "content": "hello"}]))
    auth.close()


def test_dsh_provider_cancel_keeps_connection_alive(monkeypatch):
    conn = FakeConnection([update_event(text_frame("片段"))])
    _install_connection(monkeypatch, dsh_acp, conn)
    monkeypatch.setattr(dsh_acp, "_turn_consumption", lambda *args: (None, 0))
    provider = dsh_acp.DshAcpLLM(workspace_root=".")
    turn = provider.chat([{"role": "user", "content": "hello"}])
    assert next(turn).text == "片段"
    provider.cancel()
    with pytest.raises(TurnAborted):
        next(turn)
    assert conn.cancel_calls == 1 and conn.abort_calls == 1
    assert conn.terminate_calls == 0 and conn.is_alive
    provider.close()


def test_local_provider_text_usage_stop_reason_and_config_error(monkeypatch):
    conn = FakeConnection([
        update_event(text_frame("local 正文")), update_event(usage_frame(3, 30)),
        _response(),
    ])
    _install_connection(monkeypatch, local_acp, conn)
    provider = local_acp.LocalAcpLLM(workspace_root=".")
    chunks = list(provider.chat([{"role": "user", "content": "hello"}]))
    assert [(chunk.kind, chunk.text) for chunk in chunks] == [
        ("text", "local 正文"), ("usage", "")]
    assert chunks[1].usage.used == 3
    provider.close()
    assert conn.terminate_calls == 1

    failed_conn = FakeConnection([_response("error")])
    _install_connection(monkeypatch, local_acp, failed_conn)
    failed = local_acp.LocalAcpLLM(workspace_root=".")
    with pytest.raises(RuntimeError, match="Local 本轮响应失败"):
        list(failed.chat([{"role": "user", "content": "hello"}]))
    failed.close()

    auth_conn = FakeConnection(session_error="model not configured")
    _install_connection(monkeypatch, local_acp, auth_conn)
    auth = local_acp.LocalAcpLLM(workspace_root=".")
    with pytest.raises(RuntimeError, match="本地模型未加载"):
        list(auth.chat([{"role": "user", "content": "hello"}]))
    auth.close()


def test_local_provider_cancel_keeps_connection_alive(monkeypatch):
    conn = FakeConnection([update_event(text_frame("片段"))])
    _install_connection(monkeypatch, local_acp, conn)
    provider = local_acp.LocalAcpLLM(workspace_root=".")
    turn = provider.chat([{"role": "user", "content": "hello"}])
    assert next(turn).text == "片段"
    provider.cancel()
    with pytest.raises(TurnAborted):
        next(turn)
    assert conn.cancel_calls == 1 and conn.abort_calls == 1
    assert conn.terminate_calls == 0 and conn.is_alive
    provider.close()


def test_kimi_provider_text_usage_and_cancelled_stop(monkeypatch):
    conn = FakeConnection([
        update_event(text_frame("kimi 正文")), update_event(usage_frame(9, 90)),
        _response(),
    ])
    _install_connection(monkeypatch, kimi_acp, conn)
    monkeypatch.setattr(kimi_usage.UsageTracker, "ensure_context_sizes",
                        lambda self: None)
    monkeypatch.setattr(kimi_wire, "session_dir_of", lambda session_id: None)
    monkeypatch.setattr(kimi_usage, "WIRE_USAGE_WAIT_S", 0.0)
    provider = kimi_acp.KimiAcpLLM(workspace_root=".")
    chunks = list(provider.chat([{"role": "user", "content": "hello"}]))
    assert [chunk.text for chunk in chunks if chunk.kind == "text"] == ["kimi 正文"]
    usage_chunks = [chunk for chunk in chunks if chunk.kind == "usage"]
    assert usage_chunks and all(chunk.usage.used == 9 for chunk in usage_chunks)
    provider.close()
    assert conn.terminate_calls == 1

    cancelled_conn = FakeConnection([_response("cancelled")])
    _install_connection(monkeypatch, kimi_acp, cancelled_conn)
    cancelled = kimi_acp.KimiAcpLLM(workspace_root=".")
    with pytest.raises(TurnCancelled):
        list(cancelled.chat([{"role": "user", "content": "hello"}]))
    cancelled.close()


def _tool_call_frame(tid: str, title: str, kind: str, raw_input: dict) -> dict:
    return frame({
        "sessionUpdate": "tool_call", "toolCallId": tid, "title": title,
        "kind": kind, "status": "pending", "rawInput": raw_input,
    })


def _tool_update_frame(tid: str, output: str) -> dict:
    return frame({
        "sessionUpdate": "tool_call_update", "toolCallId": tid,
        "status": "completed", "rawOutput": {"output": output},
    })


_TOOL_LIFECYCLE_CASES = [
    # (标签, provider 模块, 类名, 工具名, 首帧 kind, rawInput, 期望 tool_kind)
    ("kimi", kimi_acp, "KimiAcpLLM", "ReadMediaFile", "other",
     {"path": "shot.png"}, "other"),
    ("dsh", dsh_acp, "DshAcpLLM", "read_image", "other",
     {"file_path": "shot.png"}, "read"),
    ("local", local_acp, "LocalAcpLLM", "Bash", "execute",
     {"command": "printf hi"}, "execute"),
]


@pytest.mark.parametrize(
    "label,module,class_name,title,kind,raw_input,want_kind",
    _TOOL_LIFECYCLE_CASES, ids=[case[0] for case in _TOOL_LIFECYCLE_CASES])
def test_vendor_tool_turn_stop_and_close(
        monkeypatch, label, module, class_name, title, kind, raw_input,
        want_kind):
    """三家「建立会话 → 工具帧 → 正文 → 自然停止 → 关闭」全链路。

    连接是内存假 agent，不产生真实子进程；这里锁定终止只发生一次、连接
    引用与实例账本随 close 清空，并用同一 toolCallId 的第二个实例确认
    账本没有跨实例串联。
    """
    tid = f"{label}-tool-tid"

    def _events():
        return [
            update_event(_tool_call_frame(tid, title, kind, raw_input)),
            update_event(_tool_update_frame(tid, "工具输出")),
            update_event(text_frame(f"{label} 正文")),
            _response(),
        ]

    if label == "kimi":
        monkeypatch.setattr(kimi_usage.UsageTracker, "ensure_context_sizes",
                            lambda self: None)
        monkeypatch.setattr(kimi_wire, "session_dir_of",
                            lambda session_id: None)
        monkeypatch.setattr(kimi_usage, "WIRE_USAGE_WAIT_S", 0.0)
    elif label == "dsh":
        monkeypatch.setattr(dsh_acp, "_turn_consumption",
                            lambda *args: (None, 0))

    conn = FakeConnection(_events())
    _install_connection(monkeypatch, module, conn)
    provider = getattr(module, class_name)(workspace_root=".")
    chunks = list(provider.chat([{"role": "user", "content": "hello"}]))

    assert [chunk.kind for chunk in chunks] == [
        "tool_call", "tool_call_update", "text"]
    assert chunks[0].payload["tool_call_id"] == tid
    assert chunks[0].payload["title"] == title
    assert chunks[0].payload["tool_kind"] == want_kind
    assert chunks[1].payload["status"] == "completed"
    assert chunks[2].text == f"{label} 正文"
    assert conn.session_error is None
    assert ("session/new", {"cwd": provider._cwd, "mcpServers": []}) in conn.requests
    assert provider._session_id == SESSION_ID

    provider.close()
    assert conn.terminate_calls == 1
    assert conn.is_alive is False
    assert provider._conn is None
    assert provider._session_id is None
    provider.close()  # 幂等：不得二次 terminate
    assert conn.terminate_calls == 1

    # 同一 toolCallId 的另一个实例必须重新建立自己的入参账本；若账本
    # 跨实例共享，第二张卡会缺失 input_detail。
    second_conn = FakeConnection(_events())
    _install_connection(monkeypatch, module, second_conn)
    second = getattr(module, class_name)(workspace_root=".")
    second_chunks = list(second.chat([{"role": "user", "content": "hello"}]))
    assert provider._updates is not second._updates
    assert second_chunks[0].payload["input_detail"] == \
        chunks[0].payload["input_detail"]
    second.close()
    assert second_conn.terminate_calls == 1
