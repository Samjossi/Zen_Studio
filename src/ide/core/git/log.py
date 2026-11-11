"""提交历史图数据源：ASCII 文本透传降级 + parents/lane 结构化主形态。

- fetch_commit_graph：原样透传 stdout，纯展示降级形态。
- fetch_commit_rows：以完整 commit id、有序 parent ids 和提交元数据构造
  拓扑模型；--topo-order 保证纯泳道算法的输入前提。
- --all 覆盖全部分支/标签引用；--decorate=full 输出 refs 全路径供精确分类。
- rev-list HEAD 提供三态 on_head：可达、不可达、查询失败的未知；未知不触发降级。
- MAX_COUNT 限制加载量；未加载父提交只形成图边界，不生成额外提交行。
- 主取数失败（无 git/非仓库/空仓库/超时/格式漂移）返回 None 静默降级。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from ide.core.git import runner
from ide.core.git.graph import GraphRow, compute_graph_rows

logger = logging.getLogger(__name__)

MAX_COUNT = 500

REF_BRANCH = "branch"
REF_TAG = "tag"
REF_REMOTE = "remote"
REF_HEAD = "head"

#: %H/%P 提供图身份与有序父提交；%h 仅用于展示；%x1e 为记录尾。
_ROWS_FORMAT = "%H%x1f%P%x1f%h%x1f%s%x1f%an%x1f%ar%x1f%aI%x1f%D%x1e"
_EMPTY_GRAPH_ROW = GraphRow(
    lane=0, color=0, edges=(), has_continuation=False, has_top_line=False)


@dataclass(frozen=True)
class RefBadge:
    """单个 refs 徽标：类型（branch/tag/remote/head）+ 展示名。"""

    kind: str
    label: str


@dataclass(frozen=True)
class CommitRow:
    """一条提交及其拓扑行；commit_id 是身份，commit 是短 hash 展示字段。"""

    graph_row: GraphRow = _EMPTY_GRAPH_ROW
    commit_id: str = ""
    parent_ids: tuple[str, ...] = ()
    is_head: bool = False
    on_head: bool | None = None
    commit: str = ""
    subject: str = ""
    author: str = ""
    rel_date: str = ""
    abs_date: str = ""
    refs: tuple[RefBadge, ...] = field(default=())


@dataclass(frozen=True)
class CommitGraph:
    """结构化提交图：行序列 + 截断提示（未截断为 None）。"""

    rows: tuple[CommitRow, ...]
    truncated_hint: str | None


def fetch_commit_graph(repo_root: str, colored: bool = False) -> str | None:
    """仓库提交历史 ASCII 拓扑图（含分支/标签装饰）；失败/尚无提交返回 None。

    空仓库（无任何引用）时 `git log --all` 退出码为 0、输出为空，
    归一为 None——UI 层与命令失败同一占位语义（尚无提交）。
    colored=True 时以 --color=always 输出 SGR 着色（1542 回退形态用），
    默认 --color=never 显式去色（防御全局 gitconfig color.ui=always）。
    """
    graph = runner.run_git(
        repo_root, "log", "--graph", "--oneline", "--decorate", "--all",
        "--color=always" if colored else "--color=never",
        "-n", str(MAX_COUNT))
    if graph is None:
        return None
    text = graph.rstrip("\n")
    if not text:
        return None
    total = count_commits(repo_root)
    if total is not None and total > MAX_COUNT:
        text += f"\n……仅显示最近 {MAX_COUNT} 条（共 {total} 条）"
    return text


def fetch_commit_rows(repo_root: str) -> CommitGraph | None:
    """结构化提交图（parents/lane + 元数据 + refs）；失败/格式漂移返回 None。"""
    out = runner.run_git(
        repo_root, "log", "--topo-order", "--all", "--decorate=full",
        "-n", str(MAX_COUNT), f"--pretty=format:{_ROWS_FORMAT}")
    if out is None:
        return None
    text = out.rstrip("\n")
    if not text:
        return None

    rows: list[CommitRow] = []
    # 不用 splitlines()：其将 \x1e（RS）等控制符也视为行界，会多出空行
    for line in text.split("\n"):
        line = line.rstrip("\x1e").rstrip(" ")
        parts = line.split("\x1f")
        if len(parts) != 8:
            return None
        commit_id, parents, commit, subject, author, rel_date, abs_date, decorate = parts
        if not commit_id:
            return None
        refs, is_head = _parse_refs(decorate)
        rows.append(CommitRow(
            commit_id=commit_id,
            parent_ids=tuple(parents.split()),
            is_head=is_head,
            commit=commit,
            subject=subject,
            author=author,
            rel_date=rel_date,
            abs_date=_format_abs_date(abs_date),
            refs=refs,
        ))
    if not rows:
        return None

    graph_rows = compute_graph_rows(
        (row.commit_id, row.parent_ids) for row in rows)
    reachable = runner.run_git(repo_root, "rev-list", "HEAD")
    head_ids = set(reachable.split()) if reachable is not None else None
    rows = [
        replace(
            row,
            graph_row=graph_row,
            on_head=None if head_ids is None else row.commit_id in head_ids,
        )
        for row, graph_row in zip(rows, graph_rows, strict=True)
    ]
    hint = None
    total = count_commits(repo_root)
    if total is not None and total > MAX_COUNT:
        hint = f"……仅显示最近 {MAX_COUNT} 条（共 {total} 条）"
    return CommitGraph(rows=tuple(rows), truncated_hint=hint)


def _format_abs_date(iso: str) -> str:
    """%aI（ISO 8601，如 2026-08-02T16:35:22+08:00）→ "YYYY-MM-DD HH:MM"。

    截断到分钟：秒与时区偏移对「看一眼具体时间」场景是噪音；格式异常
    （非预期 ISO 形态）时原样返回，宽容不判格式漂移。
    """
    if len(iso) >= 16 and iso[4] == "-" and iso[10] == "T":
        return iso[:16].replace("T", " ")
    return iso


def _parse_refs(decorate: str) -> tuple[tuple[RefBadge, ...], bool]:
    """%D 全路径装饰 → (徽标列表, 是否 HEAD 行)；未知形态宽容归为 branch。"""
    badges: list[RefBadge] = []
    is_head = False
    for token in decorate.split(", "):
        token = token.strip()
        if not token:
            continue
        if token.startswith("HEAD -> "):
            is_head = True
            token = token[len("HEAD -> "):]
        elif token == "HEAD":  # detached：HEAD 直接指向提交
            is_head = True
            badges.append(RefBadge(REF_HEAD, "HEAD"))
            continue
        if token.startswith("tag: refs/tags/"):
            badges.append(RefBadge(REF_TAG, token[len("tag: refs/tags/"):]))
        elif token.startswith("refs/heads/"):
            badges.append(RefBadge(REF_BRANCH, token[len("refs/heads/"):]))
        elif token.startswith("refs/remotes/"):
            badges.append(RefBadge(REF_REMOTE, token[len("refs/remotes/"):]))
        else:  # 未识别形态（短路径/未来新增 ref 类）宽容兜底为分支徽标
            badges.append(RefBadge(REF_BRANCH, token))
    if is_head and not any(b.kind == REF_HEAD for b in badges):
        badges.insert(0, RefBadge(REF_HEAD, "HEAD"))
    return tuple(badges), is_head


def count_commits(repo_root: str) -> int | None:
    """--all 视野内提交总数；失败返回 None（空仓库无引用输出 0）。"""
    out = runner.run_git(repo_root, "rev-list", "--all", "--count")
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        logger.exception("提交总数解析失败")
        return None


def current_branch(repo_root: str) -> str | None:
    """当前分支名；detached HEAD 为空串，命令失败返回 None。"""
    out = runner.run_git(repo_root, "branch", "--show-current")
    return out.strip() if out is not None else None
