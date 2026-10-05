"""工具围栏块流式过滤器：两态状态机 + 尾部扣留（计划 §3.1/§3.2）。

职责边界（计划 §3.3）：
- 只认「起始标记」，疑似即扣，**不做合法性裁决**——合法与否归流结束后的
  parser；过滤器误扣的唯一后果是延迟上屏，flush 必补发，不丢内容。
- NORMAL：正文直出，但尾部始终扣住「可能是被 chunk 切开的起始标记前缀」
  的最长后缀（≤ 标记长度−1），不错检跨 chunk 标记；上屏节奏人眼无感。
- SUSPEND：自标记起全部扣留，等待调用方在流结束后按 parser 裁决处置
  （合法调用丢弃围栏块 / 解析失败全量补发，裁决不在本类内）。
"""
from __future__ import annotations

from local_cli.toolkit.definitions import TOOL_CALL_FENCE_START_MARKER


class ToolCallFenceFilter:
    """喂正文 chunk、吐可上屏文本；flush 给扣留原文。实例外不可共享（逐迭代新建）。"""

    def __init__(self, start_marker: str = TOOL_CALL_FENCE_START_MARKER) -> None:
        self._start_marker = start_marker
        self._held_text = ""
        self._is_suspended = False

    @property
    def is_suspended(self) -> bool:
        """是否已扣留（见过起始标记）：调用方据此区分 flush 余量的语义。"""
        return self._is_suspended

    def feed(self, chunk_text: str) -> str:
        """喂一段正文，返回本次可安全上屏的部分（可以为空串）。"""
        if self._is_suspended:
            self._held_text += chunk_text
            return ""
        self._held_text += chunk_text
        marker_index = self._held_text.find(self._start_marker)
        if marker_index >= 0:
            visible_text = self._held_text[:marker_index]
            self._held_text = self._held_text[marker_index:]
            self._is_suspended = True
            return visible_text
        holdback_length = self._marker_prefix_tail_length()
        if holdback_length == 0:
            visible_text, self._held_text = self._held_text, ""
            return visible_text
        visible_text = self._held_text[:-holdback_length]
        self._held_text = self._held_text[-holdback_length:]
        return visible_text

    def flush(self) -> str:
        """流结束：返回全部扣留原文（NORMAL 的尾量 / SUSPEND 的标记起全文）。"""
        held_text = self._held_text
        self._held_text = ""
        return held_text

    def _marker_prefix_tail_length(self) -> int:
        """扣留尾量与起始标记前缀的最长匹配长度（这些字符可能是被切开的标记）。"""
        max_candidate = min(len(self._held_text), len(self._start_marker) - 1)
        for candidate_length in range(max_candidate, 0, -1):
            if self._start_marker.startswith(self._held_text[-candidate_length:]):
                return candidate_length
        return 0
