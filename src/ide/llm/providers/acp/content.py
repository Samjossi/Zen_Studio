"""标准 ACP content/diff/文本截断纯函数。

本模块只放无厂商词表、无卡片格式假设的技术设施；具体工具名、入参键表、
厂商回执模板与厂商私有图片标记归各厂商 update mapper，不得回流。
"""
import difflib
import json
import re

_SUMMARY_MAX = 80  # 参数摘要/错误首行截断阈值（防单行过长撑爆输出区）

#: ANSI 转义序列（CSI 颜色/光标、OSC 标题/超链接、字符集切换、两字符转义）
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][0-9A-B]"
    r"|\x1b."
)


def _clean_terminal_text(text: str) -> str:
    """终端控制字符净化：ANSI 转义剥离 + `\\r` 进度帧折叠（逐行取最后非空帧）。

    1836 计划 T2（摘 kilo-ui shell-rolling-results 思路）：bash 输出进
    error/summary 预格式化前单点净化，四家 provider 经公共映射自动受益。
    """
    text = _ANSI_RE.sub("", text)
    if "\r" not in text:
        return text
    lines = []
    for line in text.split("\n"):
        frames = line.split("\r")
        picked = frames[-1]
        for frame in reversed(frames):
            if frame.strip():
                picked = frame
                break
        lines.append(picked)
    return "\n".join(lines)


def _truncate_line(text: object) -> str | None:
    """取首行并截断至 _SUMMARY_MAX；非字符串/空串返回 None（摘要缺省）。

    先经 _clean_terminal_text 净化（T2）：bash 错误/命令常带 ANSI 着色与
    `\\r` 进度帧，不净化则乱码直上屏。
    """
    if not isinstance(text, str):
        return None
    stripped = _clean_terminal_text(text).strip()
    if not stripped:
        return None
    line = stripped.splitlines()[0]
    return line if len(line) <= _SUMMARY_MAX else line[: _SUMMARY_MAX - 1] + "…"



_OUTPUT_LINE_MAX = 300       # 输出单行字符上限（防 minified 单行撑爆卡片）
_BODY_SOFT_LIMIT_LINES = 1000  # 卡 body 正文软上限（0645 计划 D2；折叠载体下
                               # 唯一保留的截断——性能护栏而非版面妥协）


def _collect_content_block(block: dict, texts: list[str], images: list[str]) -> None:
    """单个标准 ACP/MCP content 块按 type 分发（0806 计划 T2 识别矩阵）：

    text 原样拼入 texts；image / image_url / resource 图片形态入 images
    （data-URI 或 file://）；http(s) 图片链接不联网拉取，留一行占位文本。
    厂商私有文本标记（如 `<image path>`）不得在此解析。
    """
    btype = block.get("type")
    if btype == "text" or btype is None:
        text = block.get("text")
        if isinstance(text, str) and text:
            texts.append(text)
        return
    if btype == "image":  # ACP 原生：data + mimeType
        data = block.get("data")
        if isinstance(data, str) and data:
            images.append(f"data:{block.get('mimeType') or 'image/png'};base64,{data}")
        return
    if btype == "image_url":  # OpenAI 形态：image_url/imageUrl.url
        url_obj = block.get("image_url") or block.get("imageUrl") or {}
        url = url_obj.get("url") if isinstance(url_obj, dict) else None
        if isinstance(url, str) and url:
            if url.startswith(("data:", "file:")):
                images.append(url)
            else:
                texts.append(f"[image: {url}]")
        return
    if btype == "resource":
        res = block.get("resource") or {}
        if not isinstance(res, dict):
            return
        if isinstance(res.get("text"), str):
            texts.append(res["text"])
        elif isinstance(res.get("blob"), str):
            mime = res.get("mimeType") or ""
            if mime.startswith("image/"):
                images.append(f"data:{mime};base64,{res['blob']}")
            else:
                texts.append(f"[resource: {res.get('uri') or '?'}]")


