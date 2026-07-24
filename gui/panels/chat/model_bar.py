"""模型行：模型下拉（后端）+ 版本下拉（输入区顶行）。

选择持久化（2026-07-19，见 文档/修改记录/2026-0719-0712_GUI窗口状态
与模型选择持久化计划.md）：启动时 set_selection 恢复上次选择（无效项
静默回退默认），用户主动切换即时写盘。

左栏宽度根治（2026-07-24，work plans/2026-0724-2305 计划 T1/T4）：
- 双下拉 setMinimumContentsLength + AdjustToMinimumContentsLengthWithIcon：
  minimumSizeHint 按固定字符数计算，与「最长条目文本宽度」脱钩——
  消除模型别名长度决定左栏静态下限（诊断报告 §6，528px 地板）
- 停止按钮移除：busy 显隐曾使本行最小宽度 528↔613 跳变，经 ChatTabs
  上传触发 QSplitter 撑宽左栏（诊断报告 §3）；停止改归各标签输入区
  底行的发送/停止双态按钮（panel.py），本行 sizeHint 全程恒定
"""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from gui.popups import make_translucent_combo_popup
from gui.settings import (
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSION,
    load_settings,
    update_settings,
)
from llm import BACKEND_KIMI_CLI, BACKEND_LABELS, kimi_available, list_kimi_models


class ModelBar(QWidget):
    """输入区顶行：模型（后端）+ 版本双下拉，版本列表按后端联动刷新。

    本期统一为本机 agent CLI 后端（Kimi CLI，不可用时项禁用）；
    OpenCode/Kilo Code 等后端接入后恢复多项（见 1455 计划第 8 节备案）。
    """

    #: 后端/版本切换（携带 registry 后端名 + 版本载荷：模型别名 str）
    selection_changed = Signal(str, object)

    #: 下拉框最小内容宽度（字符数）：sizeHint 与最长条目脱钩（T1）。
    #: 取值权衡：过小则版本别名（如 kimi-k2-0905-preview）裁剪过度不可读，
    #: 过大则左栏静态下限压不下去——10 为验收实测调优值（探针 §7.2 ≤420px）
    _MIN_CONTENTS_LENGTH = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_updating = False  # 联动刷新时抑制信号

        self._model_combo = QComboBox(self)
        if kimi_available():
            for name, label in BACKEND_LABELS.items():
                self._model_combo.addItem(label, name)
        else:
            self._model_combo.addItem(f"{BACKEND_LABELS[BACKEND_KIMI_CLI]}（未检测到）", BACKEND_KIMI_CLI)
            self._model_combo.model().item(0).setEnabled(False)

        self._version_combo = QComboBox(self)
        self._refresh_versions(BACKEND_KIMI_CLI)

        # 下拉弹出层修复：容器矩形面板（StyledPanel）+ 不透明窗口底都会
        # 在 qss 圆角（QListView 全局规则）外露出直角，需透明 + 去框
        # （见 gui/popups.py 模块 docstring）
        for combo in (self._model_combo, self._version_combo):
            make_translucent_combo_popup(combo)
            # T1：minimumSizeHint 按固定字符数计算，不再随最长条目撑大
            # 左栏静态下限；tooltip 兜底显示被裁剪的选中项全名
            combo.setMinimumContentsLength(self._MIN_CONTENTS_LENGTH)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.currentIndexChanged.connect(
                lambda _i, c=combo: c.setToolTip(c.currentText()))

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel("模型", self))
        layout.addWidget(self._model_combo)
        layout.addWidget(QLabel("版本", self))
        layout.addWidget(self._version_combo, 1)
        # 边距归零：外边距/行距由 ChatTabs 容器统一分配（面板级 6px 体系，
        # 2026-0722-1725 走查 F2/F6）；下拉框自身 qss padding 已够内留白
        layout.setContentsMargins(0, 0, 0, 0)

        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._version_combo.currentIndexChanged.connect(self._on_version_changed)

        # 启动时恢复持久化选择（无记录/无效项回退默认）
        settings = load_settings()
        self.set_selection(settings.get(KEY_MODEL_BACKEND), settings.get(KEY_MODEL_VERSION))

    def _refresh_tooltips(self) -> None:
        """选中项全名写入 tooltip（信号阻断路径的兜底刷新）。

        用户交互路径由 currentIndexChanged 连接覆盖；set_selection 全程
        QSignalBlocker 阻断，需调用方显式刷新。
        """
        for combo in (self._model_combo, self._version_combo):
            combo.setToolTip(combo.currentText())

    # ------------------------------------------------------------------
    # 忙碌态（流式响应中）
    # ------------------------------------------------------------------
    def set_busy(self, is_busy: bool) -> None:
        """busy：禁用双下拉（防响应中切后端/版本）。

        停止按钮已移至各标签输入区底行（panel.py 双态按钮），
        本行 sizeHint 不再随 busy 变化（诊断报告 §3 病根切除）。
        """
        self._model_combo.setEnabled(not is_busy)
        self._version_combo.setEnabled(not is_busy)

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
        self._refresh_tooltips()  # 阻断路径：信号未发，显式刷新

    # ------------------------------------------------------------------
    # 联动
    # ------------------------------------------------------------------
    def _refresh_versions(self, backend: str) -> None:
        self._is_updating = True
        self._version_combo.clear()
        if backend in BACKEND_LABELS:  # kimi 系后端共用 kimi 模型别名列表
            for alias in list_kimi_models():
                self._version_combo.addItem(alias, alias)
        self._is_updating = False

    def _on_model_changed(self, index: int) -> None:
        backend = self._model_combo.itemData(index)
        self._refresh_versions(backend)
        if self._version_combo.count():
            self._emit(0)

    def _on_version_changed(self, index: int) -> None:
        if not self._is_updating and index >= 0:
            self._emit(index)

    def _emit(self, index: int) -> None:
        backend = self._model_combo.currentData()
        version = self._version_combo.itemData(index)
        # 用户主动切换（非启动恢复）即时持久化
        update_settings({KEY_MODEL_BACKEND: backend, KEY_MODEL_VERSION: version})
        self.selection_changed.emit(backend, version)
