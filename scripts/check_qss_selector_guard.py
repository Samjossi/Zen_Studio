"""控件级 qss 选择器静态守卫（0811 左栏右键菜单透明修复 T5）。

病根：文本类控件 setStyleSheet 挂**无选择器**的 background 声明时，
控件级样式表沿父子链级联——Qt 以控件为父即时创建的标准右键菜单
（QMenu）会被灌入 background: transparent，覆盖应用级主题底色致
菜单全透明（cards.py BodyText/BodyHtml 实证根因）。

本守卫扫描 gui/panels/chat/ 下全部 setStyleSheet 调用，拦截
「无选择器（无 {} 规则块）且含 background 声明」的模式；命中即非零
退出。selection-background-color 等连字符复合属性不算命中。

运行（项目根）：uv run scripts/check_qss_selector_guard.py
"""
import ast
import re
import sys
from pathlib import Path

SCAN_DIR = Path("gui/panels/chat")

#: 裸 background 声明（负向回顾排除 selection-background-color 等
#: 连字符复合属性）
_BARE_BACKGROUND_RE = re.compile(r"(?<![-a-zA-Z])background(-color)?\s*:")


def _constant_parts(node: ast.AST) -> list[str]:
    """提取表达式中全部字符串常量片段（f-string 常量段并入）。"""
    parts: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            parts.append(child.value)
    return parts


def main() -> int:
    violations: list[str] = []
    for path in sorted(SCAN_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setStyleSheet"
                    and node.args):
                continue
            text = "".join(_constant_parts(node.args[0]))
            if "{" not in text and _BARE_BACKGROUND_RE.search(text):
                violations.append(f"{path}:{node.lineno}")
    if violations:
        print("[FAIL] 无选择器 background 声明（会级联进右键菜单）：")
        for v in violations:
            print(f"  {v}")
        return 1
    print("[OK] gui/panels/chat/ 无无选择器 background 声明")
    return 0


if __name__ == "__main__":
    sys.exit(main())
