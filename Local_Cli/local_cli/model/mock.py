"""Mock 语言模型：规则回复 + 模拟流式 + 意图分类（本期替代真实本地模型）。

回复正文必须包含当前模型别名——spike 断言「切换后正文反映新别名」，
与参考实现（参考代码/dream-acp/example/dream）行为一致。
"""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Iterator

from local_cli.model.base import LanguageModel, StreamChunk
from local_cli.toolkit.definitions import (
    TOOL_CALL_BLOCK_PATTERN,
    TOOL_CALL_FENCE_LANGUAGE,
    TOOL_ERROR_FEEDBACK_PREFIX,
    TOOL_NAME_WRITE_FILE,
    TOOL_RESULT_FEEDBACK_PREFIX,
)

#: 演示模型别名（§1.5：不透明字符串；同时是宿主 IDE 侧模型枚举的来源）
DEMO_MODEL_ALIASES = ["local/demo-fast", "local/demo-smart"]

#: 演示工具触发关键词（§3 审批回环演示）
TRIGGER_TOOL_KEYWORD = "建文件"
#: 错误路径触发关键词（§2.4/A.3 stopReason="error" 演示）
TRIGGER_ERROR_KEYWORD = "报错"
#: 真实工具循环回归关键词（计划 §3.5）：意图仍归 NORMAL，但回复内嵌合法
#: tool_call 围栏块，使轮次层工具循环经 ACP 帧全程可被 spike 断言
TRIGGER_PROTOCOL_TOOL_KEYWORD = "协议工具"

#: 协议工具演示落盘的文件（相对会话 cwd，spike 据此断言真实副作用）
PROTOCOL_TOOL_FILE_NAME = "protocol_tool_demo.txt"
PROTOCOL_TOOL_FILE_CONTENT = "Mock 协议工具演示文件\n"

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
        # A.4：容忍空 text 块，空文本按占位文案处理而不是崩轮次。
        # 回显前消毒围栏块：工具循环第 2 轮起的摊平文本含上轮 assistant 的
        # tool_call 块原文，verbatim 回显会让解析器把回声当成新调用死循环
        shown_text = TOOL_CALL_BLOCK_PATTERN.sub(
            "（工具调用块）", prompt_text.strip()) or "（空文本/占位）"
        if self._should_emit_tool_call(prompt_text):
            return self._compose_tool_call_reply(model_alias)
        return (
            f"你好，我是 Local 示例 agent（当前模型 {model_alias}）。"
            f"我收到了你的消息：「{shown_text}」。"
            "这是协议演示回复——试试发送含「建文件」或「报错」的消息。"
        )

    def _should_emit_tool_call(self, prompt_text: str) -> bool:
        """协议工具关键词且非回灌轮才输出围栏块。

        工具循环第 2 轮起，摊平文本里带着上轮 assistant 的围栏块原文
        （Mock 正文会回显全量输入）与【工具结果】/【工具错误】回执——任一
        出现即「调用已完结」信号，再输出围栏块会死循环到迭代上限。
        """
        if TRIGGER_PROTOCOL_TOOL_KEYWORD not in prompt_text:
            return False
        return TOOL_RESULT_FEEDBACK_PREFIX not in prompt_text \
            and TOOL_ERROR_FEEDBACK_PREFIX not in prompt_text \
            and f"```{TOOL_CALL_FENCE_LANGUAGE}" not in prompt_text

    def _compose_tool_call_reply(self, model_alias: str) -> str:
        """内嵌合法 write_file 围栏块的回复（§3.2 格式；json.dumps 保证可解析）。"""
        block = json.dumps(
            {"tool": TOOL_NAME_WRITE_FILE,
             "args": {"path": PROTOCOL_TOOL_FILE_NAME,
                      "content": PROTOCOL_TOOL_FILE_CONTENT}},
            ensure_ascii=False, indent=2)
        return (
            f"好的，我是 Local 示例 agent（当前模型 {model_alias}），"
            f"演示真实工具循环：写入 {PROTOCOL_TOOL_FILE_NAME}。\n"
            f"```{TOOL_CALL_FENCE_LANGUAGE}\n{block}\n```\n"
        )

    def estimate_usage(self, reply_text: str) -> int:
        """演示 used token 估算：字符数 × 倍率（无真实数据，仅演示 used/size 同帧格式）。"""
        return len(reply_text) * _CHARS_PER_TOKEN_ESTIMATE
