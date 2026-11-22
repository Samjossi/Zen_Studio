"""Kimi 后台厂商包：公开入口只服务 Kimi。

轮次链路只走官方 ACP（响应即终态），不读私有落盘、不做终态确认；
provider 运行时、二进制/模型目录与 ACP 帧投影均居本目录，不得跨厂商再导出。
"""
from ide.llm.providers.kimi.catalog import (
    display_names_from_catalog,
    efforts_from_catalog,
    kimi_available,
    list_kimi_models,
    load_kimi_provider_catalog,
    models_from_catalog,
)
from ide.llm.providers.kimi.provider import KimiAcpLLM

__all__ = [
    "KimiAcpLLM",
    "kimi_available",
    "list_kimi_models",
    "load_kimi_provider_catalog",
    "models_from_catalog",
    "efforts_from_catalog",
    "display_names_from_catalog",
]
