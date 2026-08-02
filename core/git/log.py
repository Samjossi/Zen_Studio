"""提交历史图数据源：`git log --graph` 文本原样透传（只读展示，无解析）。

实施计划：work plans/2026-0802-1507_Git提交历史图弹窗显示计划.md（T1）。

- 原样透传 stdout 不做结构化解析：--graph 的 ASCII 线条在等宽字体下
  自对齐，纯展示无交互无消费方，解析反而引入错排风险（计划 D1）
- --all 覆盖全部分支/标签引用（对齐 Git Graph 类工具默认视野）；
  --color=never 显式去色（防御全局 gitconfig color.ui=always 混入
  ANSI 转义；runner 管道捕获时 git 默认已去色，此行是显式保险）
- MAX_COUNT 条数上限 + 截断提示行：大仓库防护（runner 2s 超时是
  兜底不是体验保障；计划 D4）；总数经 rev-list --count 获取
- 失败（无 git/非仓库/空仓库/超时）一律返回 None 静默降级（计划 D6）
"""
from __future__ import annotations

from core.git import runner

#: 提交条数上限：超出时尾部追加截断提示行（计划 D4）
MAX_COUNT = 500


def fetch_commit_graph(repo_root: str) -> str | None:
    """仓库提交历史 ASCII 拓扑图（含分支/标签装饰）；失败/尚无提交返回 None。

    空仓库（无任何引用）时 `git log --all` 退出码为 0、输出为空，
    归一为 None——UI 层与命令失败同一占位语义（尚无提交）。
    """
    graph = runner.run_git(
        repo_root, "log", "--graph", "--oneline", "--decorate", "--all",
        "--color=never", "-n", str(MAX_COUNT))
    if graph is None:
        return None
    text = graph.rstrip("\n")
    if not text:
        return None
    total = count_commits(repo_root)
    if total is not None and total > MAX_COUNT:
        text += f"\n……仅显示最近 {MAX_COUNT} 条（共 {total} 条）"
    return text


def count_commits(repo_root: str) -> int | None:
    """--all 视野内提交总数；失败返回 None（空仓库无引用输出 0）。"""
    out = runner.run_git(repo_root, "rev-list", "--all", "--count")
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def current_branch(repo_root: str) -> str | None:
    """当前分支名；detached HEAD 为空串，命令失败返回 None。"""
    out = runner.run_git(repo_root, "branch", "--show-current")
    return out.strip() if out is not None else None
