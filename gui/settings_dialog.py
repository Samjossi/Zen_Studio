"""设置中心对话框：左导航 + 右分页（AI 工具权限 / AI 模型 / 外观 / 终端 / 高级）。

实施依据：work plans/2026-0722-1240_设置中心对话框与AI工具权限四态实施计划.md。
- 排入逻辑：安全风险 × 使用频率排序，破坏性操作（恢复默认）沉底
- 生效纪律：控件 change 即时持久化 + 即时应用，无确定/取消（沿用菜单勾选
  先例）；仅「关闭」（Esc 同效）；非模态单例，边改边看主窗口效果
- 纯 GUI 装配层：应用逻辑全部委托 MainWindow 现有应用槽（零业务逻辑，
  同菜单文件先例）；状态同步由 MainWindow._sync_settings_dialog 回调 reload
- 防回环：reload 全程 _reloading 标志抑制槽响应（同 ModelBar._is_updating 先例）
"""
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from gui.popups import make_translucent_combo_popup
from gui.settings import (
    CONFIG_DIR,
    KEY_FONT_SIZE,
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSION,
    KEY_PERMISSION_MODE,
    KEY_TERMINAL_SWAP_COPY_PASTE,
    KEY_THEME,
    update_settings,
)
from gui.theme import WARNING_COLOR, get_label, list_available_themes, load_settings
from llm import BACKEND_LABELS, kimi_available, list_kimi_models
from llm.permission_policy import (
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_PERMISSION_MODE,
    MODE_AUTO_ALL,
    PERMISSION_MODE_LABELS,
    PERMISSION_MODES,
)

#: 说明文字与警示文字颜色（说明灰多主题下中性可读；警示色经 theme 单一来源）
_STYLE_HINT = "color: gray;"
_STYLE_DANGER = f"color: {WARNING_COLOR};"


