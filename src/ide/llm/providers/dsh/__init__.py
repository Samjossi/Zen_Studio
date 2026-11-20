"""DeepSeek Harness（DSH）后台厂商包：公开入口只服务 DSH。"""
from ide.llm.providers.dsh.provider import DshAcpLLM, dsh_available
from ide.llm.providers.dsh.spec import list_dsh_display_names, list_dsh_models

__all__ = [
    "DshAcpLLM",
    "dsh_available",
    "list_dsh_models",
    "list_dsh_display_names",
]
