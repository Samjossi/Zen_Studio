"""模型选择：模型按钮（后端）+ 版本按钮（各标签输入区底行左端）。

选择持久化（2026-07-19，见 文档/修改记录/2026-0719-0712_GUI窗口状态
与模型选择持久化计划.md）：启动时 set_selection 恢复上次选择（无效项
静默回退默认），用户主动切换即时写盘。

左栏宽度根治（2026-07-24，work plans/2026-0724-2305 计划 T1/T4）：
- 双下拉 minimumSizeHint 与「最长条目文本宽度」脱钩
- 停止按钮移除：busy 显隐曾使模型行最小宽度跳变触发 QSplitter 撑宽
  左栏；停止改归输入区底行右端的发送/停止双态按钮（panel.py）

下移底行与瘦身（2026-07-25，work plans/2026-0724-2354 计划）：
- 从 ChatTabs 顶部全局单例改为每 ChatPanel 底行实例（纯视图组件）：
  写盘与选择状态上移 ChatTabs（单一来源，多实例广播同步防分裂），
  本组件只管 UI 与发射 selection_changed
- 迭代 2：双 QComboBox 换 QToolButton(InstantPopup)+QMenu——框体恒显
  「模型」「版本」标签（用户拍板选项 1：切模型低频，当前值归 tooltip
  全名与菜单 ✓ 勾选）；菜单天然按内容加宽显示全文，创建经
  make_translucent_popup() 规约处理（见 gui/popups.py）
"""
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from gui.popups import make_translucent_popup
from llm import BACKEND_KIMI_CLI, BACKEND_LABELS, kimi_available, list_kimi_models


