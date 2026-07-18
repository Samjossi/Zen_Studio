"""主题加载：读取 config/settings.json，应用对应 qss 与字体。

机制（见 work plans/2026-0718-1112_PyGPT主页面样式配色参考说明.md 4.1 节）：
启动时读 settings.json 的 theme 字段 → 加载 config/themes/<主题名>.qss
应用到 QApplication；用户切换主题时调用 save_theme() 回写持久化。
新增主题只需在 config/themes/ 增加 qss 文件。

字体（2026-07-19）：优先注册 assets/fonts/思源黑体/ 自带字体
（Source Han Sans CN，SIL OFL 1.1，见该目录 LICENSE.txt），
保障分发一致性与真字重层级；注册失败回退系统 Noto Sans CJK SC。
"""
import json
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
THEMES_DIR = CONFIG_DIR / "themes"

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

DEFAULT_SETTINGS = {
    "theme": "light",
    "font_family": BUNDLED_FAMILY,
    "font_size": 10,
}


def load_settings() -> dict:
    """读取持久化配置，缺失字段回退默认值。"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_theme(theme: str) -> None:
    """回写用户所选主题，实现持久化。"""
    settings = load_settings()
    settings["theme"] = theme
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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
