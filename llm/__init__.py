"""LLM 调用层：统一接口 + provider（本机 agent CLI 后端，代码库零密钥）。

后端注册单点在 llm/registry.py（BackendSpec + REGISTRY，计划
2026-0730-0150 §4-D3）；provider 装配单点在 ChatPanel（D4 懒实例化，
多标签改造后每标签自持实例，标签间完全隔离）；import 无副作用
（AFCP 2.3 依赖显式）：CLI 探测与 provider 实例化不发生在模块级，
由消费方显式构造。

依赖方向（无环）：本模块 → registry → providers → llm.base。
"""
from llm.base import Chunk, LanguageModel, Message
from llm.providers.acp import PermissionHandler, PermissionParams
from llm.providers.kimi_acp import KimiAcpLLM
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models
from llm.providers.reasonix_acp import ReasonixAcpLLM, list_reasonix_models, reasonix_available
from llm.registry import (
    BACKEND_LABELS,
    REGISTRY,
    VENDOR_LABELS,
    BackendSpec,
    spec_of,
    vendor_groups,
    vendor_of,
)

#: 后端注册名（settings 默认值、设置菜单、ModelBar 共用此常量）——
#: 即 REGISTRY 的键；注册表条目以这两字面量为名，无从再行"派生"
BACKEND_KIMI_CLI = "kimi-cli"
BACKEND_KIMI_ACP = "kimi-acp"


__all__ = [
    "Chunk",
    "LanguageModel",
    "Message",
    "BACKEND_KIMI_CLI",
    "BACKEND_KIMI_ACP",
    "BACKEND_LABELS",
    # 注册表（D3；阶段二 UI 三级选择的数据源）
    "REGISTRY",
    "BackendSpec",
    "spec_of",
    "vendor_of",
    "vendor_groups",
    "VENDOR_LABELS",
    # Kimi CLI 专有符号（非 Protocol 成员）
    "KimiCliLLM",
    "KimiAcpLLM",
    "kimi_available",
    "list_kimi_models",
    # Reasonix CLI 专有符号（非 Protocol 成员）
    "ReasonixAcpLLM",
    "reasonix_available",
    "list_reasonix_models",
    # ACP 协议层定型（审批回环共用；现居 llm.providers.acp，此处 re-export）
    "PermissionHandler",
    "PermissionParams",
]
