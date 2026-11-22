"""Kimi 后台注册声明：显示名、可用性、目录派生、能力与设置键。

本模块只做装配：目录载荷经 `llm.cache` 的原始载荷缓存拉取一次，模型别名、
强度档位与显示名三路从同一载荷派生，防多倍 `provider list --json` 子进程。
可用性探测与目录子进程仍是惰性可调用，import 本模块不执行 CLI。

依赖方向：本模块 → `llm.spec`/`llm.cache`/`llm.base` 与本厂商模块；
不得 import `ide.llm.registry`、GUI 或其他厂商。
"""
from typing import cast

from ide.llm.cache import cached_raw
from ide.llm.providers.kimi.catalog import (
    display_names_from_catalog,
    efforts_from_catalog,
    kimi_kap_available,
    load_kimi_provider_catalog,
    models_from_catalog,
)
from ide.llm.providers.kimi.kap.provider import KimiKapLLM
from ide.llm.spec import BackendSpec

#: 共享目录载荷（注册段 list_models/list_efforts/list_display_names 三路同源）
_cached_catalog = cached_raw("kimi-kap", load_kimi_provider_catalog)


def _catalog() -> dict:
    return cast(dict, _cached_catalog())


#: Kimi 唯一接口：服务发现与进程生命周期归 kap 子包，可用性要求 CLI >= 2.7.0。
SPEC = BackendSpec(
    name="kimi-kap",
    label="Kimi KAP",
    vendor="kimi",
    vendor_label="Kimi",
    available=kimi_kap_available,
    list_models=lambda: models_from_catalog(_catalog()),
    factory=KimiKapLLM,
    supports_images=True,
    # 接口级兜底值：模型级目录整体不可得时回退（k3-256k 值域 low/high/max）
    efforts=("low", "high", "max"),
    default_effort="high",
    list_efforts=lambda: efforts_from_catalog(_catalog()),
    list_display_names=lambda: display_names_from_catalog(_catalog()),
)
