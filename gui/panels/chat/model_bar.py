"""模型行：模型标签 + 版本下拉框（输入区顶行）。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from llm import MODELS, label_for


class ModelBar(QWidget):
    """输入区顶行：显示当前模型名 + 版本，下拉切换版本。

    本期仅 DeepSeek 一家 provider，直取其 MODELS 常量与 label_for 显示格式；
    多 provider 时代改为分组下拉（见实施计划第 8 节备案）。
    """

    #: 版本切换（携带模型 ID）
    model_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._combo = QComboBox(self)
        for model_id in MODELS:
            self._combo.addItem(label_for(model_id), model_id)

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("模型", self))
        layout.addWidget(self._combo, 1)
        layout.setContentsMargins(4, 2, 4, 2)

        self._combo.currentIndexChanged.connect(self._on_changed)

    def _on_changed(self, index: int) -> None:
        self.model_changed.emit(self._combo.itemData(index))
