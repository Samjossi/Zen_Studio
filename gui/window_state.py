"""窗口状态持久化：读写 config/window_state.json。

自 settings.py 分离（AFCP 整改 P3 任务 4.4）：用户偏好（settings.json）
与窗口状态（本文件）分文件存放——reset_settings 重置偏好即重写
settings.json，保留布局即不动本文件，消除"手动挑键保留"的脆弱点。
键空间由 WindowState 定型（5 个固定键），消费侧一律经 KEY_* 常量
引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。
"""
import json
from typing import TypedDict

from PySide6.QtCore import QByteArray

from core.paths import PROJECT_ROOT

WINDOW_STATE_FILE = PROJECT_ROOT / "config" / "window_state.json"

# ----------------------------------------------------------------------
# 键名常量（消费侧唯一合法引用方式）
# ----------------------------------------------------------------------
KEY_WINDOW_GEOMETRY = "window_geometry"
KEY_SPLITTER_MAIN = "splitter_main"
KEY_SPLITTER_EDITOR = "splitter_editor"
KEY_SPLITTER_SIDEBAR = "splitter_sidebar"
KEY_SPLITTER_CHAT = "splitter_chat"


class WindowState(TypedDict):
    """window_state.json 全量结构（5 个固定键）。

    值均为 base64 编码的 QByteArray；None = 无记录用默认布局。
    """

    window_geometry: str | None   # 窗口几何
    splitter_main: str | None     # 外层水平：聊天 / 中栏 / 右栏
    splitter_editor: str | None   # 中栏垂直：查看器 / 终端
    splitter_sidebar: str | None  # 右栏垂直：文件树 / 变更面板
    splitter_chat: str | None     # 聊天面板内：输出 / 输入区


class WindowStatePatch(TypedDict, total=False):
    """update_window_state 接受的部分键集合；键空间与 WindowState 一致。"""

    window_geometry: str | None
    splitter_main: str | None
    splitter_editor: str | None
    splitter_sidebar: str | None
    splitter_chat: str | None


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退（全 None = 默认布局）
DEFAULT_WINDOW_STATE: WindowState = {
    KEY_WINDOW_GEOMETRY: None,
    KEY_SPLITTER_MAIN: None,
    KEY_SPLITTER_EDITOR: None,
    KEY_SPLITTER_SIDEBAR: None,
    KEY_SPLITTER_CHAT: None,
}


def load_window_state() -> WindowState:
    """读取窗口状态，缺失字段回退默认值；JSON 损坏静默回退全默认。

    未登记键读取即丢弃（不写回）：键改名后存量旧键自然失效，无需迁移代码。
    值级防御：非 ASCII str/None 以外的值一并丢弃——base64 状态值恒为
    ASCII str，类型或编码异常即视为人为损坏，防 decode_state 的
    encode("ascii") 抛异常（UnicodeEncodeError/AttributeError 启动崩溃）。
    """
    state = WindowState(DEFAULT_WINDOW_STATE)
    try:
        with open(WINDOW_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items()
                          if k in DEFAULT_WINDOW_STATE
                          and (v is None or (isinstance(v, str) and v.isascii()))})
    except (OSError, json.JSONDecodeError):
        pass
    return state


def update_window_state(patch: WindowStatePatch) -> None:
    """读全量 → 合并 patch → 写回，实现单键/多键持久化。"""
    state = load_window_state()
    state.update(patch)
    WINDOW_STATE_FILE.parent.mkdir(exist_ok=True)
    with open(WINDOW_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def reset_window_state() -> None:
    """重置窗口状态（恢复默认设置且不保留布局时）：删文件回默认布局。"""
    try:
        WINDOW_STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def encode_state(state: QByteArray) -> str:
    """QByteArray（窗口几何/分隔栏状态）→ base64 字符串，供 JSON 存储。"""
    return bytes(state.toBase64().data()).decode("ascii")


def decode_state(text: str) -> QByteArray:
    """base64 字符串 → QByteArray；损坏数据由 restore* 返回 False 静默兜底。"""
    return QByteArray.fromBase64(text.encode("ascii"))
