"""GGUF 语言模型：llama-server 子进程编排 + OpenAI 兼容 SSE 流式。

orchestrator 职责（而不是库内嵌推理）：
- 按别名拉起 `llama-server --model <model_dir>/<alias>.gguf --port <随机空闲端口>`，
  轮询 /health 就绪后才放行轮次（模型加载期间 prompt 同步阻塞等待）；
- 对话走 `POST /v1/chat/completions`（stream=true，stdlib urllib），
  SSE 逐块解析：`reasoning_content` → 思维链通道、`content` → 正文通道；
- 切换模型 = 停旧实例再起新实例；进程随 Local CLI 退出经 atexit 回收。

llama.cpp 二进制独立升级，Python 侧零重型依赖；llama-server 的日志
继承本进程 stderr（§1.2/A.6 只约束 stdout 纯度，stderr 是合法日志通道）。
"""
from __future__ import annotations

import atexit
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Any, Iterator

from local_cli.config import LocalCliConfig
from local_cli.model.base import ChatMessage, ChatRole, LanguageModel, StreamChunk

_GGUF_SUFFIX = ".gguf"

#: /health 轮询节奏与总超时：21 GB 模型冷启动数十秒，超时留足余量
_HEALTH_POLL_INTERVAL_SECONDS = 0.5
_HEALTH_TIMEOUT_SECONDS = 300.0

#: HTTP 读超时：llama-server 逐 token 出块，正常间隔远小于此；仅兜底连接僵死
_HTTP_TIMEOUT_SECONDS = 120.0

#: 停实例时先 terminate，宽限期后 kill（防孤儿进程）
_STOP_GRACE_SECONDS = 5.0


def list_gguf_aliases(model_dir: Path) -> list[str] | None:
    """扫描 model_dir 的 *.gguf 别名（去后缀文件名，字典序稳定排序）。

    目录不存在返回 None（调用方据此区分「缺目录」与「空目录」报错文案）。
    """
    if not model_dir.is_dir():
        return None
    return sorted(path.name[:-len(_GGUF_SUFFIX)]
                  for path in model_dir.glob(f"*{_GGUF_SUFFIX}"))


class GgufModelError(Exception):
    """GGUF 推理链路的可读错误：轮次层转成 stopReason=error（A.3 显式标记）。"""


