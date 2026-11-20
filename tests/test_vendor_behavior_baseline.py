"""厂商行为基线：帧 → 载荷 → 卡片，以及三家 provider 的轮次终态。

T0 迁移前置基线（2026-1120-2115 T0 子计划）。断言只锁用户可观察语义与
稳定载荷键，不锁私有辅助函数；任何目录/映射拆分都必须先保持本文件全绿。
"""
import queue

import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.cards import (
    BashCard,
    CardColors,
    DiffCard,
    MediaReadCard,
    OpenStateMap,
    QuestionCard,
    SubagentCard,
    TextOutputCard,
    TodoListCard,
    make_tool_card,
)
from ide.llm.base import UsageStats
from ide.llm.providers.acp import TurnAborted, TurnCancelled
from ide.llm.providers.acp.updates import map_session_update
from ide.llm.providers.dsh import provider as dsh_acp
from ide.llm.providers.kimi import provider as kimi_acp
from ide.llm.providers.local import provider as local_acp

SESSION_ID = "session-baseline"

#: 每个用例使用独立的 toolCallId / sessionId，避免跨用例账本串联；
#: 不 import 协议层私有账本做手工清理（拆分后账本将变为实例状态）。
_TID = iter(f"baseline-{index}" for index in range(1, 100))


def _tid() -> str:
    return next(_TID)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def colors():
    return CardColors(
        reasoning_fg="#8899aa",
        tool_fg="#aabbcc",
        tool_error_fg="#ff5555",
        user_bubble_bg="#223344",
        tool_output_bg="#112233",
        link_fg="#66ccff",
        diff_add_fg="#55cc55",
        diff_del_fg="#cc5555",
    )


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
    """provider 的 next_update 线格式（与 map_session_update 入参区分）。"""
    return "update", update_frame


def tool_call(update: dict) -> dict:
    update = {"sessionUpdate": "tool_call", **update}
    return frame(update)


def tool_update(update: dict) -> dict:
    update = {"sessionUpdate": "tool_call_update", **update}
    return frame(update)


# ----------------------------------------------------------------------
# Kimi：camelCase + 显式 kind + wire 旁路
# ----------------------------------------------------------------------
def test_kimi_text_reasoning_usage_are_stable_chunks():
    assert map_session_update(text_frame("正文")).kind == "text"
    assert map_session_update(text_frame("正文")).text == "正文"
    assert map_session_update(text_frame("思考", thought=True)).kind == "reasoning"
    usage = map_session_update(usage_frame(42, 200))
    assert usage.kind == "usage"
    assert usage.usage == UsageStats(used=42, size=200, cost=None)


def test_kimi_edit_frames_reach_diff_card(qapp, colors):
    tid = _tid()
    first = map_session_update(tool_call({
        "toolCallId": tid, "title": "Edit", "kind": "edit",
        "status": "pending", "content": [],
    }))
    assert first.kind == "tool_call"
    payload = first.payload
    assert payload["tool_kind"] == "edit"
    card = make_tool_card(colors, OpenStateMap(), payload)
    assert isinstance(card, DiffCard)

    update = map_session_update(tool_update({
        "toolCallId": tid, "title": "Editing src/a.py", "kind": "edit",
        "status": "in_progress",
        "rawInput": {"path": "src/a.py", "old_string": "旧", "new_string": "新"},
        "locations": [{"path": "src/a.py"}],
        "content": [{
            "type": "diff", "path": "src/a.py",
            "oldText": "旧", "newText": "新",
        }],
    }))
    assert update.kind == "tool_call_update"
    assert update.payload["diff_stat"] == "+1 −1"
    assert "old_string: 旧" in update.payload["input_detail"]
    assert update.payload["summary"] == "src/a.py"

    card.apply_update(update.payload)
    badge = card._diff_badge.text()
    assert "+1" in badge and "−1" in badge
    subtitle = card._subtitle_label.text()
    assert "a.py" in subtitle and "src/" in subtitle
    assert "旧" in card._copy_source and "新" in card._copy_source


