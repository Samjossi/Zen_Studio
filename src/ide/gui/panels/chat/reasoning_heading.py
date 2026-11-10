"""reasoning 一句话标题提取（1836 计划 T1，直译 kilo-ui reasoning-heading.ts）。

四级正则按优先级提取推理块首行标题：HTML 标题 → md ATX → setext →
粗体首行；均不命中则整体为正文。规则与 `参考代码/kilocode-main/
packages/kilo-ui/src/components/reasoning-heading.ts` 逐条等价，
测试样本见 .temp/test_reasoning_heading.py（移植其 bun 测试用例集）。
"""
import re

_RE_HTML = re.compile(r"^<h[1-6][^>]*>([\s\S]*?)</h[1-6]>[ \t]*(?:\n|$)?", re.I)
_RE_ATX = re.compile(r"^#{1,6}[ \t]+([^\n]+?)(?:[ \t]+#+[ \t]*)?(?:\n|$)")
_RE_SETEXT = re.compile(r"^([^\n]+)\n(?:=+|-+)[ \t]*(?:\n|$)")
_RE_STRONG = re.compile(r"^(\*\*|__)([^\n]+?)\1[ \t]*(?:\n|$)")

_RE_BACKTICK = re.compile(r"`([^`]+)`")
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_TAG = re.compile(r"<[^>]+>")
_RE_EMPHASIS = re.compile(r"[*_~]+")
_RE_SPACES = re.compile(r"\s+")
_RE_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_RE_CRLF = re.compile(r"\r\n?")


def _clean(value: str) -> str:
    """标题净化：去行内代码/链接/标签/强调符，折叠空白。"""
    value = _RE_BACKTICK.sub(r"\1", value)
    value = _RE_LINK.sub(r"\1", value)
    value = _RE_TAG.sub(" ", value)
    value = _RE_EMPHASIS.sub("", value)
    return _RE_SPACES.sub(" ", value).strip()


def _visible(value: str) -> str:
    """正文可见性：剔除闭合 HTML 注释；整体不可见（含未闭合注释头）返回空串。"""
    closed = _RE_COMMENT.sub("", value)
    start = closed.find("<!--")
    if start == -1:
        return value if closed.strip() else ""
    body = closed[:start] + closed[start + 4:]
    return body.lstrip() if body.strip() else ""


def _pick(src: str, expr: re.Pattern, group: int = 1) -> tuple[str, str] | None:
    """单级正则尝试：命中且净化后标题非空 → (title, body)，否则 None。"""
    found = expr.match(src)
    if not found or not found.group(group):
        return None
    title = _clean(found.group(group))
    if not title:
        return None
    body = src[found.end():].lstrip()
    return title, _visible(body)


def split_heading(text: str) -> tuple[str | None, str]:
    """推理文本 → (标题, 正文)；无标题时标题为 None、正文为可见原文。"""
    src = _RE_CRLF.sub("\n", text).strip()
    for expr, group in (
        (_RE_HTML, 1),
        (_RE_ATX, 1),
        (_RE_SETEXT, 1),
        (_RE_STRONG, 2),
    ):
        if result := _pick(src, expr, group):
            return result
    return None, _visible(src)
