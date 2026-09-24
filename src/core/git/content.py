"""git 文件内容读取：对比基准（上游分支/HEAD）与工作区文本。

对比基准取「已推送状态」：`@{upstream}` 优先（未提交改动与已提交未推送
改动都能对比出差异）；仓库未配置上游时回退 HEAD（本地仓库无推送概念，
已提交未推送改动在此类仓库不可见，语义可接受）。

失败语义对齐 runner.py：一律静默返回 None（二进制/解码失败同），
降级呈现由调用方决定。
"""
from __future__ import annotations

from pathlib import Path

from core.git.runner import run_git


def fetch_base_content(repo_root: str, rel_path: str) -> str | None:
    """取对比基准内容：上游分支版本优先，无上游回退 HEAD。

    :param repo_root: 仓库根目录
    :param rel_path: 相对仓库根路径（porcelain 输出形式）
    """
    for ref in ("@{upstream}", "HEAD"):
        content = run_git(repo_root, "show", f"{ref}:{rel_path}")
        if content is not None and "\x00" not in content:  # 二进制嗅探：含 NUL 不可对比
            return content
    return None


def fetch_worktree_content(repo_root: str, rel_path: str) -> str | None:
    """取工作区当前内容；读取失败/二进制/非 UTF-8 返回 None。"""
    try:
        data = (Path(repo_root) / rel_path).read_bytes()
    except OSError:
        return None
    if b"\x00" in data:  # 二进制嗅探（对齐 service._count_lines 同款手法）
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
