"""ANSI 调色板：16 色 × 明暗双主题（渲染层消费：颜色名 → QColor）。

pyte 的颜色表示：基础色为名字串（"red"/"brightred"…），256 色为 6 位 hex 串，
默认前后景为 "default"。
"""
from PySide6.QtGui import QColor

#: 明暗双主题 16 色表（与 config/themes/ 同源色板）
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "default_fg": "#1a1a1a", "default_bg": "#ffffff",
        "black": "#000000", "red": "#c41a16", "green": "#007a1c", "brown": "#a05000",
        "blue": "#0451a5", "magenta": "#a02c91", "cyan": "#168396", "white": "#767676",
        "brightblack": "#555555", "brightred": "#e5484d", "brightgreen": "#18a058",
        "brightbrown": "#c26a00", "brightblue": "#1a6fd4", "brightmagenta": "#c044ae",
        "brightcyan": "#00a7b5", "brightwhite": "#000000",
    },
    "dark": {
        "default_fg": "#d4d4d4", "default_bg": "#1e1e1e",
        "black": "#4d4d4d", "red": "#f14c4c", "green": "#23d18b", "brown": "#e5e510",
        "blue": "#3b8eea", "magenta": "#d670d6", "cyan": "#29b8db", "white": "#e5e5e5",
        "brightblack": "#666666", "brightred": "#ff6e67", "brightgreen": "#5ff7a0",
        "brightbrown": "#f4f47c", "brightblue": "#6ea6ff", "brightmagenta": "#e28bff",
        "brightcyan": "#4de8ff", "brightwhite": "#ffffff",
    },
}


class AnsiPalette:
    """一次主题实例：颜色名 → QColor 查表（256 色 hex 串直接解析）。"""

    def __init__(self, theme: str) -> None:
        colors = PALETTES.get(theme, PALETTES["light"])
        self.default_fg = QColor(colors["default_fg"])
        self.default_bg = QColor(colors["default_bg"])
        self._colors = {
            name: QColor(value)
            for name, value in colors.items()
            if not name.startswith("default_")
        }

    def color(self, name: str, fallback: QColor | None = None) -> QColor:
        """颜色名/hex 串 → QColor；未知名称回退 fallback（默认前景色）。"""
        if name in self._colors:
            return self._colors[name]
        if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
            return QColor("#" + name)  # 256 色：pyte 以 6 位 hex 串表示
        return fallback if fallback is not None else self.default_fg
