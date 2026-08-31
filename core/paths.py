"""项目级路径常量：全库唯一推导点，其余模块一律 import。

此前多处独立推导（main / main_window / terminal.session / llm providers 等），
`parents[N]` 层数不一，文件移动即静默漂移；收口后移动本文件只需改一行。

PyInstaller frozen 兼容（2026-07-25，见 文档/修改记录/2026-0725-0859 计划 T4）：
打包态（onedir）内嵌解释器的 __file__ 指向解包目录，parents 推导原地失效；
检测到 sys._MEIPASS 即改指解包根（onedir 下即 exe 同级的 _internal/），
assets/ 经 spec datas 原样收编落位于此。

用户数据写盘路径（2026-07-25，文档/修改记录/2026-0725-1053 计划 T7）：
开发态维持项目内 config/；打包态（frozen/AppImage）指向 XDG 配置目录
${XDG_CONFIG_HOME:-~/.config}/zen-studio/——AppImage 为只读 squashfs，
写解包目录必然失败，用户数据必须写用户目录。
"""
import hashlib
import os
import sys
from pathlib import Path

#: PyInstaller 解包根（非 frozen 为 None）；frozen 判据全库唯一定义点，
#: 消费方一律经 IS_FROZEN / PROJECT_ROOT / USER_CONFIG_DIR 间接使用，禁止散落重写
_MEIPASS = getattr(sys, "_MEIPASS", None)

#: 打包态判据（PyInstaller frozen）公共导出口：全库唯一判定来源，
#: 消费方一律 import 本常量，禁止各自探测 sys.frozen / sys._MEIPASS
IS_FROZEN = _MEIPASS is not None

#: 项目根（开发态：本文件位于 core/，上一级即项目根）
#: 打包态（PyInstaller frozen）：解包根 sys._MEIPASS
PROJECT_ROOT = Path(
    _MEIPASS if _MEIPASS else Path(__file__).resolve().parents[1]
)

#: 资产根（assets/：字体 + Logo + 主题模板等只读资源，spec datas 按子目录收编）
ASSETS_DIR = PROJECT_ROOT / "assets"

#: Logo 专目录（assets/logo/：母版 logo.svg + 八尺寸 PNG 成套件）
LOGO_DIR = ASSETS_DIR / "logo"

#: 主题模板专目录（assets/themes/：base.qss，gui/theme.py 消费的只读资源）
THEMES_ASSETS_DIR = ASSETS_DIR / "themes"

#: 用户数据根（可写：settings/recent_projects/window_state 等）
#: 开发态：项目内 config/；打包态：${XDG_CONFIG_HOME:-~/.config}/zen-studio/
#: XDG 规范：XDG_CONFIG_HOME 为相对路径时视为无效须忽略（审计 W5），
#: 否则写盘落点随进程 CWD 漂移，AppImage 只读 CWD 下 mkdir 必崩
def _user_config_dir() -> Path:
    if not _MEIPASS:
        return PROJECT_ROOT / "config"
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or "")
    base = xdg if xdg.is_absolute() else Path.home() / ".config"
    return base / "zen-studio"


USER_CONFIG_DIR = _user_config_dir()


def workspace_digest(workspace_root: str) -> str:
    """工作区根 → 8 位摘要（sha256 前缀）。

    单一来源（一窗一根 2026-0831-2350 计划 D1）：window_state / sessions
    分文件命名与 root_ownership 套接字命名共用，防三处内联哈希漂移。
    """
    return hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:8]


def workspace_display_path(path: str | Path, workspace_root: str | Path | None) -> str:
    """本地绝对路径 → 展示路径：工作区内转相对（posix），区外保留绝对（posix）。

    单一换算来源（2026-0806-1908 计划 T1 改动点 2）：输入框 @mention
    （gui/panels/chat/input.py _mention_text）与 ACP 图片附件路径透传
    （llm/providers/acp.py build_prompt_blocks）共用，防两处漂移。
    root 为 None（未知工作区）时退化为绝对路径。
    """
    p = Path(path).resolve()
    if workspace_root is not None:
        try:
            return p.relative_to(Path(workspace_root).resolve()).as_posix()
        except ValueError:
            pass  # 工作区外路径：保留绝对路径，agent CLI 同样可解析
    return p.as_posix()
