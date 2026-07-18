"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from typing import Iterator, Protocol

# 消息格式：{"role": "system"|"user"|"assistant", "content": str}
Message = dict[str, str]


class LanguageModel(Protocol):
    """统一 LLM 接口：流式 chat，与 UI 解耦。"""

    def chat(self, messages: list[Message]) -> Iterator[str]:
        """发送多轮消息，流式产出文本块。

        :param messages: OpenAI 格式的消息列表
        :yield: 文本增量块
        """
        ...
