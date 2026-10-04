"""接口按钮条件显隐测试（计划 2026-1004-2356 T3）。

接口级是「一家厂商多接入实现」的扩展位：当前各家均单接口，按钮点开
恒为单选菜单，故 ≤1 个时隐藏、≥2 个时自动恢复显示。判定按接口总数
（含「未检测到」不可用项——不可用状态本身是排错信息，不遮蔽）。

monkeypatch 伪造注册表数据源（vendor_groups / spec_of / vendor_of /
resolve_efforts），不起真实 CLI 子进程；offscreen 平台由 conftest 注入。
断言用 isHidden() 而非 isVisible()——后者依赖父部件链已 show，offscreen
下恒 False。
"""
import pytest
from PySide6.QtWidgets import QApplication

import gui.panels.chat.model_bar as model_bar_mod
from gui.panels.chat.model_bar import ModelBar
from llm.registry import BackendSpec


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _spec(name: str, vendor: str, vendor_label: str,
          available: bool = True) -> BackendSpec:
    return BackendSpec(
        name=name,
        label=f"{vendor_label} ACP",
        vendor=vendor,
        vendor_label=vendor_label,
        available=lambda: available,
        list_models=lambda: ["m1"],
        factory=lambda **_kw: None,
        efforts=("auto",),
        default_effort="auto",
    )


@pytest.fixture()
def fake_registry(monkeypatch):
    groups = {
        "solo": [_spec("solo-acp", "solo", "Solo")],
        "multi": [_spec("multi-a", "multi", "Multi"),
                  _spec("multi-b", "multi", "Multi")],
        "mixed": [_spec("mixed-a", "mixed", "Mixed"),
                  _spec("mixed-b", "mixed", "Mixed", available=False)],
    }
    specs = {spec.name: spec for group in groups.values() for spec in group}
    monkeypatch.setattr(model_bar_mod, "vendor_groups", lambda: groups)
    monkeypatch.setattr(model_bar_mod, "spec_of", specs.get)
    monkeypatch.setattr(model_bar_mod, "vendor_of",
                        lambda name: specs[name].vendor
                        if name in specs else None)
    monkeypatch.setattr(model_bar_mod, "resolve_efforts",
                        lambda spec, model: (spec.efforts, spec.default_effort))
    return groups


def test_single_interface_hidden(qapp, fake_registry):
    bar = ModelBar()
    assert bar.current_vendor() == "solo"
    assert bar._interface_button.isHidden()


def test_multi_interface_shown(qapp, fake_registry):
    bar = ModelBar()
    bar.set_selection("multi-a", None)
    assert bar.current_vendor() == "multi"
    assert not bar._interface_button.isHidden()


def test_unavailable_interface_still_counts(qapp, fake_registry):
    bar = ModelBar()
    bar.set_selection("mixed-a", None)
    assert bar.current_vendor() == "mixed"
    assert not bar._interface_button.isHidden()


def test_switch_back_hides_again(qapp, fake_registry):
    bar = ModelBar()
    bar.set_selection("multi-a", None)
    assert not bar._interface_button.isHidden()
    bar.set_selection("solo-acp", None)
    assert bar._interface_button.isHidden()
