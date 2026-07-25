"""Logo 栅格化脚本：读 assets/logo/logo.svg 母版，一次渲染八尺寸 PNG 落回同目录。

幂等覆盖写。换标流程 = 改母版 → 重跑本脚本（禁止手改单件 PNG 造成尺寸间不一致）。
零新增第三方依赖：QSvgRenderer / QImage 均来自项目已有 PySide6。

用法：
    uv run scripts/render_logo.py
"""
import os
import sys
from pathlib import Path

# 无显示环境（CI / SSH）亦可运行；有显示时 offscreen 同样无副作用
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

#: 正式产出的全套尺寸（freedesktop 常用规格，各 DE 自取最近尺寸）
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGO_DIR = PROJECT_ROOT / "assets" / "logo"
MASTER_SVG = LOGO_DIR / "logo.svg"


def main() -> int:
    if not MASTER_SVG.is_file():
        print(f"[render_logo] 错误：母版缺失 {MASTER_SVG}", file=sys.stderr)
        return 1

    app = QGuiApplication(sys.argv)  # QPainter 需要应用实例（offscreen 不开窗）
    renderer = QSvgRenderer(str(MASTER_SVG))
    if not renderer.isValid():
        print(f"[render_logo] 错误：SVG 解析失败 {MASTER_SVG}", file=sys.stderr)
        return 1

    for size in SIZES:
        image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        out = LOGO_DIR / f"logo_{size}.png"
        if not image.save(str(out)):
            print(f"[render_logo] 错误：写出失败 {out}", file=sys.stderr)
            return 1
        print(f"[render_logo] {out.relative_to(PROJECT_ROOT)}")

    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
