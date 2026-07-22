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

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from llm.base import Chunk, LanguageModel, Message

KIMI_BIN = "kimi"


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

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（None = CLI 默认模型 default_model）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）
        """
        self._model = model
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._session_id: str | None = None
        #: 当前请求的活动子进程（cancel 目标；chat 开始登记、结束置 None）
        self._active_proc: subprocess.Popen | None = None
        #: 关闭标志（标签销毁）：close() 置位后 chat 拒绝 spawn，
        #: spawn 前后双检——与清理线程的竞态窗口内迟到的进程即建即杀
        self._closed = False

    def set_model(self, alias: str) -> None:
        """切换模型别名（下次请求生效）。"""
        self._model = alias

    def set_workspace_root(self, root: str) -> None:
        """切换 agent 工作目录；换目录即弃旧 sessionId，防跨目录续接旧会话。

        当前无调用方（多开模型下工作区根进程级固定），按计划 2026-0722-0756 预留。
        """
        self._cwd = root
        self.reset_session()

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

    def close(self) -> None:
        """标签销毁：置关闭标志并终止活动子进程（评审 CRITICAL#1）。

        `_closed` 先置位：chat 在 spawn 前后双检该标志，"close 之后
        新建的子进程"语义上不可能存活（此前 cancel 对未 spawn 的进程
        no-op，清理竞态下迟到进程无人收割）。
        """
        self._closed = True
        self.cancel()

    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        prompt = self._extract_prompt(messages)
        if not prompt:
            return
        if self._closed:  # 标签已销毁：拒绝 spawn（评审 CRITICAL#1）
            raise RuntimeError("kimi CLI 后端已关闭（标签已销毁）")
        proc = self._spawn(self._build_command(prompt))
        if self._closed:  # spawn 与 close 竞态：迟到的进程即建即杀
            proc.terminate()
            raise RuntimeError("kimi CLI 后端已关闭（标签已销毁）")
        # stderr 持续排空（thinking/工具进度/错误诊断均走 stderr）：
        # 防管道缓冲写满阻塞子进程；仅保留尾部数块，失败时附进异常
        stderr_tail: list[str] = []
        drain = self._start_stderr_drain(proc, stderr_tail)
        try:
            yield from self._iter_chunks(proc)
            self._raise_on_failure(proc, stderr_tail)
        finally:
            # 统一清理（正常结束/异常/生成器 close 三路径共用）
            self._cleanup(proc, drain)

    # ------------------------------------------------------------------
    # chat 拆分（单一职责私有方法；行为与原单体实现逐行等价）
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_prompt(messages: list[Message]) -> str:
        """历史由 kimi 会话管理，仅取最后一条 user 消息作 prompt。"""
        return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

    def _build_command(self, prompt: str) -> list[str]:
        """命令行拼装：`-p` auto 权限 + stream-json；模型/会话续接按需附加。"""
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        cmd = [bin_path, "-p", prompt, "--output-format", "stream-json"]
        if self._model:
            cmd += ["-m", self._model]
        if self._session_id:
            cmd += ["-S", self._session_id]
        return cmd

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        """spawn 子进程并登记为 cancel 目标。"""
        proc = subprocess.Popen(
            cmd,
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._active_proc = proc  # cancel() 据此 terminate 当前进程
        return proc

    @staticmethod
    def _start_stderr_drain(proc: subprocess.Popen, stderr_tail: list[str]) -> threading.Thread:
        """启动 stderr 排空线程（防管道缓冲写满阻塞子进程），返回线程句柄。"""
        def drain_stderr() -> None:
            assert proc.stderr is not None
            while chunk := proc.stderr.read(4096):
                stderr_tail.append(chunk)
                del stderr_tail[:-4]

        thread = threading.Thread(target=drain_stderr, daemon=True)
        thread.start()
        return thread

    def _iter_chunks(self, proc: subprocess.Popen) -> Iterator[Chunk]:
        """stdout JSONL 逐行解析分发；非 JSON 行静默跳过（兼容未来格式噪声）。"""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield from self._map_message(obj)

    def _map_message(self, obj: dict) -> Iterator[Chunk]:
        """单条 stream-json 消息 → Chunk 序列；会话续接凭证就地更新。"""
        role = obj.get("role")
        if role == "assistant":
            content = obj.get("content")
            if content:
                yield Chunk("text", content)
            # 工具调用复用灰字通道展示 agent 动作
            for tool_call in obj.get("tool_calls") or []:
                name = (tool_call.get("function") or {}).get("name", "?")
                yield Chunk("reasoning", f"• 调用工具 {name}\n")
        elif role == "meta" and obj.get("type") == "session.resume_hint":
            self._session_id = obj.get("session_id", self._session_id)

    @staticmethod
    def _raise_on_failure(proc: subprocess.Popen, stderr_tail: list[str]) -> None:
        """退出码非 0 时附 stderr 尾部抛错（cancel 路径由 worker 归一化拦截）。"""
        return_code = proc.wait()
        if return_code == 0:
            return
        tail = "".join(stderr_tail).strip()[-500:]
        detail = f"：{tail}" if tail else ""
        raise RuntimeError(f"kimi CLI 调用失败（退出码 {return_code}）{detail}")

    def _cleanup(self, proc: subprocess.Popen, drain: threading.Thread) -> None:
        """资源清理：进程存活则终止（cancel 通常已杀，此处兜底）；drain 随 EOF 收尾。"""
        self._active_proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        drain.join(timeout=5)
