"""Mock 语言模型：规则回复 + 模拟流式 + 意图分类（本期替代真实本地模型）。

回复正文必须包含当前模型别名——spike 断言「切换后正文反映新别名」，
与参考实现（参考代码/dream-acp/example/dream）行为一致。
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Iterator

from local_cli.model.base import LanguageModel, StreamChunk

#: 演示模型别名（§1.5：不透明字符串；同时是宿主 IDE 侧模型枚举的来源）
DEMO_MODEL_ALIASES = ["local/demo-fast", "local/demo-smart"]

#: 演示工具触发关键词（§3 审批回环演示）
TRIGGER_TOOL_KEYWORD = "建文件"
#: 错误路径触发关键词（§2.4/A.3 stopReason="error" 演示）
TRIGGER_ERROR_KEYWORD = "报错"

#: usage_update 演示上下文上限（§2.3：used/size 同帧；真实实现须用真实统计）
DEMO_CONTEXT_SIZE = 262144

#: 每个流式 chunk 的字符数：小步流式既演示增量上屏，也给 cancel 留出到达窗口
_CHUNK_CHAR_COUNT = 8

#: 流式节奏：chunk 间隔秒数（仅 Mock 演示定速；真实模型由推理速度决定）
_STREAM_INTERVAL_SECONDS = 0.05

#: 演示 usage 的字符→token 换算倍率（无真实 tokenizer，仅演示 used/size 同帧格式）
_CHARS_PER_TOKEN_ESTIMATE = 2


class MockIntent(Enum):
    """Mock 对用户文本的意图分类：决定轮次走哪条演示路径。"""
    NORMAL = "normal"
    TOOL_DEMO = "tool_demo"
    ERROR_DEMO = "error_demo"


class MockLanguageModel(LanguageModel):
    """恒可用的规则模型：无外部依赖，行为完全可预期。"""

    def __init__(self) -> None:
        #: 最近一轮的演示 usage（流式结束后才就位；轮次层经 take_usage 取用）
        self._last_usage: tuple[int, int] | None = None

    def is_available(self) -> bool:
        # Mock 恒可用；不可用的错误路径代码保留在 dispatcher 的 session/new 中
        return True

    def known_aliases(self) -> list[str]:
        return list(DEMO_MODEL_ALIASES)

    def take_usage(self) -> tuple[int, int] | None:
        return self._last_usage

    def classify(self, prompt_text: str) -> MockIntent:
        """按关键词分类意图（触发关键词是本期 Mock 的私有演示语义）。"""
        if TRIGGER_ERROR_KEYWORD in prompt_text:
            return MockIntent.ERROR_DEMO
        if TRIGGER_TOOL_KEYWORD in prompt_text:
            return MockIntent.TOOL_DEMO
        return MockIntent.NORMAL

    def stream_reply(self, prompt_text: str, model_alias: str) -> Iterator[StreamChunk]:
        """先发一段思维链，再按固定步长切正文（模拟真实模型的流式节奏）。

        节奏 sleep 放在模型侧而不是轮次层：真实模型（GGUF）的流式节奏由
        llama-server 出 token 速度决定，轮次层不再人为定速。
        """
        self._last_usage = None
        yield StreamChunk(
            is_thought=True,
            text=f"（演示思维链：模型 {model_alias} 正在组织回复……）",
        )
        reply_text = self.compose_reply(prompt_text, model_alias)
        for offset in range(0, len(reply_text), _CHUNK_CHAR_COUNT):
            time.sleep(_STREAM_INTERVAL_SECONDS)
            yield StreamChunk(
                is_thought=False,
                text=reply_text[offset:offset + _CHUNK_CHAR_COUNT],
            )
        # 生成器跑完才记录 usage：中途 cancel 弃流时保持 None，轮次层不发 usage_update
        self._last_usage = (self.estimate_usage(reply_text), DEMO_CONTEXT_SIZE)

    def compose_reply(self, prompt_text: str, model_alias: str) -> str:
        """完整回复正文（轮次用它估算演示 usage：字符数 × 倍率）。"""
        # A.4：容忍空 text 块，空文本按占位文案处理而不是崩轮次
        shown_text = prompt_text.strip() or "（空文本/占位）"
        return (
            f"你好，我是 Local 示例 agent（当前模型 {model_alias}）。"
            f"我收到了你的消息：「{shown_text}」。"
            "这是协议演示回复——试试发送含「建文件」或「报错」的消息。"
        )

    def estimate_usage(self, reply_text: str) -> int:
        """演示 used token 估算：字符数 × 倍率（无真实数据，仅演示 used/size 同帧格式）。"""
        return len(reply_text) * _CHARS_PER_TOKEN_ESTIMATE
