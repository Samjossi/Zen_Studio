"""Zen Studio 入口文件。

支持命令行自动截图（供界面走查）：
    uv run main.py --auto-screenshot --screenshot-interval 1
    uv run main.py --auto-screenshot --screenshot-on-start --screenshot-interval 5
"""
import argparse
import sys
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.paths import PROJECT_ROOT
from gui import MainWindow
from gui.theme import apply_theme
from llm import build_default_registry

SCREENSHOT_DIR = PROJECT_ROOT / ".tmp"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zen Studio")
    parser.add_argument("--auto-screenshot", action="store_true", help="启用自动截图")
    parser.add_argument("--screenshot-interval", type=int, default=1, help="截图间隔（秒），默认 1")
    parser.add_argument("--screenshot-on-start", action="store_true", help="启动时立即截一张图")
    return parser.parse_args(argv)


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
    app = QApplication(sys.argv)
    apply_theme(app)
    # LLM 注册表显式装配（探测 + 实例化副作用集中于此），注入主窗口
    window = MainWindow(llm_registry=build_default_registry())
    window.show()

    if args.auto_screenshot:
        setup_screenshot(window, args.screenshot_interval, args.screenshot_on_start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
