"""DeepSeek provider：openai SDK 直连 DeepSeek 端点（流式）。

密钥安全：仅从 api_key/deepseek 本地文件读取，代码中不出现密钥字面量。
"""
from pathlib import Path
from typing import Iterator

from openai import OpenAI

from llm.base import Chunk, LanguageModel, Message

BASE_URL = "https://api.deepseek.com"

#: 可用版本：模型 ID → 版本别名（DeepSeek API 仅此两个模型）
MODELS: dict[str, str] = {
    "deepseek-chat": "V3.2 通用",
    "deepseek-reasoner": "V3.2 思考",
}
DEFAULT_MODEL = "deepseek-chat"

# 项目根/api_key/deepseek（本文件位于 llm/providers/，上两级为项目根）
KEY_FILE = Path(__file__).resolve().parents[2] / "api_key" / "deepseek"


def label_for(model_id: str) -> str:
    """版本显示名，如 DeepSeek · V3.2 思考（deepseek-reasoner）。显示格式单点维护处。"""
    return f"DeepSeek · {MODELS[model_id]}（{model_id}）"


def _load_api_key() -> str:
    """读取密钥文件：跳过备注行，取首个 `sk-` 开头的纯 ASCII 行。"""
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("sk-") and stripped.isascii():
            return stripped
    raise ValueError(f"密钥文件中未找到 sk- 开头的密钥：{KEY_FILE}")


class DeepSeekLLM(LanguageModel):
    """DeepSeek 直连（openai 兼容端点，流式，支持版本切换）。"""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._client = OpenAI(api_key=_load_api_key(), base_url=BASE_URL)
        self.set_model(model)

    def set_model(self, model: str) -> None:
        """切换版本（下次请求生效）。model 须在 MODELS 内。"""
        if model not in MODELS:
            raise ValueError(f"未知模型版本：{model}（可用：{list(MODELS)}）")
        self._model = model

    @property
    def current_label(self) -> str:
        """当前版本显示名，如 DeepSeek · V3.2 思考（deepseek-reasoner）。"""
        return label_for(self._model)

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            # reasoner 版本先吐思维链（SDK 类型无此字段，getattr 容错）
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield Chunk("reasoning", reasoning)
            if delta.content:
                yield Chunk("text", delta.content)
