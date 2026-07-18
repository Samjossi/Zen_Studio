"""通用配置持久化：读写 config/settings.json。

自 theme.py 抽出（2026-07-19，见 文档/修改记录/2026-0719-0712_
GUI窗口状态与模型选择持久化计划.md）：窗口几何、分隔栏、模型选择等
各模块共用同一份 JSON，统一"读全量 → 合并 → 写回"入口，避免多处
各自读写互相覆盖。主题有效性校验仍留在 theme.py（此处不感知主题注册表）。
"""
import json
from pathlib import Path

from PySide6.QtCore import QByteArray

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退
#: 注：font_family 字面量与 gui.theme.BUNDLED_FAMILY 同源，改名需同步
DEFAULT_SETTINGS = {
    "theme": "cloud",
    "font_family": "Source Han Sans CN",
    "font_size": 10,
    # 窗口几何与三处分隔栏状态（base64 编码的 QByteArray；None = 无记录用默认布局）
    "window_geometry": None,
    "splitter_main": None,     # 外层水平：聊天 / 中栏 / 文件树
    "splitter_middle": None,   # 中栏垂直：查看器 / 终端
    "splitter_chat": None,     # 聊天面板内：输出 / 输入区
    # 聊天面板模型（registry 后端名）与版本（模型别名；None = 取版本列表第一项）
    "model_backend": "kimi-cli",
    "model_version": None,
}


def load_settings() -> dict:
    """读取持久化配置，缺失字段回退默认值；JSON 损坏静默回退全默认。"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def update_settings(patch: dict) -> None:
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
