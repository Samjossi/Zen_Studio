"""工具调用解析：从模型回复提取围栏块并校验（§3.2）。

一切不合规（坏 JSON、未知工具、缺参数、多参数类型错误）都产出结构化
ToolCallParseFailure 而非抛异常——格式抖动是本地小模型的常态，轮次层
把错误原因回灌给模型自我修正（计划 §6 对策），崩轮次等于放弃修正机会。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from local_cli.toolkit.definitions import (
    TOOL_CALL_BLOCK_PATTERN,
    TOOL_DEFINITION_MAP,
)


@dataclass(frozen=True)
class ToolCallRequest:
    """一次校验通过的工具调用（AFCP 3.1：显式数据结构）。"""
    tool_name: str
    arguments: dict[str, str]


@dataclass(frozen=True)
class ToolCallParseFailure:
    """解析/校验失败的可读原因：轮次层原样回灌模型（【工具错误】前缀在外层拼）。"""
    error_message: str


class ToolCallParser:
    """无状态解析器：构造函数注入以显式声明依赖（AFCP 2.3），可复用实例。"""

    def parse(self, reply_text: str) -> ToolCallRequest | ToolCallParseFailure | None:
        """None=回复无围栏块（正常文字轮次）；其余两态见返回类型注解。"""
        # 取末个块：模型偶尔先写草稿块再给正式块，末块是最新意图
        blocks = TOOL_CALL_BLOCK_PATTERN.findall(reply_text)
        if not blocks:
            return None
        return self._parse_block(blocks[-1].strip())

    def _parse_block(self, raw_json: str) -> ToolCallRequest | ToolCallParseFailure:
        try:
            payload: Any = json.loads(raw_json)
        except json.JSONDecodeError as decode_error:
            return ToolCallParseFailure(
                f"工具调用 JSON 解析失败（{decode_error.msg}）；"
                "请输出恰好一个合法 JSON 对象")
        if not isinstance(payload, dict):
            return ToolCallParseFailure(
                '工具调用必须是 JSON 对象，形如 {"tool": "...", "args": {...}}')
        tool_name = payload.get("tool")
        definition = TOOL_DEFINITION_MAP.get(tool_name) \
            if isinstance(tool_name, str) else None
        if definition is None:
            known = "/".join(TOOL_DEFINITION_MAP)
            return ToolCallParseFailure(
                f"未知工具 {tool_name!r}（可用：{known}）")
        arguments_raw = payload.get("args", {})
        if not isinstance(arguments_raw, dict):
            return ToolCallParseFailure('"args" 必须是 JSON 对象')
        return self._validate_arguments(definition.name, arguments_raw)

    def _validate_arguments(
        self, tool_name: str, arguments_raw: dict[str, Any]
    ) -> ToolCallRequest | ToolCallParseFailure:
        """参数白名单校验：缺必填、多未知键、非字符串值都算不合规。"""
        definition = TOOL_DEFINITION_MAP[tool_name]
        known_names = {parameter.name for parameter in definition.parameters}
        unknown_names = sorted(set(arguments_raw) - known_names)
        if unknown_names:
            return ToolCallParseFailure(
                f"工具 {tool_name} 不支持参数 {unknown_names}（可用：{sorted(known_names)}）")
        arguments: dict[str, str] = {}
        for parameter in definition.parameters:
            value = arguments_raw.get(parameter.name)
            if value is None:
                if parameter.is_required:
                    return ToolCallParseFailure(
                        f"工具 {tool_name} 缺少必填参数 {parameter.name!r}")
                continue
            if not isinstance(value, str):
                return ToolCallParseFailure(
                    f"工具 {tool_name} 参数 {parameter.name!r} 必须是字符串")
            arguments[parameter.name] = value
        return ToolCallRequest(tool_name=tool_name, arguments=arguments)
