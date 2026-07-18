"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol

# 消息格式：{"role": "system"|"user"|"assistant", "content": str}
Message = dict[str, str]


@dataclass(frozen=True)
class Chunk:
    """流式块：kind 区分正文与思维链。"""

    kind: Literal["text", "reasoning"]
    text: str


class LanguageModel(Protocol):
    """统一 LLM 接口：流式 chat，与 UI 解耦。"""

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        """发送多轮消息，流式产出文本/思维链块。

        :param messages: OpenAI 格式的消息列表
        :yield: Chunk（kind="text" 为正文增量，kind="reasoning" 为思维链增量；
            思维链仅当次显示，调用方不得回传入请求历史）
        """
        ...
