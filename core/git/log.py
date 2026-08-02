"""提交历史图数据源：ASCII 文本透传（只读降级形态）+ 结构化提交行（主形态）。

实施计划：work plans/2026-0802-1507_Git提交历史图弹窗显示计划.md（T1）、
work plans/2026-0802-1542_Git提交历史图美化计划.md（T2）。

- fetch_commit_graph：原样透传 stdout 不做结构化解析，纯展示降级形态
  （1507 计划 D1；1542 计划 D3 回退形态，colored=True 时带 SGR 着色）
- fetch_commit_rows：结构化主形态——单条命令同时取回图形前缀与元数据，
  行首 %x1f 是图形前缀与元数据的分界锚点（1542 计划 §6.1）；图形布局
  复用 git 自身 --graph 引擎（含 lane 配色），不移植 lane 算法（D2）；
  --graph 非官方机器接口，字段数不符即整体返回 None 触发 UI 回退（D3）
- --all 覆盖全部分支/标签引用；--decorate=full 输出 refs 全路径供精确
  分类（refs/heads/ refs/tags/ refs/remotes/）
- MAX_COUNT 条数上限 + 截断提示：大仓库防护（runner 2s 超时是兜底不是
  体验保障；1507 计划 D4）；总数经 rev-list --count 获取
- 失败（无 git/非仓库/空仓库/超时/格式漂移）一律返回 None 静默降级
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.git import runner
from core.git.ansi import parse_segments, resolve_key

#: 提交条数上限：超出时尾部追加截断提示（1507 计划 D4；1542 沿用）
MAX_COUNT = 500

#: refs 徽标类型枚举（GIT_GRAPH_PACK 配色键同名）
REF_BRANCH = "branch"
REF_TAG = "tag"
REF_REMOTE = "remote"
REF_HEAD = "head"

#: 结构化取数 format 串：行首 %x1f 为图形前缀/元数据分界锚点，%x1e 记录尾
_ROWS_FORMAT = "%x1f%h%x1f%s%x1f%an%x1f%ar%x1f%D%x1e"


@dataclass(frozen=True)
class RefBadge:
    """单个 refs 徽标：类型（branch/tag/remote/head）+ 展示名。"""

    kind: str
    label: str


@dataclass(frozen=True)
class CommitRow:
    """一行提交记录（或纯图线连接行）。

    graph: 图线格序列 (字符, TERMINAL_PACK 键或 None)；None 键 = 未着色格
        （git 对单 lane 线性段不输出颜色），由 UI 映射主题默认色。
    is_connector: 纯图线连接行（如合并后的 `|\\` 行），无提交元数据。
    """

    graph: tuple[tuple[str, str | None], ...] = ()
    is_connector: bool = False
    is_head: bool = False
    commit: str = ""
    subject: str = ""
    author: str = ""
    rel_date: str = ""
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
    """结构化提交图（图形格 + 元数据 + refs 徽标）；失败/格式漂移返回 None。

    单条命令同时取回图形与元数据；逐行解析：
    - 含锚点 \\x1f 的行：前缀（图线字符 + SGR 色码）| hash | subject |
      author | rel_date | refs——字段数不符即格式漂移，整体返回 None（D3）
    - 无锚点的行：纯图线连接行（is_connector）
    """
    out = runner.run_git(
        repo_root, "log", "--graph", "--color=always", "--all",
        "--decorate=full", "-n", str(MAX_COUNT),
        f"--pretty=format:{_ROWS_FORMAT}")
    if out is None:
        return None
    text = out.rstrip("\n")
    if not text:
        return None  # 空仓库：退出码 0、输出为空（同 fetch_commit_graph 归一）

    rows: list[CommitRow] = []
    # 不用 splitlines()：其将 \x1e（RS）等控制符也视为行界，会多出空行
    for line in text.split("\n"):
        line = line.rstrip("\x1e").rstrip(" ")
        if "\x1f" not in line:
            rows.append(CommitRow(graph=_parse_graph_prefix(line),
                                  is_connector=True))
            continue
        parts = line.split("\x1f")
        if len(parts) != 6:  # 前缀 + 5 字段；不符即 --graph 格式漂移（D3）
            return None
        prefix, commit, subject, author, rel_date, decorate = parts
        refs, is_head = _parse_refs(decorate)
        rows.append(CommitRow(
            graph=_parse_graph_prefix(prefix),
            is_head=is_head,
            commit=commit,
            subject=subject,
            author=author,
            rel_date=rel_date,
            refs=refs,
        ))
    if not rows:
        return None

    hint = None
    total = count_commits(repo_root)
    if total is not None and total > MAX_COUNT:
        hint = f"……仅显示最近 {MAX_COUNT} 条（共 {total} 条）"
    return CommitGraph(rows=tuple(rows), truncated_hint=hint)


def _parse_graph_prefix(prefix: str) -> tuple[tuple[str, str | None], ...]:
    """图线前缀 → 格序列 (字符, TERMINAL_PACK 键)；SGR 色码经 ansi 模块解析。"""
    cells: list[tuple[str, str | None]] = []
    for text, color, bold in parse_segments(prefix):
        key = resolve_key(color, bold)
        cells.extend((ch, key) for ch in text)
    return tuple(cells)


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
        return None


def current_branch(repo_root: str) -> str | None:
    """当前分支名；detached HEAD 为空串，命令失败返回 None。"""
    out = runner.run_git(repo_root, "branch", "--show-current")
    return out.strip() if out is not None else None
