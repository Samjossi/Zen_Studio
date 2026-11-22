"""厂商设置契约：注册表只声明键名，provider 自行解释，通用面板不出现键名。"""
from dataclasses import dataclass
from typing import Callable

from ide.gui import settings as settings_module
from ide.gui.panels.chat import panel as panel_module
from ide.llm.base import apply_vendor_settings_to
from ide.llm.providers.dsh.provider import DshAcpLLM
from ide.llm.providers.local.provider import LocalAcpLLM
from ide.llm.registry import REGISTRY


def test_no_vendor_settings_declared():
    # 极简重写后 Kimi 无私有旁路开关；三家均不声明厂商设置键
    assert REGISTRY["kimi-acp"].vendor_settings == ()
    assert REGISTRY["dsh-acp"].vendor_settings == ()
    assert REGISTRY["local-acp"].vendor_settings == ()


def test_declared_vendor_keys_exist_in_default_settings():
    for spec in REGISTRY.values():
        for key in spec.vendor_settings:
            assert key in settings_module.DEFAULT_SETTINGS, (
                f"{spec.name} 声明的厂商设置键 {key!r} 不在公共设置默认值中")


def test_dsh_and_local_unknown_vendor_settings_change_nothing():
    for cls in (DshAcpLLM, LocalAcpLLM):
        llm = cls()
        before = llm.capabilities()
        apply_vendor_settings_to(llm, {"kimi_wire_sidecar": False})
        assert llm.capabilities() == before


@dataclass
class FakeSpec:
    available: Callable[[], bool]
    factory: Callable[..., object]
    vendor_settings: tuple[str, ...] = ()


class RecordingProvider:
    """只实现厂商设置入口的最小 provider，记录收到的映射。"""

    def __init__(self) -> None:
        self.received: dict[str, object] | None = None

    def apply_vendor_settings(self, settings) -> None:
        self.received = dict(settings)


class PanelStub:
    def __init__(self) -> None:
        self._providers: dict[str, object] = {}
        self._workspace_root = None
        self._terminal_bridge = None
        self._effort_explicit = None
        self.model_bar = type("ModelBarStub", (), {
            "current_version": staticmethod(lambda: None),
        })()
        self.image_refreshes = 0

    def _apply_image_capability(self) -> None:
        self.image_refreshes += 1


def _install_spec(monkeypatch, spec):
    monkeypatch.setattr(panel_module, "spec_of", lambda _name: spec)
    monkeypatch.setattr(panel_module, "load_settings", lambda: {
        "kimi_wire_sidecar": False,
        "theme": "cloud",
        "unrelated_key": "noise",
    })


def test_panel_injects_only_declared_settings(monkeypatch):
    provider = RecordingProvider()
    _install_spec(
        monkeypatch,
        FakeSpec(available=lambda: True, factory=lambda **_kw: provider,
                 vendor_settings=("kimi_wire_sidecar",)))
    panel = PanelStub()

    assert panel_module.ChatPanel._get_provider(panel, "kimi-acp") is provider
    assert provider.received == {"kimi_wire_sidecar": False}
    assert panel.image_refreshes == 1  # provider 创建后刷新能力位


def test_panel_skips_injection_without_declared_settings(monkeypatch):
    provider = RecordingProvider()
    _install_spec(
        monkeypatch,
        FakeSpec(available=lambda: True, factory=lambda **_kw: provider))
    panel = PanelStub()

    assert panel_module.ChatPanel._get_provider(panel, "dsh-acp") is provider
    assert provider.received is None  # 无声明：完全不下发
    assert panel.image_refreshes == 1


def test_vendor_settings_helper_is_noop_for_plain_object():
    class BareStub:
        pass

    apply_vendor_settings_to(BareStub(), {"any": 1})  # 不抛异常即通过
