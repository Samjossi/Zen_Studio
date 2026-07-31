"""kimi 二进制公共探测与模型目录枚举。

供 kimi-acp 后台与注册表共用：`_find_bin` 解析二进制路径（PATH →
$KIMI_CODE_HOME/bin → ~/.kimi-code/bin），`kimi_available` 判断可用性，
`list_kimi_models` 经 `kimi provider list --json` 动态枚举模型别名。

注：CLI 传输层（`kimi -p` 一次性子进程，原 kimi_cli.py）已于
2026-07-31 精简移除（文档/修改记录/2026-0731-0036），本模块为其残留共享设施，
现仅服务 ACP 传输层。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

KIMI_BIN = "kimi"


def _find_bin() -> str | None:
    """解析 kimi 二进制路径：PATH → $KIMI_CODE_HOME/bin/kimi → ~/.kimi-code/bin/kimi。

    桌面启动 Zen Studio 时 PATH 可能不含 ~/.kimi-code/bin，fallback 避免误判未安装。
    """
    if path := shutil.which(KIMI_BIN):
        return path
    candidates: list[Path] = []
    if home := os.environ.get("KIMI_CODE_HOME"):
        candidates.append(Path(home) / "bin" / KIMI_BIN)
    candidates.append(Path.home() / ".kimi-code" / "bin" / KIMI_BIN)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def kimi_available() -> bool:
    """检测 kimi CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_kimi_models() -> list[str]:
    """经 `kimi provider list --json` 解析可用模型别名；失败返回空列表。"""
    bin_path = _find_bin()
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, "provider", "list", "--json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        data = json.loads(proc.stdout)
        return sorted(data.get("models", {}).keys())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
