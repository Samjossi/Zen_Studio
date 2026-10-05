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


class ChatRole:
    """消息角色取值（OpenAI 兼容线格式，魔法字符串命名常量）。"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息：工具循环的轮次内上下文（system 之外的交替序列）由它组成。"""
    role: str
    content: str


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

    def stream_chat(
        self,
        messages: list[ChatMessage],
        system_text: str | None,
        model_alias: str,
    ) -> Iterator[StreamChunk]:
        """对消息序列做流式生成；默认摊平成纯文本委托 stream_reply。

        无消息概念的后端（Mock）零改动获得新契约：system_text 无从表达即
        丢弃，单条 user 消息摊平后与原 prompt_text 逐字节一致——帧不变量
        （tools_enabled 下 Mock 常规轮次出站帧与纯文本路径相同）靠此成立。
        """
        prompt_text = "\n".join(message.content for message in messages)
        yield from self.stream_reply(prompt_text, model_alias)

    def take_usage(self) -> tuple[int, int] | None:
        """最近一次 stream_reply 的 (used, size)；无真实统计返回 None。

        轮次层据此决定发不发 usage_update：None 即不发（不臆造数据），
        返回值要求 used/size 同帧（§2.3）。
        """
        return None
