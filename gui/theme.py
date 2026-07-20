"""主题体系：唯一模板 config/themes/base.qss + 每主题一包色值令牌（THEME_PALETTES）。

模板化（2026-07-20，见 work plans/2026-0720-1046_主题模板化与去明暗族分类
实施计划.md，C2 全量模板化）：五套 qss 副本合并为唯一模板 base.qss，
$token 占位符由 string.Template 按 THEME_PALETTES[主题名] 渲染（缺键即抛
KeyError，fail-fast）。耦合色（如菜单/下拉同底色）物理同源，不可能漂移；
新增主题 = THEME_PALETTES 加一行字典。

资源包单套化（2026-07-21，见 work plans/2026-0721-0205_dark主题移除与
资源包单套化实施计划.md）：dark 主题移除，语法高亮/终端 ANSI/查看器行号/
Git 状态色四套资源包全库各只此一套，四个主题字典直接引用同一模块级常量
（共享即引用，不复制）。架构中不存在任何明暗判断代码（get_family 已废除、
明暗双包已删除）；未来新增主题（含中间态/深色）逐值手写或基于共享包覆盖
单项（如 {**CHROME_PACK, "find_bg": "#xxx"}）。

字体（2026-07-19，见 work plans/2026-0719-1152_字体库统一计划.md）：
双字体族全部自带、统一注册自 assets/fonts/，不引用系统字体——
① UI 族：思源黑体 Source Han Sans CN（SIL OFL 1.1，7 档注册其三）；
② 等宽族：更纱黑体 Sarasa Term SC（SIL OFL 1.1，中英 2:1 严格等宽，
供终端与代码查看器）。注册失败仅打印告警（属打包错误），Qt 自然回退兜底。

配置读写（2026-07-19）：通用持久化已抽到 gui/settings.py，
本模块仅保留主题相关的校验包装与样式应用。
"""
from pathlib import Path
from string import Template

from pygments.token import Token
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from gui.settings import (
    CONFIG_DIR,
    DEFAULT_SETTINGS,
    load_settings as _load_raw_settings,
    update_settings,
)

THEMES_DIR = CONFIG_DIR / "themes"
THEME_TEMPLATE_FILE = THEMES_DIR / "base.qss"

# ----------------------------------------------------------------------
# 资源包（全部主题数据单点汇聚于本模块；四套资源各只此一套，四主题共享引用）
# ----------------------------------------------------------------------

#: 语法高亮配色包（token 类别 → 样式）
SYNTAX_PACK: dict = {
    Token.Keyword: {"color": "#0000AF", "bold": True},
    Token.Keyword.Namespace: {"color": "#7A3DB8", "bold": True},
    Token.Name.Builtin: {"color": "#7A3DB8"},
    Token.Name.Function: {"color": "#803080"},
    Token.Name.Class: {"color": "#2050A0", "bold": True},
    Token.Name.Decorator: {"color": "#806000"},
    Token.String: {"color": "#007A1C"},
    Token.Number: {"color": "#A0522D"},
    Token.Comment: {"color": "#888888", "italic": True},
    Token.Operator: {"color": "#333333"},
    Token.Generic.Heading: {"color": "#2050A0", "bold": True},
    Token.Generic.Subheading: {"color": "#2050A0", "bold": True},
    Token.Generic.Strong: {"bold": True},
    Token.Generic.Emph: {"italic": True},
    Token.Error: {"color": "#CC0000"},
}

#: 终端 ANSI 16 色包
TERMINAL_PACK: dict[str, str] = {
    "default_fg": "#1a1a1a", "default_bg": "#ffffff",
    "black": "#000000", "red": "#c41a16", "green": "#007a1c", "brown": "#a05000",
    "blue": "#0451a5", "magenta": "#a02c91", "cyan": "#168396", "white": "#767676",
    "brightblack": "#555555", "brightred": "#e5484d", "brightgreen": "#18a058",
    "brightbrown": "#c26a00", "brightblue": "#1a6fd4", "brightmagenta": "#c044ae",
    "brightcyan": "#00a7b5", "brightwhite": "#000000",
}

