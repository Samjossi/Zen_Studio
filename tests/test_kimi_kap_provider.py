"""KAP provider 轮次契约：prompt 绑定、通知轮隔离、取消与审批映射。

用假 transport 驱动，不启动 kimi web；真实链路冒烟在 `.temp/` 证据里。
"""
import queue
import time

import pytest

from ide.llm.providers.acp import TurnCancelled
from ide.llm.providers.kimi.kap import provider as kap_provider
from ide.llm.providers.kimi.kap.provider import KimiKapLLM
from ide.llm.providers.kimi.kap.transport import KapEndpoint, KapError

SID = "session-kap-test"


class FakeWs:
    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.sent: list[dict] = []
        self.alive = True

    def connect(self) -> None:
        pass

    def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        if payload.get("type") == "subscribe":
            self.q.put({
                "type": "response",
                "request_id": payload["request_id"],
                "code": 0,
            })

    def next_message(self, timeout=None):
        return self.q.get(timeout=timeout)

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.alive = False

    def feed(self, *messages: dict) -> None:
        for message in messages:
            self.q.put(message)


class FakeHttp:
    def __init__(self, transport: "FakeTransport") -> None:
        self.transport = transport
        self.prompts: list[dict] = []
        self.profiles: list[dict] = []
        self.cancels: list[dict] = []
        self.interactions: list[dict] = []

    def get(self, path: str, timeout=None) -> dict:
        if path == "/api/v1/models":
            return {"items": [{
                "model": "kimi-code/kimi-for-coding",
                "max_context_size": 1000,
            }]}
        if path == "/api/v1/workspaces":
            return {"items": []}
        if path == f"/api/v1/sessions/{SID}":
            return {"id": SID}
        raise KapError("not found", code=40401)

    def post(self, path: str, body: dict | None = None, timeout=None) -> dict:
        if path == "/api/v1/sessions":
            return {"id": SID}
        if path.endswith("/prompts"):
            self.prompts.append(body or {})
            prompt_id = (body or {}).get("prompt_id", "")
            return self.transport.prompt_script(prompt_id, self.transport.ws)
        if path.endswith("/profile"):
            self.profiles.append(body or {})
            return {}
        if ":cancel" in path:
            self.cancels.append(body or {})
            if self.transport.on_cancel is not None:
                self.transport.on_cancel(self.transport.ws)
            return {"cancelled": True}
        if "/interactions/" in path:
            self.interactions.append(body or {})
            return {}
        return {}


class FakeTransport:
    def __init__(self, prompt_script, on_cancel=None) -> None:
        self.endpoint = KapEndpoint(
            origin="http://127.0.0.1:1",
            ws_url="ws://127.0.0.1:1/api/v1/ws",
            token="test-token",
        )
        self.ws = FakeWs()
        self.http = FakeHttp(self)
        self.prompt_script = prompt_script
        self.on_cancel = on_cancel

    def make_http(self, endpoint):
        return self.http

    def make_ws(self, ws_url, token):
        return self.ws


class FakeServer:
    def __init__(self, endpoint: KapEndpoint) -> None:
        self.endpoint = endpoint
        self.closed = False

    def ensure(self):
        return self.endpoint

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return True


def _provider(monkeypatch, transport: FakeTransport):
    monkeypatch.setattr(kap_provider, "KapHttp", transport.make_http)
    monkeypatch.setattr(kap_provider, "KapWebSocket", transport.make_ws)
    server = FakeServer(transport.endpoint)
    llm = KimiKapLLM(
        model="kimi-code/kimi-for-coding",
        workspace_root=".",
        server=server,
    )
    return llm, server


def _turn_started(turn_id: str, ordinal: int) -> dict:
    return {"type": "turn.started", "turn": {
        "turn_id": turn_id, "ordinal": ordinal, "kind": "agent",
        "status": "running"}}


def _user_message(turn_id: str, prompt_id: str) -> dict:
    return {"type": "item.ended", "turn_id": turn_id, "item": {
        "type": "user_message", "item_id": "um-1",
        "prompt_ids": [prompt_id], "text": "hi"}}


