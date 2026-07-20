"""通用配置持久化：读写 config/settings.json。

自 theme.py 抽出（2026-07-19，见 文档/修改记录/2026-0719-0712_
GUI窗口状态与模型选择持久化计划.md）：窗口几何、分隔栏、模型选择等
各模块共用同一份 JSON，统一"读全量 → 合并 → 写回"入口，避免多处
各自读写互相覆盖。主题有效性校验仍留在 theme.py（此处不感知主题注册表）。

键空间由 AppSettings 定型（11 个固定键），消费侧一律经 KEY_* 常量
引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。
"""
import json
from pathlib import Path
from typing import TypedDict

from PySide6.QtCore import QByteArray

from llm import BACKEND_KIMI_CLI

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# ----------------------------------------------------------------------
# 键名常量（消费侧唯一合法引用方式）
# ----------------------------------------------------------------------
KEY_THEME = "theme"
KEY_FONT_SIZE = "font_size"
KEY_FONT_FAMILY = "font_family"
KEY_WORKSPACE_ROOT = "workspace_root"
KEY_WINDOW_GEOMETRY = "window_geometry"
KEY_SPLITTER_MAIN = "splitter_main"
KEY_SPLITTER_MIDDLE = "splitter_middle"
KEY_SPLITTER_RIGHT = "splitter_right"
KEY_SPLITTER_CHAT = "splitter_chat"
KEY_MODEL_BACKEND = "model_backend"
KEY_MODEL_VERSION = "model_version"

#: 默认主题名（全库唯一来源；theme.py FALLBACK_THEME 与各面板缺省主题均引用此值，
#: 不可反向引用 theme.py——theme 依赖本模块，反向成环）
DEFAULT_THEME = "cloud"


class AppSettings(TypedDict):
    """settings.json 全量结构（11 个固定键）。"""

    theme: str                   # 主题名（gui/theme.py 注册表键）
    font_size: int               # 全局 UI 字号（pt）
    #: 全局 UI 字体族（apply_theme 应用；目前固定自带思源黑体，登记供持久化一致）
    font_family: str
    #: 工作区根（打开文件夹切换；None = 项目根目录）
    workspace_root: str | None
    #: 窗口几何与四处分隔栏状态（base64 编码的 QByteArray；None = 无记录用默认布局）
    window_geometry: str | None
    splitter_main: str | None    # 外层水平：聊天 / 中栏 / 右栏
    splitter_middle: str | None  # 中栏垂直：查看器 / 终端
    splitter_right: str | None   # 右栏垂直：文件树 / 变更面板
    splitter_chat: str | None    # 聊天面板内：输出 / 输入区
    #: 聊天面板模型（registry 后端名）与版本（模型别名；None = 取版本列表第一项）
    model_backend: str
    model_version: str | None


class AppSettingsPatch(TypedDict, total=False):
    """update_settings 接受的部分键集合；键空间与 AppSettings 一致。"""

    theme: str
    font_size: int
    font_family: str
    workspace_root: str | None
    window_geometry: str | None
    splitter_main: str | None
    splitter_middle: str | None
    splitter_right: str | None
    splitter_chat: str | None
    model_backend: str
    model_version: str | None


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退
DEFAULT_SETTINGS: AppSettings = {
    KEY_THEME: DEFAULT_THEME,
    KEY_FONT_SIZE: 10,
    KEY_FONT_FAMILY: "Source Han Sans CN",
    KEY_WORKSPACE_ROOT: None,
    KEY_WINDOW_GEOMETRY: None,
    KEY_SPLITTER_MAIN: None,
    KEY_SPLITTER_MIDDLE: None,
    KEY_SPLITTER_RIGHT: None,
    KEY_SPLITTER_CHAT: None,
    KEY_MODEL_BACKEND: BACKEND_KIMI_CLI,
    KEY_MODEL_VERSION: None,
}


def load_settings() -> AppSettings:
    """读取持久化配置，缺失字段回退默认值；JSON 损坏静默回退全默认。"""
    settings = AppSettings(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def update_settings(patch: AppSettingsPatch) -> None:
    """读全量 → 合并 patch → 写回，实现单键/多键持久化。"""
    settings = load_settings()
    settings.update(patch)
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def encode_state(state: QByteArray) -> str:
    """QByteArray（窗口几何/分隔栏状态）→ base64 字符串，供 JSON 存储。"""
    return bytes(state.toBase64().data()).decode("ascii")


def decode_state(text: str) -> QByteArray:
    """base64 字符串 → QByteArray；损坏数据由 restore* 返回 False 静默兜底。"""
    return QByteArray.fromBase64(text.encode("ascii"))
