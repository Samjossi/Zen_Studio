"""Pygments 语法高亮器：整文档一次 lexing → 区间缓存，highlightBlock 按块取区间。

只读查看器场景设计：内容仅在打开/重载/换主题时整体变化，无需 QSyntaxHighlighter
逐行增量状态机；整文档 lexing 使多行 token（块注释/多行字符串）天然正确。

配色包（2026-07-20 资源包下沉）：token 类别 → 样式字典由主题调色板提供
（gui/theme.py THEME_PALETTES[主题]["syntax"]），构造与换主题时以参数注入，
本模块不再自存配色表。
"""
from bisect import bisect_left

from pygments import lex
from pygments.lexers import get_lexer_for_filename
from pygments.lexers.special import TextLexer
from pygments.token import Token
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument


def _build_formats(palette: dict) -> dict:
    """token 类别 → QTextCharFormat 映射表。"""
    formats = {}
    for tok, style in palette.items():
        fmt = QTextCharFormat()
        if color := style.get("color"):
            fmt.setForeground(QColor(color))
        if style.get("bold"):
            fmt.setFontWeight(QFont.Weight.Bold)
        if style.get("italic"):
            fmt.setFontItalic(True)
        formats[tok] = fmt
    return formats


def _lookup(formats: dict, toktype) -> QTextCharFormat | None:
    """沿 token 层级向上回退查找（如 String.Double → String）。"""
    while toktype is not Token and toktype not in formats:
        toktype = toktype.parent
    return formats.get(toktype)


class PygmentsHighlighter(QSyntaxHighlighter):
    """整文档 lexing 的区间缓存高亮器（按文件名探测 lexer，未知类型回退纯文本）。"""

    def __init__(self, document: QTextDocument, pack: dict) -> None:
        super().__init__(document)
        self._spans: list[tuple[int, int, QTextCharFormat]] = []  # (start, end, fmt)，连续有序
        self._ends: list[int] = []
        self._formats = _build_formats(pack)

    def set_theme(self, pack: dict) -> None:
        """切换主题：以新配色包重建格式表并重绘（区间缓存与颜色无关，可复用）。"""
        self._formats = _build_formats(pack)
        self.rehighlight()

    def set_source(self, filename: str, text: str) -> None:
        """按文件名探测 lexer，整文档 lexing 生成区间缓存并触发重绘。"""
        try:
            lexer = get_lexer_for_filename(filename)
        except Exception:  # ClassNotFound 等：未知类型按纯文本
            lexer = TextLexer()
        spans: list[tuple[int, int, QTextCharFormat]] = []
        pos = 0
        for toktype, value in lex(text, lexer):
            end = pos + len(value)
            if fmt := _lookup(self._formats, toktype):
                spans.append((pos, end, fmt))
            pos = end
        self._spans = spans
        self._ends = [s[1] for s in spans]  # 连续区间，ends 严格递增
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        """按当前块范围从区间缓存取格式（二分定位首个 end > 块首的区间）。"""
        block_start = self.currentBlock().position()
        block_end = block_start + len(text)
        i = bisect_left(self._ends, block_start + 1)
        while i < len(self._spans):
            s, e, fmt = self._spans[i]
            if s >= block_end:
                break
            self.setFormat(max(s, block_start) - block_start,
                           min(e, block_end) - max(s, block_start), fmt)
            i += 1
