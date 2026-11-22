"""Kimi KAP 链路：`kimi web` 本地 REST + WebSocket 的 Kimi 私有接入层。

与 `kimi acp` 并列，不替换 ACP；公共 ACP 传输层不感知本包。只导出
provider 与错误类型，事件映射/传输细节留在包内，避免 GUI 依赖私有结构。
"""
from ide.llm.providers.kimi.kap.provider import KimiKapLLM
from ide.llm.providers.kimi.kap.transport import KapError

__all__ = ["KimiKapLLM", "KapError"]
