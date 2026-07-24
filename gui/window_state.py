"""窗口状态持久化：读写 config/window_state/<hash8>.json（按工作区分文件）。

自 settings.py 分离（AFCP 整改 P3 任务 4.4）：用户偏好（settings.json）
与窗口状态（本文件）分文件存放——reset_settings 重置偏好即重写
settings.json，保留布局即不动本文件，消除"手动挑键保留"的脆弱点。
多开改造（2026-07-22，work plans/2026-0722-0756 D4）：状态文件按工作区根
哈希分文件，多开窗口各自恢复各自几何/分隔栏，互不覆盖；文件路径由
window_state_file_for(workspace_root) 推导，调用方显式传入（AFCP 2.3
依赖显式）。键空间由 WindowState 定型（5 个固定键），消费侧一律经 KEY_*
常量引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。
最近打开文件键 recent_files 已于 2026-07-24 回收（work plans/
2026-0724-1003：功能改造为「最近打开的项目」，改存全局
config/recent_projects.json）；存量状态文件中的旧键经「未登记键读取
即丢弃」自然失效，零迁移代码。
VS Code 式改造（2026-07-24，work plans/2026-0724-1015）：
①目录收纳——状态文件统一收进 config/window_state/ 子目录（对齐 VS Code
workspaceStorage/，不散落 config 根目录），存量根目录文件经
migrate_state_dir() 一次性幂等迁入；
②布局继承——新增全局 config/window_state/default.json（仅布局键），
新工作区首开继承最近关闭窗口布局（load_layout_state 回退链：
工作区哈希文件 → default → 全默认），已知工作区仍恢复自身文件；
关闭时双写（哈希文件 + default），后写胜 = 最后关闭窗口生效。
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

#: 窗口状态子目录（2026-07-24 收编，对齐 VS Code workspaceStorage/）：
#: 哈希文件与 default.json 的唯一存放点，不散落 config 根目录
WINDOW_STATE_DIR = CONFIG_DIR / "window_state"

#: 全局布局继承源（仅布局键）：新工作区首开继承最近关闭窗口布局
DEFAULT_LAYOUT_FILE = WINDOW_STATE_DIR / "default.json"


def window_state_file_for(workspace_root: str) -> Path:
    """工作区根 → 状态文件路径（sha256 前 8 位分文件，多开互不覆盖）。"""
    digest = hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:8]
    return WINDOW_STATE_DIR / f"{digest}.json"


def migrate_state_dir() -> None:
    """根目录存量状态文件一次性迁入子目录（幂等，失败静默不阻断启动）。

    2026-07-24 目录收编前，状态文件散落 config 根目录
    （window_state_<hash8>.json）；逐个搬入子目录并去前缀改名。
    目标已存在时删根目录侧（新版本已接管该工作区，防双写期分叉）。
    """
    try:
        WINDOW_STATE_DIR.mkdir(exist_ok=True)
        for legacy_file in CONFIG_DIR.glob("window_state_*.json"):
            target = WINDOW_STATE_DIR / legacy_file.name.removeprefix("window_state_")
            if target.exists():
                legacy_file.unlink()
            else:
                os.replace(legacy_file, target)
    except OSError:
        pass


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


class WindowState(TypedDict):
    """window_state.json 全量结构（5 个固定键）。

    布局 5 键值均为 base64 编码的 QByteArray；None = 无记录用默认布局。
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


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退（布局全 None = 默认布局）
DEFAULT_WINDOW_STATE: WindowState = {
    KEY_WINDOW_GEOMETRY: None,
    KEY_SPLITTER_MAIN: None,
    KEY_SPLITTER_EDITOR: None,
    KEY_SPLITTER_SIDEBAR: None,
    KEY_SPLITTER_CHAT: None,
}

#: 布局键集合：default.json 的合法键空间（防非布局键写入全局继承源，
#: 跨工作区泄漏私有数据——如未来再扩工作区私键）
_LAYOUT_KEYS = frozenset(DEFAULT_WINDOW_STATE)


def _is_valid_value(value: object) -> bool:
    """值级防御：布局键恒为 base64——仅放行 None / ASCII str。

    类型或编码异常即视为人为损坏，防 decode_state 的 encode("ascii")
    抛异常（UnicodeEncodeError/AttributeError 启动崩溃）。
    """
    return value is None or (isinstance(value, str) and value.isascii())


def load_window_state(state_file: Path) -> WindowState:
    """读取窗口状态，缺失字段回退默认值；JSON 损坏静默回退全默认。

    未登记键读取即丢弃（不写回）：键改名/回收后存量旧键自然失效，无需
    迁移代码。值级防御经 _is_valid_value（布局键 ASCII 判据），异常值
    一律丢弃回默认。
    """
    state = WindowState(DEFAULT_WINDOW_STATE)
    try:
        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items()
                          if k in DEFAULT_WINDOW_STATE and _is_valid_value(v)})
    except (OSError, json.JSONDecodeError):
        pass
    return state


def load_layout_state(state_file: Path) -> WindowState:
    """读取布局状态（文件级回退链：工作区哈希文件 → default → 全默认）。

    哈希文件存在即全量采用（不做键级合并）；不存在时回退全局
    default.json——新工作区首开继承最近关闭窗口布局（VS Code 语义）。
    每级复用 load_window_state 的损坏防御，末端恒为全默认。
    """
    if state_file.exists():
        return load_window_state(state_file)
    return load_window_state(DEFAULT_LAYOUT_FILE)


def update_default_layout(patch: WindowStatePatch) -> None:
    """双写全局布局继承源：过滤至布局键后走原子链（后写胜 =
    最后关闭窗口成为下一个新工作区的继承源）。"""
    layout_patch = WindowStatePatch(
        {k: v for k, v in patch.items() if k in _LAYOUT_KEYS})
    update_window_state(DEFAULT_LAYOUT_FILE, layout_patch)


def update_window_state(state_file: Path, patch: WindowStatePatch) -> None:
    """读全量 → 合并 patch → 写回；原子写（同工作区重复多开并发回写防截断）。

    写入前兜底建子目录（迁移未跑过的极简启动路径亦不裸写失败）。
    """
    state_file.parent.mkdir(parents=True, exist_ok=True)
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
