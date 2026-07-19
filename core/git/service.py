"""GitStatusService：数据层门面，聚合状态/统计查询 + 结果缓存 + 环境降级。

使用方式（UI 层）：
    service = GitStatusService(文件树根目录)
    service.refresh()                    # 事件触发时调用（窗口激活/外部重载/手动）
    if not service.enabled: ...          # 无 git / 非仓库 → 功能整体隐藏
    service.status_of(绝对路径)           # 查单文件状态（着色用）
    service.numstat_of(绝对路径)          # 查单文件 (新增, 删除)

状态/统计映射的键均为相对仓库根的路径（git 原生输出形式），
本类负责把 UI 传入的绝对路径换算后再查表。
"""
from __future__ import annotations

from pathlib import Path

from core.git import numstat, runner, status


class GitStatusService:
    """单仓库 Git 状态服务；非仓库/无 git 时 enabled=False 整体降级。"""

    def __init__(self, root_dir: str) -> None:
        """
        :param root_dir: 文件树根目录（绝对路径），据此定位所属仓库
        """
        self._root_dir = root_dir
        #: 仓库根（porcelain/numstat 输出的相对路径基准）；None = 非仓库
        self._repo_root: str | None = None
        self._status: dict[str, str] = {}
        self._numstat: dict[str, tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # 环境
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """git 可用且根目录位于仓库内。"""
        return self._repo_root is not None

    @property
    def repo_root(self) -> str | None:
        return self._repo_root

    # ------------------------------------------------------------------
    # 刷新（事件驱动：窗口激活/外部重载/手动菜单 → 调用此方法）
    # ------------------------------------------------------------------
    def refresh(self) -> bool:
        """重新拉取状态与统计；返回 enabled 供调用方决定是否更新 UI。"""
        if not runner.git_available():
            self._repo_root = None
            return False
        if self._repo_root is None:
            self._repo_root = runner.find_repo_root(self._root_dir)
            if self._repo_root is None:
                return False
        status_result = status.status_map(self._repo_root)
        numstat_result = numstat.numstat_map(self._repo_root)
        if status_result is None:
            # 命令失败（如 rebase 中途锁定）：保留旧缓存，静默降级
            return True
        self._status = status_result
        self._numstat = numstat_result or {}
        return True

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

    def numstat_of(self, abs_path: str) -> tuple[int, int] | None:
        """单文件 (新增, 删除) 统计；无改动/未知返回 None。"""
        rel = self._rel(abs_path)
        return self._numstat.get(rel) if rel is not None else None

    def changes(self) -> list[dict]:
        """聚合变更清单（变更面板数据源）：[{path, status, added, deleted}]。

        - 排除 ignored；按路径排序
        - added/deleted 来自 numstat；无统计（纯改名/模式变更）为 None
        - 未跟踪文件 numstat 不含，补数行数（仅文本、≤1MB 守卫，
          二进制/超限为 None）；未跟踪目录折叠条目（`dir/`）不统计
        """
        result: list[dict] = []
        for rel, st in sorted(self._status.items()):
            if st == status.IGNORED:
                continue
            added = deleted = None
            if st == status.UNTRACKED:
                if not rel.endswith("/"):
                    counted = self._count_lines(rel)
                    if counted is not None:
                        added, deleted = counted, 0
            else:
                stat = self._numstat.get(rel)
                if stat is not None:
                    added, deleted = stat
            result.append({"path": rel, "status": st, "added": added, "deleted": deleted})
        return result

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
