"""Kimi ACP 退役后的设置迁移与单接口选择恢复。"""
import json
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from ide.gui import settings as settings_module
from ide.gui.panels.chat import model_bar as model_bar_module
from ide.gui.panels.chat.model_bar import ModelBar
from ide.gui.settings import (
    KEY_MODEL_BACKEND,
    KEY_MODEL_EFFORTS,
    KEY_MODEL_VERSIONS,
    load_settings,
)
from ide.llm.providers.kimi.spec import SPEC as KIMI_SPEC


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settings_file(monkeypatch, tmp_path, payload):
    path = tmp_path / "settings.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", path)
    return path


def test_missing_and_broken_settings_default_to_kap(monkeypatch, tmp_path):
    path = _settings_file(monkeypatch, tmp_path, None)
    assert load_settings()[KEY_MODEL_BACKEND] == "kimi-kap"
    assert not path.exists()

    path.write_text("{", encoding="utf-8")
    assert load_settings()[KEY_MODEL_BACKEND] == "kimi-kap"


def test_legacy_kimi_backend_and_memories_migrate_without_rewrite(
        monkeypatch, tmp_path):
    payload = {
        KEY_MODEL_BACKEND: "kimi-acp",
        KEY_MODEL_VERSIONS: {"kimi-acp": "old-model", "dsh-acp": "dsh-model"},
        KEY_MODEL_EFFORTS: {"kimi-acp": "max", "dsh-acp": "low"},
    }
    path = _settings_file(monkeypatch, tmp_path, payload)

    loaded = load_settings()

    assert loaded[KEY_MODEL_BACKEND] == "kimi-kap"
    assert loaded[KEY_MODEL_VERSIONS] == {
        "kimi-kap": "old-model", "dsh-acp": "dsh-model"}
    assert loaded[KEY_MODEL_EFFORTS] == {
        "kimi-kap": "max", "dsh-acp": "low"}
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_legacy_global_model_seed_is_migrated(monkeypatch, tmp_path):
    _settings_file(monkeypatch, tmp_path, {
        KEY_MODEL_BACKEND: "kimi-acp",
        "model_version": "legacy-global-model",
    })
    loaded = load_settings()
    assert loaded[KEY_MODEL_VERSIONS] == {"kimi-kap": "legacy-global-model"}


def test_existing_kap_memories_win_over_legacy_values(monkeypatch, tmp_path):
    _settings_file(monkeypatch, tmp_path, {
        KEY_MODEL_BACKEND: "kimi-acp",
        KEY_MODEL_VERSIONS: {"kimi-acp": "old", "kimi-kap": "new"},
        KEY_MODEL_EFFORTS: {"kimi-acp": "max", "kimi-kap": "low"},
    })
    loaded = load_settings()
    assert loaded[KEY_MODEL_VERSIONS] == {"kimi-kap": "new"}
    assert loaded[KEY_MODEL_EFFORTS] == {"kimi-kap": "low"}


@pytest.mark.parametrize("backend", ["dsh-acp", "local-acp", "unknown"])
def test_other_backend_values_are_not_rewritten(monkeypatch, tmp_path, backend):
    _settings_file(monkeypatch, tmp_path, {KEY_MODEL_BACKEND: backend})
    assert load_settings()[KEY_MODEL_BACKEND] == backend


def test_legacy_session_backend_falls_back_to_hidden_single_kap_interface(
        qapp, monkeypatch):
    spec = replace(
        KIMI_SPEC,
        available=lambda: True,
        list_models=lambda: ["kimi-code/test-model"],
        list_efforts=lambda: {},
        list_display_names=lambda: {},
    )
    monkeypatch.setattr(model_bar_module, "vendor_groups", lambda: {"kimi": [spec]})
    monkeypatch.setattr(
        model_bar_module, "vendor_of",
        lambda name: "kimi" if name == spec.name else None)
    monkeypatch.setattr(
        model_bar_module, "spec_of", lambda name: spec if name == spec.name else None)

    bar = ModelBar()
    bar.set_selection("kimi-acp", "kimi-code/test-model")
    bar.show()

    assert bar.current_backend() == "kimi-kap"
    assert bar.current_version() == "kimi-code/test-model"
    assert not bar._interface_button.isVisible()
    bar.close()
