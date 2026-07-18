"""DeepSeek provider：openai SDK 直连 DeepSeek 端点（流式）。

密钥安全：仅从 api_key/deepseek 本地文件读取，代码中不出现密钥字面量。
"""
from pathlib import Path
from typing import Iterator

from openai import OpenAI

from llm.base import LanguageModel, Message

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"

# 项目根/api_key/deepseek（本文件位于 llm/providers/，上两级为项目根）
KEY_FILE = Path(__file__).resolve().parents[2] / "api_key" / "deepseek"


def _load_api_key() -> str:
    """读取密钥文件：跳过备注行，取首个 `sk-` 开头的纯 ASCII 行。"""
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("sk-") and stripped.isascii():
            return stripped
    raise ValueError(f"密钥文件中未找到 sk- 开头的密钥：{KEY_FILE}")


class DeepSeekLLM(LanguageModel):
    """DeepSeek 直连（openai 兼容端点，流式）。"""

    def __init__(self) -> None:
        self._client = OpenAI(api_key=_load_api_key(), base_url=BASE_URL)

    def chat(self, messages: list[Message]) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
