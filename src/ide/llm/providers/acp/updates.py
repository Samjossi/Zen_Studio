"""过渡期公共 session/update 映射（T1 暂存旧实现）。

T2 将按厂商拆入 kimi/dsh/local 的 updates.py。本模块仍包含多厂商键表、
工具名兜底与模块级账本，不得作为稳定公共出口被 GUI 直接消费。
"""
import json
import re
from pathlib import Path

from ide.llm.base import (
    Chunk,
    TodoEntry,
    TodoPayload,
    ToolCallPayload,
    ToolUpdatePayload,
)
from ide.llm.providers.acp.content import (
    _BODY_SOFT_LIMIT_LINES,
    _OUTPUT_LINE_MAX,
    _clean_terminal_text,
    _collect_content_block,
    _dispatch_output_string,
    _extract_diff,
    _truncate_line,
    _truncate_lines,
)
from ide.llm.providers.acp.protocol import map_usage_update

_TAIL_WINDOW_LINES = 5         # in_progress 尾窗行数（0645 计划 §2.1 BashCard
                               # 运行中实时帧规格，同 0619-T6）


# ----------------------------------------------------------------------
# 系统回执过滤（0806 计划 T2：工具出参夹带的系统指令性文本剔除，
# 白名单模式保守起步——只过滤已确证的系统提示，宁漏勿错）
# ----------------------------------------------------------------------
_RECEIPT_PATTERNS = [
    re.compile(r"(?ms)^Todo list updated\.\s*$"),
    re.compile(r"(?ms)^Ensure that you continue to use the todo list.*$"),
    # 0808-0627 计划 T1：kimi 模板「Current todo list」多行块——该行及
    # 其后连续 [status] 行整体剔除（清单区渲染后回执与清单信息完全重复；
    # 锚定行首 + 连续短括号状态行结构防宽泛匹配误伤正常输出）
    re.compile(r"(?ms)^[ \t]*Current todo list:?[ \t]*\r?\n"
               r"(?:^[ \t]*\[[^\]\n]{1,20}\][^\n]*(?:\r?\n|$))+"),
    # 后续确证的模式在此追加，集中维护
]


def _filter_tool_receipt(text: str) -> str:
    """剔除工具出参中夹带的系统指令性回执文本（白名单模式，保守起步）。"""
    for pattern in _RECEIPT_PATTERNS:
        text = pattern.sub("", text)
    return text



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


_TASK_RESULT_RE = re.compile(r"<task_result>(.*?)</task_result>", re.DOTALL)



def _extract_write_diff(update: dict) -> tuple[str, list[dict], bool] | None:
    """write 形态合成 diff（0803 计划 T2）：rawInput.content 全文 → `+N −0`。

    kimi 系 write 新建文件会话的 in_progress 帧 content **无 diff 项**
    （edit 有），diff 徽标/hunk 无数据源（原取证
    `.temp/write_frames_kimi.json` 未入库、已随 .temp 清理丢失；
    0735 计划 T3 重取证存档
    `文档/帧存档/write_20260812_075201.json`——首帧空壳、
    in_progress 帧 rawInput={path,content} 且 content 无 diff 项的
    形态实证一致）；但 `rawInput.content` 是写入全文，
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
#: snake_case 键（file_path/old_string/new_string/replace_all）为 DSH 命名
#: 口径，与 kimi 系 camelCase 并存并联——漏收即整族入参区落 JSON 兜底
_INPUT_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "execute": ("command",),
    "edit": ("path", "filePath", "file_path", "oldString", "newString",
             "old_string", "new_string", "replace_all"),
    "read": ("path", "filePath", "file_path", "offset", "limit"),
    "search": ("pattern", "query", "path", "include"),
    "fetch": ("url",),
    "think": ("description", "prompt", "subagent_type"),
    # 0806 计划 T1：ReadMediaFile/AskUserQuestion/Agent/TodoList 等
    # ACP kind 恒为 "other"，无白名单则已知键提取必然落空
    "other": ("path", "filePath", "file_path", "url", "query", "command",
              "prompt", "description", "questions", "todos", "name"),
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
    path = raw.get("path") or raw.get("filePath") or raw.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    ext = Path(path).suffix.lower()
    if ext in _MEDIA_IMAGE_EXTS:
        return path
    if ext:
        return None  # 非图扩展名：无图可缩
    # 无扩展名兜底工具名集合：kimi readmediafile + DSH read_image（DSH
    # 看图 schema 仅图片格式，白名单已覆盖，此为无扩展名图片防御口径）
    title = update.get("title")
    if isinstance(title, str) and \
            title.strip().split("__")[-1].lower() in ("readmediafile", "read_image"):
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
    实证蓝本：kimi 帧（文档/帧存档/askuser_*.json）多选字段名为
    `multi_select`（蛇形）；reasonix 帧（文档/帧存档/ask_reasonix_*.json）
    为 `multiSelect`（驼峰，0812-0952 计划 T2 兼容补入）；header 为可选短标题。
    """
    raw = update.get("rawInput")
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        return None
    items: list[dict] = []
    for q in raw["questions"]:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        item: dict = {"question": q["question"],
                      "multi_select": bool(q.get("multi_select") or q.get("multiSelect"))}
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


