"""DSH 工具活动载荷类型。

只描述 DSH mapper 产出的卡片数据字段；不得被其他厂商导入，也不作为
通用层契约。DSH 的真实工具名与 rawInput 键为 snake_case，字段解释只在
DSH 自己的 mapper 与卡片包内发生。
"""
from typing import NotRequired, TypedDict


class TodoEntry(TypedDict, total=False):
    """DSH todo_write / plan 清单条目。"""

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
    """DSH tool_call 首帧载荷。"""

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


class ToolUpdatePayload(TypedDict, total=False):
    """DSH tool_call_update 载荷。"""

    tool_call_id: str
    status: str
    title: str
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
