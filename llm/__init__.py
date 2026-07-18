"""LLM 调用层：统一接口 + 注册表 + provider。"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.deepseek import MODELS, DeepSeekLLM, label_for
from llm.registry import LLMRegistry

#: 全局注册表（启动时注册内置 provider）
registry = LLMRegistry()
registry.register("deepseek", DeepSeekLLM())


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
    "label_for",
]