class GgufLanguageModel(LanguageModel):
    """llama-server 编排式实现：任意时刻只持有一个推理实例。"""

    def __init__(self, config: LocalCliConfig) -> None:
        self._config = config
        self._bin = config.resolve_llama_server_bin()
        self._proc: subprocess.Popen[bytes] | None = None
        self._loaded_alias: str | None = None
        self._port: int | None = None
        self._last_usage: tuple[int, int] | None = None
        #: 实例切换与轮次推流互斥：切模型时旧流必先收尾，端口才不会指到已死进程
        self._server_lock = threading.Lock()
        atexit.register(self.shutdown)

    # ------------------------------------------------------------------
    # LanguageModel 契约
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """二进制可解析且模型目录里有可加载的 GGUF，才算可用。"""
        return self._bin is not None and bool(self.known_aliases())

    def known_aliases(self) -> list[str]:
        return list_gguf_aliases(self._config.model_dir) or []

    def take_usage(self) -> tuple[int, int] | None:
        return self._last_usage

    def stream_reply(self, prompt_text: str, model_alias: str) -> Iterator[StreamChunk]:
        """无 system 的薄封装：纯文本轮次与工具未启用路径共用此入口。"""
        yield from self.stream_chat(
            [ChatMessage(role=ChatRole.USER, content=prompt_text)], None, model_alias)

    def stream_chat(
        self,
        messages: list[ChatMessage],
        system_text: str | None,
        model_alias: str,
    ) -> Iterator[StreamChunk]:
        self._last_usage = None
        with self._server_lock:
            self._ensure_server(model_alias)
            port = self._port
        assert port is not None
        response = self._open_chat_stream(port, messages, system_text)
        try:
            yield from self._iter_chunks(response)
        finally:
            # cancel 弃流（生成器被 close）也走到这里：断开 HTTP 让 llama-server
            # 即刻停止生成，而不是在后台白跑
            response.close()

    # ------------------------------------------------------------------
    # 实例生命周期
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """回收 llama-server 子进程（atexit 注册；幂等，可重复调用）。"""
        with self._server_lock:
            self._stop_server_locked()

    def _ensure_server(self, model_alias: str) -> None:
        """保证指定别名的实例在跑且就绪；别名变了先停旧实例再拉起新的。"""
        if model_alias not in self.known_aliases():
            raise GgufModelError(f"未知模型别名 {model_alias!r}（不在 model_dir 扫描结果中）")
        if (self._proc is not None and self._proc.poll() is None
                and self._loaded_alias == model_alias):
            return
        self._stop_server_locked()
        self._start_server_locked(model_alias)

    def _start_server_locked(self, model_alias: str) -> None:
        if self._bin is None:
            raise GgufModelError(
                "未找到 llama-server：请配置 llama_server_bin 或设置 $LLAMA_SERVER_BIN")
        model_path = self._config.model_dir / f"{model_alias}{_GGUF_SUFFIX}"
        port = _pick_free_port()
        command = [
            self._bin,
            "--model", str(model_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--ctx-size", str(self._config.ctx_size),
        ]
        if self._config.threads is not None:
            command += ["--threads", str(self._config.threads)]
        _log(f"拉起 llama-server：{model_alias}（端口 {port}，加载中请稍候……）")
        try:
            self._proc = subprocess.Popen(command, stdout=subprocess.DEVNULL)
        except OSError as spawn_error:
            raise GgufModelError(f"llama-server 启动失败：{spawn_error}") from spawn_error
        self._loaded_alias = model_alias
        self._port = port
        self._wait_ready_locked(model_alias)

    def _wait_ready_locked(self, model_alias: str) -> None:
        """轮询 /health 直到就绪；超时或进程早夭则收尸并报错。"""
        deadline = time.monotonic() + _HEALTH_TIMEOUT_SECONDS
        health_url = f"http://127.0.0.1:{self._port}/health"
        poll_count = 0
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                self._stop_server_locked()
                raise GgufModelError(f"llama-server 加载 {model_alias} 时退出（加载失败）")
            try:
                with urllib.request.urlopen(health_url, timeout=2) as health_resp:
                    if health_resp.status == 200:
                        _log(f"llama-server 就绪：{model_alias}")
                        return
            except urllib.error.HTTPError as http_error:
                # 503 = 模型仍在加载，继续等；其他状态码同样视作未就绪。
                # 加载日志约 5 秒一条，避免大模型冷启动刷屏 stderr
                if poll_count % 10 == 0:
                    _log(f"等待就绪中（HTTP {http_error.code}）……")
            except (urllib.error.URLError, OSError):
                pass  # 端口尚未监听，继续轮询
            poll_count += 1
            time.sleep(_HEALTH_POLL_INTERVAL_SECONDS)
        self._stop_server_locked()
        raise GgufModelError(
            f"llama-server 加载 {model_alias} 超时（{_HEALTH_TIMEOUT_SECONDS:.0f} 秒）")

    def _stop_server_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._loaded_alias = None
        self._port = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        _log("llama-server 实例已回收")

    # ------------------------------------------------------------------
    # 对话流
    # ------------------------------------------------------------------
    def _open_chat_stream(
        self,
        port: int,
        messages: list[ChatMessage],
        system_text: str | None,
    ) -> IO[bytes]:
        # system 走消息数组首元而不是独立字段：OpenAI 兼容端点只认 messages
        wire_messages = ([{"role": ChatRole.SYSTEM, "content": system_text}]
                         if system_text else [])
        wire_messages += [{"role": message.role, "content": message.content}
                          for message in messages]
        payload = {
            "messages": wire_messages,
            "stream": True,
            # 让末帧携带 usage（prompt/completion/total token 数）
            "stream_options": {"include_usage": True},
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS)
        except (urllib.error.URLError, OSError) as open_error:
            raise GgufModelError(f"对话请求失败：{open_error}") from open_error

    def _iter_chunks(self, response: IO[bytes]) -> Iterator[StreamChunk]:
        """逐行解析 SSE：`data: {...}` 的 delta 两通道分别映射思维链/正文。"""
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                event: dict[str, Any] = json.loads(data)
            except json.JSONDecodeError:
                continue  # 宽容坏行：丢一块比崩轮次好
            usage = event.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                # used=真实 total token；size=配置的 ctx 上限（llama-server 不回传窗口大小）
                self._last_usage = (usage["total_tokens"], self._config.ctx_size)
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content")
                if thought:
                    yield StreamChunk(is_thought=True, text=thought)
                content = delta.get("content")
                if content:
                    yield StreamChunk(is_thought=False, text=content)


def _pick_free_port() -> int:
    """向 OS 要一个空闲端口后立刻释放；竞态窗口极小，可接受。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _log(message: str) -> None:
    """日志一律 stderr（§1.2/A.6：stdout 只写协议帧）。"""
    print(f"[local] {message}", file=sys.stderr, flush=True)
