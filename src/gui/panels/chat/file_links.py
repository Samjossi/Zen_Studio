"""文件引用链接化（0645 融合计划期二：新轨卡片视图专用）。

与旧轨 `output.py` 内同名实现同源复制（旧轨冻结不改，两轨各自持有，
新轨演进不再回灌旧轨——双轨并存期的有意冗余，见 0645 计划 §3 双轨
决策）。规则与 2026-0801-0438 计划一致：反引号 `路径[:行号]` 分支免
校验，@路径 分支须经存在性校验（工作区根由面板注入）。
"""
import re
from html import escape as _html_escape
from pathlib import Path

#: 文件路径链接正则（单正则双分支，0438 计划 T1 规格）：
#: - 反引号分支（bt_* 组）：`路径` / `路径:行号`（锚点区间含反引号，
#:   文本零改动，强制扩展名）；
#: - @分支（at_* 组）：@路径——输入框 _mention_text 产出形态；
#:   (?<![\w@]) 防邮箱 user@host 误命中，尾标点由使用处 rstrip 裁剪
FILE_LINK_RE = re.compile(
    r"`(?P<bt_path>[^\s`]+?\.[A-Za-z0-9]{1,10})(?::(?P<bt_line>\d+))?`"
    r"|(?<![\w@])@(?P<at_path>[^\s`@]+)")

#: @分支尾标点裁剪集：中英文句读均不入链接范围
AT_TRAILING_PUNCT = ".,;:!?)]}\"'。，；：？！）】」"


def iter_file_links(text: str, mention_exists) -> tuple:
    """文件引用统一判定（生成器）：产出 (start, end, path, line|None)。

    :param text: 待扫描文本（HTML 通道为转义后文本——实体语法与路径
        字符集不冲突）
    :param mention_exists: @分支存在性校验回调（path: str -> bool）
    """
    for match in FILE_LINK_RE.finditer(text):
        if (bt_path := match.group("bt_path")) is not None:
            yield match.start(), match.end(), bt_path, match.group("bt_line")
            continue
        raw = match.group("at_path").rstrip(AT_TRAILING_PUNCT)
        if not raw or not mention_exists(raw):
            continue
        yield match.start(), match.start() + 1 + len(raw), raw, None


def linkify_html(escaped: str, color: str, mention_exists) -> str:
    """HTML 转义文本中的文件引用 → <a>+<span> 内联色链接（单趟拼接，
    替换产物不被二次扫描；0438 计划 T3 规格）。

    :param escaped: 已经 _html_escape 的文本（未转义文本请先转义）
    :param color: 链接前景色（十六进制）
    :param mention_exists: @分支存在性校验回调
    """
    parts, last = [], 0
    for mstart, mend, path, line in iter_file_links(escaped, mention_exists):
        href = f"file:{path}" + (f"#L{line}" if line else "")
        parts.append(escaped[last:mstart])
        parts.append(
            f'<a href="{href}"><span style="color:{color}">'
            f"{escaped[mstart:mend]}</span></a>")
        last = mend
    if not parts:
        return escaped
    parts.append(escaped[last:])
    return "".join(parts)


def linkify_plain(text: str, color: str, mention_exists) -> str:
    """未转义纯文本一步链接化（转义 + linkify_html 组合便利函数）。"""
    return linkify_html(_html_escape(text), color, mention_exists)


def make_mention_checker(workspace_root: Path | None):
    """@路径 存在性校验回调工厂：绝对路径直查，相对按工作区根；
    根未注入（独立控件用法）降级为不校验（0438 计划 D4 语义）。
    """
    def mention_exists(path: str) -> bool:
        p = Path(path)
        if not p.is_absolute():
            if workspace_root is None:
                return True
            p = workspace_root / p
        return p.exists()
    return mention_exists
