"""Local CLI —— 本地语言模型服务端（ACP 协议接入 + CLI/GUI 双模式）。

阶段 0/1 交付 ACP 协议内核与 Mock 模型；阶段 3 交付 PySide6 GUI 模式（gui/）。
"""

#: 与 pyproject.toml [project].version 保持一致，作为 initialize 的 agentInfo.version
__version__ = "0.1.0"

#: initialize 响应的 agentInfo.name（§1.3，客户端记入诊断日志）；
#: 放在包根是因为 CLI（__main__）与 GUI（gui/service.py）两种模式共用同一身份
AGENT_NAME = "local"