#: 查看器控件配色包（行号/当前行/查找命中）
CHROME_PACK: dict[str, str] = {
    "ln_fg": "#999999", "ln_bg": "#f5f5f5", "cur_bg": "#eef4fb",
    "find_bg": "#fff3bf", "find_cur": "#ffd43b",
}

#: Git 状态色包（model 的 ForegroundRole 不走 qss，故色值在此集中维护。
#: 状态枚举见 core/git/status.py：modified/untracked/deleted/ignored/conflict）
GIT_STATUS_PACK: dict[str, str] = {
    "modified": "#1e88e5",   # 天蓝
    "untracked": "#1f8a3d",  # 绿
    "deleted": "#c0392b",    # 红
    "ignored": "#a8a8a8",    # 灰
    "conflict": "#d40000",   # 强红
}

# ----------------------------------------------------------------------
# 主题调色板注册表（qss 令牌 + 四资源包；注册顺序即视图菜单顺序）
# ----------------------------------------------------------------------

#: qss 令牌键集合（校验脚本据此剔除资源包键，双向比对模板占位符）
QSS_TOKEN_KEYS = (
    "accent", "text", "muted_text",
    "window_bg", "side_bg", "card_bg",
    "border", "border_hover", "input_bg", "combo_hover_bg",
    "popup_bg", "popup_border", "popup_separator", "menu_border",
    "list_item_hover",
    "btn_hover_bg", "btn_pressed_bg",
    "btn_disabled_text", "btn_disabled_bg", "btn_disabled_border",
    "separator", "splitter_hover",
    "scrollbar_handle", "scrollbar_handle_hover",
    "tooltip_bg", "tooltip_text",
)

#: 资源包键集合（非 qss 令牌，渲染时不参与替换）
PACK_KEYS = ("label", "syntax", "terminal", "chrome", "git_status")