#: todo status 外部词表 → 内部约定归一（0812-0918 计划 T1；渲染层只认
#: pending/in_progress/completed/cancelled）：kimi 系完成态为 "done"
#: （回执 [done] 同源，todolist_a2_frames_20260812_080109.json 实证）；
#: 未收录取值原样透传（渲染层按缺省 pending 容错，降级方向安全）
_TODO_STATUS_NORMALIZE = {"done": "completed"}


def _extract_todo_entries(items: object) -> list[TodoEntry]:
    """plan.entries / rawInput.todos → TodoEntry 列表（两通道同构，F1）。

    条目文本键两系归一：kimi 系为 title，kilocode/opencode 系为
    content——content 或 title 任一字符串即收录（content 优先），
    内部统一 content 存储；status 经 _TODO_STATUS_NORMALIZE 词表归一
    （kimi 系 "done" → "completed"），未收录取值原样透传（渲染层
    按缺省 pending 容错）；priority 为字符串才保留，结构不符的条目
    静默跳过。
    """
    entries: list[TodoEntry] = []
    if not isinstance(items, list):
        return entries
    for item in items:
        if not isinstance(item, dict):
            continue
        # kimi 系 TodoList 条目文本键为 title；kilocode/opencode 系
        # todowrite 为 content——两形归一，内部统一 content 存储
        text = item.get("content")
        if not isinstance(text, str):
            text = item.get("title")
        if not isinstance(text, str):
            continue
        entry = TodoEntry(content=text)
        if isinstance(item.get("status"), str):
            entry["status"] = _TODO_STATUS_NORMALIZE.get(
                item["status"], item["status"])
        if isinstance(item.get("priority"), str):
            entry["priority"] = item["priority"]
        entries.append(entry)
    return entries


#: 跨调用 todo 快照簿记（0808-0627 计划 T1：kilocode TodoView.calculate
#: 复刻——before 口径为「会话当前 todo 状态」的跨调用 diff，非调用内帧间；
#: sessionId 键控防跨会话/跨标签快照串联误标。快照跨轮连续不按帧清理
#: （会话级单值，惰性增长量级极小，同 _input_detail_seen 簿记先例）。
#: changed 只是高亮标记，错标无害（降级方向安全）。
_last_todo_snapshots: dict[str, list[TodoEntry]] = {}


def _apply_todo_diff(session_id: str,
                     entries: list[TodoEntry]) -> list[TodoEntry]:
    """跨调用 diff：与上次快照逐项比对，不一致项标 changed=True
    （渲染层 TodoListCard 高亮数据源，仅供人类查看，不回传 AI）。

    比对口径抄 kilocode todo-view.ts `same()`：content+status+priority
    全等（按位逐项）；同次调用的多帧快照幂等（内容相同则 changed 全
    False 自然消退）；before 为空的首卡全量高亮语义正确（一切皆新态）。
    """
    before = _last_todo_snapshots.get(session_id) or []
    result: list[TodoEntry] = []
    for index, entry in enumerate(entries):
        prev = before[index] if index < len(before) else None
        if prev is None or any(
                entry.get(key) != prev.get(key)
                for key in ("content", "status", "priority")):
            entry = {**entry, "changed": True}
        result.append(entry)
    _last_todo_snapshots[session_id] = result
    return result


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
        return _truncate_line(raw.get("path") or raw.get("filePath")
                              or raw.get("file_path"))
    if kind == "search":
        return _truncate_line(raw.get("pattern") or raw.get("query"))
    if kind == "fetch":
        return _truncate_line(raw.get("url"))
    if kind == "think":
        return _truncate_line(raw.get("description") or raw.get("prompt"))
    return None


