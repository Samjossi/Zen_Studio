# building/version.py
"""版本中枢：pyproject.toml 为版本唯一来源，构建号取 git 提交计数。

完整版本号四段 major.minor.patch.build。构建脚本经 `get` 捕获完整版本；
spec 运行环境独立，按同规则自算，不 import 本模块。

用法：
    uv run python building/version.py get           # 打印完整版本（如 1.7.0.395）
    uv run python building/version.py bump patch    # 基础版本 +0.0.1 并打印新值
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def base_version() -> str:
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"]["version"]


def build_number() -> str | None:
    """git 提交计数；非 git 环境（如拷贝源码树、浅克隆失真）返回 None。"""
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def full_version() -> str:
    base = base_version()
    build = build_number()
    return f"{base}.{build}" if build else base


def bump(part: str) -> str:
    """改写 pyproject.toml 基础版本；只改文件，提交由人决定。"""
    major, minor, patch = (int(x) for x in base_version().split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"
    text = PYPROJECT.read_text(encoding="utf-8")
    text, count = re.subn(r'(?m)^version = "[^"]+"', f'version = "{new}"', text, count=1)
    if count != 1:
        raise SystemExit("错误：pyproject.toml 中未定位到 version 行")
    PYPROJECT.write_text(text, encoding="utf-8")
    # uv.lock 记录本项目版本（virtual source），不刷新则 uv lock --check 失败
    try:
        subprocess.run(["uv", "lock"], cwd=ROOT, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"警告：uv.lock 刷新失败（{exc}），请手动执行 uv lock", file=sys.stderr)
    return new


def main() -> int:
    args = sys.argv[1:]
    if args == ["get"]:
        print(full_version())
        return 0
    if len(args) == 2 and args[0] == "bump" and args[1] in ("patch", "minor", "major"):
        print(bump(args[1]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
