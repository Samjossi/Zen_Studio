"""模型行：模型下拉（后端）+ 版本下拉（输入区顶行）。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from llm import kimi_available, list_kimi_models


class ModelBar(QWidget):
    """输入区顶行：模型（后端）+ 版本双下拉，版本列表按后端联动刷新。

    本期统一为本机 agent CLI 后端（Kimi CLI，不可用时项禁用）；
    OpenCode/Kilo Code 等后端接入后恢复多项（见 1455 计划第 8 节备案）。
    """

    #: 后端/版本切换（携带 registry 后端名 + 版本载荷：模型别名 str）
    selection_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False  # 联动刷新时抑制信号

        self._model_combo = QComboBox(self)
        if kimi_available():
            self._model_combo.addItem("Kimi CLI", "kimi-cli")
        else:
            self._model_combo.addItem("Kimi CLI（未检测到）", "kimi-cli")
            self._model_combo.model().item(0).setEnabled(False)

        self._version_combo = QComboBox(self)
        self._refresh_versions("kimi-cli")

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("模型", self))
        layout.addWidget(self._model_combo)
        layout.addWidget(QLabel("版本", self))
        layout.addWidget(self._version_combo, 1)
        layout.setContentsMargins(4, 2, 4, 2)

        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)

    # ------------------------------------------------------------------
    # 联动
    # ------------------------------------------------------------------
    def _refresh_versions(self, backend: str) -> None:
        self._updating = True
        self._version_combo.clear()
        if backend == "kimi-cli":
            for alias in list_kimi_models():
                self._version_combo.addItem(alias, alias)
        self._updating = False

    def _on_model_changed(self, index: int) -> None:
        backend = self._model_combo.itemData(index)
        self._refresh_versions(backend)
        if self._version_combo.count():
            self._emit(0)

    def _on_version_changed(self, index: int) -> None:
        if not self._updating and index >= 0:
            self._emit(index)

    def _emit(self, index: int) -> None:
        backend = self._model_combo.currentData()
        self.selection_changed.emit(backend, self._version_combo.itemData(index))
