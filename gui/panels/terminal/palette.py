"""ANSI 调色板：颜色名 → QColor 查表（渲染层消费）。

pyte 的颜色表示：基础色为名字串（"red"/"brightred"…），256 色为 6 位 hex 串，
默认前后景为 "default"。

配色包（2026-07-20 资源包下沉）：16 色表由主题调色板提供
（gui/theme.py THEME_PALETTES[主题]["terminal"]），构造时以参数注入，
本模块不再自存配色表。
"""
from PySide6.QtGui import QColor


class AnsiPalette:
    """一次主题实例：颜色名 → QColor 查表（256 色 hex 串直接解析）。"""

    def __init__(self, colors: dict[str, str]) -> None:
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
