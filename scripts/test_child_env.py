"""core/child_env.py 环境净化单元测试。

覆盖 文档/修改记录/2026-0811-0909 计划 T3 验证清单：
    uv run python scripts/test_child_env.py

不触真实子进程：直接对注入的假 env 字典调用 sanitize_environ() 断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.child_env import _is_private_lib_dir, sanitize_environ
from core.paths import PROJECT_ROOT


def check(name: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败：{name}")


MOUNT_A = "/tmp/.mount_zen_stekLeAF/usr/bin/_internal"
MOUNT_B = "/tmp/.mount_zen_stLFnhJj/usr/bin/_internal"

# 1. 报告实景：多条 AppImage 挂载点 + 用户合法条目 → 仅剩用户条目
env = {"LD_LIBRARY_PATH": f"{MOUNT_A}:{MOUNT_B}:/opt/cuda/lib64", "PATH": "/usr/bin"}
sanitize_environ(env)
check("挂载点条目剔除、用户条目保留", env["LD_LIBRARY_PATH"] == "/opt/cuda/lib64")
check("其余变量原样透传", env["PATH"] == "/usr/bin")

# 2. 全为污染条目 → 整键删除（不留空串）
env = {"LD_LIBRARY_PATH": f"{MOUNT_A}:{MOUNT_B}"}
sanitize_environ(env)
check("全污染时整键删除", "LD_LIBRARY_PATH" not in env)

# 3. 无该键 → 不新增
env = {"PATH": "/usr/bin"}
sanitize_environ(env)
check("无 LD_LIBRARY_PATH 不新增", "LD_LIBRARY_PATH" not in env)

# 4. 用户自设条目（非挂载点、非解包根）→ 保留
check("CUDA 条目判保留", not _is_private_lib_dir("/opt/cuda/lib64"))
check("MATLAB 条目判保留", not _is_private_lib_dir("/usr/local/MATLAB/R2024a/lib"))

# 5. 解包根（frozen 态 PROJECT_ROOT 即 _MEIPASS）子路径 → 剔除
check(
    "解包根下条目判剔除",
    _is_private_lib_dir(str(PROJECT_ROOT / "usr" / "bin" / "_internal")),
)
check("解包根本身判剔除", _is_private_lib_dir(str(PROJECT_ROOT)))

# 6. 空条目（语义「当前目录」）→ 保留，不参与剔除
env = {"LD_LIBRARY_PATH": f"{MOUNT_A}::/opt/cuda/lib64"}
sanitize_environ(env)
check("空条目原样保留", env["LD_LIBRARY_PATH"] == ":/opt/cuda/lib64")

# 7. 挂载点签名判据：含 /.mount_ 即剔除（跨窗历史挂载点，与本窗解包根无关）
check("任意挂载点签名判剔除", _is_private_lib_dir("/tmp/.mount_abCdEf12/usr/bin/_internal"))

# 8. QT_PLUGIN_PATH / QML2_IMPORT_PATH：运行时钩子写入解包根下路径，同样过滤
env = {
    "QT_PLUGIN_PATH": "/tmp/.mount_zen_stcLjgGn/usr/bin/_internal/PySide6/Qt/plugins",
    "QML2_IMPORT_PATH": f"/tmp/.mount_zen_stcLjgGn/usr/bin/_internal/PySide6/Qt/qml:{MOUNT_A}",
}
sanitize_environ(env)
check("QT_PLUGIN_PATH 全污染整键删除", "QT_PLUGIN_PATH" not in env)
check("QML2_IMPORT_PATH 全污染整键删除", "QML2_IMPORT_PATH" not in env)

# 9. QT_PLUGIN_PATH 混入用户自设条目 → 仅剔除挂载点，用户条目保留
env = {
    "QT_PLUGIN_PATH": f"{MOUNT_A}:/opt/my-qt-plugins",
    "PATH": "/usr/bin",
}
sanitize_environ(env)
check("QT_PLUGIN_PATH 用户条目保留", env["QT_PLUGIN_PATH"] == "/opt/my-qt-plugins")
check("QT_PLUGIN_PATH 净化不波及其余变量", env["PATH"] == "/usr/bin")

print("全部断言通过")