def test_kimi_write_without_diff_item_synthesizes_diff(qapp, colors):
    tid = _tid()
    first = map_session_update(tool_call({
        "toolCallId": tid, "title": "Write", "kind": "edit",
        "status": "pending", "content": [],
    }))
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, DiffCard)

    update = map_session_update(tool_update({
        "toolCallId": tid, "title": "Writing .temp/a.txt", "kind": "edit",
        "status": "in_progress",
        "rawInput": {"path": ".temp/a.txt", "content": "第一行\n第二行\n第三行\n"},
        "content": [{
            "type": "content",
            "content": {"type": "text", "text": '{"path":".temp/a.txt"}'},
        }],
    }))
    assert update.payload["diff_stat"] == "+3 −0"
    assert len(update.payload["diff_hunks"]) == 1
    card.apply_update(update.payload)
    assert "第一行" in card._copy_source


def test_kimi_read_media_carries_path_and_output_image(qapp, colors):
    tid = _tid()
    first = map_session_update(tool_call({
        "toolCallId": tid, "title": "ReadMediaFile", "kind": "other",
        "status": "pending", "content": [],
    }))
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, MediaReadCard)

    update = map_session_update(tool_update({
        "toolCallId": tid, "kind": "other", "status": "in_progress",
        "rawInput": {"path": ".temp/pic.png"},
        "content": [{
            "type": "content",
            "content": {"type": "text", "text": '<image path=".temp/pic.png">'},
        }],
    }))
    assert update.payload["media_path"] == ".temp/pic.png"
    assert update.payload["images"] == ["file://.temp/pic.png"]
    card.apply_update(update.payload)
    assert ".temp/pic.png" in card._input.toPlainText()


def test_kimi_todo_and_question_payloads_reach_cards(qapp, colors):
    todo_tid = _tid()
    todo_first = map_session_update(tool_call({
        "toolCallId": todo_tid, "title": "TodoList", "kind": "other",
        "status": "pending", "content": [],
    }))
    todo_card = make_tool_card(colors, OpenStateMap(), todo_first.payload)
    assert isinstance(todo_card, TodoListCard)

    todo_update = map_session_update(tool_update({
        "toolCallId": todo_tid, "kind": "other", "status": "in_progress",
        "rawInput": {"todos": [
            {"status": "done", "title": "整理取证脚本"},
            {"status": "in_progress", "title": "运行真机会话"},
            {"status": "pending", "title": "核对帧产物"},
        ]},
    }))
    entries = todo_update.payload["todos"]
    assert [entry["content"] for entry in entries] == [
        "整理取证脚本", "运行真机会话", "核对帧产物"]
    assert [entry["status"] for entry in entries] == [
        "completed", "in_progress", "pending"]
    assert all(entry["changed"] for entry in entries)
    todo_card.apply_update(todo_update.payload)
    assert todo_card._todo_layout.count() == 3
    assert "1/3 项完成" in todo_card._subtitle_label.text()

    ask_tid = _tid()
    ask_first = map_session_update(tool_call({
        "toolCallId": ask_tid, "title": "AskUserQuestion", "kind": "other",
        "status": "pending", "content": [],
    }))
    ask_card = make_tool_card(colors, OpenStateMap(), ask_first.payload)
    assert isinstance(ask_card, QuestionCard)

    ask_update = map_session_update(tool_update({
        "toolCallId": ask_tid, "kind": "other", "status": "in_progress",
        "rawInput": {"questions": [{
            "header": "颜色",
            "question": "你希望问候语是什么颜色？",
            "options": [{"label": "红色"}, {"label": "蓝色"}],
            "multi_select": False,
        }]},
    }))
    assert ask_update.payload["questions"] == ["你希望问候语是什么颜色？"]
    assert ask_update.payload["question_options"][0]["options"][0]["label"] == "红色"
    ask_card.apply_update(ask_update.payload)
    assert ask_card._qa_rows[0][0] == "你希望问候语是什么颜色？"


