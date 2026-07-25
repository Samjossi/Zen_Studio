"""项目级路径常量：全库唯一推导点，其余模块一律 import。

此前五处独立推导（main / main_window / terminal.session / kimi_cli / kimi_acp），
`parents[N]` 层数不一，文件移动即静默漂移；收口后移动本文件只需改一行。

PyInstaller frozen 兼容（2026-07-25，见 work plans/2026-0725-0859 计划 T4）：
打包态（onedir）内嵌解释器的 __file__ 指向解包目录，parents 推导原地失效；
检测到 sys._MEIPASS 即改指解包根（onedir 下即 exe 同级的 _internal/），
assets/ 经 spec datas 原样收编落位于此。
⚠️ 本文件只保证「只读资源」定位正确；config/ 等用户数据写盘路径
另立打包配置专题处理，不在此推导。
"""
import sys
from pathlib import Path

#: 项目根（开发态：本文件位于 core/，上一级即项目根）
#: 打包态（PyInstaller frozen）：解包根 sys._MEIPASS
PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
)

#: 资产根（assets/：字体 + Logo 等只读资源，spec datas 整目录收编）
ASSETS_DIR = PROJECT_ROOT / "assets"

#: Logo 专目录（assets/logo/：母版 logo.svg + 八尺寸 PNG 成套件）
LOGO_DIR = ASSETS_DIR / "logo"
