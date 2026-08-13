"""用户偏好持久化：读写 config/settings.json（打包态：~/.config/zen-studio/）。

自 theme.py 抽出（2026-07-19，见 文档/修改记录/2026-0719-0712_
GUI窗口状态与模型选择持久化计划.md）：主题、字号、模型选择等
各模块共用同一份 JSON，统一"读全量 → 合并 → 写回"入口，避免多处
各自读写互相覆盖。主题有效性校验仍留在 theme.py（此处不感知主题注册表）。

窗口几何与分隔栏状态已分离至 window_state.py（AFCP 整改 P3 任务 4.4）。
多开并发治理（2026-07-22，文档/修改记录/2026-0722-0756 D7）：多进程共享
settings.json，update_settings 以 flock 串行化"读-合并-写"三步根治丢更新，
写临时文件 + os.replace 原子覆盖防文件损坏；工作区根改由启动参数决定，
不再持久化（KEY_WORKSPACE_ROOT 已删，存量旧键读取即丢弃自然失效）。

键空间由 AppSettings 定型（10 个固定键），消费侧一律经 KEY_* 常量
引用键名，禁止裸字符串键（AFCP 3.1：数据结构显式）。

权限键演进（2026-07-22，文档/修改记录/2026-0722-1240 计划）：二态
permission_auto_allow 布尔替换为四态 permission_mode 字符串枚举
（confirm_all / confirm_execute / auto_guarded / auto_all，模式常量单一
来源在 llm/permission_policy.py）；旧键读取时一次性迁移（false →
confirm_all，见 load_settings），迁移后随下次写盘自然消亡。

模型键演进（2026-07-31，文档/修改记录/2026-0731-0052 计划）：全局单值
model_version（切后台即被覆盖丢失）替换为记忆表 model_versions
（dict[接口实现名, 模型别名]；接口缺失 = 未定制，跟随其模型列表
首项）；旧键读取时一次性 seed 迁移（见 load_settings），随下次写盘
自然消亡。记忆单条目写入走 remember_model_version()——flock 锁内
"读-改-写"防多开实例写不同接口记忆时互相覆盖。
"""
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict

from core.paths import USER_CONFIG_DIR
from llm import BACKEND_KIMI_ACP
from llm.permission_policy import DEFAULT_PERMISSION_MODE, MODE_CONFIRM_ALL

#: 配置目录：开发态项目内 config/；打包态 XDG（core/paths.py USER_CONFIG_DIR，
#: 见 文档/修改记录/2026-0725-1053 计划 T7/T8——AppImage 只读挂载的硬性前置）
CONFIG_DIR = USER_CONFIG_DIR
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
KEY_MODEL_VERSIONS = "model_versions"
#: 推理强度记忆表（2026-0806 计划）：接口实现名 → 用户显式选定的强度值
KEY_MODEL_EFFORTS = "model_efforts"
KEY_TERMINAL_SWAP_COPY_PASTE = "terminal_swap_copy_paste"
KEY_PERMISSION_MODE = "permission_mode"
KEY_CHAT_RENDERER = "chat_renderer"
#: kimi 子代理 wire 旁路开关（0813-1919 计划 T4；默认开，False 回退
#: 纯 ACP 行为——子代理仅起止 + 成果摘要；格式漂移熔断时的手动回退通道）
KEY_KIMI_WIRE_SIDECAR = "kimi_wire_sidecar"

#: 对话区渲染轨值域（0645 融合计划 D2-A 双轨并存：cards 卡片轨默认 /
#: classic 旧轨经典——旧轨冻结保留即回退通道，新轨稳定一个版本后再议下线）
CHAT_RENDERER_CARDS = "cards"
CHAT_RENDERER_CLASSIC = "classic"
CHAT_RENDERERS = (CHAT_RENDERER_CARDS, CHAT_RENDERER_CLASSIC)
DEFAULT_CHAT_RENDERER = CHAT_RENDERER_CARDS

#: 旧权限键（2026-0722-1240 计划前）：仅用于 load_settings 一次性迁移读取，
#: 消费侧禁止引用（未登记新键时它已不在 DEFAULT_SETTINGS 内，不写回）
_LEGACY_KEY_PERMISSION_AUTO_ALLOW = "permission_auto_allow"

