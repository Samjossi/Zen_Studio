"""LLM 调用层：统一接口 + 注册表 + provider（本机 agent CLI 后端，代码库零密钥）。"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models
from llm.registry import LLMRegistry

#: 全局注册表（启动时注册可用后端；kimi CLI 检测可用后注册）
registry = LLMRegistry()
if kimi_available():
    registry.register("kimi-cli", KimiCliLLM())


def get_llm(name: str = "kimi-cli") -> LanguageModel:
    """按名称取 provider，默认 kimi-cli。"""
    return registry.get(name)


__all__ = [
    "Chunk",
    "LanguageModel",
    "Message",
    "LLMRegistry",
    "registry",
    "get_llm",
    # Kimi CLI 专有符号（非 Protocol 成员）
    "KimiCliLLM",
    "kimi_available",
    "list_kimi_models",
]
