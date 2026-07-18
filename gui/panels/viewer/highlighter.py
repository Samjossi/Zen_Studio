"""Pygments 语法高亮器：整文档一次 lexing → 区间缓存，highlightBlock 按块取区间。

只读查看器场景设计：内容仅在打开/重载/换主题时整体变化，无需 QSyntaxHighlighter
逐行增量状态机；整文档 lexing 使多行 token（块注释/多行字符串）天然正确。
"""
from bisect import bisect_left

from pygments import lex
from pygments.lexers import get_lexer_for_filename
from pygments.lexers.special import TextLexer
from pygments.token import Token
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

#: 明暗双主题高亮配色表（token 类别 → 样式；色板与 config/themes/ 同源）
PALETTES: dict[str, dict] = {
    "light": {
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
    },
    "dark": {
        Token.Keyword: {"color": "#6EA6FF", "bold": True},
        Token.Keyword.Namespace: {"color": "#C678DD", "bold": True},
        Token.Name.Builtin: {"color": "#56B6C2"},
        Token.Name.Function: {"color": "#61AFEF"},
        Token.Name.Class: {"color": "#E5C07B", "bold": True},
        Token.Name.Decorator: {"color": "#C678DD"},
        Token.String: {"color": "#98C379"},
        Token.Number: {"color": "#D19A66"},
        Token.Comment: {"color": "#7F848E", "italic": True},
        Token.Operator: {"color": "#ABB2BF"},
        Token.Generic.Heading: {"color": "#E5C07B", "bold": True},
        Token.Generic.Subheading: {"color": "#E5C07B", "bold": True},
        Token.Generic.Strong: {"bold": True},
        Token.Generic.Emph: {"italic": True},
        Token.Error: {"color": "#E06C75"},
    },
}


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

    def __init__(self, document: QTextDocument, theme: str) -> None:
        super().__init__(document)
        self._spans: list[tuple[int, int, QTextCharFormat]] = []  # (start, end, fmt)，连续有序
        self._ends: list[int] = []
        self._formats = _build_formats(PALETTES.get(theme, PALETTES["light"]))

    def set_theme(self, theme: str) -> None:
        """切换主题：重建格式表并重绘（区间缓存与颜色无关，可复用）。"""
        self._formats = _build_formats(PALETTES.get(theme, PALETTES["light"]))
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