#: 旧模型键（2026-0731-0052 计划前：全局单值，切后台即被覆盖丢失）：
#: 仅用于 load_settings 一次性迁移读取（seed 进 model_versions 记忆表），
#: 消费侧禁止引用；旧键随下次 update_settings 全量写回自然消亡
_LEGACY_KEY_MODEL_VERSION = "model_version"

#: 默认主题名（全库唯一来源；theme.py FALLBACK_THEME 与各面板缺省主题均引用此值，
#: 不可反向引用 theme.py——theme 依赖本模块，反向成环）
DEFAULT_THEME = "cloud"


class AppSettings(TypedDict):
    """settings.json 全量结构（10 个固定键，均为用户偏好）。"""

    theme: str                   # 主题名（gui/theme.py 注册表键）
    font_size: int               # 全局 UI 字号（pt）
    #: 全局 UI 字体族（apply_theme 应用；目前固定自带思源黑体，登记供持久化一致）
    font_family: str
    #: 聊天面板当前接口（registry 后端名）
    model_backend: str
    #: 模型记忆表（2026-0731-0052 计划 D1）：接口实现名 → 用户显式选定的
    #: 模型别名；某接口缺失 = 未定制，跟随其模型列表首项（不写条目）
    model_versions: dict[str, str]
    #: 推理强度记忆表（2026-0806 计划，与 model_versions 同构）：接口实现名
    #: → 用户显式选定的强度值（如 "high"）；某接口缺失 = 未定制，跟随
    #: agent 默认强度（不下发 set_config_option）
    model_efforts: dict[str, str]
    #: 终端复制/粘贴快捷键反转（True：Ctrl+C/V 复制粘贴，Ctrl+Shift+C/V 发 SIGINT/\x16）
    terminal_swap_copy_paste: bool
    #: AI 工具权限模式（四态枚举，值域见 llm/permission_policy.PERMISSION_MODES：
    #: confirm_all 逐次确认 / confirm_execute 仅命令确认 / auto_guarded 智能
    #: 放行+黑名单兜底（默认）/ auto_all 全部放行）
    permission_mode: str
    #: 对话区渲染轨（0645 融合计划：cards 卡片折叠轨（默认）/ classic
    #: 经典行文本轨；新建会话标签生效，存量标签不热切换）
    chat_renderer: str
    #: kimi 子代理 wire 旁路开关（0813-1919 计划 T4：True 默认——
    #: 子代理内部活动经 wire.jsonl 旁路嵌套显示；False 回退纯 ACP）
    kimi_wire_sidecar: bool


class AppSettingsPatch(TypedDict, total=False):
    """update_settings 接受的部分键集合；键空间与 AppSettings 一致。"""

    theme: str
    font_size: int
    font_family: str
    model_backend: str
    model_versions: dict[str, str]
    model_efforts: dict[str, str]
    terminal_swap_copy_paste: bool
    permission_mode: str
    chat_renderer: str
    kimi_wire_sidecar: bool


#: 默认值：文件缺失 / 字段缺失 / JSON 损坏时回退
DEFAULT_SETTINGS: AppSettings = {
    KEY_THEME: DEFAULT_THEME,
    KEY_FONT_SIZE: 10,
    KEY_FONT_FAMILY: "Source Han Sans CN",
    KEY_MODEL_BACKEND: BACKEND_KIMI_ACP,
    KEY_MODEL_VERSIONS: {},
    KEY_MODEL_EFFORTS: {},
    KEY_TERMINAL_SWAP_COPY_PASTE: False,
    KEY_PERMISSION_MODE: DEFAULT_PERMISSION_MODE,
    KEY_CHAT_RENDERER: DEFAULT_CHAT_RENDERER,
    KEY_KIMI_WIRE_SIDECAR: True,
}


