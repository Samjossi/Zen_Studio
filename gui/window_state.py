"""窗口状态持久化：读写 config/window_state_<hash8>.json（按工作区分文件）。

自 settings.py 分离（AFCP 整改 P3 任务 4.4）：用户偏好（settings.json）
与窗口状态（本文件）分文件存放——reset_settings 重置偏好即重写
settings.json，保留布局即不动本文件，消除"手动挑键保留"的脆弱点。
多开改造（2026-07-22，work plans/2026-0722-0756 D4）：状态文件按工作区根
哈希分文件，多开窗口各自恢复各自几何/分隔栏，互不覆盖；文件路径由
window_state_file_for(workspace_root) 推导，调用方显式传入（AFCP 2.3
依赖显式）。键空间由 WindowState 定型（6 个固定键），消费侧一律经 KEY_*
常量引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。
最近打开文件（2026-07-22，work plans/2026-0722-1901）：键空间扩
recent_files（list[str] 查看历史，按工作区隔离），值级校验按键分流
（布局值恒 ASCII base64；路径值允许多字节 UTF-8）。
"""
import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict

from PySide6.QtCore import QByteArray

from gui.settings import CONFIG_DIR, write_json_atomic

#: 旧版单文件路径（2026-07-22 多开改造前）；仅存留作一次性迁移识别
LEGACY_WINDOW_STATE_FILE = CONFIG_DIR / "window_state.json"


def window_state_file_for(workspace_root: str) -> Path:
    """工作区根 → 状态文件路径（sha256 前 8 位分文件，多开互不覆盖）。"""
    digest = hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:8]
    return CONFIG_DIR / f"window_state_{digest}.json"


def migrate_legacy_window_state(state_file: Path) -> None:
    """旧版单文件一次性迁移：本工作区哈希文件不存在则改名接管
    （默认根用户布局无损升级），否则删孤儿文件。失败静默（不阻断启动）。"""
    try:
        if not LEGACY_WINDOW_STATE_FILE.exists():
            return
        if state_file.exists():
            LEGACY_WINDOW_STATE_FILE.unlink()
        else:
            os.replace(LEGACY_WINDOW_STATE_FILE, state_file)
    except OSError:
        pass

# ----------------------------------------------------------------------
# 键名常量（消费侧唯一合法引用方式）
# ----------------------------------------------------------------------
KEY_WINDOW_GEOMETRY = "window_geometry"
KEY_SPLITTER_MAIN = "splitter_main"
KEY_SPLITTER_EDITOR = "splitter_editor"
KEY_SPLITTER_SIDEBAR = "splitter_sidebar"
KEY_SPLITTER_CHAT = "splitter_chat"
KEY_RECENT_FILES = "recent_files"


class WindowState(TypedDict):
    """window_state.json 全量结构（6 个固定键）。

    布局 5 键值均为 base64 编码的 QByteArray；None = 无记录用默认布局。
    recent_files 为最近查看文件绝对路径列表（新→旧，上限由
    gui/recent_files.py 截断）。
    """

    window_geometry: str | None   # 窗口几何
    splitter_main: str | None     # 外层水平：聊天 / 中栏 / 右栏
    splitter_editor: str | None   # 中栏垂直：查看器 / 终端
    splitter_sidebar: str | None  # 右栏垂直：文件树 / 变更面板
    splitter_chat: str | None     # 聊天面板内：输出 / 输入区
    recent_files: list[str]       # 最近打开的文件（文件菜单子菜单记录源）


class WindowStatePatch(TypedDict, total=False):
    """update_window_state 接受的部分键集合；键空间与 WindowState 一致。"""

    window_geometry: str | None
    splitter_main: str | None
    splitter_editor: str | None
    splitter_sidebar: str | None
    splitter_chat: str | None
    recent_files: list[str]


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退（布局全 None = 默认布局）
DEFAULT_WINDOW_STATE: WindowState = {
    KEY_WINDOW_GEOMETRY: None,
    KEY_SPLITTER_MAIN: None,
    KEY_SPLITTER_EDITOR: None,
    KEY_SPLITTER_SIDEBAR: None,
    KEY_SPLITTER_CHAT: None,
    KEY_RECENT_FILES: [],
}


def _is_valid_value(key: str, value: object) -> bool:
    """值级防御按键分流（损坏判据差异）：

    - 布局 5 键恒为 base64：仅放行 None / ASCII str——类型或编码异常即视为
      人为损坏，防 decode_state 的 encode("ascii") 抛异常
      （UnicodeEncodeError/AttributeError 启动崩溃）
    - recent_files 为路径列表：list 且元素全 str（路径允许多字节 UTF-8，
      不适用 ASCII 判据）
    """
    if key == KEY_RECENT_FILES:
        return isinstance(value, list) and all(isinstance(p, str) for p in value)
    return value is None or (isinstance(value, str) and value.isascii())


def load_window_state(state_file: Path) -> WindowState:
    """读取窗口状态，缺失字段回退默认值；JSON 损坏静默回退全默认。

    未登记键读取即丢弃（不写回）：键改名后存量旧键自然失效，无需迁移代码。
    值级防御经 _is_valid_value 按键分流（布局键 ASCII 判据 / recent_files
    路径列表判据），异常值一律丢弃回默认。
    """
    state = WindowState(DEFAULT_WINDOW_STATE)
    try:
        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items()
                          if k in DEFAULT_WINDOW_STATE and _is_valid_value(k, v)})
    except (OSError, json.JSONDecodeError):
        pass
    return state


def update_window_state(state_file: Path, patch: WindowStatePatch) -> None:
    """读全量 → 合并 patch → 写回；原子写（同工作区重复多开并发回写防截断）。"""
    state = load_window_state(state_file)
    state.update(patch)
    write_json_atomic(state_file, state)


def reset_window_state(state_file: Path) -> None:
    """重置窗口状态（恢复默认设置且不保留布局时）：删文件回默认布局。"""
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass


def encode_state(state: QByteArray) -> str:
    """QByteArray（窗口几何/分隔栏状态）→ base64 字符串，供 JSON 存储。"""
    return bytes(state.toBase64().data()).decode("ascii")


def decode_state(text: str) -> QByteArray:
    """base64 字符串 → QByteArray；损坏数据由 restore* 返回 False 静默兜底。"""
    return QByteArray.fromBase64(text.encode("ascii"))
