"""`git diff --numstat -z HEAD` 解析：输出 → {相对路径: (新增行数, 删除行数)}。

numstat 每行 `新增\\t删除\\t路径`；二进制文件为 `-\\t-\\t路径`（跳过）。
-z 格式路径原样不转义；rename 条目为 `新增\\t删除\\t\\0新路径\\0原路径\\0`，
取新路径为准（统计跟随当前文件）。
"""
from __future__ import annotations

from core.git.runner import run_git


def parse_numstat_z(text: str) -> dict[str, tuple[int, int]]:
    """解析 --numstat -z 输出为 {路径: (新增, 删除)}；解析失败条目静默跳过。"""
    result: dict[str, tuple[int, int]] = {}
    fields = text.split("\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if not field:
            continue
        parts = field.split("\t", 2)
        if len(parts) < 3:
            continue
        added_s, deleted_s, path = parts
        if path == "":
            # rename 条目：空路径后接 新路径\0原路径
            if i + 1 < len(fields):
                path = fields[i]
                i += 2
            else:
                break
        try:
            added, deleted = int(added_s), int(deleted_s)
        except ValueError:
            continue  # 二进制文件（-  -  path）跳过
        if path:
            result[path] = (added, deleted)
    return result


def fetch_numstat_map(repo_root: str) -> dict[str, tuple[int, int]] | None:
    """执行 git diff --numstat HEAD 并解析；失败返回 None。"""
    out = run_git(repo_root, "diff", "--numstat", "-z", "HEAD")
    if out is None:
        return None
    return parse_numstat_z(out)
