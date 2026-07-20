"""LLM 调用层：统一接口 + 注册表 + provider（本机 agent CLI 后端，代码库零密钥）。

import 无副作用（AFCP 2.3 依赖显式）：CLI 探测与 provider 实例化不发生在
模块级，注册表须由应用装配处（main.py）显式调用 build_default_registry()
创建并注入消费方——provider 单例状态（模型选择、审批处理器）的写入路径
由此在代码文本上可见。
"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.kimi_acp import KimiAcpLLM, PermissionHandler, PermissionParams
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models
from llm.registry import LLMRegistry

#: 后端注册名（registry 键；settings 默认值、设置菜单、ModelBar 共用此常量）
BACKEND_KIMI_CLI = "kimi-cli"
BACKEND_KIMI_ACP = "kimi-acp"

#: 后端注册名 → 显示名（设置菜单项 / ModelBar 下拉 / 切换提示共用单一映射）
BACKEND_LABELS = {BACKEND_KIMI_CLI: "Kimi CLI", BACKEND_KIMI_ACP: "Kimi ACP"}


def build_default_registry() -> LLMRegistry:
    """装配默认注册表：探测 kimi CLI，可用则注册两个 kimi 后端实例。

    探测与实例化集中于此（KimiAcpLLM 实例化挂 atexit 清理钩），
    调用时机即副作用发生时点，由调用方显式控制。
    """
    registry = LLMRegistry()
    if kimi_available():
        registry.register(BACKEND_KIMI_CLI, KimiCliLLM())
        registry.register(BACKEND_KIMI_ACP, KimiAcpLLM())
    return registry


__all__ = [
    "Chunk",
    "LanguageModel",
    "Message",
    "LLMRegistry",
    "build_default_registry",
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
