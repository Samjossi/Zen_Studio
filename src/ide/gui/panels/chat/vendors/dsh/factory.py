"""DSH 工具名 → DSH 具体卡片的唯一分派入口。

本表只服务 DSH：键为本厂商真实工具名（归一化后小写）。未命中按
ACP tool_kind 回退本厂商专类，最后落入本厂商通用卡；不得以统一
card_kind 或公共工厂把其他厂商工具名接进来。
"""
from pathlib import Path

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors.dsh.cards import (
    BashCard,
    DiffCard,
    McpCard,
    QuestionCard,
    SubagentCard,
    TextOutputCard,
    ToolCard,
    MediaReadCard,
    TodoListCard,
)

#: 工具名 → 卡片类（键为归一化后小写工具名，仅含本厂商真实工具名）
_TOOL_NAME_CARDS: dict[str, type] = {
    # DSH snake_case 工具名；kind 由 mapper 兜底为 read/think
    "read_image": MediaReadCard,
    "todo_write": TodoListCard,
    "subagent": SubagentCard,
}


def _normalize_tool_name(title: str) -> str:
    """去 `mcp__server__` 命名空间前缀取末段并小写化。"""
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return name.lower()


#: MCP 服务器工具命名空间前缀（区分「MCP 工具」与「未知工具/AskUserQuestion」）
_MCP_TOOL_PREFIX = "mcp__"


def _is_mcp_tool(title: str) -> bool:
    return title.strip().lower().startswith(_MCP_TOOL_PREFIX)


def is_question_tool(title: str) -> bool:
    """本厂商工具名是否分派到提问卡（子代理卡据此禁止嵌套交互卡）。"""
    return _TOOL_NAME_CARDS.get(_normalize_tool_name(title)) is QuestionCard


def make_tool_card(colors: CardColors, open_state: OpenStateMap,
                   payload: dict,
                   workspace_root: Path | None = None) -> ToolCard:
    """工具名二级分派优先，未命中按 tool_kind 分派本厂商专类。"""
    title = payload.get("title") or ""
    if title:
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
    # MCP 服务器工具用 🔌；未知工具与 AskUserQuestion 仍由 McpCard 的 🧩 兜底
    header_icon = "🔌" if _is_mcp_tool(title) else None
    return McpCard(colors, open_state, payload, workspace_root,
                   header_icon=header_icon)
