"""LLM 调用层：统一接口 + 注册表 + provider。"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.deepseek import MODELS, DeepSeekLLM, ModelVersion, label_for
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models
from llm.registry import LLMRegistry

#: 全局注册表（启动时注册内置 provider；kimi CLI 检测可用后注册）
registry = LLMRegistry()
registry.register("deepseek", DeepSeekLLM())
if kimi_available():
    registry.register("kimi-cli", KimiCliLLM())


def get_llm(name: str = "deepseek") -> LanguageModel:
    """按名称取 provider，默认 deepseek。"""
    return registry.get(name)


__all__ = [
    "Chunk",
    "LanguageModel",
    "Message",
    "LLMRegistry",
    "registry",
    "get_llm",
    # DeepSeek 专有符号（非 Protocol 成员），多 provider 时代收敛为注册表查询
    "DeepSeekLLM",
    "MODELS",
    "ModelVersion",
    "label_for",
    # Kimi CLI 专有符号（非 Protocol 成员）
    "KimiCliLLM",
    "kimi_available",
    "list_kimi_models",
]
