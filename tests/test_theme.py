"""主题注册表、每主题资源包（去共享化 + 审美键/灰键主题适配）与旧配置回退回归。"""
import json
import re
from pathlib import Path

import pytest
from pygments.token import Token
from PySide6.QtGui import QColor

import ide.gui.settings as settings_mod
from ide.gui import theme as theme_mod
from ide.gui.settings import DEFAULT_THEME, KEY_FONT_SIZE, KEY_THEME
from ide.gui.theme import (
    PACK_KEYS,
    QSS_TOKEN_KEYS,
    ChatPack,
    ChromePack,
    GitGraphPack,
    GitStatusPack,
    TerminalPack,
    get_theme_palette,
    is_valid,
    list_available_themes,
    render_theme,
    window_border_override,
)

_LIGHT_THEMES = ("cloud", "wheat", "sky", "mint")
_REMOVED_THEMES = ("graphite", "midnight")
_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_theme"

#: 六包键集合 schema（terminal/chrome/git_status/chat/git_graph 与 theme.py
#: 的 TypedDict 定义同源派生；syntax 为 Pygments token 集合，无 TypedDict）
_PACK_KEY_SETS = {
    "syntax": frozenset({
        Token.Keyword, Token.Keyword.Namespace, Token.Name.Builtin,
        Token.Name.Function, Token.Name.Class, Token.Name.Decorator,
        Token.String, Token.Number, Token.Comment, Token.Operator,
        Token.Generic.Heading, Token.Generic.Subheading,
        Token.Generic.Strong, Token.Generic.Emph, Token.Error,
    }),
    "terminal": frozenset(TerminalPack.__annotations__),
    "chrome": frozenset(ChromePack.__annotations__),
    "git_status": frozenset(GitStatusPack.__annotations__),
    "chat": frozenset(ChatPack.__annotations__),
    "git_graph": frozenset(GitGraphPack.__annotations__),
}

#: 旧共享六包基线（去共享化前的模块级常量值）：维持键逐值保留断言的数据
#: 来源。蓝键（_BLUE_KEYS）的旧值（如 modified 天蓝 #1e88e5）不参与相等
#: 断言——绝迹性由 test_old_shared_blue_values_absent_everywhere 守护。
#: Namespace/String/Number/Error 四处大写值已按现行包值归一小写（同色同值，
#: 大写使「包值 in html」型断言假阴性）。
_BASELINE_PACKS = {
    "syntax": {
        Token.Keyword: {"color": "#0000AF", "bold": True},
        Token.Keyword.Namespace: {"color": "#7a3db8", "bold": True},
        Token.Name.Builtin: {"color": "#7a3db8"},
        Token.Name.Function: {"color": "#803080"},
        Token.Name.Class: {"color": "#2050A0", "bold": True},
        Token.Name.Decorator: {"color": "#806000"},
        Token.String: {"color": "#007a1c"},
        Token.Number: {"color": "#a0522d"},
        Token.Comment: {"color": "#888888", "italic": True},
        Token.Operator: {"color": "#333333"},
        Token.Generic.Heading: {"color": "#2050A0", "bold": True},
        Token.Generic.Subheading: {"color": "#2050A0", "bold": True},
        Token.Generic.Strong: {"bold": True},
        Token.Generic.Emph: {"italic": True},
        Token.Error: {"color": "#cc0000"},
    },
    "terminal": {
        "default_fg": "#1a1a1a", "default_bg": "#ffffff",
        "black": "#000000", "red": "#c41a16", "green": "#007a1c",
        "brown": "#a05000", "blue": "#0451a5", "magenta": "#a02c91",
        "cyan": "#168396", "white": "#767676",
        "brightblack": "#555555", "brightred": "#e5484d",
        "brightgreen": "#18a058", "brightbrown": "#c26a00",
        "brightblue": "#1a6fd4", "brightmagenta": "#c044ae",
        "brightcyan": "#00a7b5", "brightwhite": "#000000",
        "find_bg": "#37ffdc64", "find_cur": "#6effb400",
    },
    "chrome": {
        "line_number_fg": "#999999", "line_number_bg": "#f5f5f5",
        "current_line_bg": "#eef4fb",
        "find_bg": "#fff3bf", "find_cur": "#ffd43b",
    },
    "git_status": {
        "modified": "#1e88e5",
        "untracked": "#1f8a3d",
        "deleted": "#c0392b",
        "ignored": "#a8a8a8",
        "conflict": "#d40000",
    },
    "git_graph": {
        "head": "#a02c91",
        "branch": "#1f8a3d",
        "tag": "#a05000",
        "remote": "#168396",
        "hash_fg": "#767676",
    },
    "chat": {
        "reasoning_fg": "#888888",
        "usage_hot_fg": "#c0392b",
        "tool_fg": "#6f6f6f",
        "tool_error_fg": "#c0392b",
        "timeline_read_fg": "#006ab1",
        "timeline_write_fg": "#0090f1",
        "timeline_tool_fg": "#168396",
        "user_bubble_bg": "#ededf0",
        "tool_output_bg": "#f5f5f7",
        "diff_add_fg": "#3a8437",
        "diff_del_fg": "#ed4831",
        "diff_add_bg": "#e6ffec",
        "diff_del_bg": "#ffebe9",
        "diff_hunk_fg": "#1d4ed8",
        "diff_hunk_bg": "#ddf4ff",
    },
}

