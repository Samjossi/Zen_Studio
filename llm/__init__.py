"""LLM 调用层：统一接口 + provider（本机 agent CLI 后端，代码库零密钥）。

provider 装配单点在 ChatPanel._build_providers（多标签改造后每标签自持
实例，标签间完全隔离）；import 无副作用（AFCP 2.3 依赖显式）：CLI 探测与
provider 实例化不发生在模块级，由消费方显式构造。
"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.kimi_acp import KimiAcpLLM, PermissionHandler, PermissionParams
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models

#: 后端注册名（settings 默认值、设置菜单、ModelBar 共用此常量）
BACKEND_KIMI_CLI = "kimi-cli"
BACKEND_KIMI_ACP = "kimi-acp"

#: 后端注册名 → 显示名（设置菜单项 / ModelBar 下拉 / 切换提示共用单一映射）
BACKEND_LABELS = {BACKEND_KIMI_CLI: "Kimi CLI", BACKEND_KIMI_ACP: "Kimi ACP"}


__all__ = [
    "Chunk",
    "LanguageModel",
    "Message",
    "BACKEND_KIMI_CLI",
    "BACKEND_KIMI_ACP",
    "BACKEND_LABELS",
    # Kimi CLI 专有符号（非 Protocol 成员）
    "KimiCliLLM",
    "KimiAcpLLM",
    "kimi_available",
    "list_kimi_models",
    # ACP 协议层定型（审批回环共用）
    "PermissionHandler",
    "PermissionParams",
]
