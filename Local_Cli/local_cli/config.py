"""配置加载：TOML 用户配置 + 探测链兜底（stdlib tomllib，零第三方依赖）。

解析顺序：`$LOCAL_CLI_CONFIG` 环境变量 → `~/.local-cli/config.toml` →
内置默认。文件缺失、TOML 语法错误、键类型错误一律静默回退默认值——
配置层不许崩进程（未配置信号归 session/new 的 -32603，协议 A.1/A.5）。
"""
from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 配置文件路径的环境变量覆盖键（冒烟/测试用，指向项目内临时配置）
ENV_CONFIG_PATH = "LOCAL_CLI_CONFIG"
#: llama-server 二进制路径的环境变量覆盖键（优先级低于配置项、高于 PATH 探测）
ENV_LLAMA_SERVER_BIN = "LLAMA_SERVER_BIN"

DEFAULT_CONFIG_PATH = Path.home() / ".local-cli" / "config.toml"

#: 各键默认值
DEFAULT_MODEL_DIR = "~/models"
DEFAULT_BACKEND = "mock"
DEFAULT_CTX_SIZE = 4096

#: backend 取值：gguf 走 llama-server 推理，其余一律按 mock 处理
BACKEND_GGUF = "gguf"

#: 探测链末位：用户本机编译产物的惯例位置（llama.cpp 源码构建输出路径）
_FALLBACK_LLAMA_SERVER_BIN = Path.home() / "opt" / "llama.cpp" / "build" / "bin" / "llama-server"


@dataclass(frozen=True)
class LocalCliConfig:
    """一份生效配置（AFCP 3.1：显式数据结构，读取后不再回查文件/环境）。"""
    model_dir: Path
    backend: str
    ctx_size: int
    threads: int | None
    #: 配置项 llama_server_bin 原值（未配置为 None，走探测链）
    llama_server_bin: str | None

    def resolve_llama_server_bin(self) -> str | None:
        """探测链：配置项 → $LLAMA_SERVER_BIN → PATH → 惯例编译路径；全落空返回 None。"""
        candidates = [
            self.llama_server_bin,
            os.environ.get(ENV_LLAMA_SERVER_BIN),
            shutil.which("llama-server"),
            str(_FALLBACK_LLAMA_SERVER_BIN),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).expanduser().is_file():
                return str(Path(candidate).expanduser())
        return None


def config_path() -> Path:
    """生效的配置文件路径：$LOCAL_CLI_CONFIG 优先，否则用户态默认路径。"""
    override = os.environ.get(ENV_CONFIG_PATH)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_config() -> LocalCliConfig:
    """读配置文件并套用默认值；任何读取/解析失败都返回全默认配置。"""
    raw: dict[str, Any] = {}
    try:
        with open(config_path(), "rb") as config_file:
            parsed = tomllib.load(config_file)
        if isinstance(parsed, dict):
            raw = parsed
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}

    model_dir_raw = raw.get("model_dir")
    model_dir = Path(model_dir_raw).expanduser() \
        if isinstance(model_dir_raw, str) and model_dir_raw.strip() \
        else Path(DEFAULT_MODEL_DIR).expanduser()

    backend_raw = raw.get("backend")
    backend = backend_raw.strip() \
        if isinstance(backend_raw, str) and backend_raw.strip() \
        else DEFAULT_BACKEND

    ctx_size_raw = raw.get("ctx_size")
    ctx_size = ctx_size_raw \
        if isinstance(ctx_size_raw, int) and ctx_size_raw > 0 \
        else DEFAULT_CTX_SIZE

    threads_raw = raw.get("threads")
    threads = threads_raw \
        if isinstance(threads_raw, int) and threads_raw > 0 \
        else None

    bin_raw = raw.get("llama_server_bin")
    llama_server_bin = bin_raw.strip() \
        if isinstance(bin_raw, str) and bin_raw.strip() \
        else None

    return LocalCliConfig(
        model_dir=model_dir,
        backend=backend,
        ctx_size=ctx_size,
        threads=threads,
        llama_server_bin=llama_server_bin,
    )
