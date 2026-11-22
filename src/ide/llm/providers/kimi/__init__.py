"""Kimi 后台厂商包：公开入口只服务 Kimi。

唯一轮次链路为 KAP（`kimi web` 本地 REST/WS），不读私有落盘判断终态；
provider 运行时、二进制/模型目录与事件投影均居本目录，不得跨厂商再导出。
"""
from ide.llm.providers.kimi.catalog import (
    display_names_from_catalog,
    efforts_from_catalog,
    kimi_kap_available,
    list_kimi_models,
    load_kimi_provider_catalog,
    models_from_catalog,
)
from ide.llm.providers.kimi.kap.provider import KimiKapLLM

__all__ = [
    "KimiKapLLM",
    "kimi_kap_available",
    "list_kimi_models",
    "load_kimi_provider_catalog",
    "models_from_catalog",
    "efforts_from_catalog",
    "display_names_from_catalog",
]