def _dispatch_output_string(output: str, texts: list[str], images: list[str]) -> bool:
    """字符串出参 JSON 探测分发（0806 计划 T2，沿用既有工具卡 formattedOutput 解析）。

    try-parse 成功：解析结果为 content 数组（各项含 "type"）则再走一遍
    按 type 分发（标准图片读取类工具的出参即此形态——序列化为字符串的
    content 数组）；其余 JSON pretty 化拼入 texts。解析失败原样拼入。
    返回 True 表示整段为合法 JSON（调用方据此豁免单行字符截断）。
    """
    stripped = output.strip()
    if stripped.startswith(("[", "{")):
        obj = None
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass
        if isinstance(obj, list) and obj and all(
                isinstance(item, dict) and "type" in item for item in obj):
            for item in obj:
                _collect_content_block(item, texts, images)
            return True
        if obj is not None:
            texts.append(json.dumps(obj, ensure_ascii=False, indent=2))
            return True
    texts.append(output)
    return False


def _truncate_lines(text: str, keep_lines: int, keep_tail: bool) -> tuple[str, int]:
    """行数软上限截断（0645 计划 §2.4）：方向按 kind——execute 保尾（排障
    看尾），read/search/fetch/think 保头（读/搜/抓取看头）。

    返回 (截断后正文, 原始行数)；原始行数恒为截断前值供尾注「共 N 行」。
    """
    lines = text.split("\n")
    total = len(lines)
    if total > keep_lines:
        lines = lines[-keep_lines:] if keep_tail else lines[:keep_lines]
    return "\n".join(lines), total


def _extract_diff(content: object) -> tuple[str, list[dict], bool] | None:
    """tool_call.content[] 的 diff 项 → (`+N −M` 计数, hunk 列表, 截断标记)。

    0645 计划 D4（1425 封存款 K7 摘用并放宽）：oldText/newText 经 difflib
    生成 unified hunk（n=3 上下文），**只留 hunk 不留全文**；行数软上限
    _BODY_SOFT_LIMIT_LINES（0619 的 200 行规格在折叠载体下放宽，§1.2），
    超限截断并置 truncated=True（卡 body 尾注用）。
    diff 项非协议必填：无 diff 项返回 None（缺省降级，不臆造）。

    hunk 结构：[{head: str, lines: list[tuple[str, str]]}]，lines 为
    (前缀 `+`/`-`/空格, 文本)；`---`/`+++` 文件头行丢弃（路径已在标题行）。
    """
    if not isinstance(content, list):
        return None
    hunks: list[dict] = []
    adds = dels = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        old_text = item.get("oldText")
        new_text = item.get("newText")
        if not isinstance(old_text, str) and not isinstance(new_text, str):
            continue  # 非 diff 项（ACP diff 项两键至少其一，新建文件 oldText 缺省）
        diff_iter = difflib.unified_diff(
            (old_text or "").splitlines(),
            (new_text or "").splitlines(),
            n=3, lineterm="")
        current: dict | None = None
        for row in diff_iter:
            if row.startswith(("---", "+++")):
                continue
            if row.startswith("@@"):
                current = {"head": row, "lines": []}
                hunks.append(current)
                continue
            if current is None:
                continue  # 理论不可达（unified_diff 首行即 @@），防御跳过
            prefix = row[:1] if row[:1] in ("+", "-") else " "
            if prefix == "+":
                adds += 1
            elif prefix == "-":
                dels += 1
            current["lines"].append((prefix, row[1:] if row else ""))
    if not hunks:
        return None
    # 软上限截断（保头：diff 从前往后读）
    truncated = False
    budget = _BODY_SOFT_LIMIT_LINES
    kept: list[dict] = []
    for hunk in hunks:
        cost = 1 + len(hunk["lines"])
        if cost > budget:
            truncated = True
            break
        budget -= cost
        kept.append(hunk)
    if kept and sum(1 + len(h["lines"]) for h in hunks) > _BODY_SOFT_LIMIT_LINES:
        truncated = truncated or len(kept) < len(hunks)
    return f"+{adds} −{dels}", kept, truncated
