"""git CLI 调用封装：超时保护、异常静默、环境检测。

所有函数失败一律返回 None/False（静默降级），由调用方决定如何呈现；
本层不抛异常、不打印错误弹窗——无 git 环境属正常场景而非故障。
"""
from __future__ import annotations

import shutil
import subprocess

#: 单条 git 命令超时（秒）：防大仓库/网络文件系统卡死 UI
TIMEOUT_S = 2.0


def git_available() -> bool:
    """系统 PATH 中存在 git 可执行文件。"""
    return shutil.which("git") is not None


def run_git(repo_dir: str, *args: str) -> str | None:
    """在 repo_dir 下执行 git 命令，成功返回 stdout，失败/超时返回 None。

    统一附加 -c core.quotepath=false：配合 -z 输出时路径不转义，
    非 -z 场景（如 rev-parse）下中文路径也能以 UTF-8 原样输出。
    """
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=repo_dir,
            capture_output=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def find_repo_root(path: str) -> str | None:
    """path 是否位于 git 工作区内；是则返回仓库根目录，否则 None。

    用于环境检测与状态映射的路径换算（porcelain 输出相对仓库根）。
    """
    out = run_git(path, "rev-parse", "--show-toplevel")
    if out is None:
        return None
    return out.strip() or None
