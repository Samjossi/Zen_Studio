"""Kimi KAP 工作区加载：生产控件共享可用性与目录探测缓存。"""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from ide.gui import settings as settings_module
from ide.gui import settings_dialog as dialog_module
from ide.gui.panels.chat.panel import ChatPanel
from ide.llm import cache, registry
from ide.llm.providers.kimi import catalog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_kimi_cli(monkeypatch, tmp_path):
    cache.clear()
    monkeypatch.setattr(
        settings_module, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(catalog, "_find_bin", lambda: "/fake/kimi")
    calls = {"version": 0, "catalog": 0}
    payload = {
        "models": {
            "kimi-code/test-model": {
                "displayName": "Test Model",
                "supportEfforts": ["low", "high"],
                "defaultEffort": "high",
                "maxContextSize": 128000,
            },
        },
    }

    def fake_run(args, *pargs, **kwargs):
        command = list(args)
        if command == ["/fake/kimi", "--version"]:
            calls["version"] += 1
            return SimpleNamespace(stdout="2.7.2\n", stderr="")
        if command == ["/fake/kimi", "provider", "list", "--json"]:
            calls["catalog"] += 1
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        raise AssertionError(f"测试不得执行其他 CLI 命令：{command}")

    monkeypatch.setattr(catalog.subprocess, "run", fake_run)
    yield calls
    cache.clear()


def test_chat_panels_and_lazy_provider_share_cli_probes(qapp, fake_kimi_cli):
    panels = []
    try:
        panels.append(ChatPanel(None, None, "/fake/workspace"))
        assert fake_kimi_cli == {"version": 1, "catalog": 1}
        assert panels[0].model_bar.current_backend() == "kimi-kap"

        panels.extend(
            ChatPanel(None, None, "/fake/workspace") for _ in range(3))
        assert fake_kimi_cli == {"version": 1, "catalog": 1}

        provider = panels[0]._get_provider("kimi-kap")
        assert provider is not None
        assert provider._server._proc is None
        assert fake_kimi_cli == {"version": 1, "catalog": 1}
    finally:
        for panel in panels:
            panel.close()
            panel.deleteLater()
        qapp.processEvents()


def test_settings_model_page_reuses_process_caches(
        qapp, monkeypatch, fake_kimi_cli):
    monkeypatch.setattr(dialog_module, "_PAGE_REGISTRY", (
        ("AI 模型", "选择 AI 后台、接口与模型。",
         "_build_model_page", "_reload_model"),
    ))
    monkeypatch.setattr(
        dialog_module.SettingsDialog, "apply_font_size", lambda self: None)
    host = QWidget()
    host.chat_tabs = SimpleNamespace(
        current_backend=lambda: "kimi-kap",
        current_version=lambda: "kimi-code/test-model",
        is_busy=lambda: False,
    )
    dialog = dialog_module.SettingsDialog(host)
    try:
        dialog.show()
        qapp.processEvents()
        assert fake_kimi_cli == {"version": 1, "catalog": 1}

        dialog.hide()
        dialog.show()
        qapp.processEvents()
        assert fake_kimi_cli == {"version": 1, "catalog": 1}
    finally:
        dialog.close()
        dialog.deleteLater()
        host.deleteLater()
        qapp.processEvents()


def test_refresh_models_reprobes_only_target_interface(fake_kimi_cli):
    kimi_spec = registry.spec_of("kimi-kap")
    assert kimi_spec is not None
    assert kimi_spec.available() is True
    assert kimi_spec.list_models() == ["kimi-code/test-model"]
    assert fake_kimi_cli == {"version": 1, "catalog": 1}

    registry.refresh_models("dsh-acp")
    assert kimi_spec.available() is True
    assert kimi_spec.list_models() == ["kimi-code/test-model"]
    assert fake_kimi_cli == {"version": 1, "catalog": 1}

    registry.refresh_models("kimi-kap")
    assert kimi_spec.available() is True
    assert kimi_spec.list_models() == ["kimi-code/test-model"]
    assert fake_kimi_cli == {"version": 2, "catalog": 2}
