"""LLM 统一接口（仿 theia-zen LanguageModel Protocol）。"""
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, TypedDict


class Message(TypedDict):
    """OpenAI 格式对话消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class UsageStats:
    """ACP `usage_update` 载荷定型（协议键 used/size/cost 入口定型）。

    口径照单接受 agent 产出端语义：used = input + cache.read（不含
    output/reasoning，偏保守但不失真）；size 为模型上下文窗口上限；
    cost 为会话累计成本（agent 未给则 None，本期解析留存不上屏）。
    """

    used: int            # 已用 token（input + cache.read，agent 口径）
    size: int            # 上下文窗口上限
    cost: float | None   # 会话累计成本（USD；agent 未给则 None）


@dataclass(frozen=True)
class Chunk:
    """流式块：kind 区分正文、思维链与上下文用量通知。

    kind="usage" 时 text 为空串、载荷经 usage 字段携带（用量不上屏进
    输出区文本流，由 GUI 路由到徽章控件）。
    """

    kind: Literal["text", "reasoning", "usage"]
    text: str
    usage: UsageStats | None = None


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