def load_settings() -> AppSettings:
    """读取持久化配置，缺失字段回退默认值；JSON 损坏静默回退全默认。

    未登记键读取即丢弃（不写回）：键改名/键迁移（如窗口状态键迁入
    window_state.json）后存量旧键自然失效，无需迁移代码。
    例外一（review 修复）：旧 permission_auto_allow=false 用户显式选择过最
    保守的逐次确认，静默落入默认中间档是安全姿态降级——读取时一次性
    映射为 confirm_all（不写回，旧键随下次 update_settings 全量写回消亡）。
    例外二（2026-0731-0052 计划 D2）：旧全局单值 model_version 为 str 且
    model_backend 已知 → seed 进 model_versions 记忆表（不写回，同上消亡）。
    """
    settings = AppSettings(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
            if (KEY_PERMISSION_MODE not in data
                    and data.get(_LEGACY_KEY_PERMISSION_AUTO_ALLOW) is False):
                settings[KEY_PERMISSION_MODE] = MODE_CONFIRM_ALL
            # 类型防御 + 脱离 DEFAULT_SETTINGS 共享引用（remember_model_version
            # 锁内原地改 dict 后写回，不拷贝会污染进程内默认值）；先拷贝再迁移
            versions = settings[KEY_MODEL_VERSIONS]
            if not isinstance(versions, dict):
                versions = {}
            versions = {k: v for k, v in versions.items()
                        if isinstance(k, str) and isinstance(v, str)}
            legacy_version = data.get(_LEGACY_KEY_MODEL_VERSION)
            legacy_backend = data.get(KEY_MODEL_BACKEND)
            if isinstance(legacy_version, str) and isinstance(legacy_backend, str):
                versions.setdefault(legacy_backend, legacy_version)
            settings[KEY_MODEL_VERSIONS] = versions
            # 强度记忆表同款类型防御 + 脱离共享引用（2026-0806 计划）
            efforts = settings[KEY_MODEL_EFFORTS]
            if not isinstance(efforts, dict):
                efforts = {}
            settings[KEY_MODEL_EFFORTS] = {k: v for k, v in efforts.items()
                                           if isinstance(k, str) and isinstance(v, str)}
    except (OSError, json.JSONDecodeError):
        pass
    # 值域防御：渲染轨非法值（手改配置/旧版残留）回退默认轨
    if settings[KEY_CHAT_RENDERER] not in CHAT_RENDERERS:
        settings[KEY_CHAT_RENDERER] = DEFAULT_CHAT_RENDERER
    # 异常/文件缺失路径同样脱离共享引用
    settings[KEY_MODEL_VERSIONS] = dict(settings[KEY_MODEL_VERSIONS])
    settings[KEY_MODEL_EFFORTS] = dict(settings[KEY_MODEL_EFFORTS])
    return settings


def write_json_atomic(file_path: Path, data: dict) -> None:
    """同分区临时文件 + os.replace 原子写 JSON（防中途崩溃留半截文件）。

    临时文件落在目标同目录（os.replace 才原子）；window_state.py 复用。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_LOCK_FILE, "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        settings = load_settings()
        settings.update(patch)
        write_json_atomic(SETTINGS_FILE, settings)


def remember_model_version(backend: str, alias: str) -> None:
    """模型记忆表单条目写入（2026-0731-0052 计划 D5）：flock 锁内
    "读全量 → 改 dict 单条目 → 原子写回"。

    不能用锁外合并 dict 再整体 update_settings——多开实例写不同接口
    记忆时 patch 值互相覆盖丢更新；锁内合并复用 update_settings 同款
    flock/原子写设施（fcntl flock 同进程可重入，直接调 load_settings
    无死锁）。
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_LOCK_FILE, "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        settings = load_settings()
        settings[KEY_MODEL_VERSIONS][backend] = alias
        write_json_atomic(SETTINGS_FILE, settings)


def remember_model_effort(backend: str, effort: str) -> None:
    """推理强度记忆表单条目写入（2026-0806 计划）：与 remember_model_version
    完全同构——flock 锁内「读全量 → 改 dict 单条目 → 原子写回」，防多开
    实例写不同接口记忆时互相覆盖。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_LOCK_FILE, "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        settings = load_settings()
        settings[KEY_MODEL_EFFORTS][backend] = effort
        write_json_atomic(SETTINGS_FILE, settings)