class SettingsDialog(QDialog):
    """设置中心：非模态单例（MainWindow 持有），五页导航。

    :param ctx: MainWindow（鸭子类型：apply_model_selection / switch_theme /
        set_font_size / set_terminal_swap_copy_paste / open_settings_file /
        reset_settings / chat_tabs；FONT_SIZE_MIN/MAX 类常量）
    """

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._ctx = ctx
        self._reloading = False  # reload 期间抑制控件槽（防回环/防写盘）
        self._backend_available = kimi_available()
        #: 模型版本列表缓存（backend → aliases）：list_kimi_models 为子进程
        #: 调用，reload 期间字号/主题等无关收敛点不得反复拉起；每次
        #: showEvent 清缓存强制刷新一次（review 修复：GUI 线程卡顿）
        self._versions_cache: dict[str, list[str]] = {}
        self.setWindowTitle("设置中心")
        self.resize(660, 480)

        self._nav = QListWidget(self)
        self._nav.setFixedWidth(140)
        self._stack = QStackedWidget(self)
        for name, page in (
            ("AI 工具权限", self._build_permission_page()),
            ("AI 模型", self._build_model_page()),
            ("外观", self._build_appearance_page()),
            ("终端", self._build_terminal_page()),
            ("高级", self._build_advanced_page()),
        ):
            self._nav.addItem(name)
            self._stack.addWidget(page)
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_box.rejected.connect(self.reject)
        right = QVBoxLayout()
        right.addWidget(self._stack, 1)
        right.addWidget(close_box)
        layout = QHBoxLayout(self)
        layout.addWidget(self._nav)
        layout.addLayout(right, 1)

    # ------------------------------------------------------------------
    # AI 工具权限页（四态单选 + 黑名单只读折叠区）
    # ------------------------------------------------------------------
    def _build_permission_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._make_hint("控制 AI 工具调用的审批粒度，切换即时生效。", page))
        self._mode_radios: dict[str, QRadioButton] = {}
        for mode in PERMISSION_MODES:
            name, desc = PERMISSION_MODE_LABELS[mode]
            radio = QRadioButton(name, page)
            radio.setStyleSheet("font-weight: bold;")
            radio.toggled.connect(lambda checked, m=mode: self._on_permission_mode(m, checked))
            self._mode_radios[mode] = radio
            layout.addWidget(radio)
            desc_label = QLabel(desc, page)
            desc_label.setIndent(24)
            desc_label.setStyleSheet(_STYLE_DANGER if mode == MODE_AUTO_ALL else _STYLE_HINT)
            layout.addWidget(desc_label)
        self._blacklist_button = QPushButton(
            f"查看危险命令黑名单（{len(DANGEROUS_COMMAND_PATTERNS)} 条，智能放行档兜底）", page)
        self._blacklist_button.setCheckable(True)
        self._blacklist_button.toggled.connect(self._on_blacklist_toggle)
        layout.addWidget(self._blacklist_button)
        self._blacklist_text = QTextEdit(page)
        self._blacklist_text.setReadOnly(True)
        self._blacklist_text.setPlainText("\n".join(
            f"{i}. {reason}"
            for i, (_, reason) in enumerate(DANGEROUS_COMMAND_PATTERNS, 1)))
        self._blacklist_text.setVisible(False)
        layout.addWidget(self._blacklist_text)
        return page

    def _on_permission_mode(self, mode: str, checked: bool) -> None:
        """四态单选：持久化即生效（决策点读取时生效，无需下发）。

        auto_all 高危档需二次确认（review 修复：误点零摩擦关闭全部危险
        命令护栏）；取消则 reload 回弹控件态到持久化档位。
        """
        if not checked or self._reloading:
            return
        if mode == MODE_AUTO_ALL and not self._confirm_auto_all():
            self.reload()
            return
        update_settings({KEY_PERMISSION_MODE: mode})
        self._ctx.statusBar().showMessage(
            f"AI 工具权限：{PERMISSION_MODE_LABELS[mode][0]}", 3000)

    def _confirm_auto_all(self) -> bool:
        """auto_all 二次确认框（默认按钮为取消）。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("全部放行确认")
        box.setText("确定切换到「全部放行」吗？")
        box.setInformativeText(
            "危险命令（rm -rf /、git push -f 等）将不再弹窗，AI 会直接执行。\n"
            "请确认你信任当前 AI 后端。")
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def _on_blacklist_toggle(self, checked: bool) -> None:
        self._blacklist_text.setVisible(checked)

    # ------------------------------------------------------------------
    # AI 模型页（双下拉；与菜单/ModelBar 三方经 MainWindow 收敛点同步）
    # ------------------------------------------------------------------
    def _build_model_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._backend_combo = QComboBox(page)
        for name, label in BACKEND_LABELS.items():
            text = label if self._backend_available else f"{label}（未检测到）"
            self._backend_combo.addItem(text, name)
        self._version_combo = QComboBox(page)
        for combo in (self._backend_combo, self._version_combo):
            make_translucent_combo_popup(combo)
        layout.addRow("后端", self._backend_combo)
        layout.addRow("版本", self._version_combo)
        hint = self._make_hint(
            "与聊天面板顶部模型行、设置菜单 ▸ AI 模型同步；AI 响应中暂不可切换。", page)
        hint.setWordWrap(True)
        layout.addRow(hint)
        self._backend_combo.activated.connect(self._on_backend_activated)
        self._version_combo.activated.connect(self._on_version_activated)
        return page

    def _on_backend_activated(self, index: int) -> None:
        """后端切换：版本取 None 收敛 MainWindow（落到该后端版本列表首项）。"""
        self._ctx.apply_model_selection(self._backend_combo.itemData(index), None)

    def _on_version_activated(self, index: int) -> None:
        self._ctx.apply_model_selection(
            self._backend_combo.currentData(), self._version_combo.itemData(index))

    def set_model_enabled(self, enabled: bool) -> None:
        """busy 联动：AI 响应中禁用双下拉（与 ModelBar/菜单对齐）。"""
        self._backend_combo.setEnabled(enabled and self._backend_available)
        self._version_combo.setEnabled(enabled and self._backend_available)

    # ------------------------------------------------------------------
    # 外观页（主题下拉 + 字号 SpinBox）
    # ------------------------------------------------------------------
    def _build_appearance_page(self) -> QWidget:
        page = QWidget(self)
        layout = QFormLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._theme_combo = QComboBox(page)
        for name in list_available_themes():
            self._theme_combo.addItem(get_label(name), name)
        make_translucent_combo_popup(self._theme_combo)
        self._font_spin = QSpinBox(page)
        self._font_spin.setRange(self._ctx.FONT_SIZE_MIN, self._ctx.FONT_SIZE_MAX)
        self._font_spin.setSuffix(" pt")
        layout.addRow("主题", self._theme_combo)
        layout.addRow("字号", self._font_spin)
        layout.addRow(self._make_hint("主题与字号即时应用全窗口（含各面板配色）。"))
        self._theme_combo.activated.connect(self._on_theme_activated)
        self._font_spin.valueChanged.connect(self._on_font_changed)
        return page

    def _on_theme_activated(self, index: int) -> None:
        self._ctx.switch_theme(self._theme_combo.itemData(index))

    def _on_font_changed(self, value: int) -> None:
        if not self._reloading:
            self._ctx.set_font_size(value)

    # ------------------------------------------------------------------
    # 终端页
    # ------------------------------------------------------------------
    def _build_terminal_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._swap_check = QCheckBox("Ctrl+C/V 复制粘贴", page)
        self._swap_check.toggled.connect(self._on_swap_toggled)
        layout.addWidget(self._swap_check)
        hint = self._make_hint(
            "勾选后：Ctrl+C/V 复制粘贴，Ctrl+Shift+C/V 发中断（SIGINT）/粘贴标记。\n"
            "与设置菜单 ▸「终端：Ctrl+C/V 复制粘贴」同一开关。", page)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _on_swap_toggled(self, checked: bool) -> None:
        if not self._reloading:
            self._ctx.set_terminal_swap_copy_paste(checked)

    # ------------------------------------------------------------------
    # 高级页（打开配置文件 / 恢复默认设置——破坏性沉底）
    # ------------------------------------------------------------------
    def _build_advanced_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        path_label = QLabel(f"配置目录：{CONFIG_DIR}", page)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setStyleSheet(_STYLE_HINT)
        layout.addWidget(path_label)
        open_button = QPushButton("打开配置文件", page)
        open_button.clicked.connect(self._ctx.open_settings_file)
        layout.addWidget(open_button)
        layout.addWidget(self._make_hint("配置文件为只读查看；修改请经 AI 落盘。", page))
        reset_button = QPushButton("恢复默认设置…", page)
        reset_button.clicked.connect(self._ctx.reset_settings)
        layout.addWidget(reset_button)
        return page

    # ------------------------------------------------------------------
    # 状态重载（MainWindow._sync_settings_dialog 唯一回调入口）
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """从持久化全量重读并刷新五页控件态；全程抑制控件槽（防回环）。"""
        self._reloading = True
        try:
            settings = load_settings()
            mode = settings[KEY_PERMISSION_MODE]
            radio = self._mode_radios.get(mode) or self._mode_radios[DEFAULT_PERMISSION_MODE]
            radio.setChecked(True)
            self._reload_model(settings)
            theme_index = self._theme_combo.findData(settings[KEY_THEME])
            self._theme_combo.setCurrentIndex(max(theme_index, 0))
            self._font_spin.setValue(settings[KEY_FONT_SIZE])
            self._swap_check.setChecked(settings[KEY_TERMINAL_SWAP_COPY_PASTE])
        finally:
            self._reloading = False

    def _reload_model(self, settings) -> None:
        """模型页：定位后端 → 版本列表（缓存优先） → 定位版本（静默回退默认项）。

        版本列表按 backend 缓存：list_kimi_models 为子进程调用，仅缓存 miss
        （后端切换 / showEvent 清缓存后的首次 reload）才拉起，避免字号/主题
        等无关收敛点触发的 reload 在 GUI 线程反复 spawn 子进程。
        """
        backend = settings[KEY_MODEL_BACKEND]
        index = self._backend_combo.findData(backend)
        self._backend_combo.setCurrentIndex(max(index, 0))
        current_backend = self._backend_combo.currentData()
        if current_backend not in self._versions_cache:
            self._versions_cache[current_backend] = (
                list_kimi_models() if current_backend in BACKEND_LABELS else [])
        self._version_combo.clear()
        for alias in self._versions_cache[current_backend]:
            self._version_combo.addItem(alias, alias)
        version_index = self._version_combo.findData(settings[KEY_MODEL_VERSION])
        if self._version_combo.count():
            self._version_combo.setCurrentIndex(max(version_index, 0))

    # ------------------------------------------------------------------
    # 显示：重载控件态 + busy 联动初值
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._versions_cache.clear()  # 每次打开强制刷新一次模型列表
        self.reload()
        self.set_model_enabled(not self._ctx.chat_tabs.is_busy())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _make_hint(text: str, parent: QWidget | None = None) -> QLabel:
        label = QLabel(text, parent)
        label.setStyleSheet(_STYLE_HINT)
        return label
