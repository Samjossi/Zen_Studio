"""Kimi 工具活动载荷类型。

只描述 Kimi mapper 产出的卡片数据字段；不得被其他厂商导入，也不作为
通用层契约。字段值均为 Kimi 原始帧经本厂商规则预格式化后的展示数据，
通用 GUI 不解析 rawInput。
"""
from typing import NotRequired, TypedDict


class TodoEntry(TypedDict, total=False):
    """Kimi TodoList 清单条目。"""

    content: str
    status: str
    priority: str
    #: 相对本会话上次快照的变更标记，供清单卡高亮；仅供人类查看。
    changed: bool


class TodoPayload(TypedDict):
    """kind="todo" 的载荷：entries 为全量快照。"""

    entries: list[TodoEntry]


class QuestionOption(TypedDict, total=False):
    label: str
    description: str


class QuestionItem(TypedDict):
    question: str
    multi_select: bool
    options: list[QuestionOption]
    header: NotRequired[str]


class ToolCallPayload(TypedDict, total=False):
    """Kimi tool_call 首帧载荷。"""

    tool_call_id: str
    title: str
    tool_kind: str
    summary: str
    is_subagent: bool
    command: str
    diff_stat: str
    diff_hunks: list[dict]
    diff_truncated: bool
    input_detail: str
    questions: list[str]
    question_options: list[QuestionItem]
    media_path: str
    todos: list[TodoEntry]
    #: 子代理内部活动的父调用 ID；Kimi 层级 toolCallId 或 wire 旁路合成帧。
    parent_tool_call_id: str


class ToolUpdatePayload(TypedDict, total=False):
    """Kimi tool_call_update 载荷。"""

    tool_call_id: str
    status: str
    title: str
    tool_kind: str
    error: str
    error_detail: str
    output: str
    output_total_lines: int
    command: str
    result_summary: str
    diff_stat: str
    diff_hunks: list[dict]
    diff_truncated: bool
    summary: str
    input_detail: str
    images: list[str]
    questions: list[str]
    question_options: list[QuestionItem]
    media_path: str
    todos: list[TodoEntry]
    parent_tool_call_id: str
