"""子进程环境净化：剥离 IDE 私有库路径，防私有环境变量泄漏。

PyInstaller 打包态会污染环境（onedir/AppImage 同构）：
- bootloader 启动时前插 _internal 路径到 LD_LIBRARY_PATH（让主程序找到
  打包私有库），对 IDE 自身必要；但原样继承给集成终端 / ACP agent / 外部
  应用即污染用户工作环境——LD_LIBRARY_PATH 优先级高于应用自带库路径，
  自带同类动态库（Qt/OpenSSL/ICU 等）的用户程序会抢到 IDE 版本而崩溃
  （见 2026-0811-0841 客户报告，Qt_6.11 not found）；
- PySide6 运行时钩子（pyi_rth_pyside6）写入 QT_PLUGIN_PATH /
  QML2_IMPORT_PATH，指向解包根（AppImage 挂载点）下的 plugins/qml 目录，
  同样原样泄漏给子进程——子进程里跑 PyInstaller 时 Qt 插件依赖被解析到
  挂载点，钩子判定「解析出界」而静默剔除全部插件（2026-08-11 实证，
  产物缺 libqxcb.so 直到冒烟验证才暴雷）。

采用启动时一次性净化（文档/修改记录/2026-0811-0909 计划，2026-08-11）：
main.py 启动早期对 os.environ 本身调用 sanitize_environ()，此后一切
子进程派生点（终端/ACP/Typora/新窗）零改动自然继承干净环境，跨窗
挂载点累积亦自动根治。安全性依据：
- glibc 动态链接器仅在进程启动时读一次 LD_LIBRARY_PATH 并缓存，运行期
  删改不影响 IDE 自身的 dlopen（Qt 插件懒加载不受波及）；
- QT_PLUGIN_PATH / QML2_IMPORT_PATH 由 Qt 在 QApplication 构造时才读取，
  main() 中净化先于 QApplication 构造；净化后插件发现走 PySide6 wheel 的
  QLibraryInfo/qt.conf 机制（与开发态一致，开发态本无 QT_PLUGIN_PATH 而
  插件加载正常），运行时钩子写入该变量仅为兼容非 wheel 布局的自编译
  PySide6（见 pyi_rth_pyside6.py 注释）。
"""
import os
from pathlib import Path

from core.paths import PROJECT_ROOT

#: AppImage FUSE 运行时挂载点签名（/tmp/.mount_XXXXXX/...）：覆盖
#: _spawn_window 跨窗继承来的历史挂载点（路径与本窗 PROJECT_ROOT 不同）
_APPIMAGE_MOUNT_MARK = "/.mount_"

#: 需净化的「路径列表型」环境变量（冒号分隔，逐条过滤 IDE 私有条目）
_TARGET_VARS = ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH")


def _is_private_lib_dir(entry: str) -> bool:
    """条目是否 IDE 私有库路径（双判据，满足任一即剔除）。

    - 位于解包根之下：frozen 态 PROJECT_ROOT 即 sys._MEIPASS（_internal/），
      覆盖 bootloader 前插的本窗条目；开发态 PROJECT_ROOT 为项目目录，
      正常不会出现在 LD_LIBRARY_PATH，判据天然幂等、行为零变化；
    - 含 AppImage 挂载点签名：覆盖跨窗继承的历史挂载点。
    """
    if not entry:
        return False  # 空条目语义为「当前目录」，非本模块污染面，原样保留
    if _APPIMAGE_MOUNT_MARK in entry:
        return True
    try:
        Path(entry).resolve().relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def sanitize_environ(env: dict[str, str] | None = None) -> None:
    """原地净化环境（缺省 os.environ）：目标变量剔除 IDE 私有条目。

    逐变量按条目过滤：用户自设条目（CUDA/MATLAB 等）原样保留；过滤后为空
    则整键删除——空串与未设置语义不同，空串等价于向搜索路径注入「当前
    目录」，必须避免。其余环境变量原样透传。
    """
    if env is None:
        env = os.environ
    for var in _TARGET_VARS:
        raw = env.get(var)
        if raw is None:
            continue
        kept = [e for e in raw.split(":") if not _is_private_lib_dir(e)]
        if kept:
            env[var] = ":".join(kept)
        else:
            del env[var]
