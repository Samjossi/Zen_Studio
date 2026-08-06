"""ACP 连接层泛化：长驻 `<agent> acp` 子进程 + JSON-RPC（ndjson 帧）收发。

由 kimi_acp._AcpConnection 抽出泛化（计划 2026-0730-0150 §4-D5）：帧收发、
请求 id 配对、通知与反向请求分发、死讯注入均已协议无关；仅 agent 名
（错误文案）参数化，argv 恒为 `[bin_path, "acp"]`（`kimi acp` /
`reasonix acp` 同构）。initialize 载荷（clientInfo）不在连接层——
归各 provider 的 `_ensure_session`；kimi 专有的 `-32000 authRequired →
"请 kimi login"` 文案映射亦不搬，由各 provider 钩子自行翻译。

协议定型类型（PermissionOption/ToolCallInfo/PermissionParams/
PermissionHandler/_TurnMessage）是 ACP 协议层产物，不属 kimi 专有，
随连接层同居本模块。
"""
import base64
import difflib
import json
import queue
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict

from llm.base import (
    Chunk,
    Message,
    TodoEntry,
    TodoPayload,
    ToolCallPayload,
    ToolUpdatePayload,
    UsageStats,
)
from core.paths import workspace_display_path

_ACP_TIMEOUT_S = 30  # initialize / session/new / set_config_option 等控制请求超时


# ----------------------------------------------------------------------
# prompt 块构造（0340 方案 B 计划 T1：text 单块 → text + image 多块）
# ----------------------------------------------------------------------
def build_prompt_blocks(message: Message, workspace_root: str | None = None) -> list[dict]:
    """末条 user 消息 → ACP `session/prompt` 的 ContentBlock 数组。

    恒以 text 块打头；随后每张附件一个 image 块（读盘 base64，调用点
    位于 worker 线程，GUI 零阻塞）。读盘失败（文件被删/无权限）跳过
    该图续发其余块——尽力而为，不阻断整轮发送。

    图片路径透传（2026-0806-1908 计划 T1）：图片内容与路径同等重要——
    AI 不知路径就无法用工具再次读取或准确引用。每张成功入块的图片在
    text 块尾部追加一行「[附图 N] 图片路径：<路径>」，路径形式经
    workspace_display_path 换算（工作区内相对、区外绝对；root 未知时
    退化绝对路径）。读盘失败的图不拼路径（块未发，避免引用未送达内容）。

    空 text 回退占位文案（0340 计划 D5，T0 spike 2026-08-01 实证）：
    kimi（-32603）与 reasonix（-32602）均拒绝空 text 块——纯图发送时
    占位「请查看附图。」；opencode/kilocode 虽接受空块，占位文案
    对二者无害且语义更明确，故统一回退不区分后端。
    """
    images = message.get("images", [])
    text = message["content"] or ("请查看附图。" if images else "")
    blocks: list[dict] = []
    path_lines: list[str] = []
    for img in images:
        try:
            data = base64.b64encode(Path(img["path"]).read_bytes()).decode()
        except OSError:
            continue
        blocks.append({"type": "image", "data": data, "mimeType": img["mime_type"]})
        path_lines.append(
            f"[附图 {len(path_lines) + 1}] 图片路径："
            f"{workspace_display_path(img['path'], workspace_root)}"
        )
    if path_lines:
        text = "\n".join([text, *path_lines])
    return [{"type": "text", "text": text}, *blocks]


# ----------------------------------------------------------------------
# ACP 协议层定型（传输边界 dict 合理，入口处定型；键名依协议原文 camelCase）
# ----------------------------------------------------------------------
class PermissionOption(TypedDict, total=False):
    """`session/request_permission` 的单个选项（agent 提供，optionId 回应用原值）。"""
    optionId: str
    name: str
    kind: str  # allow_once / allow_always / reject_once / reject_always


class ToolCallInfo(TypedDict, total=False):
    """审批请求携带的工具调用信息（键全为可选，agent 实发字段随工具而异）。"""
    title: str
    kind: str
    rawInput: dict
    content: list
    locations: list


class PermissionParams(TypedDict, total=False):
    """`session/request_permission` 的 params（ACP 入口定型点）。"""
    sessionId: str
    toolCall: ToolCallInfo
    options: list[PermissionOption]


#: 审批处理器签名：session/request_permission params → optionId（None 视为拒绝）
PermissionHandler = Callable[[PermissionParams], str | None]

#: 轮次内消息：update/response 载荷为 JSON-RPC 帧，dead 载荷为进程退出码
_TurnMessage = tuple[Literal["update", "response"], dict] | tuple[Literal["dead"], int | None]


def map_usage_update(update: dict) -> UsageStats | None:
    """`usage_update` 通知载荷 → UsageStats；无有效上限数据返回 None。

    协议载荷：`{"sessionUpdate": "usage_update", "used": N, "size": M,
    "cost": {"amount": f, "currency": "USD"}}`——used/size 同帧送达，
    IDE 侧无需自维护模型上下文上限表。size 缺失或为 0 的通知无意义
    （算不出百分比），返回 None 不上屏（UI 须容忍收不到用量的后端，
    保持隐藏而非显示 0%）。
    """
    size = update.get("size")
    used = update.get("used")
    if not isinstance(size, int) or size <= 0 or not isinstance(used, int) or used < 0:
        return None
    cost_amount = (update.get("cost") or {}).get("amount")
    return UsageStats(
        used=used,
        size=size,
        cost=cost_amount if isinstance(cost_amount, (int, float)) else None,
    )


