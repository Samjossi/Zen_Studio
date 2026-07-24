"""最近打开的项目存取：全局共享的工作区根历史（config/recent_projects.json）。

记录源为 MainWindow 启动（一进程绑定一工作区根，菜单选文件夹 / 命令行
main.py <folder> / 新建窗口三路径汇聚），消费侧为文件菜单「最近打开的项目」
子菜单（aboutToShow 动态重建）。

存储选址（work plans/2026-0724-1003 §2.1）：
- 全局单文件而非 window_state 按工作区哈希分文件——项目列表的意义是在
  任意窗口回访任意项目，按工作区隔离时每个窗口只会记录自己，列表恒 1 条
- 不放 settings.json——reset_settings 全量重写偏好文件，最近项目是历史
  数据而非偏好，不应随「恢复默认设置」被清

读写走「读全量 → 合并 → write_json_atomic 写回」链，不自造 IO；多开窗口
并发写为后写胜（列表非关键数据，收敛即正确，不加锁）。
"""
from __future__ import annotations  # list() 方法与内建类型同名，注解延迟求值

import json
from pathlib import Path

from gui.settings import write_json_atomic

#: 列表上限（超出截断最旧）
MAX_RECENT_PROJECTS = 10

#: 文件内键名常量（消费侧唯一合法引用方式，AFCP 3.1）
KEY_RECENT_PROJECTS = "recent_projects"


class RecentProjectsStore:
    """最近打开的项目（工作区根）列表，全局共享，存 config/recent_projects.json。"""

    def __init__(self, state_file: Path) -> None:
        """
        :param state_file: 全局列表文件路径（调用方传
            CONFIG_DIR / "recent_projects.json"；显式传入，AFCP 2.3 依赖显式）
        """
        self._state_file = state_file

    def list(self) -> list[str]:
        """读回列表（新→旧）；缺失/损坏一律回退空表（拷出隔离）。

        损坏判据：文件非 dict，或键值非「元素全 str 的 list」——路径允许
        多字节 UTF-8，不适用 ASCII 判据（与 window_state 路径值判据同理）。
        """
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        paths = data.get(KEY_RECENT_PROJECTS)
        if not (isinstance(paths, list)
                and all(isinstance(p, str) for p in paths)):
            return []
        return list(paths)

    def add(self, path: str) -> None:
        """记录：规范化（resolve）+ 去重 + 置顶 + 截断上限，即时原子写。

        重复打开同根只把它固定在首位，不产生噪音条目（「新建窗口」同根
        多开 / 重复启动均无害）。
        """
        normalized = str(Path(path).resolve())
        paths = [normalized] + [p for p in self.list() if p != normalized]
        self._write(paths[:MAX_RECENT_PROJECTS])

    def remove(self, path: str) -> None:
        """剔除单条（回放发现目录已消失时）。"""
        remaining = [p for p in self.list() if p != path]
        self._write(remaining)

    def clear(self) -> None:
        """清空（子菜单「清除列表」项）。"""
        self._write([])

    def _write(self, paths: list[str]) -> None:
        """整表原子写（列表即全量，无部分键合并需求）。"""
        write_json_atomic(self._state_file, {KEY_RECENT_PROJECTS: paths})
