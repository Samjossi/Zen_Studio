"""设置中心对话框：左导航 + 右分页（页面注册表驱动，唯一偏好配置面）。

实施依据：work plans/2026-0722-1240（对话框落地）、2026-0722-1344
（页面注册表重构 + 设置中心唯一配置面）与 2026-0722-1510（UI 改进）三份计划。
- 排入逻辑：安全风险 × 使用频率排序，破坏性操作（恢复默认）沉底
- 生效纪律：控件 change 即时持久化 + 即时应用，无确定/取消（沿用菜单勾选
  先例）；仅「关闭」（Esc 同效，中文按钮弃 QDialogButtonBox——其本地化依赖
  QTranslator 未装载故英文）；非模态单例，边改边看主窗口效果
- 骨架：左导航（卡片化 QListWidget，base.qss QListView 段统一换肤）+
  右页标题区（标题/副标题/分隔线，currentRowChanged 骨架统一刷新）+
  堆叠页 + 底部「关闭」按钮；各页不再自带页顶说明（1510 计划 D4/D8）
- 主题化：app 级 qss 覆盖标准控件；内联 style 不受其管辖的部分（hint 灰、
  分隔线）由 apply_theme(theme) 收敛点按 muted_text/border 令牌重刷
  （挂 MainWindow.switch_theme 链，同五面板先例；1510 计划 D6——
  QPalette 角色色方案被否决：apply_theme 只设样式表不设 palette，角色色
  不会随主题变化）
- 纯 GUI 装配层：应用逻辑全部委托 MainWindow 现有应用槽（零业务逻辑，
  同菜单文件先例）；状态同步由 MainWindow._sync_settings_dialog 回调 reload
- 防回环：reload 全程 _reloading 标志抑制槽响应（同 ModelBar._is_updating 先例）

新增设置项标准流程（2026-0722-1344 计划 §3.2，AI/人均须遵循）：
1. gui/settings.py 加 KEY_* 常量 + AppSettings 键 + 默认值
2. 选归宿页（或新页）：写 _build_xxx_page（控件 change 接 MainWindow
   应用槽或 update_settings）
3. 写 _reload_xxx(settings)（控件态回读，由 reload 的 _reloading 抑制）
4. _PAGE_REGISTRY 加一行（导航名 + 副标题 + 两方法名）——骨架与 reload
   分发零改动
（页数 >8 再引入导航分组：注册表扩 category 字段即可，不提前实现）
"""
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont

from gui.popups import make_translucent_combo_popup
from gui.settings import (
    CONFIG_DIR,
    KEY_FONT_SIZE,
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSION,
    KEY_NOISE_FILTER,
    KEY_PERMISSION_MODE,
    KEY_TERMINAL_SWAP_COPY_PASTE,
    KEY_THEME,
    update_settings,
)
from gui.theme import (
    WARNING_COLOR,
    get_label,
    get_mono_family,
    get_theme_palette,
    list_available_themes,
    load_settings,
)
from llm import BACKEND_LABELS, kimi_available, list_kimi_models
from llm.permission_policy import (
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_PERMISSION_MODE,
    MODE_AUTO_ALL,
    PERMISSION_MODE_LABELS,
    PERMISSION_MODES,
)

#: 警示文字样式（auto_all 档说明；WARNING_COLOR 跨主题固定，单一来源不动）
_STYLE_DANGER = f"color: {WARNING_COLOR};"
#: 单选项名加粗（只设字重不设色，颜色随 app 级 qss 主题）
_STYLE_RADIO = "font-weight: bold;"
#: 单选描述缩进（radio 指示器宽 16 + 间距 4，与指示器视觉对齐；1510 计划 D5）
_DESC_INDENT = 20
#: 导航宽度带（min/max 非 Fixed；1510 计划 D1）与项高/内边距。
#: 项高经 sizeHint 设定（跨 QStyle 可靠）：qss min-height 对 item delegate
#: 的采纳依赖平台样式——offscreen（Fusion）生效，但 Linux 原生样式不采纳，
#: 项高压过字高致相邻项文字重叠（1510 复核修复）；qss 仅覆 padding
#: （不决定行高，无平台差异），颜色选择器留 app 级 qss 统一换肤
_NAV_MIN_WIDTH = 160
_NAV_MAX_WIDTH = 200
_NAV_ITEM_HEIGHT = 32
_NAV_ITEM_QSS = "QListWidget::item { padding: 4px 12px; }"
#: 页标题字号（pt，加粗；副标题取 muted_text 令牌）
_TITLE_FONT_SIZE_PT = 14
#: 黑名单只读区固定高度（防展开撑变形；1510 计划 D13）
_BLACKLIST_HEIGHT = 160

