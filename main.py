"""Zen Studio 入口文件。

支持命令行自动截图（供界面走查）：
    uv run main.py --auto-screenshot --screenshot-interval 1
    uv run main.py --auto-screenshot --screenshot-on-start --screenshot-interval 5

一窗一根与空白新窗口（work plans/2026-0831-2350 计划）：
    uv run main.py [folder]     # 绑定工作区根；根已被占用则唤活已有窗口后
                                # 以退出码 3（EXIT_ROOT_OCCUPIED）退出
    uv run main.py --blank      # 空白窗口（不绑定目录；与 folder 互斥，blank 优先）

无参启动恒空白窗口（work plans/2026-0905-2347 计划，撤销 2026-0905-2025
的启动恢复特性）：folder 缺省/无效时不读任何历史、不恢复上次项目，
与 --blank 同形态——空白窗口，绝不回退任何默认目录（开发态/打包态
行为一致，2026-0903-0305 计划：旧版回退 Path.home() 会把 home 当工作区）。
打开项目只经两条显式路径：命令行 folder 参数 / 窗内「打开文件夹」。
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.child_env import sanitize_environ
from core.paths import IS_FROZEN, LOGO_DIR, PROJECT_ROOT, USER_CONFIG_DIR
from gui import MainWindow
from gui.root_ownership import EXIT_ROOT_OCCUPIED, acquire_root_ownership
from gui.theme import apply_theme

#: 截图输出目录：开发态项目内 .tmp/；打包态落用户数据根（AppImage 只读
#: 挂载下写解包目录必抛 OSError，见 文档/修改记录/2026-0725-1234 计划 T3）
SCREENSHOT_DIR = (USER_CONFIG_DIR / "screenshots") if IS_FROZEN else (PROJECT_ROOT / ".tmp")

#: 运行日志文件：开发态项目内 config/logs/；打包态落用户数据根
#: （config/logs/ 已入 .gitignore，见 2026-0909-2349 计划 T1）
LOG_FILE = USER_CONFIG_DIR / "logs" / "zen-studio.log"

#: 窗口图标注册尺寸（assets/logo/ 成套件取 16–256；512 留给 .desktop 与高分屏）
LOGO_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def setup_logging() -> None:
    """统一日志初始化（2026-0909-2349 计划 T1）：INFO 级，文件 + stderr 双输出。

    须在 main() 最先调用；各模块一律 `logging.getLogger(__name__)`，
    禁止二次 basicConfig。打包态 stderr 不可见，日志文件是唯一观测面。
    """
    LOG_FILE.parent.mkdir(exist_ok=True, parents=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


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
                        help="工作区根目录（缺省/无效则空白窗口，不恢复历史；与 --blank 互斥，blank 优先）")
    parser.add_argument("--blank", action="store_true",
                        help="空白窗口（不绑定任何目录，对齐 VS Code New Window）")
    parser.add_argument("--auto-screenshot", action="store_true", help="启用自动截图")
    parser.add_argument("--screenshot-interval", type=int, default=1, help="截图间隔（秒），默认 1")
    parser.add_argument("--screenshot-on-start", action="store_true", help="启动时立即截一张图")
    return parser.parse_args(argv)


def resolve_workspace_root(folder: str | None) -> str | None:
    """启动参数 → 工作区根（文件菜单多开路径已校验）。
    folder 缺省/无效返回 None——无参启动恒空白窗口，不读任何历史、
    不恢复上次项目（2026-0905-2347 计划，撤销启动恢复特性）。"""
    if folder:
        root = Path(folder).expanduser().resolve()
        if root.is_dir():
            return str(root)
        print(f"[main] 工作区目录无效，启动空白窗口：{folder}", file=sys.stderr)
    return None


def setup_screenshot(window: MainWindow, interval: int, on_start: bool) -> QTimer:
    """配置自动截图计时器，返回计时器（调用方需保持引用）。"""
    SCREENSHOT_DIR.mkdir(exist_ok=True, parents=True)
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
    setup_logging()
    # 启动时一次性净化 LD_LIBRARY_PATH 的 IDE 私有条目（bootloader 前插的
    # _internal）：此后一切用户子进程（终端/ACP/Typora/新窗）自然继承干净
    # 环境；glibc 启动时已缓存链接搜索路径，运行期改写不影响自身 dlopen
    sanitize_environ(os.environ)
    args = parse_args(sys.argv[1:])
    # QApplication 提前创建（2026-0831-2350 计划 D2）：占用登记的
    # QLocalServer 依赖事件循环投递 newConnection，须在判定前就绪
    app = QApplication(sys.argv)
    apply_theme(app)
    # 窗口图标（标题栏左侧 + X11 任务栏）；desktopFileName 供 Wayland
    # 与 .desktop 的 StartupWMClass 关联（同名 zen-studio，见 T10）
    app.setWindowIcon(build_app_icon())
    app.setDesktopFileName("zen-studio")
    # 一窗一根占用判定收口：所有起窗路径（菜单 spawn / 命令行 / 双击图标）
    # 汇聚于此。⚠️ 行为变化备忘：双击图标/默认启动命中已开根时变为激活
    # 已有窗口（对齐 VS Code，预期）。空窗无根不登记（多空窗可共存）
    if args.blank:
        workspace_root = None
        root_server = None
    else:
        workspace_root = resolve_workspace_root(args.folder)
        if workspace_root is None:
            # 无显式有效 folder（无参/目录无效）：与 --blank 同形态，
            # 空白窗口不做占用登记（2026-0903-0305 计划 D1）
            root_server = None
        else:
            root_server = acquire_root_ownership(workspace_root)
            if root_server is None:
                # 根已被活窗口占用：唤活消息已发出，本进程静默退出
                sys.exit(EXIT_ROOT_OCCUPIED)
    window = MainWindow(workspace_root=workspace_root, root_server=root_server)
    window.show()

    if args.auto_screenshot:
        setup_screenshot(window, args.screenshot_interval, args.screenshot_on_start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
