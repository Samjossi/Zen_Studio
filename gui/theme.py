"""主题加载：读取 config/settings.json，应用对应 qss 与字体。

机制（见 文档/修改记录/2026-0718-1112_PyGPT主页面样式配色参考说明.md 4.1 节）：
启动时读 settings.json 的 theme 字段 → 加载 config/themes/<主题名>.qss
应用到 QApplication；用户切换主题时调用 save_theme() 回写持久化。

多主题体系（2026-07-19，见 文档/修改记录/2026-0719-0704_多亮色主题体系
与现有主题改名计划.md）：新增主题 = ① config/themes/ 放 <名>.qss
② THEME_META 注册一行（显示名 + 族）。族（light/dark）供语法高亮、
终端调色板、查看器行号配色等只认明暗两套的模块查表。

字体（2026-07-19，见 work plans/2026-0719-1152_字体库统一计划.md）：
双字体族全部自带、统一注册自 assets/fonts/，不引用系统字体——
① UI 族：思源黑体 Source Han Sans CN（SIL OFL 1.1，7 档注册其三）；
② 等宽族：更纱黑体 Sarasa Term SC（SIL OFL 1.1，中英 2:1 严格等宽，
供终端与代码查看器）。注册失败仅打印告警（属打包错误），Qt 自然回退兜底。

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

#: Git 状态色注册表（按明暗两族各一套；model 的 ForegroundRole 不走 qss，
#: 故色值在此集中维护——见 work plans/2026-0720-0131 计划任务 2.3）。
#: 状态枚举见 core/git/status.py：modified/untracked/deleted/ignored/conflict
GIT_STATUS_COLORS: dict[str, dict[str, str]] = {
    "light": {
        "modified": "#9a6a00",   # 黄橙
        "untracked": "#1f8a3d",  # 绿
        "deleted": "#c0392b",    # 红
        "ignored": "#a8a8a8",    # 灰
        "conflict": "#d40000",   # 强红
    },
    "dark": {
        "modified": "#e5b567",
        "untracked": "#4ec971",
        "deleted": "#e06c75",
        "ignored": "#6e6e6e",
        "conflict": "#ff5f5f",
    },
}


def git_status_color(family: str, status: str) -> str | None:
    """族名 + Git 状态 → 十六进制色值；未知状态/族返回 None（不着色）。"""
    return GIT_STATUS_COLORS.get(family, GIT_STATUS_COLORS["light"]).get(status)

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

    qss_file = THEMES_DIR / f"{settings['theme']}.qss"
    try:
        app.setStyleSheet(qss_file.read_text(encoding="utf-8"))
    except OSError:
        # 主题文件缺失时静默回退到 Qt 默认样式
        app.setStyleSheet("")

    register_bundled_fonts()
    app.setFont(QFont(BUNDLED_FAMILY, settings["font_size"]))
