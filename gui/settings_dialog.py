"""设置中心对话框：左导航 + 右分页（页面注册表驱动，唯一偏好配置面）。

实施依据：文档/修改记录/2026-0722-1240（对话框落地）、2026-0722-1344
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
- 字号化：页标题/黑名单等宽区字号**相对派生**自 app 全局字号（禁止绝对值
  写死——不随用户字号缩放，全局调大后标题反而比正文小），由
  apply_font_size() 收敛点重刷（挂 MainWindow._apply_font_size 链，同
  apply_theme 先例）
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
    QApplication,
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
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont

from gui.popups import make_translucent_combo_popup
from gui.settings import (
    CONFIG_DIR,
    KEY_FONT_SIZE,
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSIONS,
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
from llm import REGISTRY, spec_of, vendor_groups, vendor_of
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
#: 页标题相对 app 全局字号的放大步长（pt，加粗；副标题取 muted_text 令牌）。
#: 相对派生而非绝对值：绝对字号不随用户全局字号缩放（全局 16pt 时 14pt
#: 标题反而比正文小），apply_font_size 收敛点按「app 字号 + 本步长」重刷
_TITLE_FONT_DELTA_PT = 4
#: 黑名单只读区固定高度（防展开撑变形；1510 计划 D13）
_BLACKLIST_HEIGHT = 160

#: 页面注册表：(导航名, 副标题, 构建方法名, 重载方法名)；元组顺序即导航顺序。
#: 副标题由骨架页标题区统一展示（各页不再自带页顶说明）。
#: 新增设置页 = 写 _build_xxx_page / _reload_xxx 两函数 + 此处加一行，
#: 骨架与 reload 分发零改动（AFCP 3.4 常量化；同 menus/assembler.MODULES 先例）
_PAGE_REGISTRY: tuple[tuple[str, str, str, str | None], ...] = (
    ("AI 工具权限", "控制 AI 工具调用的审批粒度，切换即时生效。",
     "_build_permission_page", "_reload_permission"),
    ("AI 模型", "选择 AI 后台、接口与模型；与聊天面板底行模型按钮双向同步。",
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
        set_font_size / set_terminal_swap_copy_paste /
        open_settings_file / reset_settings / chat_tabs / statusBar；
        FONT_SIZE_MIN/MAX/STATUS_MSG_TIMEOUT_MS 类常量）
    """

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._ctx = ctx
        self._reloading = False  # reload 期间抑制控件槽（防回环/防写盘）
        #: 任一接口可用即整体可用（注册表遍历，惰性探测；全不可用三下拉禁用）
        self._any_backend_available = any(spec.available() for spec in REGISTRY.values())
        #: 模型别名列表缓存（接口实现名 → aliases）：spec.list_models 可能是
        #: 子进程调用（如 kimi → `kimi provider list --json`），reload 期间
        #: 字号/主题等无关收敛点不得反复拉起；缓存按接口级隔离（D6 红线 1，
        #: 2026-0730-0150 计划阶段二 T7）；每次 showEvent 清缓存强制刷新一次
        #: （review 修复：GUI 线程卡顿）
        self._models_cache: dict[str, list[str]] = {}
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
        self.apply_font_size()

    def _build_chrome(self) -> None:
        """骨架装配：页标题区 + 导航分隔线 + 中文关闭按钮（1510 计划 P0）。

        页标题字号不在此设定——由 apply_font_size() 按 app 字号相对派生
        （构造末尾统一调用，随 MainWindow._apply_font_size 链重刷）。
        """
        self._title_label = QLabel(self)
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

    def apply_font_size(self) -> None:
        """字号收敛点（MainWindow._apply_font_size 链，同 apply_theme 先例）。

        两处相对派生自 app 全局字号（禁止绝对值写死，否则不随用户字号
        缩放，全局调大后标题反而比正文小）：
        - 页标题 = app 字号 + _TITLE_FONT_DELTA_PT（加粗）；
        - 权限页黑名单等宽区 = app 字号（构造时 QFont(family) 只设族，
          字号是创建时刻的快照，不随后续全局字号调整跟随）。
        """
        app = QApplication.instance()
        if app is None:
            return
        base = app.font().pointSizeF()
        title_font = self._title_label.font()
        title_font.setPointSizeF(base + _TITLE_FONT_DELTA_PT)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        mono_font = QFont(get_mono_family())
        mono_font.setPointSizeF(base)
        self._blacklist_text.setFont(mono_font)

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
    # AI 模型页（三级下拉：后台/接口/模型；与 ModelBar 经 MainWindow 收敛点同步，
    # 2026-0730-0150 计划阶段二 T7）
    # ------------------------------------------------------------------
    def _build_model_page(self) -> QWidget:
        """三级语义（D1）：后台 = CLI 产品（vendor_label）；接口 = 该后台下的
        接入实现（BackendSpec.name/label，持久化值）；模型 = 模型别名
        （接口级 spec.list_models()）。不可用项行级禁用并标「（未检测到）」
        （禁用项不可被用户选中，也不作静默回退落点——杜绝无效项收敛写盘）。"""
        page = QWidget(self)
        layout = QFormLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._vendor_combo = QComboBox(page)
        for vendor, specs in vendor_groups().items():
            available = any(spec.available() for spec in specs)
            text = specs[0].vendor_label if available else f"{specs[0].vendor_label}（未检测到）"
            self._vendor_combo.addItem(text, vendor)
            if not available:
                self._vendor_combo.model().item(
                    self._vendor_combo.count() - 1).setEnabled(False)
        self._interface_combo = QComboBox(page)  # 初为空，reload 时按后台重建
        self._model_combo = QComboBox(page)      # 初为空，reload 时按接口重建
        for combo in (self._vendor_combo, self._interface_combo, self._model_combo):
            make_translucent_combo_popup(combo)
        layout.addRow("后台", self._vendor_combo)
        layout.addRow("接口", self._interface_combo)
        layout.addRow("模型", self._model_combo)
        layout.addRow(self._make_hint("AI 响应中暂不可切换。", page))
        self._vendor_combo.activated.connect(self._on_vendor_activated)
        self._interface_combo.activated.connect(self._on_interface_activated)
        self._model_combo.activated.connect(self._on_model_activated)
        return page

    def _on_vendor_activated(self, index: int) -> None:
        """后台切换：接口下拉先清后建（回退首个可用接口）→ 以模型 None
        收敛 MainWindow（落到该接口模型列表首项；reload 回显三级勾选）。"""
        self._rebuild_interfaces(self._vendor_combo.itemData(index))
        self._ctx.apply_model_selection(self._interface_combo.currentData(), None)
        self._ctx.statusBar().showMessage(
            f"AI 模型：后台 → {self._vendor_combo.itemText(index)}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

    def _on_interface_activated(self, index: int) -> None:
        """接口切换：以模型 None 收敛（落到模型列表首项，D6 红线 4：
        旧接口别名立即失效、不随切换残留/写盘，红线 5）。"""
        self._ctx.apply_model_selection(self._interface_combo.itemData(index), None)
        self._ctx.statusBar().showMessage(
            f"AI 模型：接口 → {self._interface_combo.itemText(index)}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

    def _on_model_activated(self, index: int) -> None:
        """模型切换：以（当前接口, 别名）收敛（别名按不透明字符串透传，红线 2）。"""
        self._ctx.apply_model_selection(
            self._interface_combo.currentData(), self._model_combo.itemData(index))
        self._ctx.statusBar().showMessage(
            f"AI 模型：模型 → {self._model_combo.itemText(index)}",
            self._ctx.STATUS_MSG_TIMEOUT_MS)

    def set_model_enabled(self, enabled: bool) -> None:
        """busy 联动：AI 响应中禁用三下拉（与 ModelBar 三按钮对齐）。"""
        on = enabled and self._any_backend_available
        self._vendor_combo.setEnabled(on)
        self._interface_combo.setEnabled(on)
        self._model_combo.setEnabled(on)

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
        self._theme_combo.activated.connect(self._on_theme_activated)
        self._font_spin.valueChanged.connect(self._on_font_changed)
        return page

    def _on_theme_activated(self, index: int) -> None:
        # 状态栏反馈由 MainWindow.switch_theme 统一发（「已切换为xx主题」），不重复
        self._ctx.switch_theme(self._theme_combo.itemData(index))

    def _on_font_changed(self, value: int) -> None:
        if not self._reloading:
            self._ctx.set_font_size(value)
            self._ctx.statusBar().showMessage(
                f"外观：字号 → {value} pt", self._ctx.STATUS_MSG_TIMEOUT_MS)

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
        """外观页：主题 / 字号回读。"""
        theme_index = self._theme_combo.findData(settings[KEY_THEME])
        self._theme_combo.setCurrentIndex(max(theme_index, 0))
        self._font_spin.setValue(settings[KEY_FONT_SIZE])

    def _reload_terminal(self, settings) -> None:
        self._swap_check.setChecked(settings[KEY_TERMINAL_SWAP_COPY_PASTE])

    def _reload_model(self, settings) -> None:
        """模型页三级回显：后台（vendor 由 backend 经注册表推导，D2 不读
        settings）→ 接口列表重建并定位 backend → 模型列表（缓存优先）定位
        持久化别名；每级失效静默回退该级首个可用项。

        模型列表按接口级缓存：spec.list_models 可能是子进程调用，缓存命中
        时同步填充；缓存 miss（showEvent 清缓存后 / 接口切换）改投
        事件循环空闲异步拉取——showEvent 在 GUI 线程同步执行子进程会
        卡住窗口首帧绘制，呈现「先闪一个小窗口再出完整窗口」的中间态；
        窗口显示定型后填充，视觉闪动消除。
        """
        backend = settings[KEY_MODEL_BACKEND]
        # 一级：后台（持久化不存后台键，由接口实现名推导）
        vendor_index = self._vendor_combo.findData(vendor_of(backend))
        if vendor_index < 0 or not self._vendor_combo.model().item(vendor_index).isEnabled():
            vendor_index = self._first_enabled_index(self._vendor_combo)
        self._vendor_combo.setCurrentIndex(max(vendor_index, 0))
        # 二级：接口（先清后建后定位；失效回退首个可用接口）
        self._rebuild_interfaces(self._vendor_combo.currentData())
        backend_index = self._interface_combo.findData(backend)
        if backend_index < 0 or not self._interface_combo.model().item(backend_index).isEnabled():
            backend_index = self._first_enabled_index(self._interface_combo)
        self._interface_combo.setCurrentIndex(max(backend_index, 0))
        # 三级：模型（缓存命中同步填，miss 异步拉取；回显值取该接口
        # 记忆表条目，无记忆 = None → 落列表首项，2026-0731-0052 计划 D4）
        current_backend = self._interface_combo.currentData()
        remembered = settings[KEY_MODEL_VERSIONS].get(current_backend or "")
        if current_backend in self._models_cache:
            self._fill_models(current_backend, remembered)
        else:
            QTimer.singleShot(0, lambda: self._load_and_fill_models(
                current_backend, remembered))

    def _rebuild_interfaces(self, vendor: str | None) -> None:
        """接口下拉按后台重建（先清后建，D6 红线 4）：仅列该后台的 spec；
        不可用接口行级禁用并标注；默认定位首个可用项。"""
        self._interface_combo.clear()
        for spec in vendor_groups().get(vendor or "", []):
            available = spec.available()
            text = spec.label if available else f"{spec.label}（未检测到）"
            self._interface_combo.addItem(text, spec.name)
            if not available:
                self._interface_combo.model().item(
                    self._interface_combo.count() - 1).setEnabled(False)
        index = self._first_enabled_index(self._interface_combo)
        self._interface_combo.setCurrentIndex(max(index, 0))

    def _load_and_fill_models(self, backend: str, version: str | None) -> None:
        """异步拉取并填充模型列表（缓存 miss 路径）：子进程调用挪出 show
        同步路径后的实际执行点；全程置 _reloading 与同步路径语义对齐。"""
        self._reloading = True
        try:
            if backend not in self._models_cache:
                spec = spec_of(backend or "")
                self._models_cache[backend] = (
                    spec.list_models() if spec is not None and spec.available() else [])
            self._fill_models(backend, version)
        finally:
            self._reloading = False

    def _fill_models(self, backend: str, version: str | None) -> None:
        """模型下拉框填充（缓存已就绪）：清空 → 逐别名灌入 → 定位持久化别名
        （失效回退首项；只调接口级缓存，无跨后台共享模型表，D6 红线 1）。"""
        self._model_combo.clear()
        for alias in self._models_cache.get(backend, []):
            self._model_combo.addItem(alias, alias)
        version_index = self._model_combo.findData(version)
        if self._model_combo.count():
            self._model_combo.setCurrentIndex(max(version_index, 0))

    @staticmethod
    def _first_enabled_index(combo: QComboBox) -> int:
        """首个可用项索引（禁用项不作回退落点）；全部禁用返回 -1。"""
        model = combo.model()
        for i in range(combo.count()):
            if model.item(i).isEnabled():
                return i
        return -1

    # ------------------------------------------------------------------
    # 显示：重载控件态 + busy 联动初值
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._models_cache.clear()  # 每次打开强制刷新一次模型列表
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