def _assistant_delta(turn_id: str, text: str) -> list[dict]:
    return [
        {"type": "item.started", "turn_id": turn_id, "item": {
            "type": "assistant_message", "item_id": "am-1", "text": ""}},
        {"type": "item.delta", "turn_id": turn_id, "item_id": "am-1",
         "field": "text", "delta": text},
    ]


def _turn_ended(turn_id: str, ordinal: int, outcome="completed",
                usage=None) -> dict:
    turn = {"turn_id": turn_id, "ordinal": ordinal, "status": "ended",
            "outcome": outcome}
    if usage is not None:
        turn["usage"] = usage
    return {"type": "turn.ended", "turn": turn}


def test_chat_binds_user_turn_and_emits_consumption(monkeypatch):
    def script(prompt_id, ws):
        ws.feed(
            _turn_started("t1", 0),
            _user_message("t1", prompt_id),
            *_assistant_delta("t1", "OK"),
            _turn_ended("t1", 0, usage={
                "input_other": 10, "input_cache_read": 5,
                "input_cache_creation": 0, "output": 2}),
            {"type": "context", "context_tokens": 100,
             "max_context_tokens": 1000},
        )
        return {"user_message_id": "um-1", "prompt_id": prompt_id,
                "status": "running"}

    transport = FakeTransport(script)
    llm, _server = _provider(monkeypatch, transport)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "hi"}]))
        assert "".join(chunk.text for chunk in chunks if chunk.kind == "text") == "OK"
        consumption = [chunk.consumption for chunk in chunks if chunk.consumption]
        assert consumption and consumption[0].total == 17
        time.sleep(0.2)  # 轮末 context 终值由空闲 pump 补写
        assert llm.poll_usage().used == 100
        assert llm.poll_usage().size == 1000
    finally:
        llm.close()


def test_queued_prompt_ignores_foreign_notification_turn(monkeypatch):
    def script(prompt_id, ws):
        ws.feed(
            _turn_started("tn", 0),
            {"type": "item.ended", "turn_id": "tn", "item": {
                "type": "notification", "item_id": "n1",
                "notification_type": "task.completed", "title": "done"}},
            *_assistant_delta("tn", "NOTIFY"),
            _turn_ended("tn", 0),
            _turn_started("tu", 1),
            _user_message("tu", prompt_id),
            *_assistant_delta("tu", "OK"),
            _turn_ended("tu", 1),
        )
        return {"user_message_id": "um-1", "prompt_id": prompt_id,
                "status": "queued"}

    transport = FakeTransport(script)
    llm, _server = _provider(monkeypatch, transport)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "hi"}]))
        text = "".join(chunk.text for chunk in chunks if chunk.kind == "text")
        assert text == "OK"
        assert "NOTIFY" not in text
    finally:
        llm.close()


def test_unbound_notification_tail_does_not_end_user_turn(monkeypatch):
    """通知轮的 started/notification item 在空闲期被丢弃，只看到正文与 ended。

    旧实现会把这种无 user_message 的轮次按本轮收尾；现在必须忽略，等真正
    命中 prompt_id 的追问轮。
    """
    def script(prompt_id, ws):
        ws.feed(
            {"type": "item.started", "turn_id": "tn", "item": {
                "type": "reasoning", "item_id": "r-notify", "text": ""}},
            {"type": "item.delta", "turn_id": "tn", "item_id": "r-notify",
             "field": "text", "delta": "NOTIFY"},
            _turn_ended("tn", 1),
            _turn_started("tu", 2),
            _user_message("tu", prompt_id),
            *_assistant_delta("tu", "OK"),
            _turn_ended("tu", 2),
        )
        return {"user_message_id": "um-1", "prompt_id": prompt_id,
                "status": "queued"}

    transport = FakeTransport(script)
    llm, _server = _provider(monkeypatch, transport)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "hi"}]))
        text = "".join(chunk.text for chunk in chunks if chunk.kind == "text")
        assert text == "OK"
    finally:
        llm.close()


