"""`git status --porcelain=v1 -z --ignored` 解析：输出 → {相对路径: 状态}。

采用 -z 格式（NUL 分隔、路径原样不转义），从根本上规避中文/空格/
引号路径的转义解析问题（项目内即有"参考代码/"等中文目录）。

状态归并规则（XY 双列：X=暂存区，Y=工作区）：
    冲突 UU/AA/DD/AU/UA/DU/UD → CONFLICT
    任一侧含 D                → DELETED
    任一侧含 M/R/C/A          → MODIFIED（A=已暂存新文件，归入"修改"族）
    ??                        → UNTRACKED（目录折叠为 `dir/` 形式保留）
    !!                        → IGNORED（目录折叠同理）
解析失败的条目静默跳过（降级为"干净"），不抛异常。
"""
from __future__ import annotations

from core.git.runner import run_git

#: 状态枚举（字符串常量，供 UI 层查配色表）
CONFLICT = "conflict"
MODIFIED = "modified"
DELETED = "deleted"
UNTRACKED = "untracked"
IGNORED = "ignored"

_CONFLICT_CODES = {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}


def parse_porcelain_z(text: str) -> dict[str, str]:
    """解析 --porcelain=v1 -z 输出为 {路径: 状态}。

    -z 格式每条记录为 `XY <路径>\\0`；rename/copy（含 R/C）条目
    紧随其后多一个 NUL 字段为原路径（顺序：新路径\\0原路径\\0）。
    """
    result: dict[str, str] = {}
    fields = text.split("\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:  # 末尾空段或畸形条目
            continue
        code, path = field[:2], field[3:]
        if "R" in code or "C" in code:
            i += 1  # 跳过 rename/copy 的原路径字段
        status = _merge_status(code)
        if status is not None and path:
            result[path] = status
    return result


def _merge_status(code: str) -> str | None:
    """XY 双列状态码归并为单一状态；无法识别返回 None（视为干净）。"""
    if code in ("??", "!!"):
        return UNTRACKED if code == "??" else IGNORED
    if code in _CONFLICT_CODES:
        return CONFLICT
    if "D" in code:
        return DELETED
    if any(c in code for c in "MRCA"):
        return MODIFIED
    return None


def status_map(repo_root: str) -> dict[str, str] | None:
    """执行 git status 并解析；失败返回 None（非仓库/超时/无 git）。"""
    out = run_git(repo_root, "status", "--porcelain=v1", "-z", "--ignored")
    if out is None:
        return None
    return parse_porcelain_z(out)
