"""模型行：模型下拉（后端）+ 版本下拉（输入区顶行）。

选择持久化（2026-07-19，见 文档/修改记录/2026-0719-0712_GUI窗口状态
与模型选择持久化计划.md）：启动时 set_selection 恢复上次选择（无效项
静默回退默认），用户主动切换即时写盘。
"""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from gui.settings import load_settings, update_settings
from llm import kimi_available, list_kimi_models


class ModelBar(QWidget):
    """输入区顶行：模型（后端）+ 版本双下拉，版本列表按后端联动刷新。

    本期统一为本机 agent CLI 后端（Kimi CLI，不可用时项禁用）；
    OpenCode/Kilo Code 等后端接入后恢复多项（见 1455 计划第 8 节备案）。
    """

    #: 后端/版本切换（携带 registry 后端名 + 版本载荷：模型别名 str）
    selection_changed = Signal(str, object)

    #: 请求停止当前生成（busy 时出现的 ■ 停止按钮被点击）
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False  # 联动刷新时抑制信号

        self._model_combo = QComboBox(self)
        if kimi_available():
            self._model_combo.addItem("Kimi CLI", "kimi-cli")
            self._model_combo.addItem("Kimi ACP", "kimi-acp")
        else:
            self._model_combo.addItem("Kimi CLI（未检测到）", "kimi-cli")
            self._model_combo.model().item(0).setEnabled(False)

        self._version_combo = QComboBox(self)
        self._refresh_versions("kimi-cli")

        # 停止按钮：仅 busy（流式响应中）可见，替代三家参考实现的
        # "发送/停止互斥"形态（本项目输入区无发送按钮，Enter 直发）
        self.stop_btn = QPushButton("■ 停止", self)
        self.stop_btn.setObjectName("chatStopBtn")
        self.stop_btn.setToolTip("停止当前生成")
        self.stop_btn.setVisible(False)

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("模型", self))
        layout.addWidget(self._model_combo)
        layout.addWidget(QLabel("版本", self))
        layout.addWidget(self._version_combo, 1)
        layout.addWidget(self.stop_btn)
        layout.setContentsMargins(4, 2, 4, 2)

        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.stop_btn.clicked.connect(self.stop_requested)

        # 启动时恢复持久化选择（无记录/无效项回退默认）
        settings = load_settings()
        self.set_selection(settings.get("model_backend"), settings.get("model_version"))

    # ------------------------------------------------------------------
    # 忙碌态（流式响应中）
    # ------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        """busy：禁用双下拉（防响应中切后端/版本）+ 显示停止按钮。

        不用整体 setEnabled——停止按钮在 busy 时必须可点。
        """
        self._model_combo.setEnabled(not busy)
        self._version_combo.setEnabled(not busy)
        self.stop_btn.setVisible(busy)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def current_backend(self) -> str:
        """当前后端（registry 名）。"""
        return self._model_combo.currentData()

    def current_version(self) -> str | None:
        """当前版本（模型别名）；版本列表为空时为 None。"""
        return self._version_combo.currentData()

    def set_selection(self, backend: str | None, version: str | None) -> None:
        """恢复持久化选择：定位后端 → 刷新版本列表 → 定位版本。

        全程阻断信号（不触发 selection_changed / 不写盘）；
        后端或版本已失效时静默回退到可用默认项。
        """
        with QSignalBlocker(self._model_combo), QSignalBlocker(self._version_combo):
            index = self._model_combo.findData(backend) if backend else -1
            self._model_combo.setCurrentIndex(max(index, 0))
            self._refresh_versions(self._model_combo.currentData())
            vindex = self._version_combo.findData(version) if version else -1
            if self._version_combo.count():
                self._version_combo.setCurrentIndex(max(vindex, 0))

    # ------------------------------------------------------------------
    # 联动
    # ------------------------------------------------------------------
    def _refresh_versions(self, backend: str) -> None:
        self._updating = True
        self._version_combo.clear()
        if backend in ("kimi-cli", "kimi-acp"):
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
        version = self._version_combo.itemData(index)
        # 用户主动切换（非启动恢复）即时持久化
        update_settings({"model_backend": backend, "model_version": version})
        self.selection_changed.emit(backend, version)
