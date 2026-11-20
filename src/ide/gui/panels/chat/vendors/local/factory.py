"""Local 工具名 → Local 具体卡片的唯一分派入口。

本表只服务 Local：键为本厂商真实工具名（归一化后小写）。未命中按
ACP tool_kind 回退本厂商专类，最后落入本厂商通用卡；不得以统一
card_kind 或公共工厂把其他厂商工具名接进来。
"""
from pathlib import Path

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors.local.cards import (
    BashCard,
    DiffCard,
    McpCard,
    MediaReadCard,
    QuestionCard,
    SubagentCard,
    TextOutputCard,
    TodoListCard,
    ToolCard,
)

#: 工具名 → 卡片类（键为归一化后小写工具名，仅含本厂商真实工具名）
_TOOL_NAME_CARDS: dict[str, type] = {
    # Local 自有词表：保留旧共享表里 Local 可能收到的通用工具名，但不收
    # DSH 的 read_image/todo_write；未命中的标题由下方 kind 回退承接。
    "askuserquestion": QuestionCard,
    "ask": QuestionCard,
    "agent": SubagentCard,
    "task": SubagentCard,
    "readmediafile": MediaReadCard,
    "todolist": TodoListCard,
    "todowrite": TodoListCard,
}


def _normalize_tool_name(title: str) -> str:
    """去 `mcp__server__` 命名空间前缀取末段并小写化。"""
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return name.lower()


def is_question_tool(title: str) -> bool:
    """本厂商工具名是否分派到提问卡（子代理卡据此禁止嵌套交互卡）。"""
    return _TOOL_NAME_CARDS.get(_normalize_tool_name(title)) is QuestionCard


def make_tool_card(colors: CardColors, open_state: OpenStateMap,
                   payload: dict,
                   workspace_root: Path | None = None) -> ToolCard:
    """工具名二级分派优先，未命中按 tool_kind 分派本厂商专类。"""
    if title := payload.get("title"):
        if card_cls := _TOOL_NAME_CARDS.get(_normalize_tool_name(title)):
            return card_cls(colors, open_state, payload, workspace_root)
    kind = payload.get("tool_kind") or "other"
    if kind == "execute":
        return BashCard(colors, open_state, payload, workspace_root)
    if kind == "edit":
        return DiffCard(colors, open_state, payload, workspace_root)
    if kind in ("read", "search", "fetch"):
        return TextOutputCard(colors, open_state, payload, workspace_root)
    if kind == "think":
        return SubagentCard(colors, open_state, payload, workspace_root)
    return McpCard(colors, open_state, payload, workspace_root)
