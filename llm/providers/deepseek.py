"""DeepSeek provider：openai SDK 直连 DeepSeek 端点（流式）。

密钥安全：仅从 api_key/deepseek 本地文件读取，代码中不出现密钥字面量。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from openai import OpenAI

from llm.base import Chunk, LanguageModel, Message

BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class ModelVersion:
    """版本项：API 模型 ID + 思考模式组合（V4 思考模式为请求参数，非独立模型）。

    :param api_id: API 模型 ID
    :param alias: 版本别名（UI 显示）
    :param thinking: 思考模式开关
    :param send_thinking: 是否下发 thinking 参数（未来可能存在不下发的版本项）
    """

    api_id: str
    alias: str
    thinking: bool
    send_thinking: bool = True


#: 可用版本项（V4 思考模式经 extra_body 参数化，默认思考）
MODELS: tuple[ModelVersion, ...] = (
    ModelVersion("deepseek-v4-flash", "V4 Flash · 思考", thinking=True),
    ModelVersion("deepseek-v4-flash", "V4 Flash · 非思考", thinking=False),
    ModelVersion("deepseek-v4-pro", "V4 Pro · 思考", thinking=True),
    ModelVersion("deepseek-v4-pro", "V4 Pro · 非思考", thinking=False),
)
DEFAULT_VERSION: ModelVersion = MODELS[0]  # V4 Flash · 思考（官方默认即思考）

# 项目根/api_key/deepseek（本文件位于 llm/providers/，上两级为项目根）
KEY_FILE = Path(__file__).resolve().parents[2] / "api_key" / "deepseek"


def _load_api_key() -> str:
    """读取密钥文件：跳过备注行，取首个 `sk-` 开头的纯 ASCII 行。"""
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("sk-") and stripped.isascii():
            return stripped
    raise ValueError(f"密钥文件中未找到 sk- 开头的密钥：{KEY_FILE}")


def label_for(version: ModelVersion) -> str:
    """版本项显示名，如 V4 Flash · 思考（deepseek-v4-flash）。显示格式单点维护处。"""
    return f"{version.alias}（{version.api_id}）"


class DeepSeekLLM(LanguageModel):
    """DeepSeek 直连（openai 兼容端点，流式，支持版本/思考模式切换）。"""

    def __init__(self, version: ModelVersion = DEFAULT_VERSION) -> None:
        self._client = OpenAI(api_key=_load_api_key(), base_url=BASE_URL)
        self.set_version(version)

    def set_version(self, version: ModelVersion) -> None:
        """切换版本项（下次请求生效）。version 须在 MODELS 内。"""
        if version not in MODELS:
            raise ValueError(f"未知模型版本：{version}（可用：{[v.alias for v in MODELS]}）")
        self._version = version

    @property
    def current_label(self) -> str:
        """当前版本项显示名。"""
        return label_for(self._version)

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # v4 版本项经 extra_body 下发思考开关
        extra: dict = {}
        if self._version.send_thinking:
            extra["extra_body"] = {"thinking": {"type": "enabled" if self._version.thinking else "disabled"}}
        stream = self._client.chat.completions.create(
            model=self._version.api_id,
            messages=messages,
            stream=True,
            **extra,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            # 思考模式下思维链经 reasoning_content 返回（SDK 类型无此字段，getattr 容错）
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield Chunk("reasoning", reasoning)
            if delta.content:
                yield Chunk("text", delta.content)
