"""ACP 审批队列：多标签并发请求串行弹窗（防多模态框互相阻塞）。

多标签改造（2026-07-22，work plans/2026-0722-0756 计划 P3 任务 15）：
每标签独立 provider 实例后，多个标签的 agent 可能同时发起
session/request_permission；模态框并发会互相阻塞（后弹者冻结前者
所在窗口的事件流）。本模块把全窗口的审批请求排队，GUI 线程逐个弹框。
模块级单例 PERMISSION_QUEUE：一窗口一队列（多开为独立进程，天然隔离）。
"""
import threading
from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from gui.panels.chat.permission_dialog import PermissionDialog
from llm import PermissionParams

#: 审批等待超时（秒）；超时按拒绝兜底，防 agent 永久阻塞
PERMISSION_TIMEOUT_S = 180

#: 队列条目：[params, parent, choice, done, is_stale, is_claimed, danger_reason]
_Entry = list


class PermissionQueue:
    """session/request_permission 串行化：reader 线程排队，GUI 线程逐个弹框。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[_Entry] = deque()
        self._is_showing = False

    def ask(
        self,
        params: PermissionParams,
        parent: QWidget,
        danger_reason: str | None = None,
    ) -> str | None:
        """agent reader 线程调用：排队等弹窗，返回 optionId（None = 拒绝兜底）。

        :param danger_reason: 危险命令黑名单命中原因（方案 F）；非 None 时
            对话框加警示标题与原因行
        超时条目仅当未被 pump 认领时才标记作废（is_stale），消除"已按拒绝
        兜底、僵尸弹框仍弹出占队"的 TOCTOU 窗口；已认领（弹窗中）则结果
        自然丢弃。
        """
        entry: _Entry = [params, parent, None, threading.Event(), False, False, danger_reason]
        with self._lock:
            self._pending.append(entry)
        # QTimer.singleShot(receiver, callable)：callable 在 receiver 所在
        # （GUI）线程执行；receiver 销毁则不发（标签关闭的迟到请求自然丢弃）
        QTimer.singleShot(0, parent, self._pump)
        if entry[3].wait(timeout=PERMISSION_TIMEOUT_S):
            return entry[2]
        with self._lock:
            if not entry[5]:
                entry[4] = True
        return None

    def _pump(self) -> None:
        """GUI 线程消费循环：逐个弹框直到队列空；正在弹时重入直接返回。"""
        with self._lock:
            if self._is_showing:
                return
            self._is_showing = True
        try:
            while (entry := self._next_entry()) is not None:
                self._ask_one(entry)
        finally:
            with self._lock:
                self._is_showing = False

    def _next_entry(self) -> _Entry | None:
        """取下一个有效条目（丢弃已超时作废项）并在锁内认领；队列空返回 None。"""
        with self._lock:
            while self._pending and self._pending[0][4]:
                self._pending.popleft()
            if not self._pending:
                return None
            entry = self._pending.popleft()
            entry[5] = True  # 认领：此后超时不再置 stale，结果随弹窗丢弃
            return entry

    @staticmethod
    def _ask_one(entry: _Entry) -> None:
        """单条弹框：父对象失效/弹框异常均不阻断后续 drain，done 必置位。"""
        try:
            if not isValid(entry[1]):  # parent 标签排队期间被关闭销毁
                return
            dialog = PermissionDialog(entry[0], entry[1], danger_reason=entry[6])
            dialog.exec()
            entry[2] = dialog.selected_option_id()
        except RuntimeError:
            pass  # isValid 后仍销毁的竞态残留/构造失败：按拒绝兜底（choice 留 None）
        finally:
            entry[3].set()


#: 全窗口共用单例（ChatPanel._ask_permission 统一入口）
PERMISSION_QUEUE = PermissionQueue()