def test_kimi_agent_sidecar_nests_child_card(qapp, colors):
    parent_tid = _tid()
    parent = map_session_update(tool_call({
        "toolCallId": parent_tid, "title": "Agent", "kind": "think",
        "status": "pending", "content": [],
    }))
    parent_card = make_tool_card(colors, OpenStateMap(), parent.payload)
    assert isinstance(parent_card, SubagentCard)
    assert parent.payload["is_subagent"] is True

    child = kimi_acp._synthesize_wire_call(
        {"name": "Bash", "toolCallId": "wire-1",
         "args": {"command": "ls -la"}},
        parent_tid,
    )
    child_chunk = map_session_update(frame(child))
    assert child_chunk.payload["parent_tool_call_id"] == parent_tid
    assert child_chunk.payload["title"] == "Bash"
    assert child_chunk.payload["tool_kind"] == "execute"
    assert child_chunk.payload["command"] == "ls -la"
    parent_card.append_child_call(child_chunk.payload)
    assert parent_tid + "/wire-1" in parent_card._tool_cards

    result = kimi_acp._synthesize_wire_result(
        {"toolCallId": "wire-1",
         "result": {"output": "README.md"}},
        parent_tid,
        "execute",
    )
    result_chunk = map_session_update(frame(result))
    assert result_chunk.payload["status"] == "completed"
    assert result_chunk.payload["parent_tool_call_id"] == parent_tid
    parent_card.append_child_update(result_chunk.payload)


