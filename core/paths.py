"""项目级路径常量：全库唯一推导点，其余模块一律 import。

此前五处独立推导（main / main_window / terminal.session / kimi_cli / kimi_acp），
`parents[N]` 层数不一，文件移动即静默漂移；收口后移动本文件只需改一行。

PyInstaller frozen 兼容（2026-07-25，见 work plans/2026-0725-0859 计划 T4）：
打包态（onedir）内嵌解释器的 __file__ 指向解包目录，parents 推导原地失效；
检测到 sys._MEIPASS 即改指解包根（onedir 下即 exe 同级的 _internal/），
assets/ 经 spec datas 原样收编落位于此。

用户数据写盘路径（2026-07-25，work plans/2026-0725-1053 计划 T7）：
开发态维持项目内 config/；打包态（frozen/AppImage）指向 XDG 配置目录
${XDG_CONFIG_HOME:-~/.config}/zen-studio/——AppImage 为只读 squashfs，
写解包目录必然失败，用户数据必须写用户目录。
"""
import os
import sys
from pathlib import Path

#: 项目根（开发态：本文件位于 core/，上一级即项目根）
#: 打包态（PyInstaller frozen）：解包根 sys._MEIPASS
PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
)

#: 资产根（assets/：字体 + Logo + 主题模板等只读资源，spec datas 按子目录收编）
ASSETS_DIR = PROJECT_ROOT / "assets"

#: Logo 专目录（assets/logo/：母版 logo.svg + 八尺寸 PNG 成套件）
LOGO_DIR = ASSETS_DIR / "logo"

#: 主题模板专目录（assets/themes/：base.qss，gui/theme.py 消费的只读资源）
THEMES_ASSETS_DIR = ASSETS_DIR / "themes"

#: 用户数据根（可写：settings/recent_projects/window_state 等）
#: 开发态：项目内 config/；打包态：${XDG_CONFIG_HOME:-~/.config}/zen-studio/
USER_CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    / "zen-studio"
    if getattr(sys, "_MEIPASS", None)
    else PROJECT_ROOT / "config"
)
