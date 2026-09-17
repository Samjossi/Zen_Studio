"""kilocode 后端封存行为测试（计划 2026-0917-0646 T5）。

覆盖：
- 注册表层：kilocode-acp 项 archived=True、dev_note 非空、菜单分组名
  含「已封存」；其余接口 archived 默认为 False（封存不影响成熟后端）；
- panel 层：_blocked_by_archive 对封存接口拦停并追加「系统」消息，
  对成熟接口放行；_on_send 直发路径在封存接口上不实例化 provider
  （不为禁用后端拉起 agent 子进程）。

panel 用例经 __new__ 裸实例 + 属性桩驱动，不构造 Qt 控件树，offscreen
平台下无 QApplication 依赖。
"""
from types import SimpleNamespace

from gui.panels.chat.panel import ChatPanel
from llm.registry import REGISTRY, spec_of


def _bare_panel(backend: str) -> tuple[ChatPanel, list[tuple[str, str]]]:
    """裸 ChatPanel 实例 + 输出消息捕获表（(角色, 文本)）。"""
    panel = ChatPanel.__new__(ChatPanel)
    panel._llm_name = backend
    panel._busy = False
    messages: list[tuple[str, str]] = []
    panel.output = SimpleNamespace(
        append_message=lambda role, text: messages.append((role, text)))
    return panel, messages


def test_kilocode_spec_is_archived():
    spec = spec_of("kilocode-acp")
    assert spec is not None
    assert spec.archived is True
    assert spec.dev_note and "封存" in spec.dev_note
    assert "已封存" in spec.vendor_label


def test_other_backends_not_archived():
    for name, spec in REGISTRY.items():
        if name != "kilocode-acp":
            assert spec.archived is False, f"{name} 不应被封存"


def test_blocked_by_archive_intercepts_with_note():
    panel, messages = _bare_panel("kilocode-acp")
    assert panel._blocked_by_archive() is True
    assert messages == [("系统", spec_of("kilocode-acp").dev_note)]


def test_blocked_by_archive_passes_mature_backend():
    panel, messages = _bare_panel("kimi-acp")
    assert panel._blocked_by_archive() is False
    assert messages == []


def test_on_send_archived_never_spawns_provider():
    panel, messages = _bare_panel("kilocode-acp")

    def _fail(_name: str) -> None:
        raise AssertionError("封存后端不得实例化 provider（不起子进程）")

    panel._get_provider = _fail
    panel._on_send("你好")
    assert any("封存" in text for _role, text in messages)
