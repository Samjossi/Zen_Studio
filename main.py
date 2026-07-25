"""Zen Studio 入口文件。

支持命令行自动截图（供界面走查）：
    uv run main.py --auto-screenshot --screenshot-interval 1
    uv run main.py --auto-screenshot --screenshot-on-start --screenshot-interval 5

多开工作区（一进程绑定一工作区根，见 work plans/2026-0722-0756 计划）：
    uv run main.py [folder]
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.paths import LOGO_DIR, PROJECT_ROOT
from gui import MainWindow
from gui.theme import apply_theme

SCREENSHOT_DIR = PROJECT_ROOT / ".tmp"

#: 窗口图标注册尺寸（assets/logo/ 成套件取 16–256；512 留给 .desktop 与高分屏）
LOGO_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_app_icon() -> QIcon:
    """多尺寸注册窗口图标：Qt 按场景（标题栏/任务栏/Alt-Tab）自取最近尺寸，
    杜绝单图缩放糊边。全套缺失仅告警不阻断（与字体注册失败策略一致）。
    """
    icon = QIcon()
    for size in LOGO_ICON_SIZES:
        path = LOGO_DIR / f"logo_{size}.png"
        if path.is_file():
            icon.addFile(str(path))
    if icon.isNull():
        print(f"[main] 警告：Logo 图标缺失（{LOGO_DIR}），窗口将用系统默认图标")
    return icon


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zen Studio")
    parser.add_argument("folder", nargs="?", default=None,
                        help="工作区根目录（缺省回退项目根）")
    parser.add_argument("--auto-screenshot", action="store_true", help="启用自动截图")
    parser.add_argument("--screenshot-interval", type=int, default=1, help="截图间隔（秒），默认 1")
    parser.add_argument("--screenshot-on-start", action="store_true", help="启动时立即截一张图")
    return parser.parse_args(argv)


def resolve_workspace_root(folder: str | None) -> str:
    """启动参数 → 工作区根：缺省/无效静默回退项目根（文件菜单多开路径已校验）。"""
    if folder:
        root = Path(folder).expanduser().resolve()
        if root.is_dir():
            return str(root)
        print(f"[main] 工作区目录无效，回退项目根：{folder}", file=sys.stderr)
    return str(PROJECT_ROOT)


def setup_screenshot(window: MainWindow, interval: int, on_start: bool) -> QTimer:
    """配置自动截图计时器，返回计时器（调用方需保持引用）。"""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    screenshot_count = [0]  # 闭包共享计数（nonlocal 需嵌套作用域，列表容器更直白）

    def grab_screenshot() -> None:
        screenshot_count[0] += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"screenshot_{stamp}_{screenshot_count[0]}.png"
        window.grab().save(str(path))
        print(f"[screenshot] {path}")

    timer = QTimer(window)
    timer.timeout.connect(grab_screenshot)
    timer.start(interval * 1000)

    if on_start:
        # 延迟 500ms 待窗口完成首帧渲染
        QTimer.singleShot(500, grab_screenshot)

    return timer


def main() -> None:
    args = parse_args(sys.argv[1:])
    workspace_root = resolve_workspace_root(args.folder)
    app = QApplication(sys.argv)
    apply_theme(app)
    # 窗口图标（标题栏左侧 + X11 任务栏）；desktopFileName 供 Wayland
    # 与 .desktop 的 StartupWMClass 关联（同名 zen-studio，见 T10）
    app.setWindowIcon(build_app_icon())
    app.setDesktopFileName("zen-studio")
    window = MainWindow(workspace_root=workspace_root)
    window.show()

    if args.auto_screenshot:
        setup_screenshot(window, args.screenshot_interval, args.screenshot_on_start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
