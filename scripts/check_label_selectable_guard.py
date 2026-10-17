"""可选中 QLabel 裸用静态守卫（2315 QLabel 右键原生英文菜单修复 T8）。

病根：Qt 对开 TextSelectableByMouse 的 QLabel 内建英文原生标准右键
菜单（带主题图标与快捷键显示后缀），QLabel 无 createStandardContextMenu()
无法走 exec_standard_context_menu()——任何绕过 popups.make_label_selectable()
的裸调用都会再生散兵，右键形态与全局只读精简规约不一致。

本守卫扫描 src/gui/ 下全部 TextSelectableByMouse 字面量，命中点不在
白名单（popups.py，make_label_selectable 本体所在）内即违规非零退出。

运行（项目根）：uv run scripts/check_label_selectable_guard.py
"""
import sys
from pathlib import Path

SCAN_DIR = Path("src/gui")
NEEDLE = "TextSelectableByMouse"

#: 唯一合法开口：make_label_selectable() 本体
WHITELIST = {Path("src/gui/popups.py")}


def main() -> int:
    violations: list[str] = []
    for path in sorted(SCAN_DIR.rglob("*.py")):
        if path in WHITELIST:
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if NEEDLE in line:
                violations.append(f"{path}:{lineno}")
    if violations:
        print("[FAIL] TextSelectableByMouse 裸用（QLabel 右键回落英文原生"
              "菜单，请走 popups.make_label_selectable）：")
        for v in violations:
            print(f"  {v}")
        return 1
    print(f"[OK] {SCAN_DIR} 可选中 QLabel 入口体检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