class ModelBar(QWidget):
    """输入区底行左端：模型（后端）+ 版本双按钮（InstantPopup 菜单联动刷新）。

    本期统一为本机 agent CLI 后端（Kimi CLI，不可用时项禁用）；
    OpenCode/Kilo Code 等后端接入后恢复多项（见 1455 计划第 8 节备案）。
    """

    #: 后端/版本切换（携带 registry 后端名 + 版本载荷：模型别名 str）
    selection_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._model_button = self._make_button("模型")
        self._version_button = self._make_button("版本")
        self._model_group = QActionGroup(self)  # 默认互斥
        self._version_group = QActionGroup(self)

        if kimi_available():
            for name, label in BACKEND_LABELS.items():
                self._add_action(self._model_group, label, name, self._on_model_picked)
        else:
            self._add_action(
                self._model_group,
                f"{BACKEND_LABELS[BACKEND_KIMI_CLI]}（未检测到）",
                BACKEND_KIMI_CLI, None).setEnabled(False)
        # 构造默认勾选首项（静默：setChecked 不发 triggered，见类设计注）
        if self._model_group.actions():
            self._model_group.actions()[0].setChecked(True)
        self._refresh_versions(BACKEND_KIMI_CLI)

        layout = QHBoxLayout(self)
        layout.addWidget(self._model_button)
        layout.addWidget(self._version_button)
        # 边距归零：底行装配（按钮/stretch/间距）归 ChatPanel 统一分配
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # 控件构造（按钮 + 透明化菜单 + 互斥勾选项）
    # ------------------------------------------------------------------
    def _make_button(self, title: str) -> QToolButton:
        """恒显标签的下拉按钮：文本不随选择变化（当前值归 tooltip/菜单勾选）。

        最小宽度按「文字 + 菜单箭头区 + qss padding」显式给定——默认
        sizeHint 过窄会把 InstantPopup 箭头挤到文字下方（观感修复，
        2026-07-25 迭代 2 补丁）。
        """
        button = QToolButton(self)
        button.setObjectName("chatModelButton")
        button.setText(title)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(make_translucent_popup(QMenu(button)))
        text_width = button.fontMetrics().horizontalAdvance(title)
        button.setMinimumWidth(text_width + 16 + 24)  # 箭头区 16 + padding 12*2
        return button

    def _add_action(self, group: QActionGroup, text: str, data, slot) -> QAction:
        """互斥勾选项：data 携带 registry 名/别名；slot 为 None 时仅挂菜单（禁用项）。"""
        button = self._model_button if group is self._model_group else self._version_button
        action = QAction(text, self)
        action.setCheckable(True)
        action.setData(data)
        group.addAction(action)
        button.menu().addAction(action)
        if slot is not None:
            action.triggered.connect(lambda _checked=False, d=data: slot(d))
        return action

    @staticmethod
    def _find_action(group: QActionGroup, data) -> QAction | None:
        for action in group.actions():
            if action.data() == data:
                return action
        return None

    def _refresh_tooltips(self) -> None:
        """当前项全名写入按钮 tooltip（标签恒显下当前值的常驻可见入口）。"""
        model = self._model_group.checkedAction()
        version = self._version_group.checkedAction()
        self._model_button.setToolTip(f"模型后端：{model.text() if model else '无'}")
        self._version_button.setToolTip(f"模型版本：{version.text() if version else '无'}")

    # ------------------------------------------------------------------
    # 忙碌态（流式响应中）
    # ------------------------------------------------------------------
    def set_busy(self, is_busy: bool) -> None:
        """busy：禁用双按钮（任一标签响应中即全标签禁用，ChatTabs 遍历调用）。

        按钮恒显标签、菜单随点随建，sizeHint 不随 busy 变化。
        """
        self._model_button.setEnabled(not is_busy)
        self._version_button.setEnabled(not is_busy)

    # ------------------------------------------------------------------
    # 选择查询与恢复（持久化写盘上移 ChatTabs，本组件不管）
    # ------------------------------------------------------------------
    def current_backend(self) -> str | None:
        """当前后端（registry 名）。"""
        checked = self._model_group.checkedAction()
        return checked.data() if checked is not None else None

    def current_version(self) -> str | None:
        """当前版本（模型别名）；版本列表为空时为 None。"""
        checked = self._version_group.checkedAction()
        return checked.data() if checked is not None else None

    def set_selection(self, backend: str | None, version: str | None) -> None:
        """注入选择：勾选后端 → 刷新版本列表 → 勾选版本。

        阻断语义天然成立：QAction.setChecked 不发射 triggered（仅用户
        点击发射），广播同步天然无回环；后端或版本已失效时静默回退到
        可用默认项（首项）。
        """
        target = self._find_action(self._model_group, backend)
        if target is None and self._model_group.actions():
            target = self._model_group.actions()[0]
        if target is not None:
            target.setChecked(True)
        self._refresh_versions(self.current_backend())
        vtarget = self._find_action(self._version_group, version)
        if vtarget is None and self._version_group.actions():
            vtarget = self._version_group.actions()[0]
        if vtarget is not None:
            vtarget.setChecked(True)
        self._refresh_tooltips()

    # ------------------------------------------------------------------
    # 联动
    # ------------------------------------------------------------------
    def _refresh_versions(self, backend: str | None) -> None:
        """版本菜单按后端重建（先清后建）；重建后默认勾选首项（静默）。"""
        menu = self._version_button.menu()
        for action in list(self._version_group.actions()):
            self._version_group.removeAction(action)
        menu.clear()
        if backend in BACKEND_LABELS:  # kimi 系后端共用 kimi 模型别名列表
            for alias in list_kimi_models():
                self._add_action(self._version_group, alias, alias, self._on_version_picked)
        if self._version_group.actions():
            self._version_group.actions()[0].setChecked(True)

    def _on_model_picked(self, backend: str) -> None:
        """用户勾选后端 → 版本列表联动重建（默认首项）→ 发射切换。"""
        self._refresh_versions(backend)
        self._refresh_tooltips()
        self.selection_changed.emit(backend, self.current_version())

    def _on_version_picked(self, alias: str) -> None:
        """用户勾选版本 → 发射切换（写盘与广播归 ChatTabs 单一来源）。"""
        self._refresh_tooltips()
        self.selection_changed.emit(self.current_backend(), alias)