#: 蓝键集合（去共享化按主题色相归位的键；git_graph 无——remote 青非蓝，
#: D1 口径全主题保留 #168396）
_BLUE_KEYS = {
    "syntax": frozenset({
        Token.Keyword, Token.Name.Class,
        Token.Generic.Heading, Token.Generic.Subheading,
    }),
    "terminal": frozenset({"blue", "brightblue"}),
    "chrome": frozenset({"current_line_bg"}),
    "git_status": frozenset({"modified"}),
    "chat": frozenset({
        "timeline_read_fg", "timeline_write_fg",
        "diff_hunk_fg", "diff_hunk_bg",
    }),
    "git_graph": frozenset(),
}

#: 维持键集合（选项 3 口径：语义/功能色四主题互相同值且等于旧共享基线）：
#: terminal 非蓝全槽、chrome find_* 黄系查找色、git_status 三状态色、
#: git_graph 徽标四色、chat diff_* 四键与 timeline_tool_fg / usage_hot_fg /
#: tool_error_fg、syntax Error 红与 Generic.Strong/Emph（无颜色属性）
_MAINTAINED_KEYS = {
    "syntax": frozenset({
        Token.Generic.Strong, Token.Generic.Emph, Token.Error,
    }),
    "terminal": frozenset(TerminalPack.__annotations__) - {"blue", "brightblue"},
    "chrome": frozenset({"find_bg", "find_cur"}),
    "git_status": frozenset({"untracked", "deleted", "conflict"}),
    "chat": frozenset({
        "diff_add_fg", "diff_del_fg", "diff_add_bg", "diff_del_bg",
        "timeline_tool_fg", "usage_hot_fg", "tool_error_fg",
    }),
    "git_graph": frozenset({"head", "branch", "tag", "remote"}),
}

