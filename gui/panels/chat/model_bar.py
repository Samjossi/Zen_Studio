"""模型选择：后台 + 接口 + 模型三按钮（各标签输入区底行左端）。

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
  标签（用户拍板选项 1：切模型低频，当前值归 tooltip 全名与菜单 ✓
  勾选）；菜单天然按内容加宽显示全文，创建经 make_translucent_popup()
  规约处理（见 gui/popups.py）

三级选择语义（2026-07-30，work plans/2026-0730-0150 计划阶段二 T5/T6）：
- 双按钮「模型（实为接入实现）+ 版本（实为模型别名）」扩为三按钮：
  「后台」（CLI 产品/厂商，vendor_groups 一级）→「接口」（该后台下的
  接入实现，BackendSpec.name/label）→「模型」（模型别名，接口级
  spec.list_models()）；原「模型」按钮改名「接口」、原「版本」改名
  「模型」，恒显标签范式不变
- 持久化不新增键（D2）：后台不入 settings，由接口实现名经注册表
  vendor_of(backend) 推导；selection_changed 信号签名保持 (backend,
  version) 不破，tabs/panel/主窗口接线零改动
- D6 红线落实：红线 1 模型目录挂接口级——只调当前接口的
  spec.list_models()，无跨后台共享模型表；红线 2 别名按不透明字符串
  透传（不解析/不切分/不校验）；红线 4 切后台/接口时下级菜单先清后建、
  回退首项，旧后台别名立即失效不残留；红线 5 回退不写盘（本组件本就
  不管写盘，写盘只发生在用户主动切换经 ChatTabs 收敛时）

当前值直显与文本精简（2026-07-30，work plans/2026-0730-1143 计划）：
- 三按钮由恒显层级标签改为直显当前值短文本（D4-A 纯值直显）：
  后台原文；接口剥 vendor 前缀（short_interface_label，"Kimi ACP"→
  "ACP"，不匹配前缀回退原文）；模型别名取 '/' 末段（short_model_alias，
  "kimi-code/k3-256k"→"k3-256k"，无 '/' 回退全文）——截取仅发生在
  UI 呈现层，action data/信号载荷/持久化值全程仍是全名（D6 红线 2 不破）
- 宽度策略（用户拍板 2026-07-30，覆盖计划 D2-A 恒宽稿）：按钮宽度
  **贴合当前选中文本**（文本宽 + 样式余量 setFixedWidth），不做菜单最大
  项恒宽、不设上限——短文本精简后各选项长度差异小，宽度随选择小幅
  变化是可接受的即时反馈；QSplitter 不自动回缩，长选项顶宽左栏后
  切回短选项需手动拖回（取舍明示）
- 菜单项保持全文 + ✓ 勾选（D3，防不同 provider 同后段名歧义）；
  tooltip 三行全名链不变（悬停仍可见完整信息）；无勾选项时按钮回退
  层级标签「后台/接口/模型」（空态不显示空串）

模型记忆持久化（2026-07-31，work plans/2026-0731-0052 计划）：
- 每接口各自记住用户显式选定的模型（settings model_versions 记忆表，
  接口实现名 → 别名）；切回时恢复记忆值，未定制接口落列表首项
- 信号载荷语义（D3）：切后台/接口 emit (backend, None)——None =
  "用户未指定模型"，记忆/首项解析归 ChatTabs；点模型 emit
  (backend, 别名)——str = 显式选定，状态层写记忆
- 本组件边界不变：不读 settings、不管写盘（持久化写盘上移 ChatTabs）
"""
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from gui.popups import make_translucent_popup
from llm import spec_of, vendor_groups, vendor_of


def short_interface_label(spec_label: str, vendor_label: str) -> str:
    """接口显示名剥后台前缀："Kimi CLI" - "Kimi" → "CLI"。

    不匹配前缀（或剥后为空）时回退原文——注册项 label 无强制命名公约，
    兜底防误判（2026-0730-1143 计划 D1）。仅用于 UI 呈现，不碰数据链路。
    """
    prefix = vendor_label.strip()
    if prefix and spec_label.startswith(prefix):
        short = spec_label[len(prefix):].strip()
        if short:
            return short
    return spec_label


def short_model_alias(alias: str) -> str:
    """模型别名取 '/' 末段："kimi-code/k3-256k" → "k3-256k"。

    多层 '/' 取最末段（rsplit）；无 '/' 回退全文（D1）。仅用于 UI 呈现。
    """
    return alias.rsplit("/", 1)[-1]


