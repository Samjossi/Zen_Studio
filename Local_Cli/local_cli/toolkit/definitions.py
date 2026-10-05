"""工具定义与 system prompt 文案：格式约定（§3.2）的单一出处。

schema 表、围栏语言标记、结果回灌前缀、system prompt 全部收敛在本模块——
parser/executor/tool_loop/mock 各处只 import 不另写副本，文案改一处即全链路
生效（AFCP 3.1：契约集中声明）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 工具名（模型↔服务端线格式的魔法字符串，命名常量）
TOOL_NAME_WRITE_FILE = "write_file"
TOOL_NAME_READ_FILE = "read_file"
TOOL_NAME_LIST_DIR = "list_dir"

#: 围栏代码块语言标记（§3.2）：模型以此标记输出工具调用 JSON；IDE 端按普通
#: 代码块渲染，即使裸露也可读
TOOL_CALL_FENCE_LANGUAGE = "tool_call"

#: 围栏块整块的匹配模式（§3.2 线格式的单一出处）：parser 用它提取、Mock 用它
#: 消毒回显文本；取末个/拒绝多块等语义归 parser，本模块只定义「块长什么样」
TOOL_CALL_BLOCK_PATTERN = re.compile(
    rf"```{TOOL_CALL_FENCE_LANGUAGE}[ \t]*\r?\n(.*?)```", re.DOTALL)

#: 围栏起始标记（```+语言标记）：流式过滤器（fence_filter）识别的最短无歧义
#: 前缀——识别容忍度与 TOOL_CALL_BLOCK_PATTERN 严格对齐（标记前不接受空格，
#: 与 parser 同源防漂移）；标记后的空白/换行变体归 parser 裁决，过滤器不管
TOOL_CALL_FENCE_START_MARKER = f"```{TOOL_CALL_FENCE_LANGUAGE}"

#: 工具结果回灌前缀（§3.2）：结果以 user 角色消息回灌模型，前缀区分成败
TOOL_RESULT_FEEDBACK_PREFIX = "【工具结果】"
TOOL_ERROR_FEEDBACK_PREFIX = "【工具错误】"


@dataclass(frozen=True)
class ToolParameter:
    """一个工具参数（AFCP 3.1：显式数据结构）。"""
    name: str
    description: str
    is_required: bool


@dataclass(frozen=True)
class ToolDefinition:
    """一个工具的完整契约：schema + 审批策略（§3.3）。"""
    name: str
    description: str
    parameters: tuple[ToolParameter, ...]
    #: 写操作必须经 session/request_permission 审批；只读工具免审批（§3.3）
    needs_permission: bool


_PATH_PARAMETER = ToolParameter(
    name="path",
    description="相对会话工作目录的相对路径（绝对路径与 .. 一律拒绝）",
    is_required=True,
)

#: 工具 schema 表（§3.3）：parser 的白名单校验与 system prompt 文案共用此表
TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=TOOL_NAME_WRITE_FILE,
        description="把 content 写入 path 指向的文件（父目录自动创建，UTF-8）",
        parameters=(
            _PATH_PARAMETER,
            ToolParameter(name="content", description="要写入的完整文件内容",
                          is_required=True),
        ),
        needs_permission=True,
    ),
    ToolDefinition(
        name=TOOL_NAME_READ_FILE,
        description="读取 path 指向的文件内容（超长截断）",
        parameters=(_PATH_PARAMETER,),
        needs_permission=False,
    ),
    ToolDefinition(
        name=TOOL_NAME_LIST_DIR,
        description="列出 path 指向的目录内容（条目过多截断）",
        parameters=(_PATH_PARAMETER,),
        needs_permission=False,
    ),
)

TOOL_DEFINITION_MAP: dict[str, ToolDefinition] = {
    definition.name: definition for definition in TOOL_DEFINITIONS
}


def build_system_prompt() -> str:
    """组装注入 GGUF 链路的 system prompt：工具表 + §3.2 格式约定。

    工具说明由 TOOL_DEFINITIONS 生成而非手抄，保证文案与 schema 不漂移。
    """
    tool_lines = [
        f"- {definition.name}（{definition.description}）。参数："
        + "、".join(f"{parameter.name}（{parameter.description}）"
                    for parameter in definition.parameters)
        for definition in TOOL_DEFINITIONS
    ]
    return "\n".join([
        "你可以调用以下工具读写用户项目中的文件，全部操作限制在会话工作目录内：",
        *tool_lines,
        "",
        "需要调用工具时，在回复中输出恰好一个如下格式的围栏代码块：",
        "",
        f"```{TOOL_CALL_FENCE_LANGUAGE}",
        '{"tool": "write_file", "args": {"path": "相对路径.md", "content": "文件内容"}}',
        "```",
        "",
        "约定：",
        "- 每条回复至多一个工具调用；需要连续操作时，等待工具结果后再发下一个调用。",
        "- path 一律为相对会话工作目录的相对路径；绝对路径与 .. 会被拒绝。",
        f"- 工具结果会以用户消息返回，形如 {TOOL_RESULT_FEEDBACK_PREFIX}… 或 "
        f"{TOOL_ERROR_FEEDBACK_PREFIX}…，收到后再继续回答。",
        "- 不需要工具时用正常文字回复，不要输出上述代码块。",
    ])
