"""Local 权限请求分类：按 Local/Dream ACP 自有结构解释，不导入 Kimi。

Local CLI 与 Dream CLI 共用线协议（`dream-acp/protocol/dream-acp-v1.md`），
该协议由 Kimi ACP 协议资料复制改名而来，因此 Local 侧同样存在
`AskUserQuestion`/`ask` 标题、`rawInput.questions` 列表与历史
`rawInput.question` + `options` 形态。这里按 Local 自己的协议文档和卡片
工厂词表保留完整分类，属于受控重复：不 import Kimi 权限模块，也不共享
工具名表，任一家规则变化只改本厂商文件。

原始 `params` 原样放进 `PermissionRequest`；问题文本只做中性提取，卡片
布局与答案渲染归 Local 卡片包。
"""
from ide.llm.providers.acp import PermissionParams, PermissionRequest

#: Local 侧提问工具名（归一化后小写）；与 Local 卡片工厂的提问词表一致
_QUESTION_TOOL_NAMES = frozenset({"askuserquestion", "ask"})


def _normalize_tool_name(title: str) -> str:
    """去 `mcp__server__` 命名空间前缀取末段并小写化。"""
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return name.lower()


def _question_texts(tool_call: dict) -> tuple[str, ...]:
    """从 Local 提问载荷提取问题文本（结构化优先，content 文本兜底）。"""
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
    """把 Local 原始审批参数分类为 permission/question，并保留原始参数。"""
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
