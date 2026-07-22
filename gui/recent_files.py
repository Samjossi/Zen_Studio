"""最近打开文件存取：按工作区隔离的查看历史（window_state_<hash8>.json）。

记录源为 ViewerPanel.file_opened 信号（open_file 成功路径发射），消费侧为
文件菜单「最近打开的文件」子菜单（aboutToShow 动态重建）。读写复用
window_state 的读-合并-写原子链，不自造 IO；同根多开窗口并发写为后写胜
（列表非关键数据，收敛即正确，不加锁）。
（work plans/2026-0722-1901）
"""
from pathlib import Path

from gui.window_state import (
    KEY_RECENT_FILES,
    load_window_state,
    update_window_state,
)

#: 列表上限（超出截断最旧）
MAX_RECENT_FILES = 10


class RecentFilesStore:
    """最近查看文件列表（按工作区隔离，存 window_state_<hash8>.json）。"""

    def __init__(self, state_file: Path) -> None:
        """
        :param state_file: 工作区状态文件路径（window_state_file_for 推导，
            与 WindowStateStore 同源；显式传入，AFCP 2.3 依赖显式）
        """
        self._state_file = state_file

    def list(self) -> list[str]:
        """读回列表（新→旧）；缺失/损坏由 load_window_state 回退空表。

        返回值拷出隔离：DEFAULT_WINDOW_STATE 的共享默认列表不被调用方污染。
        """
        return list(load_window_state(self._state_file)[KEY_RECENT_FILES])

    def add(self, path: str) -> None:
        """记录：去重 + 置顶 + 截断上限，即时原子写。

        重载/重复打开同文件只把它固定在首位，不产生噪音条目。
        """
        paths = [path] + [p for p in self.list() if p != path]
        update_window_state(
            self._state_file, {KEY_RECENT_FILES: paths[:MAX_RECENT_FILES]})

    def remove(self, path: str) -> None:
        """剔除单条（回放发现文件已消失时）。"""
        remaining = [p for p in self.list() if p != path]
        update_window_state(self._state_file, {KEY_RECENT_FILES: remaining})

    def clear(self) -> None:
        """清空（子菜单「清除列表」项）。"""
        update_window_state(self._state_file, {KEY_RECENT_FILES: []})