#: 页面注册表：(导航名, 副标题, 构建方法名, 重载方法名)；元组顺序即导航顺序。
#: 副标题由骨架页标题区统一展示（各页不再自带页顶说明）。
#: 新增设置页 = 写 _build_xxx_page / _reload_xxx 两函数 + 此处加一行，
#: 骨架与 reload 分发零改动（AFCP 3.4 常量化；同 menus/assembler.MODULES 先例）
_PAGE_REGISTRY: tuple[tuple[str, str, str, str | None], ...] = (
    ("AI 工具权限", "控制 AI 工具调用的审批粒度，切换即时生效。",
     "_build_permission_page", "_reload_permission"),
    ("AI 模型", "选择 AI 后端与版本；与聊天面板顶部模型行双向同步。",
     "_build_model_page", "_reload_model"),
    ("外观", "主题与字号即时应用全窗口（含各面板配色）。",
     "_build_appearance_page", "_reload_appearance"),
    ("终端", "终端按键行为配置，切换即时生效。",
     "_build_terminal_page", "_reload_terminal"),
    ("高级", "配置文件查看与重置操作。",
     "_build_advanced_page", None),
)


class SettingsDialog(QDialog):
    """设置中心：非模态单例（MainWindow 持有），页面注册表驱动导航。

    :param ctx: MainWindow（鸭子类型：apply_model_selection / switch_theme /
        set_font_size / set_terminal_swap_copy_paste / set_noise_filter /
        open_settings_file / reset_settings / chat_tabs / statusBar；
        FONT_SIZE_MIN/MAX/STATUS_MSG_TIMEOUT_MS 类常量）
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
        #: hint 控件与分隔线集合：内联 style 不受 app 级 qss 管辖，
        #: apply_theme 统一按令牌重刷（1510 计划 D6/D10）
        self._hint_labels: list[QLabel] = []
        self._sep_frames: list[QFrame] = []
        self.setWindowTitle("设置中心")
        self.setMinimumSize(600, 420)
        self.resize(680, 500)

        self._nav = QListWidget(self)
        self._nav.setMinimumWidth(_NAV_MIN_WIDTH)
        self._nav.setMaximumWidth(_NAV_MAX_WIDTH)
        self._nav.setStyleSheet(_NAV_ITEM_QSS)
        self._stack = QStackedWidget(self)
        for name, _, build_name, _ in _PAGE_REGISTRY:
            item = QListWidgetItem(name)
            # 宽 0 占位（列表模式项宽由视口定）：QSize(-1, h) 为无效尺寸，
            # setSizeHint 会按清除语义丢掉高度（1510 复核修复实测）
            item.setSizeHint(QSize(0, _NAV_ITEM_HEIGHT))
            self._nav.addItem(item)
            self._stack.addWidget(getattr(self, build_name)())
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._build_chrome()
        self._nav.setCurrentRow(0)
        self.apply_theme(load_settings()[KEY_THEME])

    def _build_chrome(self) -> None:
        """骨架装配：页标题区 + 导航分隔线 + 中文关闭按钮（1510 计划 P0）。"""
        self._title_label = QLabel(self)
        title_font = self._title_label.font()
        title_font.setPointSize(_TITLE_FONT_SIZE_PT)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._subtitle_label = self._make_hint("", self)
        self._nav_sep = self._make_separator(QFrame.Shape.VLine)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        right = QVBoxLayout()
        right.setContentsMargins(16, 12, 16, 12)
        right.setSpacing(8)
        right.addWidget(self._title_label)
        right.addWidget(self._subtitle_label)
        right.addWidget(self._make_separator(QFrame.Shape.HLine))
        right.addWidget(self._stack, 1)
        right.addLayout(button_row)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 0, 8)
        layout.setSpacing(0)
        layout.addWidget(self._nav)
        layout.addWidget(self._nav_sep)
        layout.addLayout(right, 1)

    def _on_nav_changed(self, row: int) -> None:
        """导航切页：堆叠页 + 页标题区同步刷新（副标题取注册表第二字段）。"""
        self._stack.setCurrentIndex(row)
        if 0 <= row < len(_PAGE_REGISTRY):
            name, subtitle, _, _ = _PAGE_REGISTRY[row]
            self._title_label.setText(name)
            self._subtitle_label.setText(subtitle)

    def apply_theme(self, theme: str) -> None:
        """主题收敛点（MainWindow.switch_theme 链，同五面板先例）。

        app 级 qss 覆盖标准控件；此处仅重刷其管辖不到的内联 style——
        hint 取 muted_text 令牌，分隔线取 border 令牌（QFrame 线色走
        qss color 属性）；警示红 WARNING_COLOR 跨主题固定不动。
        """
        tokens = get_theme_palette(theme)
        hint_qss = f"color: {tokens['muted_text']};"
        for label in self._hint_labels:
            label.setStyleSheet(hint_qss)
        sep_qss = f"color: {tokens['border']};"
        for frame in self._sep_frames:
            frame.setStyleSheet(sep_qss)

    # ------------------------------------------------------------------
    # AI 工具权限页（四态单选 + 黑名单只读折叠区）
    # ------------------------------------------------------------------
    def _build_permission_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._mode_radios: dict[str, QRadioButton] = {}
        for mode in PERMISSION_MODES:
            name, desc = PERMISSION_MODE_LABELS[mode]
            radio = QRadioButton(name, page)
            radio.setStyleSheet(_STYLE_RADIO)
            radio.toggled.connect(lambda checked, m=mode: self._on_permission_mode(m, checked))
            self._mode_radios[mode] = radio
            layout.addWidget(radio)
            desc_label = QLabel(desc, page)
            desc_label.setIndent(_DESC_INDENT)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(_STYLE_DANGER if mode == MODE_AUTO_ALL else "")
            if mode != MODE_AUTO_ALL:
                self._hint_labels.append(desc_label)
            layout.addWidget(desc_label)
        self._blacklist_label = (
            f"查看危险命令黑名单（{len(DANGEROUS_COMMAND_PATTERNS)} 条，智能放行档兜底）")
        self._blacklist_button = QToolButton(page)
        self._blacklist_button.setCheckable(True)
        self._blacklist_button.toggled.connect(self._on_blacklist_toggle)
        # 初态折叠箭头（_blacklist_text 尚未建，不走 toggled 槽）
        self._blacklist_button.setText(f"▶ {self._blacklist_label}")
        layout.addWidget(self._blacklist_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._blacklist_text = QTextEdit(page)
        self._blacklist_text.setReadOnly(True)
        self._blacklist_text.setFont(QFont(get_mono_family()))
        self._blacklist_text.setFixedHeight(_BLACKLIST_HEIGHT)
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
            f"AI 工具权限：{PERMISSION_MODE_LABELS[mode][0]}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

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
        """折叠钮：箭头随展开态翻转（▶/▼），只读区定高不撑窗（D2/D13）。"""
        self._blacklist_button.setText(f"{'▼' if checked else '▶'} {self._blacklist_label}")
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
        layout.addRow(self._make_hint("AI 响应中暂不可切换。", page))
        self._backend_combo.activated.connect(self._on_backend_activated)
        self._version_combo.activated.connect(self._on_version_activated)
        return page

    def _on_backend_activated(self, index: int) -> None:
        """后端切换：版本取 None 收敛 MainWindow（落到该后端版本列表首项）。"""
        self._ctx.apply_model_selection(self._backend_combo.itemData(index), None)
        self._ctx.statusBar().showMessage(
            f"AI 模型：后端 → {self._backend_combo.itemText(index)}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

    def _on_version_activated(self, index: int) -> None:
        self._ctx.apply_model_selection(
            self._backend_combo.currentData(), self._version_combo.itemData(index))
        self._ctx.statusBar().showMessage(
            f"AI 模型：版本 → {self._version_combo.itemText(index)}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

    def set_model_enabled(self, enabled: bool) -> None:
        """busy 联动：AI 响应中禁用双下拉（与 ModelBar/菜单对齐）。"""
        self._backend_combo.setEnabled(enabled and self._backend_available)
        self._version_combo.setEnabled(enabled and self._backend_available)

    # ------------------------------------------------------------------
    # 外观页（主题下拉 + 字号 SpinBox + 文件树小节）
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
        # 文件树小节（P2 首个迁移验证项：噪音过滤由视图菜单会话态升级为偏好）
        self._noise_check = QCheckBox("过滤噪音目录", page)
        layout.addRow("文件树", self._noise_check)
        layout.addRow(self._make_hint(
            "隐藏 __pycache__ / .git / .venv / node_modules；与视图菜单同一开关。", page))
        self._theme_combo.activated.connect(self._on_theme_activated)
        self._font_spin.valueChanged.connect(self._on_font_changed)
        self._noise_check.toggled.connect(self._on_noise_toggled)
        return page

    def _on_theme_activated(self, index: int) -> None:
        # 状态栏反馈由 MainWindow.switch_theme 统一发（「已切换为xx主题」），不重复
        self._ctx.switch_theme(self._theme_combo.itemData(index))

    def _on_font_changed(self, value: int) -> None:
        if not self._reloading:
            self._ctx.set_font_size(value)
            self._ctx.statusBar().showMessage(
                f"外观：字号 → {value} pt", self._ctx.STATUS_MSG_TIMEOUT_MS)

    def _on_noise_toggled(self, checked: bool) -> None:
        if not self._reloading:
            self._ctx.set_noise_filter(checked)
            self._ctx.statusBar().showMessage(
                f"外观：过滤噪音目录 → {'开' if checked else '关'}",
                self._ctx.STATUS_MSG_TIMEOUT_MS)

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
        layout.addWidget(self._make_hint(
            "勾选后：Ctrl+C/V 复制粘贴，Ctrl+Shift+C/V 发中断（SIGINT）/粘贴标记。", page))
        return page

    def _on_swap_toggled(self, checked: bool) -> None:
        if not self._reloading:
            self._ctx.set_terminal_swap_copy_paste(checked)
            self._ctx.statusBar().showMessage(
                f"终端：Ctrl+C/V 复制粘贴 → {'开' if checked else '关'}",
                self._ctx.STATUS_MSG_TIMEOUT_MS)

    # ------------------------------------------------------------------
    # 高级页（打开配置文件 / 恢复默认设置——破坏性沉底，按钮水平排列）
    # ------------------------------------------------------------------
    def _build_advanced_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        path_label = QLabel(f"配置目录：{CONFIG_DIR}", page)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        self._hint_labels.append(path_label)
        layout.addWidget(path_label)
        open_button = QPushButton("打开配置文件", page)
        open_button.clicked.connect(self._ctx.open_settings_file)
        reset_button = QPushButton("恢复默认设置…", page)
        reset_button.clicked.connect(self._ctx.reset_settings)
        button_row = QHBoxLayout()
        button_row.addWidget(open_button)
        button_row.addWidget(reset_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addWidget(self._make_hint("配置文件为只读查看；修改请经 AI 落盘。", page))
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # 状态重载（MainWindow._sync_settings_dialog 唯一回调入口）
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """从持久化全量重读，按 _PAGE_REGISTRY 分发各页 reload；全程抑制控件槽。"""
        self._reloading = True
        try:
            settings = load_settings()
            for _, _, _, reload_name in _PAGE_REGISTRY:
                if reload_name is not None:
                    getattr(self, reload_name)(settings)
        finally:
            self._reloading = False

    def _reload_permission(self, settings) -> None:
        """权限页：定位持久化档位（未知档静默回退默认档）。"""
        mode = settings[KEY_PERMISSION_MODE]
        radio = self._mode_radios.get(mode) or self._mode_radios[DEFAULT_PERMISSION_MODE]
        radio.setChecked(True)

    def _reload_appearance(self, settings) -> None:
        """外观页：主题 / 字号 / 文件树噪音过滤回读。"""
        theme_index = self._theme_combo.findData(settings[KEY_THEME])
        self._theme_combo.setCurrentIndex(max(theme_index, 0))
        self._font_spin.setValue(settings[KEY_FONT_SIZE])
        self._noise_check.setChecked(settings[KEY_NOISE_FILTER])

    def _reload_terminal(self, settings) -> None:
        self._swap_check.setChecked(settings[KEY_TERMINAL_SWAP_COPY_PASTE])

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
    def _make_hint(self, text: str, parent: QWidget | None = None) -> QLabel:
        """说明文字（颜色由 apply_theme 按 muted_text 令牌重刷；一律可换行）。"""
        label = QLabel(text, parent)
        label.setWordWrap(True)
        self._hint_labels.append(label)
        return label

    def _make_separator(self, shape: QFrame.Shape) -> QFrame:
        """分隔线（颜色由 apply_theme 按 border 令牌重刷）。"""
        frame = QFrame(self)
        frame.setFrameShape(shape)
        self._sep_frames.append(frame)
        return frame
