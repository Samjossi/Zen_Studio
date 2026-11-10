"""主题注册表、共享资源包与旧配置回退回归。"""
import json
from pathlib import Path

import pytest

import ide.gui.settings as settings_mod
from ide.gui import theme as theme_mod
from ide.gui.settings import DEFAULT_THEME, KEY_FONT_SIZE, KEY_THEME
from ide.gui.theme import (
    CHAT_PACK,
    CHROME_PACK,
    GIT_GRAPH_PACK,
    GIT_STATUS_PACK,
    PACK_KEYS,
    QSS_TOKEN_KEYS,
    SYNTAX_PACK,
    TERMINAL_PACK,
    get_theme_palette,
    is_valid,
    list_available_themes,
    render_theme,
)

_LIGHT_THEMES = ("cloud", "wheat", "sky", "mint")
_REMOVED_THEMES = ("graphite", "midnight")
_SHARED_PACKS = {
    "syntax": SYNTAX_PACK,
    "terminal": TERMINAL_PACK,
    "chrome": CHROME_PACK,
    "git_status": GIT_STATUS_PACK,
    "chat": CHAT_PACK,
    "git_graph": GIT_GRAPH_PACK,
}
_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_theme"


@pytest.fixture()
def isolated_settings(monkeypatch):
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", _TEMP_DIR / "settings.json")
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    return _TEMP_DIR / "settings.json"


def test_only_four_light_themes_are_registered():
    assert list_available_themes() == list(_LIGHT_THEMES)
    assert all(is_valid(theme) for theme in _LIGHT_THEMES)
    assert all(not is_valid(theme) for theme in _REMOVED_THEMES)


@pytest.mark.parametrize("removed", _REMOVED_THEMES)
def test_removed_theme_queries_fall_back_to_default(removed):
    assert get_theme_palette(removed) is get_theme_palette(DEFAULT_THEME)
    assert render_theme(removed) == render_theme(DEFAULT_THEME)


@pytest.mark.parametrize("removed", _REMOVED_THEMES)
def test_removed_theme_settings_fall_back_without_rewriting(removed, isolated_settings):
    isolated_settings.write_text(
        json.dumps({KEY_THEME: removed, KEY_FONT_SIZE: 14}), encoding="utf-8")

    settings = theme_mod.load_settings()

    assert settings[KEY_THEME] == DEFAULT_THEME
    assert settings[KEY_FONT_SIZE] == 14
    persisted = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert persisted == {KEY_THEME: removed, KEY_FONT_SIZE: 14}


def test_light_themes_render_and_share_single_resource_packs():
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        assert set(QSS_TOKEN_KEYS + PACK_KEYS) <= palette.keys()
        qss = render_theme(name)
        assert all(f"${token}" not in qss for token in QSS_TOKEN_KEYS)
        for key, pack in _SHARED_PACKS.items():
            assert palette[key] is pack
