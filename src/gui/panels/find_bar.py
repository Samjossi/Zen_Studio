"""查找浮层组件：viewer 与 terminal 两面板共用的右上角悬浮查找条。

抽取自两面板近乎逐行复制的两套实现（2026-07-21，AFCP 整改任务 2.4）：
外观（布局/尺寸/半透明输入框）、宿主右上角定位（resize 自动重定位）、
↑/↓/× 与 Enter/Esc 按键分发为单一实现；搜索语义（命中收集/高亮/步进）
仍归宿主面板——经 textChanged 直连与 step/close 信号注入，层间单向依赖。
"""
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from gui.popups import TranslucentMenuLineEdit

#: 输入框宽度（px）
INPUT_WIDTH_PX = 180
#: 浮层定位：距宿主右缘/顶缘的边距（px）
FLOAT_MARGIN_RIGHT_PX = 16
FLOAT_MARGIN_TOP_PX = 6
#: 步进/关闭按钮尺寸（px）
BUTTON_WIDTH_PX = 24
BUTTON_HEIGHT_PX = 22


class FindBar(QFrame):
    """右上角悬浮查找条：输入框 + ↑/↓/×（初始隐藏，show_and_focus 打开）。

    宿主职责：input.textChanged 接搜索槽；step_requested 接环形步进；
    close_requested 接「隐藏 + 清高亮 + 焦点归还」。
    """

    #: 上一个/下一个请求（-1/+1；↑↓ 按钮与输入框 Enter 同一出口）
    step_requested = Signal(int)
    #: 关闭请求（× 按钮与输入框 Esc 同一出口）
    close_requested = Signal()

    def __init__(self, host: QWidget, placeholder: str) -> None:
        """
        :param host: 宿主控件（浮层以其为 parent 悬浮于右上角，resize 随动）
        :param placeholder: 输入框占位文本（各面板标注搜索范围，如「查找（当前屏）」）
        """
        super().__init__(host)
        self._host = host
        self.setObjectName("FindBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(4)
        self.input = TranslucentMenuLineEdit(self)
        self.input.setPlaceholderText(placeholder)
        self.input.setFixedWidth(INPUT_WIDTH_PX)
        self.input.installEventFilter(self)  # Enter=下一个 / Esc=关闭
        prev_button = QPushButton("↑", self)
        next_button = QPushButton("↓", self)
        close_button = QPushButton("×", self)
        for button in (prev_button, next_button, close_button):
            button.setFixedSize(BUTTON_WIDTH_PX, BUTTON_HEIGHT_PX)
        prev_button.setToolTip("上一个")
        next_button.setToolTip("下一个")
        prev_button.clicked.connect(lambda: self.step_requested.emit(-1))
        next_button.clicked.connect(lambda: self.step_requested.emit(1))
        close_button.clicked.connect(self.close_requested)
        row.addWidget(self.input)
        row.addWidget(prev_button)
        row.addWidget(next_button)
        row.addWidget(close_button)
        host.installEventFilter(self)  # 宿主 resize → 重定位
        self.setVisible(False)

    def show_and_focus(self) -> None:
        """定位右上角并显示，焦点入输入框（全选便于直接改词重搜）。"""
        self._place()
        self.setVisible(True)
        self.raise_()
        self.input.setFocus()
        self.input.selectAll()

    def _place(self) -> None:
        """定位于宿主右上角（子控件坐标系）。"""
        self.adjustSize()
        self.move(max(0, self._host.width() - self.width() - FLOAT_MARGIN_RIGHT_PX),
                  FLOAT_MARGIN_TOP_PX)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """宿主 resize → 重定位；输入框 Esc/Enter → close/step 信号。"""
        if watched is self._host:
            if event.type() == QEvent.Type.Resize and self.isVisible():
                self._place()
        elif watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_requested.emit()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step_requested.emit(1)
                return True
        return super().eventFilter(watched, event)
