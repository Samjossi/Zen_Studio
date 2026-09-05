"""最近打开的项目存取：全局共享的工作区根历史（config/recent_projects.json）。

记录源为 MainWindow 启动（一进程绑定一工作区根，菜单选文件夹 / 命令行
main.py <folder> / 新建窗口三路径汇聚），消费侧为文件菜单「最近打开的项目」
子菜单（aboutToShow 动态重建）。

存储选址（文档/修改记录/2026-0724-1003 §2.1）：
- 全局单文件而非 window_state 按工作区哈希分文件——项目列表的意义是在
  任意窗口回访任意项目，按工作区隔离时每个窗口只会记录自己，列表恒 1 条
- 不放 settings.json——reset_settings 全量重写偏好文件，最近项目是历史
  数据而非偏好，不应随「恢复默认设置」被清

读写走「读全量 → 合并 → write_json_atomic 写回」链，不自造 IO；多开窗口
并发写为后写胜（列表非关键数据，收敛即正确，不加锁）。

last_closed_root 键（2026-0905-2025 计划）：仅由 MainWindow.closeEvent
写入（后关者胜，天然即「最后关闭」），仅由 main.py 无参启动恢复路径
（startup_mode = restore）读取；与 recent_projects 列表的「最近打开」
语义相互独立，互不扰动。
"""
from __future__ import annotations  # list() 方法与内建类型同名，注解延迟求值

import json
from pathlib import Path

from gui.settings import write_json_atomic

#: 列表上限（超出截断最旧；2026-09-02 由 10 扩到 24，菜单全量展示）
MAX_RECENT_PROJECTS = 24

#: 文件内键名常量（消费侧唯一合法引用方式，AFCP 3.1）
KEY_RECENT_PROJECTS = "recent_projects"
#: 最后关闭的工作区根（2026-0905-2025 计划）：closeEvent 唯一写入，
#: 无参启动 restore 模式唯一读取；「清除列表」连带清除
KEY_LAST_CLOSED_ROOT = "last_closed_root"


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
        paths = self._read_all().get(KEY_RECENT_PROJECTS)
        if not (isinstance(paths, list)
                and all(isinstance(p, str) for p in paths)):
            return []
        return list(paths)

    def get_last_closed(self) -> str | None:
        """读回最后关闭的工作区根；缺失/损坏/类型非 str 一律回退 None。"""
        path = self._read_all().get(KEY_LAST_CLOSED_ROOT)
        return path if isinstance(path, str) else None

    def set_last_closed(self, path: str) -> None:
        """记录最后关闭的工作区根：规范化（resolve）+ 合并写回（保留列表键）。

        仅 MainWindow.closeEvent 调用（后关者胜）；启动路径不写——
        写入时机即「最后关闭」语义的定义本身（2026-0905-2025 计划 D1/D2）。
        """
        data = self._read_all()
        data[KEY_LAST_CLOSED_ROOT] = str(Path(path).resolve())
        write_json_atomic(self._state_file, data)

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
        """清空（子菜单「清除列表」项）：列表与 last_closed_root 一并清除
        （2026-0905-2025 计划 D5——两者同属历史，只清列表会让清空后启动
        仍恢复旧项目）。"""
        write_json_atomic(self._state_file, {KEY_RECENT_PROJECTS: []})

    def _read_all(self) -> dict:
        """读全量原始 dict；缺失/损坏/非 dict 一律回退空 dict。"""
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, paths: list[str]) -> None:
        """整表原子写：列表键全量覆盖，保留 last_closed_root 键。"""
        data = self._read_all()
        data[KEY_RECENT_PROJECTS] = paths
        write_json_atomic(self._state_file, data)
