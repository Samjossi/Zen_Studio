"""迷你 SGR（ANSI 转义）解析：`git log --color=always` 输出的着色信息提取。

实施计划：work plans/2026-0802-1542_Git提交历史图美化计划.md（T1/T2）。

仅覆盖 git 实际使用的子集：ESC[参数m 序列（前景色 30–37/90–97、粗体 1、
重置 0/空）。其余转义序列（OSC、光标控制等）git log 不产生，一律剥离。
颜色名与 gui/theme.py TERMINAL_PACK 键对齐（brown 即 ANSI yellow）。
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable

#: SGR 序列：ESC[ 参数 m（参数可为空或多段分号分隔）
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

#: 前景色码 30–37 → TERMINAL_PACK 颜色名（33 yellow 对应包内 brown 键）
_FG_NAMES = {
    30: "black", 31: "red", 32: "green", 33: "brown",
    34: "blue", 35: "magenta", 36: "cyan", 37: "white",
}

#: 解析片段：(文本, 颜色名或 None, 是否粗体)；颜色名为 TERMINAL_PACK 基础键
Segment = tuple[str, str | None, bool]


def resolve_key(color: str | None, bold: bool) -> str | None:
    """(颜色名, 粗体) → TERMINAL_PACK 键：粗体按终端惯例映射 bright 变体。"""
    if color is None:
        return None
    return f"bright{color}" if bold else color


def parse_segments(text: str) -> list[Segment]:
    """含 SGR 转义的文本 → 样式片段列表（转义序列被消费，不出现于片段）。"""
    segments: list[Segment] = []
    color: str | None = None
    bold = False
    pos = 0
    for match in _SGR_RE.finditer(text):
        if match.start() > pos:
            segments.append((text[pos:match.start()], color, bold))
        params = match.group(1)
        codes = [int(p) for p in params.split(";")] if params else [0]
        for code in codes:
            if code == 0:
                color, bold = None, False
            elif code == 1:
                bold = True
            elif code == 22:
                bold = False
            elif code in _FG_NAMES:
                color = _FG_NAMES[code]
            elif 90 <= code <= 97:  # bright 前景色：直接等价 粗体+基础色
                color = _FG_NAMES[code - 60]
                bold = True
            elif code == 39:
                color = None
        pos = match.end()
    if pos < len(text):
        segments.append((text[pos:], color, bold))
    return [(t, c, b) for t, c, b in segments if t]


def strip_sgr(text: str) -> str:
    """剥离全部 SGR 序列，返回纯文本。"""
    return _SGR_RE.sub("", text)


def sgr_to_html(text: str, color_for: Callable[[str | None], str | None]) -> str:
    """SGR 文本 → HTML 片段（<span style=color>）；供降级形态富文本展示。

    :param color_for: TERMINAL_PACK 键（已按粗体映射 bright 变体；None =
        未着色段）→ 十六进制色值；返回 None 表示该段不包裹 span。
    文本经 HTML 转义；换行保留，调用方以 <pre> 包裹维持等宽对齐。
    """
    parts: list[str] = []
    for seg_text, color, bold in parse_segments(text):
        escaped = html.escape(seg_text)
        hex_color = color_for(resolve_key(color, bold))
        if hex_color:
            parts.append(f'<span style="color:{hex_color}">{escaped}</span>')
        else:
            parts.append(escaped)
    return "".join(parts)
