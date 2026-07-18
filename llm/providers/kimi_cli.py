"""Kimi Code CLI provider：spawn `kimi -p --output-format stream-json` 子进程对接。

本机 agent CLI 后端：历史由 kimi 会话管理（session_id 续接）；`-p` 固定 auto 权限，
agent 可在项目目录读写文件与执行命令（静态 deny 规则生效）。凭证由 CLI 自管
（OAuth），代码库零密钥。
"""
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from llm.base import Chunk, LanguageModel, Message

KIMI_BIN = "kimi"

# 项目根（本文件位于 llm/providers/，上两级为项目根）；agent 工作目录限定于此
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def kimi_available() -> bool:
    """检测 kimi CLI 是否可用（PATH 中存在）。"""
    return shutil.which(KIMI_BIN) is not None


def list_kimi_models() -> list[str]:
    """经 `kimi provider list --json` 解析可用模型别名；失败返回空列表。"""
    if not kimi_available():
        return []
    try:
        proc = subprocess.run(
            [KIMI_BIN, "provider", "list", "--json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        data = json.loads(proc.stdout)
        return sorted(data.get("models", {}).keys())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


class KimiCliLLM(LanguageModel):
    """Kimi Code CLI 后端（子进程 + JSONL 解析，消息粒度上屏）。"""

    def __init__(self, model: str | None = None) -> None:
        self._model = model  # None = CLI 默认模型（default_model）
        self._session_id: str | None = None

    def set_model(self, alias: str) -> None:
        """切换模型别名（下次请求生效）。"""
        self._model = alias

    def reset_session(self) -> None:
        """清空会话续接凭证，下次请求开新会话。"""
        self._session_id = None

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 kimi 会话管理，仅取最后一条 user 消息作 prompt
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not prompt:
            return

        cmd = [KIMI_BIN, "-p", prompt, "--output-format", "stream-json"]
        if self._model:
            cmd += ["-m", self._model]
        if self._session_id:
            cmd += ["-S", self._session_id]

        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 非 JSON 行（兼容未来格式噪声）
            role = obj.get("role")
            if role == "assistant":
                content = obj.get("content")
                if content:
                    yield Chunk("text", content)
                # 工具调用复用灰字通道展示 agent 动作
                for tc in obj.get("tool_calls") or []:
                    name = (tc.get("function") or {}).get("name", "?")
                    yield Chunk("reasoning", f"• 调用工具 {name}\n")
            elif role == "meta" and obj.get("type") == "session.resume_hint":
                self._session_id = obj.get("session_id", self._session_id)
        if proc.wait() != 0:
            raise RuntimeError(f"kimi CLI 调用失败（退出码 {proc.returncode}）")
