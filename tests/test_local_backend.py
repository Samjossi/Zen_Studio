"""本地模型（local-acp）注册、解封、旧配置回退与枚举兜底测试。

覆盖：
- 注册表精确保持 Kimi / DSH / Local 三家及既定顺序，Local 未封存；
- 四个已移除接口不再注册，旧持久化选择经 ModelBar/ChatTabs 回退到 Kimi，
  不改写旧配置，也不实例化已移除 provider；
- Local 不被通用归档守卫拦截，provider 可经可控 ACP 连接完成发送；
- 探测链、GGUF 模型枚举及失败兜底保持原行为。

GUI 与 provider 用例均由测试桩驱动，不依赖本机 CLI 安装状态。
"""
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import ide.gui.panels.chat.model_bar as model_bar_mod
import ide.gui.settings as settings_mod
from ide.gui.panels.chat.model_bar import ModelBar
from ide.gui.panels.chat.panel import ChatPanel
from ide.gui.panels.chat.tabs import ChatTabs
from ide.gui.settings import KEY_MODEL_BACKEND, KEY_MODEL_VERSIONS
from ide.llm.providers import local_acp
from ide.llm.registry import REGISTRY, VENDOR_LABELS, spec_of, vendor_groups, vendor_of

_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_local_backend"
_REMOVED_BACKENDS = ("reasonix-acp", "opencode-acp", "kilocode-acp", "dream-acp")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_settings(monkeypatch):
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _TEMP_DIR / "settings.json"
    settings_file.unlink(missing_ok=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    return settings_file


@pytest.fixture()
def fake_active_registry(monkeypatch):
    models = {
        "kimi-acp": ["kimi-test"],
        "dsh-acp": ["dsh-test"],
        "local-acp": ["local-test"],
    }
    specs = {
        name: replace(
            spec,
            available=lambda: True,
            list_models=lambda name=name: list(models[name]),
            list_efforts=None,
            list_display_names=None,
            factory=lambda **_kwargs: None,
        )
        for name, spec in REGISTRY.items()
    }
    groups: dict[str, list] = {}
    for spec in specs.values():
        groups.setdefault(spec.vendor, []).append(spec)
    monkeypatch.setattr(model_bar_mod, "vendor_groups", lambda: groups)
    monkeypatch.setattr(model_bar_mod, "spec_of", lambda name: specs.get(name))
    monkeypatch.setattr(
        model_bar_mod, "vendor_of",
        lambda name: specs[name].vendor if name in specs else None)
    return specs


def test_registry_contains_only_three_active_backends_in_order():
    assert tuple(REGISTRY) == ("kimi-acp", "dsh-acp", "local-acp")
    assert all(spec.archived is False for spec in REGISTRY.values())
    assert list(VENDOR_LABELS.values()) == ["Kimi", "DSH", "Local"]


def test_local_spec_registered_and_unarchived():
    spec = spec_of("local-acp")
    assert spec is not None
    assert spec.vendor == "local"
    assert spec.vendor_label == "Local"
    assert spec.archived is False
    assert spec.dev_note is None
    assert spec.supports_images is False
    assert spec.efforts == ("auto",)
    assert spec.default_effort == "auto"


def test_local_vendor_group_present():
    groups = vendor_groups()
    assert "local" in groups
    assert any(spec.name == "local-acp" for spec in groups["local"])


@pytest.mark.parametrize("name", _REMOVED_BACKENDS)
def test_removed_backends_are_unknown(name):
    assert spec_of(name) is None
    assert vendor_of(name) is None


def test_local_does_not_hit_archive_guard():
    panel = ChatPanel.__new__(ChatPanel)
    panel._llm_name = "local-acp"
    messages: list[tuple[str, str]] = []
    panel.output = SimpleNamespace(
        append_message=lambda role, text: messages.append((role, text)))

    assert panel._blocked_by_archive() is False
    assert messages == []


@pytest.mark.parametrize("removed", _REMOVED_BACKENDS)
def test_removed_saved_backend_falls_back_in_model_bar(
        qapp, fake_active_registry, removed):
    bar = ModelBar()
    bar.set_selection(removed, "legacy-model")

    assert bar.current_backend() == "kimi-acp"
    assert bar.current_version() == "kimi-test"
    bar.close()


@pytest.mark.parametrize("removed", _REMOVED_BACKENDS)
def test_tabs_restore_removed_backend_without_rewriting_or_spawning(
        qapp, isolated_settings, fake_active_registry, removed):
    isolated_settings.write_text(json.dumps({
        KEY_MODEL_BACKEND: removed,
        KEY_MODEL_VERSIONS: {removed: "legacy-model"},
    }), encoding="utf-8")

    tabs = ChatTabs(str(_TEMP_DIR))
    panel = tabs._tabs.currentWidget()

    assert tabs.current_backend() == "kimi-acp"
    assert panel._llm_name == "kimi-acp"
    assert panel._providers == {}
    assert panel._get_provider(removed) is None
    assert panel._providers == {}
    assert json.loads(isolated_settings.read_text(encoding="utf-8"))[
        KEY_MODEL_BACKEND] == removed
    tabs.close()


def test_find_bin_local_home_fallback(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "local-cli"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(local_acp.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCAL_HOME", str(tmp_path))
    assert local_acp._find_bin() == str(fake)
    assert local_acp.local_available() is True


def test_unavailable_when_bin_missing(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: None)
    assert local_acp.local_available() is False
    assert local_acp.list_local_models() == []


def test_list_models_parses_lines(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: "/fake/local")

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0,
            stdout="Qwen3.5-35B-A3B-MXFP4_MOE\n\nQwen3.5-9B-UD-Q4_K_XL\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert local_acp.list_local_models() == [
        "Qwen3.5-35B-A3B-MXFP4_MOE", "Qwen3.5-9B-UD-Q4_K_XL"]


def test_list_models_fallback_on_failure(monkeypatch):
    monkeypatch.setattr(local_acp, "_find_bin", lambda: "/fake/local")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="boom"))
    assert local_acp.list_local_models() == []

    def _raise(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert local_acp.list_local_models() == []


class _FakeConnection:
    def __init__(self):
        self.is_alive = True
        self.requests: list[tuple[str, dict]] = []
        self.turn: tuple[str, dict] | None = None
        self._updates = [
            ("update", {"params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "本地回复"},
            }}}),
            ("response", {"result": {"stopReason": "end_turn"}}),
        ]

    def request(self, method, params, timeout=30):
        self.requests.append((method, params))
        if method == "initialize":
            return {"agentInfo": {"name": "fake-local", "version": "1"}}
        if method == "session/new":
            return {"sessionId": "fake-session"}
        raise AssertionError(f"意外请求：{method}")

    def set_permission_handler(self, _handler):
        pass

    def set_terminal_handler(self, _handler):
        pass

    def purge_updates(self):
        pass

    def begin_turn(self, method, params):
        self.turn = (method, params)

    def next_update(self):
        return self._updates.pop(0)

    def end_turn(self):
        pass

    def terminate(self):
        self.is_alive = False


def test_local_provider_establishes_and_sends(monkeypatch):
    connections: list[_FakeConnection] = []
    monkeypatch.setattr(local_acp, "_find_bin", lambda: "/fake/local-cli")

    def _make_connection(*_args):
        connection = _FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(local_acp, "AcpConnection", _make_connection)
    llm = local_acp.LocalAcpLLM(workspace_root=str(_TEMP_DIR))
    chunks = list(llm.chat([{"role": "user", "content": "你好"}]))

    assert [chunk.text for chunk in chunks] == ["本地回复"]
    assert len(connections) == 1
    assert [method for method, _params in connections[0].requests] == [
        "initialize", "session/new"]
    assert connections[0].turn is not None
    assert connections[0].turn[0] == "session/prompt"
    assert connections[0].turn[1]["sessionId"] == "fake-session"
    llm.close()


def test_local_turn_error_has_actionable_fallback():
    with pytest.raises(RuntimeError, match="Local 本轮响应失败"):
        local_acp.LocalAcpLLM._raise_on_turn_error({"result": {"stopReason": "error"}})
