"""LLM 注册表（仿 theia-zen LanguageModelRegistry）。"""
from llm.base import LanguageModel


class LLMRegistry:
    """名称 → provider 实例的注册表。"""

    def __init__(self) -> None:
        self._providers: dict[str, LanguageModel] = {}

    def register(self, name: str, llm: LanguageModel) -> None:
        self._providers[name] = llm

    def get(self, name: str) -> LanguageModel:
        if name not in self._providers:
            raise KeyError(f"未注册的 LLM：{name}（可用：{list(self._providers)}）")
        return self._providers[name]

    def list_names(self) -> list[str]:
        return list(self._providers)
