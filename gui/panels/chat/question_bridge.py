"""AskUserQuestion 卡片内交互桥（0807-0148 计划 T4，交互载体终态）。

仿 PERMISSION_QUEUE 的串行化骨架，但激活的是**卡内按钮**（非模态框）——
GUI 线程绝不能阻塞等用户点击（事件流冻结按钮永远点不动），故采用
「活动条目 + 点击回调推进」模式：reader 线程阻塞等 threading.Event，
GUI 线程经 QTimer.singleShot 定位 tool_call_id 对应的 QuestionCard
激活选项按钮组后立即返回；用户点击 → 回调置结果 → Event.set() 唤醒
reader 并推进队列。同窗口多个 question 请求串行激活（防多卡同时待答
的认知混乱与结果错配）。

降级链：卡片缺失（旧会话重放、双轨旧轨行文本渲染无卡可激活）或卡片
已终态 → QuestionDialog 弹窗兜底 → 关闭/超时按拒绝兜底（None）。
"""
import threading
from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from gui.panels.chat.cards import find_question_card
from gui.panels.chat.permission_dialog import QuestionDialog
from gui.panels.chat.permission_queue import PERMISSION_TIMEOUT_S
from llm import PermissionParams

#: 队列条目：[params, tool_call_id, parent, choice, done, is_stale, is_claimed, card]
_Entry = list


class QuestionBridge:
    """question 类交互请求串行化：reader 线程排队，GUI 线程逐个激活卡内按钮。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[_Entry] = deque()
        self._active: _Entry | None = None

    def ask(
        self,
        params: PermissionParams,
        tool_call_id: str,
        parent: QWidget,
    ) -> str | None:
        """agent reader 线程调用：排队等卡片按钮作答，返回 optionId（None = 拒绝兜底）。

        超时语义同 PERMISSION_QUEUE：未认领条目标记作废（is_stale）；
        已认领（按钮已激活）条目经 _abort 在 GUI 线程撤按钮并推进队列，
        防用户永不点击时队列饿死。
        """
        entry: _Entry = [params, tool_call_id, parent, None,
                         threading.Event(), False, False, None]
        with self._lock:
            self._pending.append(entry)
        # callable 在 parent 所在（GUI）线程执行；标签关闭则不发（迟到请求自然丢弃）
        QTimer.singleShot(0, parent, self._pump)
        if entry[4].wait(timeout=PERMISSION_TIMEOUT_S):
            return entry[3]
        with self._lock:
            if not entry[6]:
                entry[5] = True
                return None
        # 已认领但超时未答：GUI 线程撤按钮、推进队列（结果按 None 拒绝兜底）
        QTimer.singleShot(0, parent, lambda: self._abort(entry))
        return None

    # ------------------------------------------------------------------
    # GUI 线程
    # ------------------------------------------------------------------
    def _pump(self) -> None:
        """GUI 线程消费循环：无活动条目时取下一个有效条目激活；有则重入返回。"""
        with self._lock:
            if self._active is not None:
                return
            while self._pending and self._pending[0][5]:
                self._pending.popleft()
            if not self._pending:
                return
            entry = self._pending.popleft()
            entry[6] = True  # 认领：此后超时走 _abort 路径而非 stale 标记
            self._active = entry
        self._activate_one(entry)

    def _activate_one(self, entry: _Entry) -> None:
        """单条激活：优先卡内按钮（异步等点击），卡片缺失/已终态降级
        QuestionDialog 模态弹窗（嵌套事件流，与 PERMISSION_QUEUE 同手法）。"""
        params, tool_call_id, parent = entry[0], entry[1], entry[2]
        try:
            if not isValid(parent):  # parent 标签排队期间被关闭销毁
                self._finish(entry, None)
                return
            card = find_question_card(tool_call_id) if tool_call_id else None
            if card is not None and card.activate_options(
                    params.get("options") or [],
                    lambda oid: self._finish(entry, oid)):
                entry[7] = card  # 异步路径：等用户点击回调或 _abort
                return
            dialog = QuestionDialog(params, parent)
            dialog.exec()
            self._finish(entry, dialog.selected_option_id())
        except Exception:  # noqa: BLE001 — 销毁竞态/构造失败：按拒绝兜底
            self._finish(entry, None)

    def _finish(self, entry: _Entry, option_id: str | None) -> None:
        """作答落定（卡片点击回调 / 弹窗返回 / 异常兜底）：唤醒 reader 并推进队列。"""
        with self._lock:
            if self._active is entry:
                self._active = None
        entry[3] = option_id
        entry[4].set()
        self._pump()

    def _abort(self, entry: _Entry) -> None:
        """超时撤销（GUI 线程）：撤掉卡内按钮并推进队列（reader 已按拒绝兜底）。"""
        with self._lock:
            if self._active is not entry:
                return
            self._active = None
        card = entry[7]
        if card is not None and isValid(card):
            card.deactivate_options()
        self._pump()


#: 全窗口共用单例（ChatPanel._ask_permission question 分支统一入口）
QUESTION_BRIDGE = QuestionBridge()