#: §5.1/§5.2 终值表（非蓝共享色审美键适配计划；cloud 行 = 旧共享基线，
#: D1 拍板锚定不动）：语法七枚审美键 + chrome 行号对/code_bg + chat 四灰键
#: + git_status ignored + git_graph hash_fg。sky/mint 的 Comment/reasoning_fg
# 与 sky hash_fg 为对比度地板同族压暗微调（实施记录走查节）。
_THEME_ADAPTED = {
    "cloud": {
        ("syntax", Token.Keyword.Namespace): "#7a3db8",
        ("syntax", Token.Name.Builtin): "#7a3db8",
        ("syntax", Token.Name.Function): "#803080",
        ("syntax", Token.Name.Decorator): "#806000",
        ("syntax", Token.String): "#007a1c",
        ("syntax", Token.Number): "#a0522d",
        ("syntax", Token.Comment): "#888888",
        ("syntax", Token.Operator): "#333333",
        ("chrome", "line_number_fg"): "#999999",
        ("chrome", "line_number_bg"): "#f5f5f5",
        ("chrome", "code_bg"): "#f5f5f5",
        ("chat", "reasoning_fg"): "#888888",
        ("chat", "tool_fg"): "#6f6f6f",
        ("chat", "user_bubble_bg"): "#ededf0",
        ("chat", "tool_output_bg"): "#f5f5f7",
        ("git_status", "ignored"): "#a8a8a8",
        ("git_graph", "hash_fg"): "#767676",
    },
    "wheat": {
        ("syntax", Token.Keyword.Namespace): "#8a4b8f",
        ("syntax", Token.Name.Builtin): "#8a4b8f",
        ("syntax", Token.Name.Function): "#9c4a6e",
        ("syntax", Token.Name.Decorator): "#8a6a1a",
        ("syntax", Token.String): "#5c7a1e",
        ("syntax", Token.Number): "#96521f",
        ("syntax", Token.Comment): "#8c8578",
        ("syntax", Token.Operator): "#3a352c",
        ("chrome", "line_number_fg"): "#9a938a",
        ("chrome", "line_number_bg"): "#f5f1e9",
        ("chrome", "code_bg"): "#f5f5f5",
        ("chat", "reasoning_fg"): "#8c8578",
        ("chat", "tool_fg"): "#6e685c",
        ("chat", "user_bubble_bg"): "#f0ebe0",
        ("chat", "tool_output_bg"): "#f7f4ec",
        ("git_status", "ignored"): "#a8a194",
        ("git_graph", "hash_fg"): "#7a746a",
    },
    "sky": {
        ("syntax", Token.Keyword.Namespace): "#7448b0",
        ("syntax", Token.Name.Builtin): "#7448b0",
        ("syntax", Token.Name.Function): "#7a3a8c",
        ("syntax", Token.Name.Decorator): "#7a6a1e",
        ("syntax", Token.String): "#0b7a30",
        ("syntax", Token.Number): "#96683c",
        ("syntax", Token.Comment): "#7a8794",
        ("syntax", Token.Operator): "#333a42",
        ("chrome", "line_number_fg"): "#8a96a2",
        ("chrome", "line_number_bg"): "#eff4f8",
        ("chrome", "code_bg"): "#f5f5f5",
        ("chat", "reasoning_fg"): "#7a8794",
        ("chat", "tool_fg"): "#5e6b78",
        ("chat", "user_bubble_bg"): "#e8eef4",
        ("chat", "tool_output_bg"): "#f0f5f9",
        ("git_status", "ignored"): "#98a2ac",
        ("git_graph", "hash_fg"): "#68727d",
    },
    "mint": {
        ("syntax", Token.Keyword.Namespace): "#7440a8",
        ("syntax", Token.Name.Builtin): "#7440a8",
        ("syntax", Token.Name.Function): "#7e3a78",
        ("syntax", Token.Name.Decorator): "#6e6a1a",
        ("syntax", Token.String): "#0b7a38",
        ("syntax", Token.Number): "#96683c",
        ("syntax", Token.Comment): "#7a8a7b",
        ("syntax", Token.Operator): "#333833",
        ("chrome", "line_number_fg"): "#8c9a8e",
        ("chrome", "line_number_bg"): "#eff5f0",
        ("chrome", "code_bg"): "#f5f5f5",
        ("chat", "reasoning_fg"): "#7a8a7b",
        ("chat", "tool_fg"): "#5e6e60",
        ("chat", "user_bubble_bg"): "#e6f0e8",
        ("chat", "tool_output_bg"): "#f0f6f1",
        ("git_status", "ignored"): "#9aa89c",
        ("git_graph", "hash_fg"): "#68736b",
    },
}

#: 正文级文字键（§5.3 在 #ffffff 底上对比度 ≥ 4.5）：语法七键 + chat tool_fg
_BODY_TEXT_TOKENS = (
    Token.Keyword.Namespace, Token.Name.Builtin, Token.Name.Function,
    Token.Name.Decorator, Token.String, Token.Number, Token.Operator,
)

