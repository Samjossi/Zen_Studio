"""ACP 连接诊断：凭证脱敏、有界 stderr ring 与统一诊断日志。

连接层只持有 ring 实例；本模块不保存全局 ring 或跨连接状态，因此同进程
多个连接（多标签/多后台）的 stderr 尾部互不串扰。脱敏在入 ring 前完成，
异常路径读到的尾部不含完整凭证。
"""
import logging
import re
import threading
from collections import deque

logger = logging.getLogger(__name__)

#: stderr 尾部上限：行数与字节数双限，超限从最旧行开始丢弃
STDERR_MAX_LINES = 40
STDERR_MAX_BYTES = 8 * 1024
#: 单行上限：超长行截断并追加标记，防止一行撑爆整个 ring
STDERR_LINE_MAX = 1000

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password)"
    r"\b[\"']?\s*[:=]\s*[\"']?([^\s,;\"'}]+)")
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def redact_diagnostic(text: str) -> str:
    """脱敏单行诊断：认证方案、键值型凭证与 JWT 原样规则替换。"""
    text = _AUTH_SCHEME_RE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = _SENSITIVE_VALUE_RE.sub(lambda match: match.group(0).replace(
        match.group(2), "<redacted>"), text)
    return _JWT_RE.sub("<redacted>", text)


class StderrRing:
    """有界诊断尾部；先入缓冲前脱敏，避免异常路径再泄漏完整凭证。"""

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._bytes = 0
        self._truncated = False
        self._lock = threading.Lock()

    @staticmethod
    def _size(line: str) -> int:
        return len(line.encode("utf-8")) + 1

    def append(self, line: str) -> None:
        line = redact_diagnostic(line.rstrip("\r\n"))
        if len(line) > STDERR_LINE_MAX:
            line = line[:STDERR_LINE_MAX] + "…[单行截断]"
            self._truncated = True
        size = self._size(line)
        with self._lock:
            if size > STDERR_MAX_BYTES:
                self._lines.clear()
                self._bytes = 0
                self._truncated = True
                return
            self._lines.append(line)
            self._bytes += size
            while (len(self._lines) > STDERR_MAX_LINES
                   or self._bytes > STDERR_MAX_BYTES):
                removed = self._lines.popleft()
                self._bytes -= self._size(removed)
                self._truncated = True

    def tail(self) -> str:
        with self._lock:
            text = "\n".join(self._lines)
            truncated = self._truncated
        if not text:
            return ""
        return ("[stderr 已截断]\n" if truncated else "") + text


def log_connection_diagnostics(
    *,
    agent_name: str,
    event: str,
    generation: int,
    pid: int | None,
    method: str | None,
    request_id: int | str | None,
    reason: str | None,
    exit_code: int | None,
    stderr_tail: str,
) -> None:
    """输出一条结构化连接诊断；有 stderr 尾部时追加脱敏后的有界摘要。"""
    logger.warning(
        "[%s] 诊断 event=%s generation=%s pid=%s method=%s requestId=%s "
        "reason=%s exitCode=%s",
        agent_name, event, generation, pid, method,
        request_id, reason, exit_code)
    if stderr_tail:
        logger.warning(
            "[%s] stderr 尾部 generation=%s（有界脱敏）\n%s",
            agent_name, generation, stderr_tail)