# ----------------------------------------------------------------------
# session/update 公共映射（1602 计划 T2：四 provider 私有 _map_update 上收，D4）
# ----------------------------------------------------------------------
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


_OUTPUT_KEEP_LINES = 20      # 旧轨 bash 输出卡截尾行数（1836 计划 L2-3；
                             # 新轨起协议层不再按此截断，旧轨由路由层钳制，
                             # 见 panel._on_activity_chunk 旧轨分支）
_OUTPUT_LINE_MAX = 300       # 输出单行字符上限（防 minified 单行撑爆卡片）
_BODY_SOFT_LIMIT_LINES = 1000  # 卡 body 正文软上限（0645 计划 D2；折叠载体下
                               # 唯一保留的截断——性能护栏而非版面妥协）
_TAIL_WINDOW_LINES = 5         # in_progress 尾窗行数（0645 计划 §2.1 BashCard
                               # 运行中实时帧规格，同 0619-T6）


# ----------------------------------------------------------------------
# 系统回执过滤（0806 计划 T2：工具出参夹带的系统指令性文本剔除，
# 白名单模式保守起步——只过滤已确证的系统提示，宁漏勿错）
# ----------------------------------------------------------------------
_RECEIPT_PATTERNS = [
    re.compile(r"(?ms)^Todo list updated\.\s*$"),
    re.compile(r"(?ms)^Ensure that you continue to use the todo list.*$"),
    # 后续确证的模式在此追加，集中维护
]

#: text 块内 `<image path="...">` 本地路径标签（ReadMediaFile 出参形态）
_IMAGE_PATH_RE = re.compile(r"<image\s+path=[\"']?([^\"'>\s]+)[\"']?\s*/?>")


def _filter_tool_receipt(text: str) -> str:
    """剔除工具出参中夹带的系统指令性回执文本（白名单模式，保守起步）。"""
    for pattern in _RECEIPT_PATTERNS:
        text = pattern.sub("", text)
    return text


