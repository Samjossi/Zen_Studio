"""ide/core/child_env.py 环境策略单元测试（收编自 scripts/test_child_env.py）。

覆盖私有库路径净化、AI 子进程非交互分页环境副本与真实 PTY Git 自然退出。
"""
from __future__ import annotations

import os
import re
import select
import time

from ptyprocess import PtyProcess

from ide.core.child_env import _is_private_lib_dir, sanitize_environ, with_non_interactive_pagers
from ide.core.paths import PROJECT_ROOT

MOUNT_A = "/tmp/.mount_zen_stekLeAF/usr/bin/_internal"
MOUNT_B = "/tmp/.mount_zen_stLFnhJj/usr/bin/_internal"


def test_non_interactive_pagers_added_and_unrelated_values_kept():
    env = with_non_interactive_pagers({"PATH": "/usr/bin", "CUSTOM": "kept"})
    assert env == {
        "PATH": "/usr/bin",
        "CUSTOM": "kept",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }


def test_non_interactive_pagers_override_without_mutating_inputs():
    source = {"GIT_PAGER": "less", "PAGER": "more", "CUSTOM": "kept"}
    original = dict(source)
    parent_before = dict(os.environ)

    env = with_non_interactive_pagers(source)

    assert env["GIT_PAGER"] == "cat"
    assert env["PAGER"] == "cat"
    assert env["CUSTOM"] == "kept"
    assert source == original
    assert dict(os.environ) == parent_before


def test_git_log_exits_in_short_real_pty_with_non_interactive_env():
    env = with_non_interactive_pagers(dict(os.environ, TERM="xterm-256color"))
    proc = PtyProcess.spawn(
        ["git", "-c", "core.pager=less", "-c", "color.ui=false", "log", "-5", "--oneline"],
        cwd=str(PROJECT_ROOT), env=env, dimensions=(2, 80))
    output = bytearray()
    deadline = time.monotonic() + 5
    try:
        while proc.isalive() and time.monotonic() < deadline:
            readable, _, _ = select.select([proc.fd], [], [], 0.1)
            if readable:
                try:
                    output.extend(proc.read(4096))
                except (EOFError, OSError):
                    pass
        while select.select([proc.fd], [], [], 0)[0]:
            try:
                output.extend(proc.read(4096))
            except (EOFError, OSError):
                break
        assert not proc.isalive(), "普通 git log 不应等待交互翻页"
        assert proc.exitstatus == 0
        assert re.search(rb"[0-9a-f]{7,40} .+", output), "应读到真实提交输出"
    finally:
        if proc.isalive():
            proc.terminate(force=True)


def test_mount_entries_removed_user_kept():
    """报告实景：多条 AppImage 挂载点 + 用户合法条目 → 仅剩用户条目。"""
    env = {"LD_LIBRARY_PATH": f"{MOUNT_A}:{MOUNT_B}:/opt/cuda/lib64", "PATH": "/usr/bin"}
    sanitize_environ(env)
    assert env["LD_LIBRARY_PATH"] == "/opt/cuda/lib64", "挂载点条目剔除、用户条目保留"
    assert env["PATH"] == "/usr/bin", "其余变量原样透传"


def test_all_polluted_key_removed():
    """全为污染条目 → 整键删除（不留空串）。"""
    env = {"LD_LIBRARY_PATH": f"{MOUNT_A}:{MOUNT_B}"}
    sanitize_environ(env)
    assert "LD_LIBRARY_PATH" not in env, "全污染时整键删除"


def test_absent_key_not_added():
    env = {"PATH": "/usr/bin"}
    sanitize_environ(env)
    assert "LD_LIBRARY_PATH" not in env, "无 LD_LIBRARY_PATH 不新增"


def test_user_entries_kept():
    """用户自设条目（非挂载点、非解包根）→ 保留。"""
    assert not _is_private_lib_dir("/opt/cuda/lib64"), "CUDA 条目判保留"
    assert not _is_private_lib_dir("/usr/local/MATLAB/R2024a/lib"), "MATLAB 条目判保留"


def test_project_root_entries_removed():
    """解包根（frozen 态 PROJECT_ROOT 即 _MEIPASS）子路径 → 剔除。"""
    assert _is_private_lib_dir(str(PROJECT_ROOT / "usr" / "bin" / "_internal")), "解包根下条目判剔除"
    assert _is_private_lib_dir(str(PROJECT_ROOT)), "解包根本身判剔除"


def test_empty_entry_kept():
    """空条目（语义「当前目录」）→ 保留，不参与剔除。"""
    env = {"LD_LIBRARY_PATH": f"{MOUNT_A}::/opt/cuda/lib64"}
    sanitize_environ(env)
    assert env["LD_LIBRARY_PATH"] == ":/opt/cuda/lib64", "空条目原样保留"


def test_mount_signature_removed():
    """挂载点签名判据：含 /.mount_ 即剔除（跨窗历史挂载点，与本窗解包根无关）。"""
    assert _is_private_lib_dir("/tmp/.mount_abCdEf12/usr/bin/_internal"), "任意挂载点签名判剔除"


def test_qt_paths_all_polluted_removed():
    """QT_PLUGIN_PATH / QML2_IMPORT_PATH：运行时钩子写入解包根下路径，同样过滤。"""
    env = {
        "QT_PLUGIN_PATH": "/tmp/.mount_zen_stcLjgGn/usr/bin/_internal/PySide6/Qt/plugins",
        "QML2_IMPORT_PATH": f"/tmp/.mount_zen_stcLjgGn/usr/bin/_internal/PySide6/Qt/qml:{MOUNT_A}",
    }
    sanitize_environ(env)
    assert "QT_PLUGIN_PATH" not in env, "QT_PLUGIN_PATH 全污染整键删除"
    assert "QML2_IMPORT_PATH" not in env, "QML2_IMPORT_PATH 全污染整键删除"


def test_qt_plugin_path_mixed():
    """QT_PLUGIN_PATH 混入用户自设条目 → 仅剔除挂载点，用户条目保留。"""
    env = {
        "QT_PLUGIN_PATH": f"{MOUNT_A}:/opt/my-qt-plugins",
        "PATH": "/usr/bin",
    }
    sanitize_environ(env)
    assert env["QT_PLUGIN_PATH"] == "/opt/my-qt-plugins", "QT_PLUGIN_PATH 用户条目保留"
    assert env["PATH"] == "/usr/bin", "QT_PLUGIN_PATH 净化不波及其余变量"
