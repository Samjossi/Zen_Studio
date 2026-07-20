"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, TypedDict


class Message(TypedDict):
    """OpenAI 格式对话消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


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

    def cancel(self) -> None:
        """取消当前进行中的 chat 调用（协议级，立即打断阻塞等待）。

        无进行中调用时须为无害 no-op；须可从非 chat 所在线程安全调用
        （典型场景：GUI 线程点击停止，chat 在 worker 线程阻塞）。
        取消后 chat 生成器应尽快结束（正常耗尽或抛异常均可，
        残余资源由生成器 finally 兜底清理）。
        """
        ...
