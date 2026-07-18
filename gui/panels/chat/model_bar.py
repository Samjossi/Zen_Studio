"""模型行：模型下拉 + 版本下拉（输入区顶行）。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from llm import MODELS, ModelVersion, label_for


class ModelBar(QWidget):
    """输入区顶行：模型（厂商）+ 版本双下拉。

    本期仅 DeepSeek 一家 provider，模型下拉仅一项占位；
    多 provider 时代模型变更时刷新版本列表（见实施计划第 8 节备案）。
    """

    #: 版本切换（携带 ModelVersion）
    model_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model_combo = QComboBox(self)
        self._model_combo.addItem("DeepSeek", "deepseek")

        self._version_combo = QComboBox(self)
        for version in MODELS:
            self._version_combo.addItem(label_for(version), version)

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("模型", self))
        layout.addWidget(self._model_combo)
        layout.addWidget(QLabel("版本", self))
        layout.addWidget(self._version_combo, 1)
        layout.setContentsMargins(4, 2, 4, 2)

        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)

    def _on_model_changed(self, index: int) -> None:
        # 本期仅 DeepSeek 一家；多 provider 时代按所选模型刷新版本列表
        pass

    def _on_version_changed(self, index: int) -> None:
        self.model_changed.emit(self._version_combo.itemData(index))
