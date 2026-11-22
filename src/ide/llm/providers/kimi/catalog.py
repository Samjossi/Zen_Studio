"""kimi 二进制公共探测与模型目录枚举。

`_find_bin` 解析二进制路径（PATH → $KIMI_CODE_HOME/bin → ~/.kimi-code/bin），
`kimi_kap_available` 校验 KAP 版本门槛；`kimi provider list --json` 同源派生
模型别名、强度档位、显示名与 maxContextSize 上下文窗口。
"""
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

KIMI_BIN = "kimi"

#: KAP 1.0.0 的硬性 CLI 门槛；低于该版本入口禁用，避免菜单可点、
#: 一发就报 `Unknown command: web`。
KAP_MIN_VERSION = (2, 7, 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


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


def kimi_kap_available() -> bool:
    """检测 CLI 是否满足 KAP 1.0.0 的 v2.7.0 门槛。

    `--version` 解析失败时 fail-open（返回 True）：版本输出格式变化不应
    让一个实际可用的后端被误判禁用；真正的兼容性问题会在首个 chat 的
    `kimi web` 启动错误里暴露。低于门槛则 fail-closed，入口禁用。
    """
    bin_path = _find_bin()
    if not bin_path:
        return False
    try:
        proc = subprocess.run(
            [bin_path, "--version"], capture_output=True, text=True,
            timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return True
    match = _VERSION_RE.search(proc.stdout or proc.stderr or "")
    if match is None:
        logger.warning("kimi --version 无法解析，KAP 可用性按 fail-open 处理")
        return True
    version = tuple(int(part) for part in match.groups())
    return version >= KAP_MIN_VERSION


def load_kimi_provider_catalog() -> dict:
    """调 `kimi provider list --json` 返回原始载荷；失败返回 {}。

    模型别名、强度档位、显示名与上下文窗口均从同一载荷派生；调用方分别
    缓存，避免 GUI 高频路径重复启动目录子进程。
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
        logger.exception("kimi provider list --json 执行失败")
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
    条目可含 `overrides` 子表（用户在 config.toml 的
    `[models."<alias>".overrides]` 固定元数据，managed 刷新不改写）：
    按 CLI 运行时 effective 语义，overrides 优先于顶层字段并入后再读。
    模型条目缺字段（如 kimi-for-coding-highspeed 无 supportEfforts）→
    不产生条目（该模型无强度轴，D1：不做静态兜底）。
    """
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    result: dict[str, tuple[list[str], str | None]] = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        if isinstance(overrides := entry.get("overrides"), dict):
            entry = {**entry, **overrides}
        efforts = entry.get("supportEfforts")
        if not isinstance(efforts, list):
            continue
        efforts = [value for value in efforts if isinstance(value, str)]
        if not efforts:
            continue
        default = entry.get("defaultEffort")
        result[alias] = (efforts, default if isinstance(default, str) else None)
    return result


def display_names_from_catalog(data: dict) -> dict[str, str]:
    """从 provider list 载荷解析模型显示名：别名 → displayName。

    与 efforts_from_catalog 同源同载荷（0455 计划 T1 缓存层合一的第三路
    派生）：条目先并入 `overrides` 子表（CLI effective 语义）再读
    `displayName`；缺字段/非字符串的条目不产生映射——该模型 UI 回退
    别名原文（注册表 list_display_names 空 dict 同款兜底语义）。
    纯 UI 呈现数据，不进协议载荷与持久化（D6 红线 2 不破）。
    """
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    result: dict[str, str] = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        if isinstance(overrides := entry.get("overrides"), dict):
            entry = {**entry, **overrides}
        display_name = entry.get("displayName")
        if isinstance(display_name, str) and display_name:
            result[alias] = display_name
    return result


def context_sizes_from_catalog(data: dict) -> dict[str, int]:
    """从 provider list 载荷解析模型上下文窗口：别名 → maxContextSize。"""
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    result: dict[str, int] = {}
    for alias, entry in models.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            continue
        if isinstance(overrides := entry.get("overrides"), dict):
            entry = {**entry, **overrides}
        size = entry.get("maxContextSize")
        if type(size) is int and size > 0:
            result[alias] = size
    return result


def list_kimi_models() -> list[str]:
    """经 `kimi provider list --json` 解析可用模型别名；失败返回空列表。"""
    return models_from_catalog(load_kimi_provider_catalog())


def list_kimi_efforts() -> dict[str, tuple[list[str], str | None]]:
    """经 `kimi provider list --json` 解析模型级强度档位；失败返回空 dict。"""
    return efforts_from_catalog(load_kimi_provider_catalog())
