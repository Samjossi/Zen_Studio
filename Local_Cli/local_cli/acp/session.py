"""会话层：会话状态 dataclass + 会话管理器。

协议依据：
- §1.4/A.2：session/new 的 cwd 硬校验绝对路径，相对路径拒 -32602。
- §1.6：cancel 之后同会话必须可续用，因此取消信号是会话内的 Event，
  轮次消费后清除，会话对象本身不销毁。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class AcpSession:
    """单个 ACP 会话的可变状态（AFCP 3.1：显式数据结构）。"""
    session_id: str
    cwd: str
    model_alias: str
    #: 轮次取消信号：session/cancel 置位，轮次线程在 chunk 间消费并清除
    cancel_event: threading.Event = field(default_factory=threading.Event)


class SessionManager:
    """会话的创建与查找；会话数量少，字典 + 锁即可。"""

    _SESSION_ID_PREFIX = "local-session"

    def __init__(self, default_model_alias: str) -> None:
        self._default_model_alias = default_model_alias
        self._sessions: dict[str, AcpSession] = {}
        self._lock = threading.Lock()

    def create(self, cwd: str) -> AcpSession:
        with self._lock:
            session_id = f"{self._SESSION_ID_PREFIX}-{len(self._sessions) + 1}"
            session = AcpSession(
                session_id=session_id,
                cwd=cwd,
                model_alias=self._default_model_alias,
            )
            self._sessions[session_id] = session
            return session

    def find(self, session_id: str) -> AcpSession | None:
        return self._sessions.get(session_id)
