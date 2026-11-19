"""DSH 图片卡片对齐 Kimi 测试（计划 2026-1119-0938）。

覆盖：
- read_image 帧端到端：MediaReadCard 分派命中、media_path 装填
  （file_path 三态并联 + 无扩展名工具名兜底）、出参 ACP image 块 →
  images 载荷、snake_case 入参区净行与路径摘要；
- DshAcpLLM.supports_images_override：initialize 实测三态
  （image=true / false / 结构缺失）；
- ChatPanel._supports_images：override 优先、注册表静态位兜底；
- ChatPanel._apply_image_capability：能力位未变幂等短路
  （残留附件提示不随轮末刷新重复弹）、能力位跃迁再应用。

全部帧级/实例级打桩，不起真实子进程；模块级账本逐用例清空防串扰。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ide.gui.panels.chat.cards import (
    CardColors,
    MediaReadCard,
    OpenStateMap,
    make_tool_card,
)
from ide.gui.panels.chat.panel import ChatPanel
from ide.llm.providers import acp
from ide.llm.providers.acp import map_session_update
from ide.llm.providers import dsh_acp
from ide.llm.providers.dsh_acp import DshAcpLLM


@pytest.fixture(autouse=True)
def _clean_ledgers():
    acp._derived_tool_kinds.clear()
    acp._input_detail_seen.clear()
    yield
    acp._derived_tool_kinds.clear()
    acp._input_detail_seen.clear()


def _map(update: dict):
    return map_session_update({"params": {"update": update, "sessionId": "test"}})


def _app():
    return QApplication.instance() or QApplication([])


def _colors() -> CardColors:
    return CardColors("#888888", "#777777", "#ff0000", "#eeeeee",
                      "#dddddd", "#0000ff", "#00aa00", "#aa0000")


# ----------------------------------------------------------------------
# read_image 帧端到端（分派 / media_path / images 载荷 / 入参区）
# ----------------------------------------------------------------------

def test_read_image_dispatches_media_read_card():
    _app()
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "r1",
                  "title": "read_image", "kind": "other",
                  "status": "in_progress",
                  "rawInput": {"file_path": "assets/logo/app.png"}})
    payload = chunk.payload or {}
    assert payload.get("media_path") == "assets/logo/app.png", \
        "DSH file_path 键装入略缩图数据源"
    assert payload.get("summary") == "assets/logo/app.png", \
        "read 分支路径摘要兼容 file_path"
    assert payload.get("input_detail") == "file_path: assets/logo/app.png", \
        "入参区净行（非 JSON 兜底）"
    card = make_tool_card(_colors(), OpenStateMap(), payload)
    assert isinstance(card, MediaReadCard), \
        "read_image 与 readmediafile 同落 MediaReadCard"


def test_read_image_extensionless_title_fallback():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "r2",
                  "title": "read_image", "kind": "other",
                  "status": "in_progress",
                  "rawInput": {"file_path": "测试文件夹/pic"}})
    assert (chunk.payload or {}).get("media_path") == "测试文件夹/pic", \
        "无扩展名图片按工具名兜底装填"

    other = _map({"sessionUpdate": "tool_call", "toolCallId": "r2b",
                  "title": "read", "kind": "other", "status": "in_progress",
                  "rawInput": {"file_path": "笔记"}})
    assert "media_path" not in (other.payload or {}), \
        "非看图工具的无扩展名路径不装填"


def test_read_image_output_image_block_to_images_payload():
    _map({"sessionUpdate": "tool_call", "toolCallId": "r3",
          "title": "read_image", "kind": "other", "status": "in_progress",
          "rawInput": {"file_path": "a.png"}})
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "r3",
                   "status": "completed",
                   "content": [{"type": "content",
                                "content": {"type": "image", "data": "QUJD",
                                            "mimeType": "image/png"}}]})
    payload = update.payload or {}
    assert payload.get("images") == ["data:image/png;base64,QUJD"], \
        "DSH 出参 image 块进 images 载荷（McpCard 渲染通道数据源）"


def test_read_image_update_backfill_media_path():
    _map({"sessionUpdate": "tool_call", "toolCallId": "r4",
          "title": "read_image", "kind": "other", "status": "in_progress"})
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "r4",
                   "status": "in_progress",
                   "rawInput": {"file_path": "b.webp"}})
    assert (update.payload or {}).get("media_path") == "b.webp", \
        "kind 缺失的迟到帧按推导 read 通道回填 file_path"


# ----------------------------------------------------------------------
# snake_case 入参键（read/edit 双形态并联）
# ----------------------------------------------------------------------

def test_snake_case_read_input_detail():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "s1",
                  "title": "read", "kind": "other", "status": "in_progress",
                  "rawInput": {"file_path": "src/x.py", "offset": 10,
                               "limit": 50}})
    assert (chunk.payload or {}).get("input_detail") == \
        "file_path: src/x.py\noffset: 10\nlimit: 50"


def test_snake_case_edit_input_detail():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "s2",
                  "title": "edit", "kind": "other", "status": "in_progress",
                  "rawInput": {"file_path": "a.py", "old_string": "x=1",
                               "new_string": "x=2", "replace_all": False}})
    detail = (chunk.payload or {}).get("input_detail") or ""
    assert "file_path: a.py" in detail
    assert "old_string: x=1" in detail
    assert "new_string: x=2" in detail


# ----------------------------------------------------------------------
# DshAcpLLM.supports_images_override（initialize 实测三态）
# ----------------------------------------------------------------------

class _FakeConn:
    """桩 ACP 连接：initialize 返回预制结果，session/new 返回固定 id。"""

    def __init__(self, init_result: dict):
        self._init_result = init_result
        self.is_alive = True

    def set_permission_handler(self, _handler):
        pass

    def set_terminal_handler(self, _handler):
        pass

    def request(self, method, _params=None, timeout=None, **_kw):
        if method == "initialize":
            return self._init_result
        if method == "session/new":
            return {"sessionId": "fake-session"}
        raise RuntimeError(method)

    def terminate(self):
        self.is_alive = False


def _provider_with_init(monkeypatch, init_result: dict) -> DshAcpLLM:
    monkeypatch.setattr(dsh_acp, "_find_bin", lambda: "/fake/dsh")
    monkeypatch.setattr(dsh_acp, "AcpConnection",
                        lambda *_a, **_k: _FakeConn(init_result))
    provider = DshAcpLLM()
    provider._ensure_session()
    return provider


@pytest.mark.parametrize("image_value,expected", [
    (True, True),
    (False, False),
])
def test_supports_images_override_from_initialize(
        monkeypatch, image_value, expected):
    provider = _provider_with_init(monkeypatch, {
        "protocolVersion": 1,
        "agentCapabilities": {"promptCapabilities": {
            "image": image_value, "audio": False}},
    })
    try:
        assert provider.supports_images_override is expected
    finally:
        provider.close()


def test_supports_images_override_missing_caps(monkeypatch):
    # 结构缺失按无能力处理：安全降级方向（附件不发胜过乱发报错）
    provider = _provider_with_init(monkeypatch, {"protocolVersion": 1})
    try:
        assert provider.supports_images_override is False
    finally:
        provider.close()


# ----------------------------------------------------------------------
# ChatPanel 能力消费（override 优先 / 静态兜底 / 幂等刷新）
# ----------------------------------------------------------------------

def _panel_stub(provider):
    stub = SimpleNamespace(_llm_name="dsh-acp",
                           _providers={"dsh-acp": provider},
                           _images_applied=None)
    stub._supports_images = lambda: ChatPanel._supports_images(stub)
    return stub


def test_panel_supports_images_override_preferred():
    assert ChatPanel._supports_images(
        _panel_stub(SimpleNamespace(supports_images_override=True))) is True


def test_panel_supports_images_none_falls_back_to_spec():
    # override None（未建连）或无该属性：回退注册表静态位（dsh=False）
    assert ChatPanel._supports_images(
        _panel_stub(SimpleNamespace(supports_images_override=None))) is False
    assert ChatPanel._supports_images(
        _panel_stub(object())) is False
    assert ChatPanel._supports_images(
        SimpleNamespace(_llm_name="dsh-acp", _providers={})) is False


class _FakeInput:
    def __init__(self):
        self.enabled_calls = []

    def set_image_attachments_enabled(self, value):
        self.enabled_calls.append(value)


class _FakeAttachments:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _FakeOutput:
    def __init__(self):
        self.messages = []

    def append_message(self, role, text):
        self.messages.append((role, text))


def test_apply_image_capability_idempotent_per_value():
    provider = SimpleNamespace(supports_images_override=False)
    stub = _panel_stub(provider)
    stub.input = _FakeInput()
    stub.attachments = _FakeAttachments(1)  # 残留附件在场
    stub.output = _FakeOutput()

    ChatPanel._apply_image_capability(stub)
    assert stub.input.enabled_calls == [False]
    assert len(stub.output.messages) == 1, "残留附件提示弹一次"

    ChatPanel._apply_image_capability(stub)  # 轮末刷新：能力位未变
    assert stub.input.enabled_calls == [False], "能力位未变不重复应用"
    assert len(stub.output.messages) == 1, "提示不随轮末刷新重复弹"

    provider.supports_images_override = True  # 实测翻案：跃迁再应用
    ChatPanel._apply_image_capability(stub)
    assert stub.input.enabled_calls == [False, True]
    assert len(stub.output.messages) == 1