def _split_hierarchical_tid(tool_call_id: str) -> str | None:
    """层级 toolCallId（`父ID/子ID`）→ 父 ID；无层级（无 `/`）返回 None。

    0813-1919 计划 T1（子代理活动嵌套显示）：reasonix 原生下发子代理
    内部 tool_call/tool_call_update 帧，父子关系编码于层级 toolCallId
    （实测帧存档 subagent_probe_reasonix_20260813_190102.json——
    `call_00_P14w9p.../call_00_twRhc...`，首段即父调用全串）；agent 侧
    一层委派封顶（两级），partition 首段即父 ID。toolCallId 保留全串
    作锚点键（唯一性），父指针独立载荷字段下传。
    纯字符串判定：无层级 ID 的后端（kimi `0:tool_…` / kilocode / dream）
    天然零影响；kimi 旁路合成帧复用同一形态（tid 拼为 `父/子`）。
    """
    parent, sep, _child = tool_call_id.partition("/")
    return parent if sep and parent else None


#: 工具名 → kind 兜底推导表：仅在后端 kind 缺失或敷衍上报 "other" 时
#: 生效，永不覆盖显式上报（dsh-acp 投影层 kind 恒为 "other" 且刻意
#: 不开关，兜底让一切同构后端受益）；未收录名保持 "other" 落通用卡，
#: 绝不猜测。键为归一化后小写名（查表前统一 `-`→`_`：DSH 连字符命名
#: fs-search 与 snake_case 双形态单写法兼容）。todo_write 显式登记
#: "other"：清单渲染走卡片侧工具名分派，不借 kind 通道
_FALLBACK_TOOL_KINDS: dict[str, str] = {
    "bash": "execute", "pwsh": "execute", "shell": "execute",
    "terminal": "execute",
    "write": "edit", "edit": "edit", "patch": "edit",
    "read": "read", "read_image": "read",
    "grep": "search", "glob": "search", "fs_search": "search",
    "web": "fetch", "web_search": "fetch", "web_fetch": "fetch",
    "fetch": "fetch",
    "todo_write": "other",
    "subagent": "think", "task": "think",
}


def _fallback_tool_kind(title: object) -> str | None:
    """工具名 → 兜底 kind（查 _FALLBACK_TOOL_KINDS）；未收录/非字符串返回 None。

    归一化口径与渲染层工具名分派同源（剥 `mcp__` 命名空间前缀取末段、
    小写），另统一 `-`→`_` 使表内只保留下划线单写法。
    """
    if not isinstance(title, str):
        return None
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return _FALLBACK_TOOL_KINDS.get(name.lower().replace("-", "_"))


#: kind 兜底推导回填账本（与 _input_detail_seen 同构）：首帧推导命中即
#: 按 toolCallId 登记；update 帧常缺 kind（F3），据此回填首帧推导结果，
#: 运行中/完成态帧 kind 稳定不漂移；completed/failed 终态帧惰性清理
_derived_tool_kinds: dict[str, str] = {}


def _map_tool_call(update: dict, session_id: str = "") -> Chunk:
    """tool_call → 结构化 Chunk。

    0808-0627 计划 T1：todowrite/TodoList 首帧特判（改产 kind="todo"
    会话级单卡）撤销——每次调用回归普通工具卡流程，由渲染层
    TodoListCard 承担清单可视化（每次调用一张新卡，历史留痕）；
    kind="todo" 仅余 plan 通道。首帧即带 rawInput.todos 时
    （kilocode/opencode 系形态）提取装入载荷，走跨调用 diff 管线。
    """
    raw = update.get("rawInput") or {}
    payload = ToolCallPayload()
    if isinstance(update.get("toolCallId"), str):
        payload["tool_call_id"] = update["toolCallId"]
        # 0813-1919 计划 T1：层级 toolCallId → 父指针（子代理内部活动帧）
        if parent := _split_hierarchical_tid(update["toolCallId"]):
            payload["parent_tool_call_id"] = parent
    payload["title"] = update.get("title") or "?"
    tool_kind = update.get("kind") if isinstance(update.get("kind"), str) else "other"
    if tool_kind == "other":
        # kind 缺失/敷衍上报时按工具名兜底推导；命中即入账本供 update
        # 帧回填，并注入 update 副本让下游提取（摘要/入参/write diff
        # 合成）同帧生效。显式上报的 kind 一律原样透传不进此分支
        if (derived := _fallback_tool_kind(update.get("title"))) \
                and derived != "other":
            tool_kind = derived
            update = {**update, "kind": derived}
            if tid := payload.get("tool_call_id"):
                _derived_tool_kinds[tid] = derived
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
    # 0808-0627 计划 T1：todowrite/TodoList 清单快照载荷（首帧齐备形态，
    # kilocode/opencode 系；kimi 系首帧空壳由 update 帧检出通道承接），
    # 走跨调用 diff 管线附 changed 高亮标记
    if isinstance(raw.get("todos"), list):
        if entries := _apply_todo_diff(
                session_id, _extract_todo_entries(raw["todos"])):
            payload["todos"] = entries
    return Chunk("tool_call", _tool_call_fallback(payload), payload=payload)