#: 主题注册表：键 = 主题名；label = 菜单显示名；其余为 qss 令牌 + 四资源包。
#: 四主题引用同一组资源包常量（共享即引用，不复制——全库各只此一套）；
#: 未来新增主题可全手写或 {**CHROME_PACK, ...} 单项覆盖。
#:
#: 1259 教训：亮色四主题 combo_hover_bg 为 transparent——hover 伪态背景不触发
#: 选中背景接管，透明等价于未设置（见 base.qss 下拉框段注释）；QComboBox 普通态
#: 永不设 background-color（check_theme_tokens.py 反模式断言机器拦截）。
THEME_PALETTES: dict[str, dict] = {
    "cloud": {
        "label": "云白",
        "accent": "#0765d4",
        "text": "#1d1d1f",
        "muted_text": "#86868b",
        "window_bg": "#ffffff",
        "side_bg": "#fafafb",
        "card_bg": "#ffffff",
        "border": "#d2d2d7",
        "border_hover": "#c0c0c5",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#f5f5f7",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(0, 0, 0, 0.15)",
        "popup_separator": "rgba(0, 0, 0, 0.10)",
        "list_item_hover": "rgba(7, 101, 212, 0.07)",
        "btn_hover_bg": "rgba(7, 101, 212, 0.07)",
        "btn_pressed_bg": "rgba(7, 101, 212, 0.12)",
        "btn_disabled_text": "rgba(29, 29, 31, 0.35)",
        "btn_disabled_bg": "#f9f9fa",
        "btn_disabled_border": "#e8e8ea",
        "separator": "#e5e5e5",
        "splitter_hover": "rgba(0, 0, 0, 0.12)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "syntax": SYNTAX_PACK, "terminal": TERMINAL_PACK,
        "chrome": CHROME_PACK, "git_status": GIT_STATUS_PACK,
    },
    "wheat": {
        "label": "暖米",
        "accent": "#c26a1a",
        "text": "#463f33",
        "muted_text": "#9a8f7d",
        "window_bg": "#fffefd",
        "side_bg": "#fefdfb",
        "card_bg": "#ffffff",
        "border": "#ddd2c0",
        "border_hover": "#cbbca4",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#f2ede3",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(120, 90, 40, 0.25)",
        "popup_separator": "rgba(120, 90, 40, 0.15)",
        "list_item_hover": "rgba(120, 90, 40, 0.08)",
        "btn_hover_bg": "rgba(194, 106, 26, 0.08)",
        "btn_pressed_bg": "rgba(194, 106, 26, 0.13)",
        "btn_disabled_text": "rgba(70, 63, 51, 0.35)",
        "btn_disabled_bg": "#fcfaf6",
        "btn_disabled_border": "#eae2d3",
        "separator": "#e8e0d2",
        "splitter_hover": "rgba(120, 90, 40, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "syntax": SYNTAX_PACK, "terminal": TERMINAL_PACK,
        "chrome": CHROME_PACK, "git_status": GIT_STATUS_PACK,
    },
    "sky": {
        "label": "晴空",
        "accent": "#0284c7",
        "text": "#1e293b",
        "muted_text": "#7d8da0",
        "window_bg": "#fdfeff",
        "side_bg": "#fbfdfe",
        "card_bg": "#ffffff",
        "border": "#c6d9e6",
        "border_hover": "#adc9dc",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#edf3f8",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(2, 132, 199, 0.25)",
        "popup_separator": "rgba(2, 132, 199, 0.15)",
        "list_item_hover": "rgba(2, 132, 199, 0.08)",
        "btn_hover_bg": "rgba(2, 132, 199, 0.08)",
        "btn_pressed_bg": "rgba(2, 132, 199, 0.13)",
        "btn_disabled_text": "rgba(30, 41, 59, 0.35)",
        "btn_disabled_bg": "#fafcfd",
        "btn_disabled_border": "#d9e6ee",
        "separator": "#dde7ee",
        "splitter_hover": "rgba(2, 132, 199, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "syntax": SYNTAX_PACK, "terminal": TERMINAL_PACK,
        "chrome": CHROME_PACK, "git_status": GIT_STATUS_PACK,
    },
    "mint": {
        "label": "薄荷",
        "accent": "#15803d",
        "text": "#20301f",
        "muted_text": "#7f9a84",
        "window_bg": "#fdfffd",
        "side_bg": "#fbfefb",
        "card_bg": "#ffffff",
        "border": "#c8dcc9",
        "border_hover": "#b2ceb4",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#edf5ed",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(21, 128, 61, 0.25)",
        "popup_separator": "rgba(21, 128, 61, 0.15)",
        "list_item_hover": "rgba(21, 128, 61, 0.08)",
        "btn_hover_bg": "rgba(21, 128, 61, 0.08)",
        "btn_pressed_bg": "rgba(21, 128, 61, 0.13)",
        "btn_disabled_text": "rgba(32, 48, 31, 0.35)",
        "btn_disabled_bg": "#fafdfa",
        "btn_disabled_border": "#dce9dd",
        "separator": "#dfe9df",
        "splitter_hover": "rgba(21, 128, 61, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "syntax": SYNTAX_PACK, "terminal": TERMINAL_PACK,
        "chrome": CHROME_PACK, "git_status": GIT_STATUS_PACK,
    },
}

#: 未知主题名/资源包键的回退主题（防御性兜底；正常路径 is_valid 已拦截）
FALLBACK_THEME = "cloud"


def theme_palette(theme: str) -> dict:
    """主题名 → 调色板（含 qss 令牌与四资源包）；未注册回退 FALLBACK_THEME。"""
    return THEME_PALETTES.get(theme, THEME_PALETTES[FALLBACK_THEME])


def git_status_color(theme: str, status: str) -> str | None:
    """主题名 + Git 状态 → 十六进制色值；未知状态返回 None（不着色）。"""
    return theme_palette(theme)["git_status"].get(status)


