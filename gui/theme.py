"""主题加载：读取 config/settings.json，应用对应 qss 与字体。

机制（见 文档/修改记录/2026-0718-1112_PyGPT主页面样式配色参考说明.md 4.1 节）：
启动时读 settings.json 的 theme 字段 → 加载 config/themes/<主题名>.qss
应用到 QApplication；用户切换主题时调用 save_theme() 回写持久化。

多主题体系（2026-07-19，见 文档/修改记录/2026-0719-0704_多亮色主题体系
与现有主题改名计划.md）：新增主题 = ① config/themes/ 放 <名>.qss
② THEME_META 注册一行（显示名 + 族）。族（light/dark）供语法高亮、
终端调色板、查看器行号配色等只认明暗两套的模块查表。

字体（2026-07-19）：优先注册 assets/fonts/思源黑体/ 自带字体
（Source Han Sans CN，SIL OFL 1.1，见该目录 LICENSE.txt），
保障分发一致性与真字重层级；注册失败回退系统 Noto Sans CJK SC。

配置读写（2026-07-19）：通用持久化已抽到 gui/settings.py，
本模块仅保留主题相关的校验包装与样式应用。
"""
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from gui.settings import (
    CONFIG_DIR,
    DEFAULT_SETTINGS,
    load_settings as _load_raw_settings,
    update_settings,
)

THEMES_DIR = CONFIG_DIR / "themes"

#: 主题注册表：键 = qss 文件名（不含扩展名）；label = 菜单显示名；family = 族
#: 注册顺序即视图菜单顺序
THEME_META: dict[str, dict[str, str]] = {
    "cloud": {"label": "云白", "family": "light"},
    "wheat": {"label": "暖米", "family": "light"},
    "sky": {"label": "晴空", "family": "light"},
    "mint": {"label": "薄荷", "family": "light"},
    "dark": {"label": "暗色", "family": "dark"},
}

#: 自带字体目录（assets/fonts/思源黑体/，全量 7 档，运行时注册其中三档）
BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "思源黑体"
#: 注册三档即可支撑正文/标题/强调层级；其余字重留目录备用
BUNDLED_FONT_FILES = (
    "SourceHanSansCN-Regular.otf",
    "SourceHanSansCN-Medium.otf",
    "SourceHanSansCN-Bold.otf",
)
BUNDLED_FAMILY = "Source Han Sans CN"
FALLBACK_FAMILY = "Noto Sans CJK SC"


# ----------------------------------------------------------------------
# 主题注册表查询
# ----------------------------------------------------------------------
def available_themes() -> list[str]:
    """已注册且 qss 文件实际存在的主题名（注册表顺序，供菜单枚举）。"""
    return [name for name in THEME_META if (THEMES_DIR / f"{name}.qss").is_file()]


def is_valid(theme: str) -> bool:
    """主题名已注册且 qss 文件存在。"""
    return theme in THEME_META and (THEMES_DIR / f"{theme}.qss").is_file()


def get_family(theme: str) -> str:
    """主题名 → 族名（light/dark），未注册回退 light。"""
    return THEME_META.get(theme, {}).get("family", "light")


def get_label(theme: str) -> str:
    """主题名 → 菜单显示名，未注册回退主题名本身。"""
    return THEME_META.get(theme, {}).get("label", theme)


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


def _register_bundled_fonts() -> bool:
    """注册自带思源黑体三档；任一字重成功即视为可用。"""
    ok = False
    for name in BUNDLED_FONT_FILES:
        path = BUNDLED_FONTS_DIR / name
        if path.is_file():
            ok = (QFontDatabase.addApplicationFont(str(path)) >= 0) or ok
    return ok


def _resolve_font_family(settings: dict) -> str:
    """解析界面字体家族：自带字体可用则用之，否则回退系统字体。"""
    if settings["font_family"] == BUNDLED_FAMILY:
        if _register_bundled_fonts():
            return BUNDLED_FAMILY
        return FALLBACK_FAMILY
    return settings["font_family"]


def apply_theme(app: QApplication) -> None:
    """按当前配置应用主题样式表与全局字体。"""
    settings = load_settings()

    qss_file = THEMES_DIR / f"{settings['theme']}.qss"
    try:
        app.setStyleSheet(qss_file.read_text(encoding="utf-8"))
    except OSError:
        # 主题文件缺失时静默回退到 Qt 默认样式
        app.setStyleSheet("")

    app.setFont(QFont(_resolve_font_family(settings), settings["font_size"]))
