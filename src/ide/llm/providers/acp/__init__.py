"""稳定 ACP 公共设施：连接、协议定型、prompt 构造与标准内容函数。

厂商 session/update 映射在过渡期暂存于 `.updates`（T1 只做机械迁移，
T2 再拆入各厂商目录）；该模块不是稳定公共出口，provider 暂时直接引用。
"""
from ide.llm.providers.acp.connection import (
    AcpConnection,
    TurnAborted,
    TurnCancelled,
)
from ide.llm.providers.acp.prompt import build_prompt_blocks
from ide.llm.providers.acp.protocol import (
    PermissionHandler,
    PermissionOption,
    PermissionParams,
    TerminalHandler,
    ToolCallInfo,
    build_client_capabilities,
    map_usage_update,
)

__all__ = [
    "AcpConnection",
    "TurnAborted",
    "TurnCancelled",
    "build_prompt_blocks",
    "PermissionHandler",
    "PermissionOption",
    "PermissionParams",
    "TerminalHandler",
    "ToolCallInfo",
    "build_client_capabilities",
    "map_usage_update",
]