# ----------------------------------------------------------------------
# DSH：kind="other" + snake_case 入参 + 工具名兜底
# ----------------------------------------------------------------------
def test_dsh_bash_read_write_and_kind_fallback(qapp, colors):
    bash_tid = _tid()
    bash_first = map_session_update(tool_call({
        "toolCallId": bash_tid, "title": "bash", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert bash_first.payload["tool_kind"] == "execute"
    bash_update = map_session_update(tool_update({
        "toolCallId": bash_tid, "status": "in_progress",
        "rawInput": {"command": "ls -la"},
    }))
    assert bash_update.payload["command"] == "ls -la"
    bash_card = make_tool_card(colors, OpenStateMap(), bash_first.payload)
    assert isinstance(bash_card, BashCard)
    bash_card.apply_update(bash_update.payload)
    assert bash_card._command == "ls -la"
    assert "$ ls -la" in bash_card._copy_text()

    read_tid = _tid()
    read_first = map_session_update(tool_call({
        "toolCallId": read_tid, "title": "read", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert read_first.payload["tool_kind"] == "read"
    read_update = map_session_update(tool_update({
        "toolCallId": read_tid, "status": "completed",
        "rawInput": {"file_path": "src/a.py"},
        "rawOutput": {"output": "第一行\n第二行\n"},
    }))
    assert "file_path: src/a.py" in read_update.payload["input_detail"]
    assert read_update.payload["output"] == "第一行\n第二行"
    read_card = make_tool_card(colors, OpenStateMap(), read_first.payload)
    assert isinstance(read_card, TextOutputCard)
    read_card.apply_update(read_update.payload)
    assert "第一行" in read_card._output.toPlainText()

    write_tid = _tid()
    write_first = map_session_update(tool_call({
        "toolCallId": write_tid, "title": "write", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert write_first.payload["tool_kind"] == "edit"
    write_update = map_session_update(tool_update({
        "toolCallId": write_tid, "status": "in_progress",
        "rawInput": {"file_path": "src/new.py", "content": "a\nb\n"},
    }))
    assert write_update.payload["diff_stat"] == "+2 −0"
    assert "file_path: src/new.py" in write_update.payload["input_detail"]
    write_card = make_tool_card(colors, OpenStateMap(), write_first.payload)
    assert isinstance(write_card, DiffCard)
    write_card.apply_update(write_update.payload)
    assert "a" in write_card._copy_source and "b" in write_card._copy_source


def test_dsh_read_image_todo_and_subagent_fallbacks(qapp, colors):
    image_tid = _tid()
    image_first = map_session_update(tool_call({
        "toolCallId": image_tid, "title": "read_image", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert image_first.payload["tool_kind"] == "read"
    image_update = map_session_update(tool_update({
        "toolCallId": image_tid, "status": "in_progress",
        "rawInput": {"file_path": ".temp/dsh.png"},
    }))
    assert image_update.payload["media_path"] == ".temp/dsh.png"
    image_card = make_tool_card(colors, OpenStateMap(), image_first.payload)
    assert isinstance(image_card, MediaReadCard)
    image_card.apply_update(image_update.payload)

    todo_tid = _tid()
    todo_first = map_session_update(tool_call({
        "toolCallId": todo_tid, "title": "todo_write", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert todo_first.payload["tool_kind"] == "other"
    todo_update = map_session_update(tool_update({
        "toolCallId": todo_tid, "status": "in_progress",
        "rawInput": {"todos": [
            {"content": "实现", "status": "in_progress"},
            {"content": "验证", "status": "pending"},
        ]},
    }))
    assert [entry["content"] for entry in todo_update.payload["todos"]] == [
        "实现", "验证"]
    todo_card = make_tool_card(colors, OpenStateMap(), todo_first.payload)
    assert isinstance(todo_card, TodoListCard)
    todo_card.apply_update(todo_update.payload)
    assert todo_card._todo_layout.count() == 2

    agent_tid = _tid()
    agent_first = map_session_update(tool_call({
        "toolCallId": agent_tid, "title": "subagent", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert agent_first.payload["tool_kind"] == "think"
    assert agent_first.payload["is_subagent"] is True
    agent_card = make_tool_card(colors, OpenStateMap(), agent_first.payload)
    assert isinstance(agent_card, SubagentCard)


def test_dsh_unknown_tool_falls_back_to_mcp_card(qapp, colors):
    tid = _tid()
    chunk = map_session_update(tool_call({
        "toolCallId": tid, "title": "未收录工具", "kind": "other",
        "status": "pending", "content": [],
    }))
    assert chunk.payload["tool_kind"] == "other"
    card = make_tool_card(colors, OpenStateMap(), chunk.payload)
    assert not isinstance(card, (BashCard, DiffCard, TextOutputCard))


# ----------------------------------------------------------------------
# Local：标准 ACP 帧 + provider 生命周期
# ----------------------------------------------------------------------
def test_local_standard_frames_are_stable(qapp, colors):
    assert map_session_update(text_frame("本地正文")).text == "本地正文"
    assert map_session_update(text_frame("本地思考", thought=True)).kind == "reasoning"
    usage = map_session_update(usage_frame(7, 70))
    assert usage.usage == UsageStats(used=7, size=70, cost=None)

    tid = _tid()
    first = map_session_update(tool_call({
        "toolCallId": tid, "title": "Bash", "kind": "execute",
        "status": "pending", "rawInput": {"command": "pwd"},
    }))
    assert first.payload["command"] == "pwd"
    card = make_tool_card(colors, OpenStateMap(), first.payload)
    assert isinstance(card, BashCard)
    update = map_session_update(tool_update({
        "toolCallId": tid, "kind": "execute", "status": "completed",
        "rawOutput": {"output": "/workspace\n"},
    }))
    assert update.payload["output"] == "/workspace"
    card.apply_update(update.payload)
    assert "/workspace" in card._output.toPlainText()


# ----------------------------------------------------------------------
# provider 轮次终态：DSH / Local（Kimi 生命周期已有独立基线文件）
# ----------------------------------------------------------------------
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
    monkeypatch.setattr(kimi_acp.KimiAcpLLM, "_ensure_context_sizes",
                        lambda self: None)
    monkeypatch.setattr(kimi_acp, "_session_dir_of", lambda session_id: None)
    monkeypatch.setattr(kimi_acp, "_WIRE_USAGE_WAIT_S", 0.0)
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