#: §5.3 终值表（含表外三键：Name.Class/Generic.Heading/Generic.Subheading
#: 取本主题 Keyword 同族较浅档，渲染走查定稿后锁定于此）
_EXPECTED_BLUE_VALUES = {
    "cloud": {
        ("syntax", Token.Keyword): "#0a5cc4",
        ("syntax", Token.Name.Class): "#4a8ad4",
        ("syntax", Token.Generic.Heading): "#4a8ad4",
        ("syntax", Token.Generic.Subheading): "#4a8ad4",
        ("terminal", "blue"): "#075cb8",
        ("terminal", "brightblue"): "#2e86e8",
        ("chrome", "current_line_bg"): "#eaf1fb",
        ("git_status", "modified"): "#0765d4",
        ("chat", "timeline_read_fg"): "#0765d4",
        ("chat", "timeline_write_fg"): "#3b8fe8",
        ("chat", "diff_hunk_fg"): "#0a5cc4",
        ("chat", "diff_hunk_bg"): "#e2eefa",
    },
    "wheat": {
        ("syntax", Token.Keyword): "#9a5410",
        ("syntax", Token.Name.Class): "#b5772a",
        ("syntax", Token.Generic.Heading): "#b5772a",
        ("syntax", Token.Generic.Subheading): "#b5772a",
        ("terminal", "blue"): "#8c5a10",
        ("terminal", "brightblue"): "#c07818",
        ("chrome", "current_line_bg"): "#f8f0e4",
        ("git_status", "modified"): "#406da0",
        ("chat", "timeline_read_fg"): "#b85e14",
        ("chat", "timeline_write_fg"): "#d08a3e",
        ("chat", "diff_hunk_fg"): "#9a5410",
        ("chat", "diff_hunk_bg"): "#f3e8d8",
    },
    "sky": {
        ("syntax", Token.Keyword): "#0369a1",
        ("syntax", Token.Name.Class): "#3f96c8",
        ("syntax", Token.Generic.Heading): "#3f96c8",
        ("syntax", Token.Generic.Subheading): "#3f96c8",
        ("terminal", "blue"): "#0369a1",
        ("terminal", "brightblue"): "#0ea5e9",
        ("chrome", "current_line_bg"): "#e6f3fa",
        ("git_status", "modified"): "#0284c7",
        ("chat", "timeline_read_fg"): "#0284c7",
        ("chat", "timeline_write_fg"): "#38bdf8",
        ("chat", "diff_hunk_fg"): "#0369a1",
        ("chat", "diff_hunk_bg"): "#ddf2fc",
    },
    "mint": {
        ("syntax", Token.Keyword): "#0f6e3f",
        ("syntax", Token.Name.Class): "#3d9060",
        ("syntax", Token.Generic.Heading): "#3d9060",
        ("syntax", Token.Generic.Subheading): "#3d9060",
        ("terminal", "blue"): "#0e5f53",
        ("terminal", "brightblue"): "#1fad8a",
        ("chrome", "current_line_bg"): "#e9f4ec",
        ("git_status", "modified"): "#0765d4",
        ("chat", "timeline_read_fg"): "#15803d",
        ("chat", "timeline_write_fg"): "#3fa96b",
        ("chat", "diff_hunk_fg"): "#0f6e3f",
        ("chat", "diff_hunk_bg"): "#dff0e4",
    },
}

#: 旧共享蓝值集合（§5.1 十值，大小写不敏感比较）：全系统绝迹断言
_OLD_SHARED_BLUES = frozenset({
    "#0000af", "#2050a0", "#0451a5", "#1a6fd4", "#eef4fb",
    "#1e88e5", "#006ab1", "#0090f1", "#1d4ed8", "#ddf4ff",
})

#: 六包在 PACK_KEYS 内的注册名（label/title_bar 另测）
_SIX_PACKS = ("syntax", "terminal", "chrome", "git_status", "chat", "git_graph")