class ModelBar(QWidget):
    """输入区底行左端：后台 + 接口 + 模型三按钮（InstantPopup 菜单三级联动）。

    数据源为注册表（llm.registry）：一级菜单 = vendor_groups() 的后台
    分组，二级菜单 = 当前后台的 BackendSpec 列表，三级菜单 = 当前接口的
    spec.list_models()。某后台下全部接口 available() 为 False 时该后台
    项禁用并标「（未检测到）」（沿用 kimi 未安装态范式）；单个接口不可用
    同理禁用标注。
    """

    #: 接口/模型切换（携带 registry 接口实现名 + 模型别名载荷：
    #: 签名保持 (str, object) 不破——后台由 backend 经 vendor_of 推导，
    #: tabs/panel/主窗口接线不改，见 2026-0730-0150 计划 T6）。
    #: 载荷语义（2026-0731-0052 计划 D3）：切后台/接口路径 emit
    #: (backend, None)——None = "用户未指定模型"，由状态层查记忆/
    #: 落首项解析；点模型路径 emit (backend, 别名)——str = 用户显式
    #: 选定，状态层写入记忆表
    selection_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._vendor_button = self._make_button("后台")
        self._interface_button = self._make_button("接口")
        self._model_button = self._make_button("模型")
        self._vendor_group = QActionGroup(self)  # 默认互斥
        self._interface_group = QActionGroup(self)
        self._model_group = QActionGroup(self)
        #: 当前接口/模型菜单对应的数据源（后台名/接口名；None = 未建过）。
        #: 用于 set_selection 跳过「数据未变」的无谓重建（计划
        #: 2026-0730-2338 D3）：重建由 _refresh_interfaces/_refresh_models
        #: 同步改写，先清后建语义不变
        self._interfaces_vendor: str | None = None
        self._models_backend: str | None = None

        # 一级菜单：后台分组（注册序即菜单序）；整组不可用 → 禁用 + 标注
        for vendor, specs in vendor_groups().items():
            label = specs[0].vendor_label
            if any(spec.available() for spec in specs):
                self._add_action(
                    self._vendor_button, self._vendor_group,
                    label, vendor, self._on_vendor_picked)
            else:
                self._add_action(
                    self._vendor_button, self._vendor_group,
                    f"{label}（未检测到）", vendor, None).setEnabled(False)
        # 构造默认勾选链：第一个可用后台 → 其第一个可用接口 → 模型首项
        # （静默：setChecked 不发 triggered，见 set_selection 设计注）
        default_vendor = self._first_enabled(self._vendor_group)
        if default_vendor is not None:
            default_vendor.setChecked(True)
        self._refresh_interfaces(self.current_vendor())
        self._refresh_tooltips()
        self._refresh_button_texts()

        layout = QHBoxLayout(self)
        layout.addWidget(self._vendor_button)
        layout.addWidget(self._interface_button)
        layout.addWidget(self._model_button)
        # 边距归零：底行装配（按钮/stretch/间距）归 ChatPanel 统一分配
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # 控件构造（按钮 + 透明化菜单 + 互斥勾选项）
    # ------------------------------------------------------------------
    def _make_button(self, title: str) -> QToolButton:
        """下拉按钮：文本 = 当前值短文本 + ▾（构造初值为层级标签占位，
        菜单建好后由 _refresh_button_texts() 统一改写）。

        箭头为文本内嵌字形「▾」（与文字同字体同色同基线），Qt 原生
        menu-arrow 由 qss 隐藏（image: none）——原生箭头过小且风格与
        字体不搭（2026-07-25 观感修复第二轮）。
        宽度不在此设定：由 _refresh_button_texts() 按当前短文本贴合
        setFixedWidth（用户拍板口径，见模块 docstring）。
        """
        button = QToolButton(self)
        button.setObjectName("chatModelButton")
        button.setText(f"{title} ▾")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(make_translucent_popup(QMenu(button)))
        return button

    def _add_action(
        self,
        button: QToolButton,
        group: QActionGroup,
        text: str,
        data,
        slot,
    ) -> QAction:
        """互斥勾选项：data 携带后台名/接口实现名/模型别名；
        slot 为 None 时仅挂菜单（禁用项）。"""
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

    @staticmethod
    def _first_enabled(group: QActionGroup) -> QAction | None:
        """组内首个可用项（默认勾选/静默回退目标；禁用项不作回退落点）。"""
        for action in group.actions():
            if action.isEnabled():
                return action
        return None

    def _refresh_tooltips(self) -> None:
        """三级当前值全名写入三个按钮 tooltip（同一三行文本，悬停任一
        按钮可见完整链：后台 / 接口 / 模型）。"""
        vendor = self._vendor_group.checkedAction()
        interface = self._interface_group.checkedAction()
        model = self._model_group.checkedAction()
        tooltip = "\n".join((
            f"后台：{vendor.text() if vendor else '无'}",
            f"接口：{interface.text() if interface else '无'}",
            f"模型：{model.text() if model else '无'}",
        ))
        for button in (self._vendor_button, self._interface_button, self._model_button):
            button.setToolTip(tooltip)

    def _refresh_button_texts(self) -> None:
        """三按钮直显当前值短文本，宽度贴合文本（用户拍板口径）。

        文本规则（D1/D4-A）：后台原文；接口剥 vendor 前缀；模型取 '/' 末段；
        无勾选项时回退层级标签（空态不显示空串，T4）。
        宽度规则：各按钮 setFixedWidth(当前短文本宽 + 样式余量)——随选择
        变化即时贴合（短文本精简后差异小，取舍见模块 docstring）。
        与 _refresh_tooltips 同点调用、同生命周期。
        """
        vendor_action = self._vendor_group.checkedAction()
        vendor_label = vendor_action.text() if vendor_action is not None else ""
        interface_action = self._interface_group.checkedAction()
        model_action = self._model_group.checkedAction()

        vendor_text = vendor_label or "后台"
        interface_text = (
            short_interface_label(interface_action.text(), vendor_label)
            if interface_action is not None else "接口")
        model_text = (
            short_model_alias(model_action.text())
            if model_action is not None else "模型")

        for button, text in ((self._vendor_button, vendor_text),
                             (self._interface_button, interface_text),
                             (self._model_button, model_text)):
            button.setText(f"{text} ▾")
            # 样式余量实测 37~38px（qss padding 12*2 + 边框/内容区余量，
            # base.qss #chatModelButton 唯一出处、四主题不覆写，取整 40）
            width = button.fontMetrics().horizontalAdvance(f"{text} ▾") + 40
            button.setFixedWidth(width)

    # ------------------------------------------------------------------
    # 忙碌态（流式响应中）
    # ------------------------------------------------------------------
    def set_busy(self, is_busy: bool) -> None:
        """busy：禁用三按钮（任一标签响应中即全标签禁用，ChatTabs 遍历调用）。

        按钮宽度贴合当前文本且 busy 不改文本，sizeHint 不随 busy 变化。
        """
        self._vendor_button.setEnabled(not is_busy)
        self._interface_button.setEnabled(not is_busy)
        self._model_button.setEnabled(not is_busy)

    # ------------------------------------------------------------------
    # 选择查询与恢复（持久化写盘上移 ChatTabs，本组件不管）
    # ------------------------------------------------------------------
    def current_vendor(self) -> str | None:
        """当前后台（注册表 vendor 名；一级勾选直接读，与 backend 推导等价）。"""
        checked = self._vendor_group.checkedAction()
        return checked.data() if checked is not None else None

    def current_backend(self) -> str | None:
        """当前接口（registry 接口实现名，settings `model_backend` 持久化值）。"""
        checked = self._interface_group.checkedAction()
        return checked.data() if checked is not None else None

    def current_version(self) -> str | None:
        """当前模型（模型别名）；模型列表为空时为 None。"""
        checked = self._model_group.checkedAction()
        return checked.data() if checked is not None else None

    def set_selection(self, backend: str | None, version: str | None) -> None:
        """注入选择：后台（由 backend 推导）→ 接口 → 模型逐级勾选。

        阻断语义天然成立：QAction.setChecked 不发射 triggered（仅用户
        点击发射），广播同步天然无回环；任一级失效时静默回退到该级首个
        可用项（持久化恢复路径 settings 只存 backend+version 天然工作，
        D2：后台由 vendor_of(backend) 推导，不读 settings）。
        """
        # 一级：后台（接口实现名未知时 vendor_of 返回 None → 回退首项）
        vtarget = self._find_action(self._vendor_group, vendor_of(backend or ""))
        if vtarget is None or not vtarget.isEnabled():
            vtarget = self._first_enabled(self._vendor_group)
        if vtarget is not None:
            vtarget.setChecked(True)
        # 二级：接口（列表先清后建后勾选；失效回退首个可用接口）。
        # 后台未变且菜单已建则跳过重建（数据未变，计划 2026-0730-2338 D3）
        vendor = self.current_vendor()
        if (vendor != self._interfaces_vendor
                or not self._interface_group.actions()):
            self._refresh_interfaces(vendor)
        itarget = self._find_action(self._interface_group, backend)
        if itarget is None or not itarget.isEnabled():
            itarget = self._first_enabled(self._interface_group)
        if itarget is not None:
            itarget.setChecked(True)
        # 三级：模型（列表先清后建后勾选；失效回退首项——旧后台别名
        # 立即失效不得残留，D6 红线 4；回退不落盘，红线 5）。
        # 接口未变且菜单已建则跳过重建（同上 D3）
        backend_effective = self.current_backend()
        if (backend_effective != self._models_backend
                or not self._model_group.actions()):
            self._refresh_models(backend_effective)
        mtarget = self._find_action(self._model_group, version)
        if mtarget is None and self._model_group.actions():
            mtarget = self._model_group.actions()[0]
        if mtarget is not None:
            mtarget.setChecked(True)
        self._refresh_tooltips()
        self._refresh_button_texts()

    # ------------------------------------------------------------------
    # 三级联动（D6 红线 4：切换先清后建 + 回退首项，无残留）
    # ------------------------------------------------------------------
    def _refresh_interfaces(self, vendor: str | None) -> None:
        """接口菜单按后台重建（先清后建）：仅列该后台的 spec；不可用接口
        禁用并标「（未检测到）」（不可用即不可勾选，杜绝无效项落点）；
        重建后默认勾选首个可用接口（静默），并联动模型列表重建。"""
        menu = self._interface_button.menu()
        for action in list(self._interface_group.actions()):
            self._interface_group.removeAction(action)
        menu.clear()
        for spec in vendor_groups().get(vendor or "", []):
            if spec.available():
                self._add_action(
                    self._interface_button, self._interface_group,
                    spec.label, spec.name, self._on_interface_picked)
            else:
                self._add_action(
                    self._interface_button, self._interface_group,
                    f"{spec.label}（未检测到）", spec.name, None).setEnabled(False)
        default = self._first_enabled(self._interface_group)
        if default is not None:
            default.setChecked(True)
        self._interfaces_vendor = vendor
        self._refresh_models(self.current_backend())

    def _refresh_models(self, backend: str | None) -> None:
        """模型菜单按接口重建（先清后建）：只调该接口的 spec.list_models()
        （模型目录挂接口级，D6 红线 1；别名按不透明字符串透传，红线 2）；
        重建后默认勾选首项（静默）。"""
        menu = self._model_button.menu()
        for action in list(self._model_group.actions()):
            self._model_group.removeAction(action)
        menu.clear()
        spec = spec_of(backend or "")
        if spec is not None and spec.available():
            for alias in spec.list_models():
                self._add_action(
                    self._model_button, self._model_group,
                    alias, alias, self._on_model_picked)
        if self._model_group.actions():
            self._model_group.actions()[0].setChecked(True)
        self._models_backend = backend

    def _on_vendor_picked(self, vendor: str) -> None:
        """用户勾选后台 → 接口/模型列表联动重建（各回退首项作即时显示）
        → 以 (backend, None) 发射切换（None = 未指定模型，记忆/首项
        解析归 ChatTabs，D3）。"""
        self._refresh_interfaces(vendor)
        self._refresh_tooltips()
        self._refresh_button_texts()
        self.selection_changed.emit(self.current_backend(), None)

    def _on_interface_picked(self, backend: str) -> None:
        """用户勾选接口 → 模型列表联动重建（回退首项作即时显示）
        → 以 (backend, None) 发射切换（解析归 ChatTabs，D3）。"""
        self._refresh_models(backend)
        self._refresh_tooltips()
        self._refresh_button_texts()
        self.selection_changed.emit(backend, None)

    def _on_model_picked(self, alias: str) -> None:
        """用户勾选模型 → 发射切换（写盘与广播归 ChatTabs 单一来源）。"""
        self._refresh_tooltips()
        self._refresh_button_texts()
        self.selection_changed.emit(self.current_backend(), alias)