def _map_tool_call_update(update: dict, session_id: str = "") -> Chunk | None:
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
        # 0813-1919 计划 T1：层级 toolCallId → 父指针（同首帧路径）
        if parent := _split_hierarchical_tid(update["toolCallId"]):
            payload["parent_tool_call_id"] = parent
    status = update.get("status")
    if not isinstance(status, str) or not status:
        return None  # 无状态可报（bash 输出快照等部分更新帧，F3 字段可缺省）
    payload["status"] = status
    if isinstance(update.get("title"), str):
        payload["title"] = update["title"]
    kind = update.get("kind")
    # 首帧兜底推导回填：update 帧常缺 kind 或仍敷衍上报 "other"，账本
    # 命中即注入 update 副本——截断方向、迟到 command、write diff 合成、
    # think 成果摘要等下游全部按推导 kind 走；账本只收推导命中帧，
    # 显式上报的后端（kimi）永不入账，此分支对其恒 no-op
    if (not isinstance(kind, str) or not kind or kind == "other") \
            and (derived := _derived_tool_kinds.get(payload.get("tool_call_id") or "")):
        kind = derived
        update = {**update, "kind": derived}
    # 0806 计划 T1：update 帧迟到入参回填——kimi 系首帧为空壳、rawInput
    # 在 in_progress 帧才补发；簿记去重（首帧优先，后续帧不重复回填）
    if (tid := payload.get("tool_call_id")) and tid not in _input_detail_seen:
        _record_input_detail(payload, _format_input_detail(update))
        # 0158 计划 T1：media_path 与 input_detail 同频回填、同一账本
        if media_path := _extract_media_path(update):
            payload["media_path"] = media_path
        # 0812-0918 计划 T2-1：execute 迟到 command 提取——kimi 系首帧
        # 空壳无 rawInput，command 随 in_progress 帧迟到（迟到帧 kind=
        # "execute" 在场，execute_20260812_075201.json 第 14 帧实证，
        # 故限 kind == "execute"）；与 input_detail 同账本防重复——
        # 首帧齐备形态（kilocode/opencode）已登记不重复提取，kimi 迟到
        # 形态只提首次。净化口径同 _map_tool_call 首帧路径
        if kind == "execute":
            raw = update.get("rawInput") or {}
            if isinstance(raw.get("command"), str):
                if command := _clean_terminal_text(raw["command"]).strip():
                    payload["command"] = command
    # 0806 计划 T5：questions 同随 update 帧迟到（首帧空壳场景），同频回填
    if questions := _extract_questions(update):
        payload["questions"] = questions
    # 0807-0148 计划 T3：选项结构载荷同频回填
    if question_options := _extract_question_options(update):
        payload["question_options"] = question_options
    # 0808-0627 计划 T1：todowrite/TodoList 清单快照检出（kimi 系首帧
    # 空壳，rawInput.todos 随 in_progress 帧迟到）。每帧都提、不设去重
    # 账本——todo 语义是快照推进（与 input_detail/media_path「首帧
    # 优先」语义不同）；同走跨调用 diff 管线，同帧重复快照 changed 自然
    # 消退（幂等）
    raw_input = update.get("rawInput")
    if isinstance(raw_input, dict) and isinstance(raw_input.get("todos"), list):
        if entries := _apply_todo_diff(
                session_id, _extract_todo_entries(raw_input["todos"])):
            payload["todos"] = entries
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
    # 终态帧惰性清理入参与推导簿记（工具调用生命周期结束）
    if status in ("completed", "failed") and payload.get("tool_call_id"):
        _input_detail_seen.discard(payload["tool_call_id"])
        _derived_tool_kinds.pop(payload["tool_call_id"], None)
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
    # 0808-0627 计划 T1：todo 跨调用 diff 簿记的会话键（params 层
    # sessionId；缺省空串——mock/异常帧防御，单值兜底不臆造）
    session_id = (obj.get("params") or {}).get("sessionId") or ""
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        text = (update.get("content") or {}).get("text")
        return Chunk("text", text) if text else None
    if kind == "agent_thought_chunk":
        text = (update.get("content") or {}).get("text")
        return Chunk("reasoning", text) if text else None
    if kind == "tool_call":
        return _map_tool_call(update, session_id)
    if kind == "tool_call_update":
        return _map_tool_call_update(update, session_id)
    if kind == "plan":
        entries = _extract_todo_entries(update.get("entries"))
        return Chunk("todo", _todo_fallback_text(entries),
                     payload=TodoPayload(entries=entries))
    if kind == "usage_update":
        stats = map_usage_update(update)
        return Chunk("usage", "", usage=stats) if stats else None
    return None  # available_commands_update / _meta 扩展等