#: wheat brown 槽走查微调值（决策点 D2 拍板的唯一非蓝键改动口子）：归位后
#: blue 槽进橙族 #8c5a10，与原 brown #a05000 色相仅差 5.8° 撞色，brown 槽
#: 明度下调为深巧克力棕靠明度差分辨（走查量化 + 截图双确认，实施记录写明）
_WHEAT_BROWN_OVERRIDES = {
    "terminal": {"brown": "#5f3200", "brightbrown": "#8f4c00"},
}


def _pack_color_values(theme: str) -> set[str]:
    """某主题六包内全部色值字符串（小写化，含 syntax 内层 dict 值）。"""
    palette = get_theme_palette(theme)
    values: set[str] = set()
    for pack_name in _SIX_PACKS:
        for value in palette[pack_name].values():
            if isinstance(value, str):
                values.add(value.lower())
            elif isinstance(value, dict):
                values.update(v.lower() for v in value.values()
                              if isinstance(v, str))
    return values


def _pack_color_values_raw(theme: str) -> set[str]:
    """某主题六包内全部色值字符串（原始大小写，大写扫描断言专用）。"""
    palette = get_theme_palette(theme)
    values: set[str] = set()
    for pack_name in _SIX_PACKS:
        for value in palette[pack_name].values():
            if isinstance(value, str):
                values.add(value)
            elif isinstance(value, dict):
                values.update(v for v in value.values() if isinstance(v, str))
    return values


def _pack_color(theme: str, pack_name: str, key) -> str:
    """取包内某键色值（syntax 包剥 color 字段），对比度断言专用。"""
    value = get_theme_palette(theme)[pack_name][key]
    return value["color"] if pack_name == "syntax" else value


def _luminance(hex_color: str) -> float:
    """WCAG 相对亮度（sRGB 线性化），QColor 取通道值。"""
    def linearize(channel: float) -> float:
        return (channel / 12.92 if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4)

    color = QColor(hex_color)
    return (0.2126 * linearize(color.redF())
            + 0.7152 * linearize(color.greenF())
            + 0.0722 * linearize(color.blueF()))


