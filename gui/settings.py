"""用户偏好持久化：读写 config/settings.json。

自 theme.py 抽出（2026-07-19，见 文档/修改记录/2026-0719-0712_
GUI窗口状态与模型选择持久化计划.md）：主题、字号、模型选择等
各模块共用同一份 JSON，统一"读全量 → 合并 → 写回"入口，避免多处
各自读写互相覆盖。主题有效性校验仍留在 theme.py（此处不感知主题注册表）。

窗口几何与分隔栏状态已分离至 window_state.py（AFCP 整改 P3 任务 4.4）。
多开并发治理（2026-07-22，work plans/2026-0722-0756 D7）：多进程共享
settings.json，update_settings 以 flock 串行化"读-合并-写"三步根治丢更新，
写临时文件 + os.replace 原子覆盖防文件损坏；工作区根改由启动参数决定，
不再持久化（KEY_WORKSPACE_ROOT 已删，存量旧键读取即丢弃自然失效）。

键空间由 AppSettings 定型（7 个固定键），消费侧一律经 KEY_* 常量
引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。
"""
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict

from core.paths import PROJECT_ROOT
from llm import BACKEND_KIMI_CLI

CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
#: flock 锁文件（仅 Linux；进程死亡锁自动释放，无残留死锁）
SETTINGS_LOCK_FILE = CONFIG_DIR / "settings.lock"

# ----------------------------------------------------------------------
# 键名常量（消费侧唯一合法引用方式）
# ----------------------------------------------------------------------
KEY_THEME = "theme"
KEY_FONT_SIZE = "font_size"
KEY_FONT_FAMILY = "font_family"
KEY_MODEL_BACKEND = "model_backend"
KEY_MODEL_VERSION = "model_version"
KEY_TERMINAL_SWAP_COPY_PASTE = "terminal_swap_copy_paste"
KEY_PERMISSION_AUTO_ALLOW = "permission_auto_allow"

#: 默认主题名（全库唯一来源；theme.py FALLBACK_THEME 与各面板缺省主题均引用此值，
#: 不可反向引用 theme.py——theme 依赖本模块，反向成环）
DEFAULT_THEME = "cloud"


class AppSettings(TypedDict):
    """settings.json 全量结构（7 个固定键，均为用户偏好）。"""

    theme: str                   # 主题名（gui/theme.py 注册表键）
    font_size: int               # 全局 UI 字号（pt）
    #: 全局 UI 字体族（apply_theme 应用；目前固定自带思源黑体，登记供持久化一致）
    font_family: str
    #: 聊天面板模型（registry 后端名）与版本（模型别名；None = 取版本列表第一项）
    model_backend: str
    model_version: str | None
    #: 终端复制/粘贴快捷键反转（True：Ctrl+C/V 复制粘贴，Ctrl+Shift+C/V 发 SIGINT/\x16）
    terminal_swap_copy_paste: bool
    #: AI 工具自动放行（方案 F 默认放手；True：仅危险命令黑名单命中弹窗，
    #: False：恢复逐次确认现状——逃生舱）
    permission_auto_allow: bool


class AppSettingsPatch(TypedDict, total=False):
    """update_settings 接受的部分键集合；键空间与 AppSettings 一致。"""

    theme: str
    font_size: int
    font_family: str
    model_backend: str
    model_version: str | None
    terminal_swap_copy_paste: bool
    permission_auto_allow: bool


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退
DEFAULT_SETTINGS: AppSettings = {
    KEY_THEME: DEFAULT_THEME,
    KEY_FONT_SIZE: 10,
    KEY_FONT_FAMILY: "Source Han Sans CN",
    KEY_MODEL_BACKEND: BACKEND_KIMI_CLI,
    KEY_MODEL_VERSION: None,
    KEY_TERMINAL_SWAP_COPY_PASTE: False,
    KEY_PERMISSION_AUTO_ALLOW: True,
}


def load_settings() -> AppSettings:
    """读取持久化配置，缺失字段回退默认值；JSON 损坏静默回退全默认。

    未登记键读取即丢弃（不写回）：键改名/键迁移（如窗口状态键迁入
    window_state.json）后存量旧键自然失效，无需迁移代码。
    """
    settings = AppSettings(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def write_json_atomic(file_path: Path, data: dict) -> None:
    """同分区临时文件 + os.replace 原子写 JSON（防中途崩溃留半截文件）。

    临时文件落在目标同目录（os.replace 才原子）；window_state.py 复用。
    """
    file_path.parent.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=file_path.parent, prefix=f".{file_path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, file_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_settings(patch: AppSettingsPatch) -> None:
    """读全量 → 合并 patch → 写回，实现单键/多键持久化。

    多开并发治理（D7）：flock 文件锁串行化"读-合并-写"三步，根治多进程
    并发丢更新；写回走 write_json_atomic 原子覆盖。
    """
    CONFIG_DIR.mkdir(exist_ok=True)
    with open(SETTINGS_LOCK_FILE, "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        settings = load_settings()
        settings.update(patch)
        write_json_atomic(SETTINGS_FILE, settings)
