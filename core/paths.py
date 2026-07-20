"""项目级路径常量：全库唯一推导点，其余模块一律 import。

此前五处独立推导（main / main_window / terminal.session / kimi_cli / kimi_acp），
`parents[N]` 层数不一，文件移动即静默漂移；收口后移动本文件只需改一行。
"""
from pathlib import Path

#: 项目根（本文件位于 core/，上一级即项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