def _contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 对比度 = (L1 + 0.05) / (L2 + 0.05)，L1 ≥ L2。"""
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _hue_degrees(hex_color: str) -> float:
    """色相角（度）。无彩色相 QColor 返回 -360.0，天然落在族区间断言外。"""
    return QColor(hex_color).hueF() * 360.0


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


def test_every_theme_has_all_six_packs_with_schema_keys():
    """去共享化：每主题六包齐备且键集合与包 schema 一致（键名拼写漂移/
    渲染缺键的结构性守护）；模板渲染无残留 $token 占位符。"""
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        assert set(QSS_TOKEN_KEYS + PACK_KEYS) <= palette.keys()
        qss = render_theme(name)
        assert all(f"${token}" not in qss for token in QSS_TOKEN_KEYS)
        for pack_name in _SIX_PACKS:
            assert set(palette[pack_name].keys()) == set(_PACK_KEY_SETS[pack_name]), \
                f"{name}/{pack_name} 键集合与包 schema 不一致"


def test_pack_keys_partition_maintained_adapted_blue():
    """基线拆分漏键的结构性拦截：每包「维持 ∪ 适配 ∪ 蓝 = 全键」且两两不相交
    （适配键集合取四主题行的公共键集——各行键集一致由适配表测试守护）。"""
    for pack_name in _SIX_PACKS:
        adapted = frozenset(
            key for (p, key) in _THEME_ADAPTED["cloud"] if p == pack_name)
        maintained = _MAINTAINED_KEYS[pack_name]
        blue = _BLUE_KEYS[pack_name]
        union = maintained | adapted | blue
        assert union == _PACK_KEY_SETS[pack_name], \
            f"{pack_name} 维持/适配/蓝三集合并 ≠ 包 schema 全键"
        assert len(union) == len(maintained) + len(adapted) + len(blue), \
            f"{pack_name} 维持/适配/蓝三集合存在交集"


def test_maintained_keys_match_old_shared_baseline():
    """选项 3 口径守护：语义/功能色四主题互相同值且等于旧共享基线
    （terminal 非蓝全槽、chrome find_* 黄系、git_status 三状态色、
    git_graph 徽标四色、chat diff_*/timeline_tool_fg/usage_hot_fg/
    tool_error_fg、syntax Error 红与 Generic.Strong/Emph）。"""
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        for pack_name in _SIX_PACKS:
            baseline = _BASELINE_PACKS[pack_name]
            for key in _MAINTAINED_KEYS[pack_name]:
                if key in _WHEAT_BROWN_OVERRIDES.get(pack_name, {}):
                    continue  # D2 拍板口子，由 test_wheat_brown_overrides 单独锁定
                assert palette[pack_name][key] == baseline[key], \
                    f"{name}/{pack_name}/{key} 维持键偏离旧共享基线"


def test_wheat_brown_overrides():
    """D2 唯一例外锁定：wheat brown/brightbrown 走查撞色后的明度微调值。"""
    palette = get_theme_palette("wheat")
    for pack_name, overrides in _WHEAT_BROWN_OVERRIDES.items():
        for key, expected in overrides.items():
            assert palette[pack_name][key] == expected, \
                f"wheat/{pack_name}/{key} 与 D2 微调值不符"


def test_adapted_keys_match_theme_table():
    """§5.1/§5.2 终值表锁定：语法七审美键 + 八灰键 + code_bg 逐主题适配
    （cloud 行 = 旧基线锚定，D1）；四主题行键集一致（拆分漏键守护）。"""
    for name in _LIGHT_THEMES:
        assert set(_THEME_ADAPTED[name]) == set(_THEME_ADAPTED["cloud"]), \
            f"{name} 适配表键集与 cloud 行不一致"
        palette = get_theme_palette(name)
        for (pack_name, key), expected in _THEME_ADAPTED[name].items():
            actual = palette[pack_name][key]
            if pack_name == "syntax":
                actual = actual["color"]
            assert actual == expected, \
                f"{name}/{pack_name}/{key} 适配值与 §5.1/§5.2 表不符"


def test_adapted_key_contrast_floors():
    """§5.3 机器断言（QColor 相对亮度公式逐主题计算，不依赖目检）：
    正文级键白底 ≥ 4.5；弱层级键不弱于 cloud 现状对比度——D1 锚定 cloud
    不动，而行号对与 ignored 的现状对比度（2.61/2.38）本身低于 3.0，
    统一以 cloud 同键同底对比度为地板；行号对按各自底成对校验。"""
    cloud = get_theme_palette("cloud")
    weak_floors = {
        ("syntax", Token.Comment): _contrast_ratio(
            cloud["syntax"][Token.Comment]["color"], "#ffffff"),
        ("chat", "reasoning_fg"): _contrast_ratio(
            cloud["chat"]["reasoning_fg"], "#ffffff"),
        ("git_graph", "hash_fg"): _contrast_ratio(
            cloud["git_graph"]["hash_fg"], "#ffffff"),
        ("git_status", "ignored"): _contrast_ratio(
            cloud["git_status"]["ignored"], "#ffffff"),
    }
    pair_floor = _contrast_ratio(cloud["chrome"]["line_number_fg"],
                                 cloud["chrome"]["line_number_bg"])
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        for token in _BODY_TEXT_TOKENS:
            fg = palette["syntax"][token]["color"]
            assert _contrast_ratio(fg, "#ffffff") >= 4.5, \
                f"{name}/syntax/{token} 白底对比度低于 4.5"
        assert _contrast_ratio(palette["chat"]["tool_fg"], "#ffffff") >= 4.5, \
            f"{name}/chat/tool_fg 白底对比度低于 4.5"
        for (pack_name, key), floor in weak_floors.items():
            fg = _pack_color(name, pack_name, key)
            assert _contrast_ratio(fg, "#ffffff") >= floor - 1e-9, \
                f"{name}/{pack_name}/{key} 白底对比度弱于 cloud 现状"
        pair = _contrast_ratio(palette["chrome"]["line_number_fg"],
                               palette["chrome"]["line_number_bg"])
        assert pair >= pair_floor - 1e-9, \
            f"{name}/chrome 行号对对比度弱于 cloud 现状"


def test_no_uppercase_hex_in_pack_values():
    """全包色值小写扫描：Qt HTML 导出色值归一小写，大写使「包值 in html」
    型断言假阴性（姊妹项目经验）；旧大写基线值（#7A3DB8/#007A1C/#A0522D/
    #CC0000）已随本次适配归一小写。"""
    hex_color = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
    for name in _LIGHT_THEMES:
        for value in _pack_color_values_raw(name):
            if hex_color.match(value):
                assert value == value.lower(), \
                    f"{name} 六包色值含大写十六进制: {value}"