#: 自带 UI 字体目录（assets/fonts/思源黑体/，全量 7 档，运行时注册其中三档）
BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "思源黑体"
#: 注册三档即可支撑正文/标题/强调层级；其余字重留目录备用
BUNDLED_FONT_FILES = (
    "SourceHanSansCN-Regular.otf",
    "SourceHanSansCN-Medium.otf",
    "SourceHanSansCN-Bold.otf",
)
BUNDLED_FAMILY = "Source Han Sans CN"

#: 自带等宽字体目录（assets/fonts/更纱黑体/，终端与代码查看器专用）
MONO_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "更纱黑体"
MONO_FONT_FILES = (
    "SarasaTermSC-Regular.ttf",
    "SarasaTermSC-Bold.ttf",
)
MONO_FAMILY = "Sarasa Term SC"

#: 注册幂等标志：主题切换会重入 apply_theme，字体只需注册一次
_fonts_registered = False


# ----------------------------------------------------------------------
# 主题注册表查询与渲染
# ----------------------------------------------------------------------
def available_themes() -> list[str]:
    """已注册主题名（注册表顺序，供菜单枚举）。"""
    return list(THEME_PALETTES)


def is_valid(theme: str) -> bool:
    """主题名已注册。"""
    return theme in THEME_PALETTES


def get_label(theme: str) -> str:
    """主题名 → 菜单显示名，未注册回退主题名本身。"""
    return THEME_PALETTES.get(theme, {}).get("label", theme)


def render_theme(theme: str) -> str:
    """按调色板渲染主题样式表（string.Template 全量替换，缺键抛 KeyError）。"""
    template = Template(THEME_TEMPLATE_FILE.read_text(encoding="utf-8"))
    return template.substitute(theme_palette(theme))


# ----------------------------------------------------------------------
# 持久化（通用读写见 gui/settings.py；此处补主题有效性校验）
# ----------------------------------------------------------------------
def load_settings() -> dict:
    """读取持久化配置，缺失字段回退默认值；无效主题名静默回退默认主题。"""
    settings = _load_raw_settings()
    if not is_valid(settings["theme"]):
        settings["theme"] = DEFAULT_SETTINGS["theme"]
    return settings


def save_theme(theme: str) -> None:
    """回写用户所选主题，实现持久化。"""
    update_settings({"theme": theme})


def _register_dir(directory: Path, files: tuple[str, ...]) -> bool:
    """注册一个字体目录下的指定文件；任一成功即视为该族可用。"""
    ok = False
    for name in files:
        path = directory / name
        if path.is_file():
            ok = (QFontDatabase.addApplicationFont(str(path)) >= 0) or ok
    return ok


def register_bundled_fonts() -> None:
    """注册库内双字体族（幂等）；缺失仅告警（属打包错误），Qt 自然回退兜底。"""
    global _fonts_registered
    if _fonts_registered:
        return
    _fonts_registered = True
    if not _register_dir(BUNDLED_FONTS_DIR, BUNDLED_FONT_FILES):
        print(f"[theme] 警告：UI 字体注册失败（{BUNDLED_FONTS_DIR}），Qt 将回退默认字体")
    if not _register_dir(MONO_FONTS_DIR, MONO_FONT_FILES):
        print(f"[theme] 警告：等宽字体注册失败（{MONO_FONTS_DIR}），等宽场景将回退 monospace")


def mono_family() -> str:
    """等宽字体族名：Sarasa Term SC 可用则返回之，否则回退 Qt 泛型 monospace。"""
    if MONO_FAMILY in QFontDatabase.families():
        return MONO_FAMILY
    return "monospace"


def apply_theme(app: QApplication) -> None:
    """按当前配置应用主题样式表与全局字体。"""
    settings = load_settings()

    try:
        app.setStyleSheet(render_theme(settings["theme"]))
    except OSError:
        # 模板文件缺失（打包错误）时静默回退到 Qt 默认样式
        app.setStyleSheet("")

    register_bundled_fonts()
    app.setFont(QFont(BUNDLED_FAMILY, settings["font_size"]))
