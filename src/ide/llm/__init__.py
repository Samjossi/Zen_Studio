"""LLM 调用层公共出口：稳定契约、ACP 公共类型与后端注册表 API。

具体 provider 类不由本模块再导出：生产代码需要实例时经 `registry` 的
`BackendSpec.factory` 装配，只有 `registry` 可以从厂商包导入具体实现。
厂商包需要具体类时直接 import 本厂商模块，不回到公共出口取型。

依赖方向（无环）：本模块 → registry → providers → llm.base；
本模块 → providers.acp（仅稳定协议类型）。本模块不导入 GUI。
import 无副作用：CLI 探测与 provider 实例化不发生在模块级。
"""
from ide.llm.base import (
    Chunk,
    LanguageModel,
    Message,
    TurnConsumption,
    TurnSpeed,
    UsageStats,
)
from ide.llm.providers.acp import (
    PermissionHandler,
    PermissionParams,
    PermissionRequest,
    TurnCancelled,
)
from ide.llm.registry import (
    BACKEND_LABELS,
    DEFAULT_BACKEND,
    REGISTRY,
    VENDOR_LABELS,
    BackendSpec,
    refresh_models,
    resolve_efforts,
    spec_of,
    vendor_groups,
    vendor_of,
)

__all__ = [
    # 稳定契约
    "Chunk",
    "LanguageModel",
    "Message",
    "TurnSpeed",
    "TurnConsumption",
    "UsageStats",
    # 注册表 API（组合根与查询；具体实现由 REGISTRY 工厂装配）
    "DEFAULT_BACKEND",
    "BACKEND_LABELS",
    "REGISTRY",
    "BackendSpec",
    "spec_of",
    "vendor_of",
    "vendor_groups",
    "refresh_models",
    "resolve_efforts",
    "VENDOR_LABELS",
    # ACP 协议层公共类型（审批回环共用；具体 provider 类不在公共出口）
    "PermissionHandler",
    "PermissionParams",
    "PermissionRequest",
    "TurnCancelled",
]