def test_blue_keys_match_theme_table():
    """§5.3 终值表锁定：蓝键按各主题 accent 同族归位（cloud/sky 蓝族、
    wheat 橙族、mint 绿族），含表外三键的 Keyword 同族较浅档；唯一例外
    git_status.modified 按语义族铁律四主题恒蓝族（1311 §5.1 终值表）。"""
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        for (pack_name, key), expected in _EXPECTED_BLUE_VALUES[name].items():
            actual = palette[pack_name][key]
            if pack_name == "syntax":
                actual = actual["color"]
            assert actual == expected, \
                f"{name}/{pack_name}/{key} 蓝键值与 §5.3 表不符"


def test_git_modified_stays_in_blue_hue_family():
    """1311 语义族铁律的机器化身：git_status.modified 四主题 hue 角全部
    落在蓝族区间（200°–222°，按四主题实际分布 201.3°–212.5° 收紧留裕）——
    任何主题把 modified 改回琥珀/绿/灰即被拦截（1643 归位初值曾实证漂移：
    wheat 琥珀出族、mint 绿与 untracked 撞色）。"""
    for name in _LIGHT_THEMES:
        hue = _hue_degrees(get_theme_palette(name)["git_status"]["modified"])
        assert 200.0 <= hue <= 222.0, \
            f"{name}/git_status/modified hue {hue:.1f}° 越出蓝族区间"


def test_git_modified_contrast_on_white():
    """1311 §5.1 取值规则①：modified 是正文级状态色，对白底 ≥ 4.5
    （白底为最浅底色保守下界）。sky 例外：modified 直取 accent 与主题强调
    同色（用户拍板），接受其 4.10 对比度，不再适用正文级门槛。"""
    for name in _LIGHT_THEMES:
        if name == "sky":
            continue
        fg = get_theme_palette(name)["git_status"]["modified"]
        assert _contrast_ratio(fg, "#ffffff") >= 4.5, \
            f"{name}/git_status/modified 白底对比度低于 4.5"


def test_old_shared_blue_values_absent_everywhere():
    """旧共享蓝值集合（§5.1 十值）不出现在任何主题任何包中（大小写不敏感）。"""
    for name in _LIGHT_THEMES:
        assert _OLD_SHARED_BLUES.isdisjoint(_pack_color_values(name)), \
            f"{name} 六包中仍残留旧共享蓝值"


def test_window_border_token_matches_accent_and_renders():
    """主窗色边令牌（1600 计划）：四主题齐备、值 = accent（防两值漂移），
    模板渲染无残留占位符。"""
    for name in _LIGHT_THEMES:
        palette = get_theme_palette(name)
        assert palette["window_border"] == palette["accent"]
        qss = render_theme(name)
        assert "$window_border" not in qss
        assert f"border: 1px solid {palette['window_border']}" in qss


def test_window_border_override_fragment():
    """关闭态覆盖片段（D4）：同选择器后写胜出；开启态空串不追加。
    纯函数断言，不依赖 QApplication。"""
    assert window_border_override(True) == ""
    off = window_border_override(False)
    assert off == "#WindowShell { border: none; }"
