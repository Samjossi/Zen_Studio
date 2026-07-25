"""GitStatusService：数据层门面，聚合状态/统计查询 + 结果缓存 + 环境降级。

使用方式（UI 层）：
    service = GitStatusService(文件树根目录)
    service.refresh()                    # 事件触发时调用（窗口激活/外部重载/手动）
    if not service.is_enabled: ...       # 无 git / 非仓库 → 功能整体隐藏
    service.status_of(绝对路径)           # 查单文件状态（着色用）
    service.numstat_of(绝对路径)          # 查单文件 (新增, 删除)

状态/统计映射的键均为相对仓库根的路径（git 原生输出形式），
本类负责把 UI 传入的绝对路径换算后再查表。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.git import numstat, runner, status

#: 目录聚合状态优先级（大者胜出，见 _build_dir_status）。
#: deleted 不在表内：删除不冒泡（对齐 VS Code propagate 语义——已删文件
#: 树上可能不可见，冒泡到目录会误导"里面有东西要处理"）。
_DIR_STATUS_PRIORITY: dict[str, int] = {
    status.IGNORED: 0,
    status.UNTRACKED: 1,
    status.MODIFIED: 2,
    status.CONFLICT: 3,
}


@dataclass(frozen=True)
class ChangeEntry:
    """单条变更记录（变更面板数据源）。

    - path/status 键为相对仓库根路径与状态枚举（见 status.py）
    - added/deleted 来自 numstat；无统计（纯改名/模式变更、二进制、
      超大小上限的未跟踪文件）为 None
    """

    path: str
    status: str
    added: int | None
    deleted: int | None


class GitStatusService:
    """单仓库 Git 状态服务；非仓库/无 git 时 is_enabled=False 整体降级。"""

    def __init__(self, root_dir: str) -> None:
        """
        :param root_dir: 文件树根目录（绝对路径），据此定位所属仓库
        """
        self._root_dir = root_dir
        #: 仓库根（porcelain/numstat 输出的相对路径基准）；None = 非仓库
        self._repo_root: str | None = None
        self._status: dict[str, str] = {}
        self._numstat: dict[str, tuple[int, int]] = {}
        #: 目录聚合状态缓存 {目录相对路径（无尾斜杠）: 归并后状态}，
        #: 随 refresh() 重建（见 _build_dir_status）
        self._dir_status: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 环境
    # ------------------------------------------------------------------
    @property
    def is_enabled(self) -> bool:
        """git 可用且根目录位于仓库内。"""
        return self._repo_root is not None

    @property
    def repo_root(self) -> str | None:
        return self._repo_root

    # ------------------------------------------------------------------
    # 刷新（事件驱动：窗口激活/外部重载/手动菜单 → 调用此方法）
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """重新拉取状态与统计；是否可用由调用方自查 is_enabled。"""
        if not runner.git_available():
            self._repo_root = None
            self._dir_status = {}
            return
        if self._repo_root is None:
            self._repo_root = runner.find_repo_root(self._root_dir)
            if self._repo_root is None:
                self._dir_status = {}
                return
        status_result = status.fetch_status_map(self._repo_root)
        numstat_result = numstat.fetch_numstat_map(self._repo_root)
        if status_result is None:
            # 命令失败（如 rebase 中途锁定）：保留旧缓存，静默降级
            return
        self._status = status_result
        self._numstat = numstat_result or {}
        self._dir_status = self._build_dir_status()

    # ------------------------------------------------------------------
    # 目录聚合（计划 2026-0725-0933：状态色沿目录向上冒泡）
    # ------------------------------------------------------------------
    def _build_dir_status(self) -> dict[str, str]:
        """由 _status 预聚合 {目录相对路径: 归并后状态}，查询 O(1)。

        对每个可冒泡状态键沿父链逐级向上写入，优先级取高者
        （conflict > modified > untracked > ignored，显式归并不依赖
        遍历顺序）；目录已有同级/更高优先级状态即 break 剪枝（对齐
        theia propagateDecorationsByUri——继续向上只会更弱，祖先必已
        被同级/更高状态占据）。deleted 不冒泡；ignored 折叠键 `dir/`
        去尾斜杠后先入缓存自身再照常上溯；仓库根不入缓存（不着色）。
        """
        result: dict[str, str] = {}
        for rel, file_status in self._status.items():
            if file_status not in _DIR_STATUS_PRIORITY:  # deleted 等不冒泡
                continue
            key = rel.rstrip("/")
            if not key:
                continue
            new_rank = _DIR_STATUS_PRIORITY[file_status]
            if rel.endswith("/"):
                # ignored 折叠目录键：目录自身直接入缓存（不虚构子文件）
                existing = result.get(key)
                if existing is None or _DIR_STATUS_PRIORITY[existing] < new_rank:
                    result[key] = file_status
            for parent in PurePosixPath(key).parents:
                parent_key = str(parent)
                if parent_key == ".":  # 仓库根不着色
                    break
                existing = result.get(parent_key)
                if existing is not None and _DIR_STATUS_PRIORITY[existing] >= new_rank:
                    break  # 剪枝：祖先已被同级/更高状态占据
                result[parent_key] = file_status
        return result

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def status_of(self, abs_path: str) -> str | None:
        """单文件 Git 状态（见 status.py 枚举）；干净/未知返回 None。

        未跟踪/忽略目录以 `dir/` 折叠条目返回——调用方对目录内文件
        需自行做前缀匹配（见 status_of_tree）。
        """
        rel = self._rel(abs_path)
        if rel is None:
            return None
        direct = self._status.get(rel)
        if direct is not None:
            return direct
        # 折叠目录前缀匹配：`?? dir/` 命中 dir/ 下任意文件
        for key, value in self._status.items():
            if key.endswith("/") and rel.startswith(key):
                return value
        return None

    def status_of_dir(self, abs_path: str) -> str | None:
        """目录的聚合 Git 状态：子树内可冒泡状态的最高优先级；无变更返回 None。

        语义为「子树内有该级别变更」，不代表目录本身被 git 跟踪变更；
        deleted 不冒泡（见 _build_dir_status）；仓库根目录恒为 None。
        """
        rel = self._rel(abs_path)
        if rel is None:
            return None
        return self._dir_status.get(rel)

    def numstat_of(self, abs_path: str) -> tuple[int, int] | None:
        """单文件 (新增, 删除) 统计；无改动/未知返回 None。"""
        rel = self._rel(abs_path)
        return self._numstat.get(rel) if rel is not None else None

    def collect_changes(self) -> list[ChangeEntry]:
        """聚合变更清单（变更面板数据源）。

        - 排除 ignored；按路径排序
        - added/deleted 统计规则见 _line_stats_of（numstat / 未跟踪补数行数）
        - status 已用 --untracked-files=all 逐条列出
        """
        result: list[ChangeEntry] = []
        for rel, file_status in sorted(self._status.items()):
            if file_status == status.IGNORED:
                continue
            added, deleted = self._line_stats_of(rel, file_status)
            result.append(ChangeEntry(path=rel, status=file_status, added=added, deleted=deleted))
        return result

    def _line_stats_of(self, rel: str, file_status: str) -> tuple[int | None, int | None]:
        """单条目的 (新增, 删除) 统计；无统计为 (None, None)。

        未跟踪文件 numstat 不含，补数行数（仅文本、≤1MB 守卫，二进制/超限
        为 None）；目录折叠条目（`dir/`）仅剩 ignored 场景（已排除），
        此处保留 ends-with-/ 判断作防御。
        """
        if file_status == status.UNTRACKED:
            if rel.endswith("/"):
                return None, None
            counted = self._count_lines(rel)
            return (counted, 0) if counted is not None else (None, None)
        line_stats = self._numstat.get(rel)
        return line_stats if line_stats is not None else (None, None)

    #: 未跟踪文件行数统计的大小上限（字节）
    UNTRACKED_COUNT_MAX_BYTES = 1_048_576

    def _count_lines(self, rel: str) -> int | None:
        """未跟踪文件行数统计；二进制/超限/读取失败返回 None。"""
        if self._repo_root is None:
            return None
        p = Path(self._repo_root) / rel
        try:
            if p.stat().st_size > self.UNTRACKED_COUNT_MAX_BYTES:
                return None
            with p.open("rb") as f:
                head = f.read(8192)
                if b"\0" in head:  # 二进制嗅探：首块含 NUL
                    return None
                rest = f.read()
            data = head + rest
        except OSError:
            return None
        # 与 git 行数口径一致：按换行符计数，末尾无换行的尾行也算一行
        lines = data.count(b"\n")
        if data and not data.endswith(b"\n"):
            lines += 1
        return lines

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _rel(self, abs_path: str) -> str | None:
        """绝对路径 → 相对仓库根路径；不在仓库内返回 None。"""
        if self._repo_root is None:
            return None
        try:
            return str(Path(abs_path).resolve().relative_to(self._repo_root))
        except ValueError:
            return None
