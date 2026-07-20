"""Kimi Code CLI provider：spawn `kimi -p --output-format stream-json` 子进程对接。

本机 agent CLI 后端：历史由 kimi 会话管理（session_id 续接）；`-p` 固定 auto 权限，
agent 可在项目目录读写文件与执行命令（静态 deny 规则生效）。凭证由 CLI 自管
（OAuth），代码库零密钥。
"""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterator

from llm.base import Chunk, LanguageModel, Message

KIMI_BIN = "kimi"

# 项目根（本文件位于 llm/providers/，上两级为项目根）；agent 工作目录限定于此
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_bin() -> str | None:
    """解析 kimi 二进制路径：PATH → $KIMI_CODE_HOME/bin/kimi → ~/.kimi-code/bin/kimi。

    桌面启动 Zen Studio 时 PATH 可能不含 ~/.kimi-code/bin，fallback 避免误判未安装。
    """
    if path := shutil.which(KIMI_BIN):
        return path
    candidates: list[Path] = []
    if home := os.environ.get("KIMI_CODE_HOME"):
        candidates.append(Path(home) / "bin" / KIMI_BIN)
    candidates.append(Path.home() / ".kimi-code" / "bin" / KIMI_BIN)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def kimi_available() -> bool:
    """检测 kimi CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_kimi_models() -> list[str]:
    """经 `kimi provider list --json` 解析可用模型别名；失败返回空列表。"""
    bin_path = _find_bin()
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, "provider", "list", "--json"],
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
        #: 当前请求的活动子进程（cancel 目标；chat 开始登记、结束置 None）
        self._active_proc: subprocess.Popen | None = None

    def set_model(self, alias: str) -> None:
        """切换模型别名（下次请求生效）。"""
        self._model = alias

    def reset_session(self) -> None:
        """清空会话续接凭证，下次请求开新会话。"""
        self._session_id = None

    def cancel(self) -> None:
        """取消当前请求：终止活动子进程（stdout EOF，chat 迭代随之结束）。

        无活动进程时 no-op；可从任意线程调用（terminate 仅发信号）。
        """
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 kimi 会话管理，仅取最后一条 user 消息作 prompt
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not prompt:
            return
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")

        cmd = [bin_path, "-p", prompt, "--output-format", "stream-json"]
        if self._model:
            cmd += ["-m", self._model]
        if self._session_id:
            cmd += ["-S", self._session_id]

        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._active_proc = proc  # 登记 cancel 目标
        # stderr 持续排空（thinking/工具进度/错误诊断均走 stderr）：
        # 防管道缓冲写满阻塞子进程；仅保留尾部数块，失败时附进异常
        stderr_tail: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            while chunk := proc.stderr.read(4096):
                stderr_tail.append(chunk)
                del stderr_tail[:-4]

        drain = threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        try:
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
            return_code = proc.wait()
            if return_code != 0:
                tail = "".join(stderr_tail).strip()[-500:]
                detail = f"：{tail}" if tail else ""
                raise RuntimeError(f"kimi CLI 调用失败（退出码 {return_code}）{detail}")
        finally:
            # 统一清理（正常结束/异常/生成器 close 三路径共用）：
            # 进程存活则终止——cancel 通常已杀，此处兜底；drain 随 EOF 收尾
            self._active_proc = None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            drain.join(timeout=5)
