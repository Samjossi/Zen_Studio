"""模型层抽象：小而专的 LanguageModel 契约（AFCP 3.2）。

只声明协议层真正依赖的三件事：可用性、别名表、流式生成。
Mock 与未来的真实模型各自实现，协议层不感知差异（继承链仅一层，
AFCP 2.4）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class StreamChunk:
    """模型流式产出的一个增量：思维链或正文。"""
    is_thought: bool
    text: str


class LanguageModel(ABC):
    """流式生成契约：协议层只面向此接口编程。"""

    @abstractmethod
    def is_available(self) -> bool:
        """模型是否已加载/配置完成。

        不可用信号归 session/new 的 -32603（协议 A.1/A.5：不要在
        initialize 的 authMethods 上表达配置状态）。
        """

    @abstractmethod
    def known_aliases(self) -> list[str]:
        """可切换的模型别名表（§1.5：别名是不透明字符串，原样传递）。"""

    @abstractmethod
    def stream_reply(self, prompt_text: str, model_alias: str) -> Iterator[StreamChunk]:
        """对一条用户文本做流式生成，逐段产出 StreamChunk。"""

    def take_usage(self) -> tuple[int, int] | None:
        """最近一次 stream_reply 的 (used, size)；无真实统计返回 None。

        轮次层据此决定发不发 usage_update：None 即不发（不臆造数据），
        返回值要求 used/size 同帧（§2.3）。
        """
        return None
