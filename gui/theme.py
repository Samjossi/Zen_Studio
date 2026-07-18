"""主题加载：读取 config/settings.json，应用对应 qss 与字体。

机制（见 work plans/2026-0718-1112_PyGPT主页面样式配色参考说明.md 4.1 节）：
启动时读 settings.json 的 theme 字段 → 加载 config/themes/<主题名>.qss
应用到 QApplication；用户切换主题时调用 save_theme() 回写持久化。
新增主题只需在 config/themes/ 增加 qss 文件。
"""
import json
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
THEMES_DIR = CONFIG_DIR / "themes"

DEFAULT_SETTINGS = {
    "theme": "dark",
    "font_family": "Noto Sans CJK SC",
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


def apply_theme(app: QApplication) -> None:
    """按当前配置应用主题样式表与全局字体。"""
    settings = load_settings()

    qss_file = THEMES_DIR / f"{settings['theme']}.qss"
    try:
        app.setStyleSheet(qss_file.read_text(encoding="utf-8"))
    except OSError:
        # 主题文件缺失时静默回退到 Qt 默认样式
        app.setStyleSheet("")

    app.setFont(QFont(settings["font_family"], settings["font_size"]))
