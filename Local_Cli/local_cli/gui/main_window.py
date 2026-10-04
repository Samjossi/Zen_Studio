"""主窗口：服务控制、会话/状态视图、演示对话与日志面板的组装与接线。

本层只做 UI 组装与信号槽接线，不含任何协议逻辑；与内核/客户端线程的
交互全部经 GuiAcpService 的 Qt 信号（队列连接），禁止跨线程触碰控件。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_cli.gui.service import GuiAcpService, ServiceState
from local_cli.model.mock import DEMO_MODEL_ALIASES

#: 窗口标题（标题栏文案非协议内容，不入内核常量）
_WINDOW_TITLE = "Local CLI"

#: 日志面板最大行数：滚动日志无限增长会拖慢重绘，截断保流畅
_LOG_MAX_BLOCK_COUNT = 2000

#: 状态栏提示驻留时长（风格规范 §6.10：短暂显示后消失）
_STATUS_MESSAGE_MS = 3000

#: ServiceState 令牌 → 界面文案（语义与展示分离）
_STATE_DISPLAY_TEXT = {
    ServiceState.STOPPED: "已停止",
    ServiceState.STARTING: "启动中…",
    ServiceState.RUNNING: "运行中",
}

#: 会话字段未协商出来时的占位文案
_EMPTY_FIELD_PLACEHOLDER = "—"


class MainWindow(QMainWindow):
    """服务状态/会话/日志的可视化外壳：持有 GuiAcpService 并转发用户操作。"""

    def __init__(self, service: GuiAcpService | None = None) -> None:
        super().__init__()
        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(720, 560)
        # 组合优先：服务门面显式注入，便于测试时替换（AFCP 2.3/2.4）
        self._service = service if service is not None else GuiAcpService(self)
        self._build_widgets()
        self._wire_signals()
        self._apply_state(ServiceState.STOPPED)

    # ------------------------------------------------------------------
    # UI 组装
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        # 规范 §5.1：窗口级外边距 上12 右6 下0 左12，面板间距 6
        root_layout.setContentsMargins(12, 12, 6, 0)
        root_layout.setSpacing(6)
        root_layout.addWidget(self._build_service_card())
        root_layout.addWidget(self._build_session_card())
        root_layout.addWidget(self._build_demo_card(), stretch=1)
        root_layout.addWidget(self._build_log_card(), stretch=1)
        self.setCentralWidget(central)

    def _make_card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """卡片 = 1px 描边容器（8px 圆角由 QSS 提供）+ 标题行。"""
        card = QFrame(self)
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)
        title_label = QLabel(title, card)
        title_label.setObjectName("panelTitle")
        layout.addWidget(title_label)
        return card, layout

    def _build_service_card(self) -> QFrame:
        card, layout = self._make_card("服务")
        controls_row = QHBoxLayout()
        self._start_button = QPushButton("启动服务", card)
        self._stop_button = QPushButton("停止服务", card)
        # 规范 §9.3：可交互元素 hover 手型指针
        self._start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_row.addWidget(self._start_button)
        controls_row.addWidget(self._stop_button)

        status_hint = QLabel("状态：", card)
        status_hint.setObjectName("hintText")
        self._state_value = QLabel(_EMPTY_FIELD_PLACEHOLDER, card)
        self._state_value.setObjectName("statusValue")
        controls_row.addWidget(status_hint)
        controls_row.addWidget(self._state_value)

        model_hint = QLabel("模型别名：", card)
        model_hint.setObjectName("hintText")
        self._model_combo = QComboBox(card)
        self._model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_combo.addItems(DEMO_MODEL_ALIASES)
        controls_row.addWidget(model_hint)
        controls_row.addWidget(self._model_combo)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)
        return card

    def _build_session_card(self) -> QFrame:
        card, layout = self._make_card("会话")
        fields_row = QHBoxLayout()
        self._session_id_value = self._add_field(
            fields_row, card, "会话 ID：")
        self._session_model_value = self._add_field(
            fields_row, card, "当前模型：")
        self._connection_value = self._add_field(
            fields_row, card, "连接：")
        fields_row.addStretch(1)
        layout.addLayout(fields_row)
        return card

    def _add_field(self, row: QHBoxLayout, parent: QWidget, hint: str) -> QLabel:
        hint_label = QLabel(hint, parent)
        hint_label.setObjectName("hintText")
        value_label = QLabel(_EMPTY_FIELD_PLACEHOLDER, parent)
        value_label.setObjectName("statusValue")
        row.addWidget(hint_label)
        row.addWidget(value_label)
        return value_label

    def _build_demo_card(self) -> QFrame:
        card, layout = self._make_card("演示对话（内嵌 ACP 客户端自连）")
        self._reply_view = QPlainTextEdit(card)
        self._reply_view.setReadOnly(True)
        self._reply_view.setPlaceholderText("启动服务后，发送一条消息查看流式回复")
        layout.addWidget(self._reply_view, stretch=1)

        input_row = QHBoxLayout()
        self._prompt_input = QLineEdit(card)
        self._prompt_input.setPlaceholderText(
            "输入消息；含「建文件」演示审批回环，含「报错」演示错误收尾")
        self._send_button = QPushButton("发送", card)
        self._cancel_button = QPushButton("取消轮次", card)
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        input_row.addWidget(self._prompt_input, stretch=1)
        input_row.addWidget(self._send_button)
        input_row.addWidget(self._cancel_button)
        layout.addLayout(input_row)
        return card

    def _build_log_card(self) -> QFrame:
        card, layout = self._make_card("日志")
        self._log_view = QPlainTextEdit(card)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(_LOG_MAX_BLOCK_COUNT)
        layout.addWidget(self._log_view, stretch=1)
        return card

    # ------------------------------------------------------------------
    # 信号接线
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self._start_button.clicked.connect(self._service.start)
        self._stop_button.clicked.connect(self._service.stop)
        self._send_button.clicked.connect(self._send_prompt)
        self._prompt_input.returnPressed.connect(self._send_prompt)
        self._cancel_button.clicked.connect(self._service.send_cancel)
        self._model_combo.activated.connect(self._change_model_alias)

        self._service.log_received.connect(self._append_log)
        self._service.state_changed.connect(self._apply_state)
        self._service.session_changed.connect(self._show_session)
        self._service.chunk_received.connect(self._append_chunk)
        self._service.turn_finished.connect(self._show_turn_finished)

    # ------------------------------------------------------------------
    # 用户操作 → 服务门面
    # ------------------------------------------------------------------
    def _send_prompt(self) -> None:
        text = self._prompt_input.text().strip()
        if not text:
            return
        if not self._service.send_prompt(text):
            self._append_log("[gui] 服务未运行，无法发送")
            return
        # 新一轮开始清旧回复；busy 态禁用发送防重复（规范 §9.2）
        self._reply_view.clear()
        self._prompt_input.clear()
        self._send_button.setEnabled(False)

    def _change_model_alias(self, index: int) -> None:
        self._service.send_model_alias(self._model_combo.itemText(index))

    # ------------------------------------------------------------------
    # 服务信号 → UI 刷新（全部经 Qt 队列连接，运行在 GUI 线程）
    # ------------------------------------------------------------------
    def _append_log(self, message: str) -> None:
        self._log_view.appendPlainText(message)

    def _apply_state(self, state: str) -> None:
        is_running = state == ServiceState.RUNNING
        self._state_value.setText(
            _STATE_DISPLAY_TEXT.get(state, _EMPTY_FIELD_PLACEHOLDER))
        self._connection_value.setText("已连接（内存管道）" if is_running else "未连接")
        self._start_button.setEnabled(state == ServiceState.STOPPED)
        self._stop_button.setEnabled(is_running)
        self._send_button.setEnabled(is_running)
        self._cancel_button.setEnabled(is_running)
        self._model_combo.setEnabled(is_running)
        if state == ServiceState.STOPPED:
            self._session_id_value.setText(_EMPTY_FIELD_PLACEHOLDER)
            self._session_model_value.setText(_EMPTY_FIELD_PLACEHOLDER)

    def _show_session(self, session_id: str, model_alias: str) -> None:
        self._session_id_value.setText(session_id or _EMPTY_FIELD_PLACEHOLDER)
        self._session_model_value.setText(model_alias or _EMPTY_FIELD_PLACEHOLDER)
        # 下拉框与内核协商结果对齐（切换被拒时回弹到真实别名）
        combo_index = self._model_combo.findText(model_alias)
        if combo_index >= 0:
            self._model_combo.setCurrentIndex(combo_index)

    def _append_chunk(self, text: str, is_thought: bool) -> None:
        # 思维链与正文同区展示但加前缀区分（规范 §8.5 的灰色由后续富文本化实现）
        self._reply_view.moveCursor(QTextCursor.MoveOperation.End)
        self._reply_view.insertPlainText(("» " if is_thought else "") + text)
        self._reply_view.moveCursor(QTextCursor.MoveOperation.End)

    def _show_turn_finished(self, stop_reason: str) -> None:
        self._send_button.setEnabled(self._service.is_running())
        self.statusBar().showMessage(
            f"轮次结束：stopReason={stop_reason}", _STATUS_MESSAGE_MS)

    # ------------------------------------------------------------------
    # 优雅退出（关机方案 §4：幂等清理，closeEvent 与 aboutToQuit 可重复触发）
    # ------------------------------------------------------------------
    def shutdown_service(self) -> None:
        """供 aboutToQuit 挂接的公共清理入口；幂等，重复调用无副作用。"""
        self._service.stop()

    def closeEvent(self, event: QCloseEvent) -> None:
        # 关窗即停线程：关管道 → 内核 EOF 退出 → join 回收，无残留线程
        self._service.stop()
        super().closeEvent(event)