def test_stale_turn_started_with_lower_ordinal_does_not_rebind(monkeypatch):
    def script(prompt_id, ws):
        ws.feed(
            _turn_started("tu", 2),
            _user_message("tu", prompt_id),
            _turn_started("stale", 1),
            *_assistant_delta("tu", "OK"),
            _turn_ended("tu", 2),
        )
        return {"user_message_id": "um-1", "prompt_id": prompt_id,
                "status": "running"}

    transport = FakeTransport(script)
    llm, _server = _provider(monkeypatch, transport)
    try:
        chunks = list(llm.chat([{"role": "user", "content": "hi"}]))
        text = "".join(chunk.text for chunk in chunks if chunk.kind == "text")
        assert text == "OK"
    finally:
        llm.close()


def test_cancel_maps_to_turn_cancelled(monkeypatch):
    def script(prompt_id, ws):
        ws.feed(
            _turn_started("t1", 0),
            _user_message("t1", prompt_id),
            *_assistant_delta("t1", "半"),
        )
        return {"user_message_id": "um-1", "prompt_id": prompt_id,
                "status": "running"}

    def on_cancel(ws):
        ws.feed(_turn_ended("t1", 0, outcome="interrupted"))

    transport = FakeTransport(script, on_cancel=on_cancel)
    llm, _server = _provider(monkeypatch, transport)
    try:
        gen = llm.chat([{"role": "user", "content": "hi"}])
        assert next(gen).text == "半"
        llm.cancel()
        with pytest.raises(TurnCancelled):
            next(gen)
        assert transport.http.cancels
        assert transport.http.cancels[0]["include_background"] is False
    finally:
        llm.close()


def test_approval_body_maps_gui_option_to_kap_decision(monkeypatch):
    transport = FakeTransport(lambda *_args: {})
    llm, _server = _provider(monkeypatch, transport)
    seen = []
    llm.set_permission_handler(lambda request: seen.append(request) or "approved_session")
    try:
        body = llm._approval_body({
            "tool_name": "Bash",
            "action": "Running: echo hi",
            "tool_input_display": {"command": "echo hi"},
        }, "tool-9")
        assert body == {"kind": "approval", "decision": "approved",
                        "scope": "session"}
        request = seen[0]
        assert request.kind == "permission"
        assert request.params["toolCall"]["kind"] == "execute"
        assert request.params["toolCall"]["rawInput"]["command"] == "echo hi"
        assert request.params["toolCall"]["toolCallId"] == "tool-9"
        assert [option["kind"] for option in request.params["options"]] == [
            "allow_always", "allow_once", "reject_once"]
    finally:
        llm.close()


def test_question_body_maps_selected_option_id(monkeypatch):
    transport = FakeTransport(lambda *_args: {})
    llm, _server = _provider(monkeypatch, transport)
    llm.set_permission_handler(lambda request: "opt_1")
    try:
        body = llm._question_body({"questions": [{
            "id": "q_0",
            "question": "选哪个？",
            "options": [
                {"id": "opt_0", "label": "A"},
                {"id": "opt_1", "label": "B"},
            ],
        }]}, "tool-9")
        assert body == {"kind": "question", "answers": {
            "q_0": {"kind": "single", "option_id": "opt_1"}}}
    finally:
        llm.close()


def test_multi_select_question_answers_multi(monkeypatch):
    transport = FakeTransport(lambda *_args: {})
    llm, _server = _provider(monkeypatch, transport)
    llm.set_permission_handler(lambda request: "opt_1")
    try:
        body = llm._question_body({"questions": [{
            "id": "q_0", "question": "多选？", "multi_select": True,
            "options": [{"id": "opt_0", "label": "A"},
                        {"id": "opt_1", "label": "B"}],
        }]}, "tool-9")
        assert body == {"kind": "question", "answers": {
            "q_0": {"kind": "multi", "option_ids": ["opt_1"]}}}
    finally:
        llm.close()


def test_missing_question_handler_dismisses_instead_of_blocking(monkeypatch):
    transport = FakeTransport(lambda *_args: {})
    llm, _server = _provider(monkeypatch, transport)
    try:
        body = llm._question_body({"questions": [{
            "id": "q_0", "question": "选哪个？",
            "options": [{"id": "opt_0", "label": "A"}]}]}, "tool-9")
        assert body == {"kind": "question", "dismissed": True}
    finally:
        llm.close()