def _collect_content_block(block: dict, texts: list[str], images: list[str]) -> None:
    """单个 MCP/ACP content 块按 type 分发（0806 计划 T2 识别矩阵）：

    text 拼入 texts（`<image path>` 标签提取为 file:// 图片并剔除标签）；
    image / image_url / resource 图片形态入 images（data-URI 或 file://）；
    http(s) 图片链接不联网拉取，留一行占位文本。
    """
    btype = block.get("type")
    if btype == "text" or btype is None:
        text = block.get("text")
        if not isinstance(text, str):
            return

        def _pick_image(match: re.Match) -> str:
            images.append(f"file://{match.group(1)}")
            return ""

        text = _IMAGE_PATH_RE.sub(_pick_image, text).strip()
        if text:
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
    """字符串出参 JSON 探测分发（0806 计划 T2，对标 kilocode McpTool.formattedOutput）。

    try-parse 成功：解析结果为 content 数组（各项含 "type"）则再走一遍
    按 type 分发（ReadMediaFile 出参即此形态——序列化为字符串的 content
    数组）；其余 JSON pretty 化拼入 texts。解析失败原样拼入。
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


def _extract_raw_output(update: dict) -> tuple[str, list[str]] | None:
    """tool_call_update 原始输出提取（0806 计划 T2 重构：双通道产出）。

    返回 (text, images)：text 为 text 块拼接 + 字符串出参 JSON try-parse
    pretty 化（净化 ANSI/`\\r`、系统回执过滤；**不做行数截断**——截断档位
    与方向归调用方按 kind/status 决定，§2.3-1）；images 为 content 数组中
    image/image_url/resource 块的 data-URI 或 `file://` 路径列表（独立
    载荷字段下传，渲染层内嵌 `<img>`）。

    合法 JSON 出参豁免 `_OUTPUT_LINE_MAX` 单行字符截断（minified JSON
    pretty 化后行短，截断只会破坏结构；0806 计划 T2 改动点 3）。
    无有效输出（text 与 images 均空）返回 None。
    """
    raw = update.get("rawOutput")
    # rawOutput 三态分流：dict 走 .output；str 为中止/失败场景的纯文本
    # 降级形态（kimi-code 工具被中止时实证），直接作为输出文本进既有管线
    if isinstance(raw, dict):
        output = raw.get("output")
    elif isinstance(raw, str):
        output = raw
    else:
        output = None
    texts: list[str] = []
    images: list[str] = []
    json_parsed = False
    if isinstance(output, str):
        output = _filter_tool_receipt(_clean_terminal_text(output))
        json_parsed = _dispatch_output_string(output, texts, images)
    else:
        for item in update.get("content") or []:
            if isinstance(item, dict):
                content = item.get("content")
                if isinstance(content, dict):
                    _collect_content_block(content, texts, images)
    text = _filter_tool_receipt("\n".join(texts)).strip("\n")
    if not text.strip() and not images:
        return None
    if not json_parsed:
        text = "\n".join(
            line if len(line) <= _OUTPUT_LINE_MAX
            else line[: _OUTPUT_LINE_MAX - 1] + "…"
            for line in text.split("\n"))
    return text, images


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


def _extract_tool_output(
    update: dict,
    keep_lines: int = _BODY_SOFT_LIMIT_LINES,
    keep_tail: bool = True,
) -> tuple[str, int, list[str]] | None:
    """tool_call_update 输出正文提取：全文提取（_extract_raw_output）+
    参数化截断（0645 计划 §2.3-1：行数档位与截断方向双参数）。

    返回 (截断正文, 原始行数, images 图片列表)；无有效输出返回 None。
    正文为空但 images 非空时仍返回（正文 ""、原始行数 0）——图片是
    独立信息通道，不因无文本而丢弃（0806 计划 T2）。
    """
    if (extracted := _extract_raw_output(update)) is None:
        return None
    raw_text, images = extracted
    if raw_text:
        body, total = _truncate_lines(raw_text, keep_lines, keep_tail)
    else:
        body, total = "", 0
    return body, total, images


# ----------------------------------------------------------------------
# diff / 成果摘要 / 入参详情（0645 融合计划 T1：信息全量化载荷扩展）
# ----------------------------------------------------------------------
_TASK_RESULT_RE = re.compile(r"<task_result>(.*?)</task_result>", re.DOTALL)


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


def _extract_write_diff(update: dict) -> tuple[str, list[dict], bool] | None:
    """write 形态合成 diff（0803 计划 T2）：rawInput.content 全文 → `+N −0`。

    kimi 系 write 新建文件会话的 in_progress 帧 content **无 diff 项**
    （edit 有），diff 徽标/hunk 无数据源（取证
    `.temp/write_frames_kimi.json`）；但 `rawInput.content` 是写入全文，
    新建文件语义的 diff 即 `oldText="" + newText=content`——数据为后端
    真实所给，合成仅是真实数据的 diff 化重表达（不臆造纪律合规），
    且直接复用 `_extract_diff` 管线（difflib hunk 化、软上限截断，
    零新增截断逻辑）。

    条件收敛（防与 edit 语义混淆）：
    - kind 须为 "edit"（write/edit 在 ACP 同 kind，靠 rawInput 键区分）；
    - `rawInput.content` 须为字符串且非空（写空文件返回 None，
      无 diff 不臆造，退化为纯标题行）；
    - rawInput 不含 old/new 文本键（snake_case 与 camelCase 双形态
      排除——edit 形态不受合成路径污染）。

    已知取舍（0803 计划 R1）：write 覆盖已存在文件时旧内容不可得
    （后端不给，协议层不碰文件系统），合成恒为「全量新增」口径
    （+N −0），与真实增删有偏差，记录于计划 §4。
    """
    if update.get("kind") != "edit":
        return None
    raw = update.get("rawInput")
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if not isinstance(content, str) or not content:
        return None
    if any(key in raw for key in
           ("old_string", "new_string", "oldString", "newString")):
        return None  # edit 形态排除
    return _extract_diff([{"oldText": "", "newText": content}])


def _extract_result_summary(text: str) -> str:
    """task 子代理成果摘要（1425 封存款 K2，0645 计划 D5 放宽）：
    `<task_result>...</task_result>` 正则提取；无包裹标记取全文兜底。
    行数软上限保头由调用方统一执行（不再硬截 10 行）。
    """
    match = _TASK_RESULT_RE.search(text)
    return (match.group(1) if match else text).strip("\n")


#: 通用入参区已知键（0645 计划 D3：按 kind 取 rawInput 关键字段，
#: 未命中任何已知键时 JSON pretty 兜底——「尽可能全」的兜底保证，
#: 即使专门渲染未覆盖的字段也不丢信息）
_INPUT_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "execute": ("command",),
    "edit": ("path", "filePath", "oldString", "newString"),
    "read": ("path", "filePath", "offset", "limit"),
    "search": ("pattern", "query", "path", "include"),
    "fetch": ("url",),
    "think": ("description", "prompt", "subagent_type"),
    # 0806 计划 T1：ReadMediaFile/AskUserQuestion/Agent/TodoList 等
    # ACP kind 恒为 "other"，无白名单则已知键提取必然落空
    "other": ("path", "filePath", "url", "query", "command", "prompt",
              "description", "questions", "todos", "name"),
}
_INPUT_DETAIL_VALUE_MAX = 2000  # 单字段值字符上限（入参区是兜底非主渲染）
_INPUT_DETAIL_BRIEF_VALUE_MAX = 200  # 长文本键摘要上限（0806 计划 T1：
                                     # prompt/questions 完整值留给出参/展开区，
                                     # 对齐 kilocode McpTool.inputArgs 克制策略）
#: 长文本键集（值超 _INPUT_DETAIL_BRIEF_VALUE_MAX 即摘要化）
_INPUT_DETAIL_BRIEF_KEYS = frozenset({"prompt", "questions", "description"})


def _format_input_detail(update: dict) -> str | None:
    """rawInput → 入参区预格式化多行文本（协议层单点格式化纪律不变：
    GUI 直渲不碰 rawInput 各家差异）。

    分 kind 取已知键逐行 `键: 值`；dict/list 值 JSON 紧凑化；
    一个已知键都未命中且 rawInput 非空 → JSON pretty 全文兜底
    （软上限保头截断）。rawInput 为空返回 None（入参区缺省不挂）。
    """
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not raw:
        return None
    kind = update.get("kind")
    keys = _INPUT_DETAIL_KEYS.get(kind if isinstance(kind, str) else "")
    if keys is None:
        # kind 缺失/未识别（update 帧常缺 kind，F3——kimi 系迟到入参帧
        # 即无 kind）：全白名单并集按序探测，命中任一已知键即免于 JSON
        # 兜底（0806 计划 T1 修订：mock 场景 01 实证）
        keys = tuple(dict.fromkeys(
            key for group in _INPUT_DETAIL_KEYS.values() for key in group))
    lines = []
    for key in keys:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        value = str(value)
        value_max = (_INPUT_DETAIL_BRIEF_VALUE_MAX
                     if key in _INPUT_DETAIL_BRIEF_KEYS
                     else _INPUT_DETAIL_VALUE_MAX)
        if len(value) > value_max:
            value = value[: value_max - 1] + "…"
        lines.append(f"{key}: {value}")
    if lines:
        return "\n".join(lines)
    pretty = json.dumps(raw, ensure_ascii=False, indent=2)
    text, _ = _truncate_lines(pretty, _BODY_SOFT_LIMIT_LINES, keep_tail=False)
    return text


#: 入参已提取簿记（0806 计划 T1：update 帧迟到入参回填的去重账本）。
#: 首帧 `_map_tool_call` 提取成功即登记；update 帧仅对未登记者回填；
#: completed/failed 终态帧到达后惰性清理（toolCallId 跨轮不复用）。
_input_detail_seen: set[str] = set()


def _record_input_detail(payload: dict, detail: str | None) -> None:
    """入参详情装填 + 簿记登记（首帧与 update 帧回填共用）。"""
    if detail:
        payload["input_detail"] = detail
        if tid := payload.get("tool_call_id"):
            _input_detail_seen.add(tid)


#: 入参图片扩展名白名单（0158 计划 T1：media_path 装填判定——非图片
#: 路径不装填，ReadMediaFile 也可读非图片，无图可缩）
_MEDIA_IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})


def _extract_media_path(update: dict) -> str | None:
    """rawInput.path/filePath → 入参图片本地路径（0158 计划 T1：
    MediaReadCard 略缩图数据源，仅供人类查看，不回传 AI）。

    判定口径：扩展名白名单命中即装填（工具名不可得的 update 迟到帧
    兜底——kimi 系 in_progress 帧常缺 title）；扩展名缺失时要求工具名
    归一化后为 readmediafile（无扩展名图片试探装填，GUI 侧解码失败
    静默降级）；有非图扩展名（.txt 等）不装填。
    纪律不变：路径原样下传（相对路径不解析），GUI 不碰 rawInput。
    """
    raw = update.get("rawInput")
    if not isinstance(raw, dict):
        return None
    path = raw.get("path") or raw.get("filePath")
    if not isinstance(path, str) or not path:
        return None
    ext = Path(path).suffix.lower()
    if ext in _MEDIA_IMAGE_EXTS:
        return path
    if ext:
        return None  # 非图扩展名：无图可缩
    title = update.get("title")
    if isinstance(title, str) and \
            title.strip().split("__")[-1].lower() == "readmediafile":
        return path
    return None


def _extract_questions(update: dict) -> list[str] | None:
    """rawInput.questions → 问题文本列表（0806 计划 T5：AskUserQuestion
    结构化载荷，QuestionCard 问答对渲染数据源）。"""
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        return None
    questions = [q["question"] for q in raw["questions"]
                 if isinstance(q, dict) and isinstance(q.get("question"), str)]
    return questions or None


def _extract_question_options(update: dict) -> list[dict] | None:
    """rawInput.questions → 结构化选项载荷（0807-0148 计划 T3：
    QuestionCard 卡片内交互数据源；与 questions 字段并存，GUI 不碰 rawInput）。

    每问 `{question, header?, options: [{label, description?}], multi_select}`。
    实证蓝本（.temp/frame_archive/askuser_*.json）：多选字段名为
    `multi_select`（snake_case，非臆测的 multiSelect）；header 为可选短标题。
    """
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        return None
    items: list[dict] = []
    for q in raw["questions"]:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        item: dict = {"question": q["question"],
                      "multi_select": bool(q.get("multi_select"))}
        if isinstance(q.get("header"), str):
            item["header"] = q["header"]
        options: list[dict] = []
        for opt in q.get("options") or []:
            if not isinstance(opt, dict) or not isinstance(opt.get("label"), str):
                continue
            entry: dict = {"label": opt["label"]}
            if isinstance(opt.get("description"), str):
                entry["description"] = opt["description"]
            options.append(entry)
        item["options"] = options
        items.append(item)
    return items or None


def _tool_call_fallback(payload: ToolCallPayload) -> str:
    """tool_call 兜底显示行（与 output.append_tool_call 渲染格式保持一致）。"""
    icon = "⧉" if payload.get("is_subagent") else "▸"
    line = f"◐ {icon} {payload.get('title') or '?'}"
    if summary := payload.get("summary"):
        line += f" — {summary}"
    if diff_stat := payload.get("diff_stat"):  # 0645 计划 T1：edit 计数随行
        line += f"  {diff_stat}"
    return f"\n{line}\n"


def _tool_update_fallback(payload: ToolUpdatePayload) -> str:
    """tool_call_update 兜底显示行（与 output.append_tool_update 保持一致）。"""
    name = payload.get("title") or payload.get("tool_call_id") or "?"
    if payload.get("status") == "failed":
        line = f"✖ ▸ {name}"
        if error := payload.get("error"):
            line += f"（{error}）"
    else:
        line = f"✔ ▸ {name}"
        if diff_stat := payload.get("diff_stat"):  # 0919 T2：completed 计数随行
            line += f"  {diff_stat}"
    return f"{line}\n"


def _todo_fallback_text(entries: list[TodoEntry]) -> str:
    """todo 清单兜底文本（与 output.upsert_todo_block 渲染格式保持一致）。"""
    marks = {"pending": "[ ]", "in_progress": "[>]"}
    lines = []
    for entry in entries:
        mark = marks.get(entry.get("status") or "", "[x]")
        lines.append(f"- {mark} {entry.get('content') or ''}")
    return "\n".join(lines) + "\n" if lines else ""


def _extract_todo_entries(items: object) -> list[TodoEntry]:
    """plan.entries / rawInput.todos → TodoEntry 列表（两通道同构，F1）。

    仅收录 content 为字符串的条目；status/priority 为字符串才保留
    （渲染层按缺省 pending 容错），结构不符的条目静默跳过。
    """
    entries: list[TodoEntry] = []
    if not isinstance(items, list):
        return entries
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        entry = TodoEntry(content=item["content"])
        if isinstance(item.get("status"), str):
            entry["status"] = item["status"]
        if isinstance(item.get("priority"), str):
            entry["priority"] = item["priority"]
        entries.append(entry)
    return entries


def _tool_call_summary(update: dict) -> str | None:
    """参数摘要（协议层单点格式化，GUI 不碰 rawInput 各家差异）。

    分 kind 取值（1602 计划 T2 规则表）；未识别/取值失败返回 None
    （摘要缺省，仅显示 title，不阻断上屏）。
    """
    kind = update.get("kind")
    raw = update.get("rawInput") or {}
    if kind == "execute":
        command = _truncate_line(raw.get("command"))
        # shell 工具 title 即命令本身（F3）：摘要与 title 重复时省略
        if command and command != _truncate_line(update.get("title")):
            return command
        return None
    if kind in ("edit", "read"):
        locations = update.get("locations") or []
        if locations and isinstance(locations[0], dict):
            if path := _truncate_line(locations[0].get("path")):
                return path
        return _truncate_line(raw.get("path") or raw.get("filePath"))
    if kind == "search":
        return _truncate_line(raw.get("pattern") or raw.get("query"))
    if kind == "fetch":
        return _truncate_line(raw.get("url"))
    if kind == "think":
        return _truncate_line(raw.get("description") or raw.get("prompt"))
    return None


def _map_tool_call(update: dict) -> Chunk:
    """tool_call → 结构化 Chunk；todowrite 特判改产 todo Chunk（F1 第二通道）。

    kilocode/opencode 系后端不发 plan，todo 走 todowrite 普通工具调用，
    载荷在 rawInput.todos——检出即与 plan 通道归一为同一 todo Chunk
    （1425 封存款 F1/HIDDEN_TOOLS 同范式：todo 块与工具块分流）。
    """
    raw = update.get("rawInput") or {}
    if isinstance(raw.get("todos"), list):
        entries = _extract_todo_entries(raw["todos"])
        return Chunk("todo", _todo_fallback_text(entries),
                     payload=TodoPayload(entries=entries))
    payload = ToolCallPayload()
    if isinstance(update.get("toolCallId"), str):
        payload["tool_call_id"] = update["toolCallId"]
    payload["title"] = update.get("title") or "?"
    tool_kind = update.get("kind") if isinstance(update.get("kind"), str) else "other"
    payload["tool_kind"] = tool_kind
    if tool_kind == "think":  # task 子代理（D5 标记）
        payload["is_subagent"] = True
    if tool_kind == "execute":
        # L2-3 bash 输出卡 `$ ` 头：留存完整命令（净化；可多行，渲染取首行）
        if isinstance(raw.get("command"), str):
            if command := _clean_terminal_text(raw["command"]).strip():
                payload["command"] = command
    if tool_kind == "edit":
        # 0645 计划 T1：edit content diff 项 → +N −M 计数 + hunk 列表
        # （非必填：缺省不臆造，DiffCard 退化为纯标题行）
        if diff := _extract_diff(update.get("content")):
            payload["diff_stat"], payload["diff_hunks"], truncated = diff
            if truncated:
                payload["diff_truncated"] = True
        elif write_diff := _extract_write_diff(update):
            # 0803 计划 T2：write 形态 content 无 diff 项，rawInput.content
            # 全文合成 +N −0（首帧防御：kimi 首帧空壳无 rawInput 自然 no-op，
            # 其他后端若首帧带全量 rawInput 通用受益）
            payload["diff_stat"], payload["diff_hunks"], truncated = write_diff
            if truncated:
                payload["diff_truncated"] = True
    if summary := _tool_call_summary(update):
        payload["summary"] = summary
    # 0645 计划 D3：通用入参区（已知键 + JSON 兜底；MCP/未知工具亦受益）
    _record_input_detail(payload, _format_input_detail(update))
    # 0806 计划 T5：AskUserQuestion 结构化问题列表（QuestionCard 数据源）
    if questions := _extract_questions(update):
        payload["questions"] = questions
    # 0807-0148 计划 T3：选项结构载荷（QuestionCard 卡片内交互数据源）
    if question_options := _extract_question_options(update):
        payload["question_options"] = question_options
    # 0158 计划 T1：入参图片路径载荷（MediaReadCard 略缩图数据源）
    if media_path := _extract_media_path(update):
        payload["media_path"] = media_path
    return Chunk("tool_call", _tool_call_fallback(payload), payload=payload)


def _map_tool_call_update(update: dict) -> Chunk | None:
    """tool_call_update → 状态流转 Chunk；status 缺省（纯 content 快照帧）返回 None。

    0645 融合计划 T1 信息全量化（§2.3）：
    - 输出正文截断参数化——in_progress 帧为尾窗规格（截尾末 5 行，0619-T6
      沿用）；完成/失败帧按 kind 定方向（execute 保尾、其余保头，§2.4）+
      软上限 1000 行（D2）。update 帧常缺 kind（F3）：缺省按保头处理
      （execute 缺 kind 时保尾信息损失的已知取舍见计划 §8）；
    - think（task 子代理）completed 帧填充 result_summary（`<task_result>`
      提取、无标记取全文、软上限保头，D5——不再硬截 10 行）；
    - failed 完整错误文本入 error_detail（§2.3-5：净化 + 软上限保尾——
      诊断信息在尾部；error 首行保留供旧轨行内尾注与兜底文本）。
    """
    payload = ToolUpdatePayload()
    if isinstance(update.get("toolCallId"), str):
        payload["tool_call_id"] = update["toolCallId"]
    status = update.get("status")
    if not isinstance(status, str) or not status:
        return None  # 无状态可报（bash 输出快照等部分更新帧，F3 字段可缺省）
    payload["status"] = status
    if isinstance(update.get("title"), str):
        payload["title"] = update["title"]
    kind = update.get("kind")
    # 0806 计划 T1：update 帧迟到入参回填——kimi 系首帧为空壳、rawInput
    # 在 in_progress 帧才补发；簿记去重（首帧优先，后续帧不重复回填）
    if (tid := payload.get("tool_call_id")) and tid not in _input_detail_seen:
        _record_input_detail(payload, _format_input_detail(update))
        # 0158 计划 T1：media_path 与 input_detail 同频回填、同一账本
        if media_path := _extract_media_path(update):
            payload["media_path"] = media_path
    # 0806 计划 T5：questions 同随 update 帧迟到（首帧空壳场景），同频回填
    if questions := _extract_questions(update):
        payload["questions"] = questions
    # 0807-0148 计划 T3：选项结构载荷同频回填
    if question_options := _extract_question_options(update):
        payload["question_options"] = question_options
    if status == "in_progress":
        extracted = _extract_tool_output(
            update, keep_lines=_TAIL_WINDOW_LINES, keep_tail=True)
    else:
        extracted = _extract_tool_output(
            update, keep_tail=(kind == "execute"))
    if extracted:
        output, total_lines, images = extracted
        if output:
            payload["output"] = output
        payload["output_total_lines"] = total_lines
        if images:  # 0806 计划 T2：content 图片通道独立载荷字段
            payload["images"] = images
    # 终态帧惰性清理入参簿记（工具调用生命周期结束）
    if status in ("completed", "failed") and payload.get("tool_call_id"):
        _input_detail_seen.discard(payload["tool_call_id"])
    # 0919 计划 T2：update 帧 diff 提取——kimi 系 edit 的 diff content 在
    # in_progress 帧才发（首帧 content 为空壳、completed 帧只剩结果文本），
    # 故不限 status 盲提；_extract_diff 对非 diff 项返回 None，天然安全
    if diff := _extract_diff(update.get("content")):
        payload["diff_stat"], payload["diff_hunks"], truncated = diff
        if truncated:
            payload["diff_truncated"] = True
    elif write_diff := _extract_write_diff(update):
        # 0803 计划 T2：write 形态 in_progress 帧 content 无 diff 项（edit 有），
        # rawInput.content 全文合成 +N −0；条件只看 rawInput 结构不看后端身份，
        # 同构后端通用受益（R4）
        payload["diff_stat"], payload["diff_hunks"], truncated = write_diff
        if truncated:
            payload["diff_truncated"] = True
    # 0919 计划 T2：参数摘要迟到刷新——edit/read 首帧无 locations/rawInput
    # 的后端（kimi 系）在 in_progress 帧补齐 rawInput.path；限 edit/read
    # 两 kind（execute 的 command 摘要有 `$ ` 头 body 承接，不进副标题）
    if kind in ("edit", "read"):
        if summary := _tool_call_summary(update):
            payload["summary"] = summary
    if status == "completed" and kind == "think":
        # D5 成果摘要：从全文提取（先于 output 截断，防 task_result 被
        # 保头截断切掉）；软上限保头由 _truncate_lines 统一执行
        if extracted_raw := _extract_raw_output(update):
            summary, _ = _truncate_lines(
                _extract_result_summary(extracted_raw[0]),
                _BODY_SOFT_LIMIT_LINES, keep_tail=False)
            if summary.strip():
                payload["result_summary"] = summary
    if status == "failed":
        raw = update.get("rawOutput")
        error = None
        if isinstance(raw, dict):
            error = raw.get("error")
            if isinstance(error, dict):
                error = error.get("message")
        elif isinstance(raw, str):
            error = raw  # 中止场景的纯文本降级形态（同 _extract_raw_output 分流）
        if not (first := _truncate_line(error)):
            for item in update.get("content") or []:
                text = (item.get("content") or {}) if isinstance(item, dict) else {}
                if first := _truncate_line(text.get("text")):
                    break
        if first:
            payload["error"] = first
        # §2.3-5 错误全文：rawOutput.error 优先，无则退回输出全文（content
        # 兜底文本已在 _extract_raw_output 管线内）；净化 + 软上限保尾
        detail_source = None
        if isinstance(error, str) and error.strip():
            detail_source = error
        elif extracted_raw := _extract_raw_output(update):
            detail_source = extracted_raw[0]
        if detail_source:
            detail, _ = _truncate_lines(
                _clean_terminal_text(detail_source).strip("\n"),
                _BODY_SOFT_LIMIT_LINES, keep_tail=True)
            if detail.strip():
                payload["error_detail"] = detail
    return Chunk("tool_call_update", _tool_update_fallback(payload), payload=payload)


def map_session_update(obj: dict) -> Chunk | None:
    """session/update 通知帧 → Chunk；未消费类型返回 None。

    四 provider 私有 _map_update 的上收公共实现（1602 计划 T2，D4）：
    原 agent_message_chunk / agent_thought_chunk / usage_update 三分支
    行为等价原样搬入；tool_call 由「压一行灰字」扩展为结构化 Chunk；
    新增 tool_call_update / plan 两分支。available_commands_update /
    未知类型 / `_meta` 厂商扩展维持返回 None（F5；R1 纪律：泛化层
    不臆造协议）。
    """
    update = (obj.get("params") or {}).get("update") or {}
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        text = (update.get("content") or {}).get("text")
        return Chunk("text", text) if text else None
    if kind == "agent_thought_chunk":
        text = (update.get("content") or {}).get("text")
        return Chunk("reasoning", text) if text else None
    if kind == "tool_call":
        return _map_tool_call(update)
    if kind == "tool_call_update":
        return _map_tool_call_update(update)
    if kind == "plan":
        entries = _extract_todo_entries(update.get("entries"))
        return Chunk("todo", _todo_fallback_text(entries),
                     payload=TodoPayload(entries=entries))
    if kind == "usage_update":
        stats = map_usage_update(update)
        return Chunk("usage", "", usage=stats) if stats else None
    return None  # available_commands_update / _meta 扩展等


class AcpConnection:
    """长驻 `<agent> acp` 子进程：ndjson 帧收发、请求 id 配对、通知与反向请求分发。

    消息分两条出路：响应按 id 进 `_pending`（同步 `request()` 阻塞等待）；
    轮次内消息（session/update 通知与 prompt 响应）进 `_updates` 由 `chat()` 消费。
    同一时刻仅一个活跃轮次（各 provider 的 `_turn_lock` 保证）。
    """

    def __init__(self, bin_path: str, cwd: str, agent_name: str) -> None:
        """
        :param bin_path: agent CLI 二进制路径（argv 恒为 `[bin_path, "acp"]`）
        :param cwd: agent 子进程工作目录（多开模式由启动参数注入）
        :param agent_name: agent 显示名（错误文案用，如 "kimi acp"）
        """
        self._agent_name = agent_name
        try:
            self._proc = subprocess.Popen(
                [bin_path, "acp"],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # ACP 日志走 stderr/agent 自有日志，不属协议
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as e:
            raise RuntimeError(f"{agent_name} 启动失败：{e}") from e
        self._next = 0
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict]] = {}
        self._updates: queue.Queue[_TurnMessage] = queue.Queue()
        self._turn_id: int | None = None  # 活跃轮次的 prompt 请求 id（其响应改走 _updates）
        self.is_alive = True
        self._terminated = False  # 死讯注入幂等标志（reader EOF 与 terminate 谁先谁注入）
        #: 审批处理器（GUI 经 set_permission_handler 注入）；None=自动允许（C2 语义）
        self._permission_handler: PermissionHandler | None = None
        threading.Thread(target=self._reader, daemon=True).start()

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器（None = 自动允许，C2 语义）。"""
        self._permission_handler = handler

    # ------------------------------------------------------------------
    # 帧收发
    # ------------------------------------------------------------------
    def _send(self, msg: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _reader(self) -> None:
        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self._dispatch_line(line)
        finally:
            self.is_alive = False
            self._inject_dead()

    def _inject_dead(self) -> None:
        """向 `_updates` 注入死讯、向残余 `_pending` 注入错误（幂等）。

        reader 线程 EOF 与 terminate() 两路径共用，先到者注入；
        check-then-set 竞态下的重复注入无害（多余死讯由 purge_updates
        清理，已 pop 的 pending 不在表中）。
        """
        if self._terminated:
            return
        self._terminated = True
        self._updates.put(("dead", self._proc.poll()))
        for pending_queue in list(self._pending.values()):
            pending_queue.put({"error": {"code": -32099,
                                         "message": f"{self._agent_name} 进程意外退出"}})

    def _dispatch_line(self, line: str) -> None:
        """单帧分发：反向请求 / 轮次内通知与响应（→_updates）/ 控制响应（→_pending）。"""
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"[{self._agent_name}] 非 JSON 帧: {line[:200]}", file=sys.stderr)
            return
        if "method" in obj and "id" in obj:
            self._handle_reverse(obj)  # 反向请求须及时应答，防 agent 阻塞
            return
        if "method" in obj:
            if obj["method"] == "session/update":
                self._updates.put(("update", obj))
            return
        if "id" not in obj:
            return
        if obj["id"] == self._turn_id:
            self._updates.put(("response", obj))
            return
        if pending_queue := self._pending.get(obj["id"]):
            pending_queue.put(obj)

    def _handle_reverse(self, obj: dict) -> None:
        """反向请求（agent→client）：审批经 handler 路由（无 handler 自动允许）；
        fs/terminal 未声明能力，兜底 methodNotFound。须及时应答，防 agent 阻塞。"""
        if obj["method"] == "session/request_permission":
            params: PermissionParams = obj.get("params") or {}
            options = params.get("options") or []
            option_id: str | None = None
            if self._permission_handler is not None:
                try:
                    option_id = self._permission_handler(params)
                except Exception as e:  # noqa: BLE001 — handler 异常不阻塞 agent，兜底拒绝
                    print(f"[{self._agent_name}] 审批处理器异常: {e}", file=sys.stderr)
                if option_id is None:  # 用户取消/超时/handler 异常 → 兜底拒绝
                    option_id = self._pick_option(options, "reject_once")
            else:
                option_id = self._pick_option(options, "allow_once")  # C2 语义：等价 -p auto
            with self._write_lock:
                if option_id is not None:
                    self._send({"jsonrpc": "2.0", "id": obj["id"], "result": {
                        "outcome": {"outcome": "selected", "optionId": option_id}}})
                else:  # agent 未给任何选项：按协议回 cancelled
                    self._send({"jsonrpc": "2.0", "id": obj["id"], "result": {
                        "outcome": {"outcome": "cancelled"}}})
        else:
            with self._write_lock:
                self._send({"jsonrpc": "2.0", "id": obj["id"],
                            "error": {"code": -32601, "message": "method not found"}})

    @staticmethod
    def _pick_option(options: list[PermissionOption], kind: str) -> str | None:
        """按 kind 选 optionId（回应用 agent 提供的 optionId 原值，不臆造）。"""
        option = next((o for o in options if o.get("kind") == kind), None)
        return option["optionId"] if option else None

    # ------------------------------------------------------------------
    # 请求原语
    # ------------------------------------------------------------------
    def request(self, method: str, params: dict, timeout: float = _ACP_TIMEOUT_S) -> dict:
        """同步请求（轮次外使用）：阻塞至响应/超时/进程死亡。"""
        with self._write_lock:
            self._next += 1
            request_id = self._next
            pending_queue: queue.Queue[dict] = queue.Queue()
            self._pending[request_id] = pending_queue
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                self._pending.pop(request_id, None)
                raise RuntimeError(f"{self._agent_name} 写入失败：{e}") from e
        try:
            resp = pending_queue.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"{self._agent_name} 请求超时：{method}") from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"{self._agent_name} {method} 错误 {err.get('code')}：{err.get('message')}")
        return resp.get("result") or {}

    def begin_turn(self, method: str, params: dict) -> int:
        """登记轮次请求：其响应与通知同走 `_updates`，由 chat() 逐条消费。"""
        with self._write_lock:
            self._next += 1
            self._turn_id = self._next
            try:
                self._send({"jsonrpc": "2.0", "id": self._turn_id, "method": method, "params": params})
            except (OSError, ValueError) as e:
                self._turn_id = None
                raise RuntimeError(f"{self._agent_name} 写入失败：{e}") from e
            return self._turn_id

    def end_turn(self) -> None:
        self._turn_id = None

    def cancel_turn(self, session_id: str) -> bool:
        """取消当前轮次：发 `session/cancel` 通知（agent 收到后结束本轮）。

        无活跃轮次或连接已死时返回 False（无害 no-op）；
        可从任意线程调用（写锁串行化）。连接进程保留，会话不毁。
        """
        if self._turn_id is None or not self.is_alive:
            return False
        try:
            with self._write_lock:
                self._send({"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": session_id}})
        except (OSError, ValueError):
            return False  # 写入失败说明连接将死，chat 路径按 dead 收尾
        return True

    def purge_updates(self) -> None:
        """清空上一轮残留的迟到通知，防串轮。"""
        while True:
            try:
                self._updates.get_nowait()
            except queue.Empty:
                return

    def next_update(self) -> _TurnMessage:
        """取下一条轮次内消息（阻塞；无超时：agent 轮次可长达数分钟）。"""
        return self._updates.get()

    def terminate(self) -> None:
        """终止子进程并主动注入死讯：consumer 立即醒来，不必等 reader EOF。

        标签关闭路径（2026-0722-1117 计划 T5）：阻塞在 next_update()/
        request() 的 worker 依赖死讯/错误帧收尾；主动注入把解封延迟
        从"reader 反应过来"压到毫秒级（check-then-set 幂等，见 _inject_dead）。
        """
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.is_alive = False
        self._inject_dead()


__all__ = [
    "AcpConnection",
    "PermissionOption",
    "ToolCallInfo",
    "PermissionParams",
    "PermissionHandler",
    "map_session_update",
    "map_usage_update",
    "build_prompt_blocks",
]
