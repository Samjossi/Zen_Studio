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


def load_kimi_provider_catalog() -> dict:
    """调 `kimi provider list --json` 返回原始载荷；失败返回 {}。

    0455 动态化计划 T1：模型别名与强度档位同源同载荷——注册层缓存本函数
    一次，list_models/list_efforts 均从缓存目录派生，防双倍子进程。
    """
    bin_path = _find_bin()
    if not bin_path:
        return {}
    try:
        proc = subprocess.run(
            [bin_path, "provider", "list", "--json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        data = json.loads(proc.stdout)
        return data if isinstance(data, dict) else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def models_from_catalog(data: dict) -> list[str]:
    """从 provider list 载荷解析可用模型别名（0455 计划：与档位同源）。"""
    models = data.get("models")
    return sorted(models.keys()) if isinstance(models, dict) else []


def efforts_from_catalog(data: dict) -> dict[str, tuple[list[str], str | None]]:
    """从 provider list 载荷解析模型级强度档位：别名 → (档位列表, 默认档)。

    0455 动态化计划 T1/G1：字段实测为 camelCase `supportEfforts` /
    `defaultEffort`（2026-08-06 实测，计划文档所记 snake_case 字段名以
    实测为准留痕修正）；服务端目录下发、CLI 重登录时刷新。
    模型条目缺字段（如 kimi-for-coding 无 supportEfforts）→ 不产生条目
    （该模型无强度轴，D1：不做静态兜底）。
    """
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    result: dict[str, tuple[list[str], str | None]] = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        efforts = entry.get("supportEfforts")
        if not isinstance(efforts, list):
            continue
        efforts = [value for value in efforts if isinstance(value, str)]
        if not efforts:
            continue
        default = entry.get("defaultEffort")
        result[alias] = (efforts, default if isinstance(default, str) else None)
    return result


def list_kimi_models() -> list[str]:
    """经 `kimi provider list --json` 解析可用模型别名；失败返回空列表。"""
    return models_from_catalog(load_kimi_provider_catalog())


def list_kimi_efforts() -> dict[str, tuple[list[str], str | None]]:
    """经 `kimi provider list --json` 解析模型级强度档位；失败返回空 dict。"""
    return efforts_from_catalog(load_kimi_provider_catalog())
