"""Kimi 权限请求分类：把原始 ACP 参数解释成本厂商的中性审批请求。

Kimi 的提问走 `session/request_permission`，但 ACP v1 没有提问语义标识：
实证帧的 `toolCall.title` 为 `AskUserQuestion`，问题文本在 `content` 块中
（`文档/帧存档/askuser_*.json`）；历史帧还存在 `rawInput.questions` 列表
与 reasonix 式 `rawInput.question` + `options` 单问题形态。三种形态都必须
保留，否则提问会被当成普通工具审批并在 auto_all 下自动选答。

本模块只做分类与中性问题文本提取；卡片布局、答案渲染和 optionId 回传
分别归 Kimi 卡片包与公共审批宿主。原始 `params` 原样放进
`PermissionRequest`，绝不改写成其他厂商口径。
"""
from ide.llm.providers.acp import PermissionParams, PermissionRequest

#: Kimi 侧提问工具名（归一化后小写）；`ask` 为历史兼容帧，当前后台不再
#: 产出但旧会话/截图蓝本仍可能回放，不能删。
_QUESTION_TOOL_NAMES = frozenset({"askuserquestion", "ask"})


def _normalize_tool_name(title: str) -> str:
    """去 `mcp__server__` 命名空间前缀取末段并小写化。"""
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return name.lower()


def _question_texts(tool_call: dict) -> tuple[str, ...]:
    """从 Kimi 提问载荷提取问题文本（仅供中性对话框/卡片匹配展示）。

    结构化 `rawInput.questions` 优先；`rawInput.question` 单问题次之；
    `toolCall.content` 文本块兜底（Kimi 实证：权限帧不带 rawInput，问题
    原文在 content 块）。只提取文本，不解释答案选项。
    """
    questions: list[str] = []
    raw = tool_call.get("rawInput")
    if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
        questions = [q["question"] for q in raw["questions"]
                     if isinstance(q, dict) and isinstance(q.get("question"), str)]
    if questions:
        return tuple(questions)
    if isinstance(raw, dict) and isinstance(raw.get("question"), str) \
            and raw["question"].strip():
        return (raw["question"].strip(),)
    for block in tool_call.get("content") or []:
        text = ((block or {}).get("content") or {}).get("text")
        if isinstance(text, str) and text.strip():
            questions.append(text.strip())
    return tuple(questions)


def build_permission_request(params: PermissionParams) -> PermissionRequest:
    """把 Kimi 原始审批参数分类为 permission/question，并保留原始参数。"""
    tool_call = params.get("toolCall") or {}
    raw = tool_call.get("rawInput")
    has_question_payload = isinstance(raw, dict) and (
        isinstance(raw.get("questions"), list)
        or (isinstance(raw.get("question"), str)
            and isinstance(raw.get("options"), list)))
    title = (tool_call.get("title") or "").strip()
    kind = ("question"
            if has_question_payload
            or _normalize_tool_name(title) in _QUESTION_TOOL_NAMES
            else "permission")
    return PermissionRequest(
        params=params,
        kind=kind,
        questions=_question_texts(tool_call) if kind == "question" else (),
    )
