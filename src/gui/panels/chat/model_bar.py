"""模型选择：后台 + 接口 + 模型三按钮（各标签输入区底行左端）。

选择持久化（2026-07-19，见 文档/修改记录/2026-0719-0712_GUI窗口状态
与模型选择持久化计划.md）：启动时 set_selection 恢复上次选择（无效项
静默回退默认），用户主动切换即时写盘。

左栏宽度根治（2026-07-24，文档/修改记录/2026-0724-2305 计划 T1/T4）：
- 双下拉 minimumSizeHint 与「最长条目文本宽度」脱钩
- 停止按钮移除：busy 显隐曾使模型行最小宽度跳变触发 QSplitter 撑宽
  左栏；停止改归输入区底行右端的发送/停止双态按钮（panel.py）

下移底行与瘦身（2026-07-25，文档/修改记录/2026-0724-2354 计划）：
- 从 ChatTabs 顶部全局单例改为每 ChatPanel 底行实例（纯视图组件），
  本组件只管 UI 与发射 selection_changed；
  选择状态曾上移 ChatTabs 广播同步，2026-0803-0112 计划翻案为
  **每标签自持**（本组件即有效值唯一来源，写「最近使用值」与新建
  注入值归 ChatTabs）
- 迭代 2：双 QComboBox 换 QToolButton(InstantPopup)+QMenu——框体恒显
  标签（用户拍板选项 1：切模型低频，当前值归 tooltip 全名与菜单 ✓
  勾选）；菜单天然按内容加宽显示全文，创建经 make_translucent_popup()
  规约处理（见 gui/popups.py）

三级选择语义（2026-07-30，文档/修改记录/2026-0730-0150 计划阶段二 T5/T6）：
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

当前值直显与文本精简（2026-07-30，文档/修改记录/2026-0730-1143 计划）：
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

模型记忆持久化（2026-07-31，文档/修改记录/2026-0731-0052 计划）：
- 每接口各自记住用户显式选定的模型（settings model_versions 记忆表，
  接口实现名 → 别名）；切回时恢复记忆值，未定制接口落列表首项
- 信号载荷语义（D3）：切后台/接口 emit (backend, None)——None =
  "用户未指定模型"，记忆/首项解析归 ChatTabs；点模型 emit
  (backend, 别名)——str = 显式选定，状态层写记忆
- 本组件边界不变：不读 settings、不管写盘（持久化写盘上移 ChatTabs）

四级「推理强度」（2026-08-06 一期静态声明；0455 动态化计划翻案为
**模型级云端动态化**）：
- 三按钮扩为四按钮：「后台 → 接口 → 模型 → 推理强度」；第四级数据源
  = resolve_efforts(spec, 当前模型别名)——模型级 list_efforts（kimi
  provider list --json 的 supportEfforts / kilo models --verbose 的
  variants keys）优先，空 dict 回退接口级静态兜底 BackendSpec.efforts；
  非空 dict 查无该模型 = 该模型无强度轴（按钮禁用标注、回退层级标签
  「推理强度」）；强度值协议原样透传（不透明字符串红线同款语义），
  configId 归各 provider 的 set_effort 自持
- 重建挂**模型级**（0455 计划 D4）：切接口与切模型均触发重建
  （_refresh_models 尾部 + _on_model_picked 联动）；切模型后勾选解析
  优先级：记忆值（校验值域，失效静默落默认不写盘）→ 模型默认档 →
  接口级 default_effort → 首项
- 独立信号 effort_changed(str, str)（backend, 强度值）——只承载用户
  显式选择；记忆表解析/写盘/provider 应用归 ChatTabs（model_efforts
  记忆表与 model_versions 同构），selection_changed 签名不破
- 未定制语义：无记忆时勾选默认档（纯 UI 呈现，与 agent 默认一致），
  不下发 set_config_option；切接口/模型时第四级联动重建（先清后建 +
  回退默认项，D6 红线 4 同款）
"""
from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from gui.popups import make_translucent_popup
from llm import resolve_efforts, spec_of, vendor_groups, vendor_of


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
    """输入区底行左端：后台 + 接口 + 模型 + 推理强度四按钮（InstantPopup
    菜单四级联动）。

    数据源为注册表（llm.registry）：一级菜单 = vendor_groups() 的后台
    分组，二级菜单 = 当前后台的 BackendSpec 列表，三级菜单 = 当前接口的
    spec.list_models()，四级菜单 = resolve_efforts(spec, 当前模型别名)
    （0455 动态化计划：模型级 list_efforts 优先、空 dict 回退接口级
    spec.efforts 静态兜底；无轴 = 不支持，按钮禁用标注）。某后台下全部
    接口 available() 为 False 时该后台项禁用并标「（未检测到）」（沿用
    kimi 未安装态范式）；单个接口不可用同理禁用标注。
    """

    #: 接口/模型切换（携带 registry 接口实现名 + 模型别名载荷：
    #: 签名保持 (str, object) 不破——后台由 backend 经 vendor_of 推导，
    #: tabs/panel/主窗口接线不改，见 2026-0730-0150 计划 T6）。
    #: 载荷语义（2026-0731-0052 计划 D3）：切后台/接口路径 emit
    #: (backend, None)——None = "用户未指定模型"，由状态层查记忆/
    #: 落首项解析；点模型路径 emit (backend, 别名)——str = 用户显式
    #: 选定，状态层写入记忆表
    selection_changed = Signal(str, object)

    #: 推理强度显式选择（2026-0806 计划）：(backend, 强度值)——只承载
    #: 用户点击；记忆表写盘与 provider 应用归 ChatTabs/ChatPanel
    effort_changed = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._vendor_button = self._make_button("后台")
        self._interface_button = self._make_button("接口")
        self._model_button = self._make_button("模型")
        self._effort_button = self._make_button("推理强度")
        self._vendor_group = QActionGroup(self)  # 默认互斥
        self._interface_group = QActionGroup(self)
        self._model_group = QActionGroup(self)
        self._effort_group = QActionGroup(self)
        #: 当前接口/模型菜单对应的数据源（后台名/接口名；None = 未建过）。
        #: 用于 set_selection 跳过「数据未变」的无谓重建（计划
        #: 2026-0730-2338 D3）：重建由 _refresh_interfaces/_refresh_models
        #: 同步改写，先清后建语义不变
        self._interfaces_vendor: str | None = None
        self._models_backend: str | None = None
        #: 第四级数据源接口名/模型别名与能力位（0455 动态化计划：重建
        #: 挂模型级——(backend, model) 二元组不变则跳过无谓重建，与
        #: _interfaces_vendor/_models_backend 同款 D3 语义）；
        #: _effort_supported = False 时按钮禁用（busy 恢复不得误启用，
        #: 见 set_busy）
        self._efforts_backend: str | None = None
        self._efforts_model: str | None = None
        self._effort_supported: bool = False

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
        layout.addWidget(self._effort_button)
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
        """四级当前值全名写入四个按钮 tooltip（同一四行文本，悬停任一
        按钮可见完整链：后台 / 接口 / 模型 / 推理强度）。"""
        vendor = self._vendor_group.checkedAction()
        interface = self._interface_group.checkedAction()
        model = self._model_group.checkedAction()
        effort = self._effort_group.checkedAction()
        if effort is not None:
            effort_line = f"推理强度：{effort.text()}"
        else:
            effort_line = "推理强度：当前接口不支持"
        tooltip = "\n".join((
            f"后台：{vendor.text() if vendor else '无'}",
            f"接口：{interface.text() if interface else '无'}",
            f"模型：{model.text() if model else '无'}",
            effort_line,
        ))
        for button in (self._vendor_button, self._interface_button,
                       self._model_button, self._effort_button):
            button.setToolTip(tooltip)

    def _refresh_button_texts(self) -> None:
        """四按钮直显当前值短文本，宽度贴合文本（用户拍板口径）。

        文本规则（D1/D4-A）：后台原文；接口剥 vendor 前缀；模型取 '/' 末段；
        强度原文（协议值本身即短文本，2026-0806 计划）；无勾选项时回退
        层级标签（空态不显示空串，T4）。
        宽度规则：各按钮 setFixedWidth(当前短文本宽 + 样式余量)——随选择
        变化即时贴合（短文本精简后差异小，取舍见模块 docstring）。
        与 _refresh_tooltips 同点调用、同生命周期。
        """
        vendor_action = self._vendor_group.checkedAction()
        vendor_label = vendor_action.text() if vendor_action is not None else ""
        interface_action = self._interface_group.checkedAction()
        model_action = self._model_group.checkedAction()
        effort_action = self._effort_group.checkedAction()

        vendor_text = vendor_label or "后台"
        interface_text = (
            short_interface_label(interface_action.text(), vendor_label)
            if interface_action is not None else "接口")
        model_text = (
            short_model_alias(model_action.text())
            if model_action is not None else "模型")
        effort_text = (
            effort_action.text()
            if effort_action is not None else "推理强度")

        for button, text in ((self._vendor_button, vendor_text),
                             (self._interface_button, interface_text),
                             (self._model_button, model_text),
                             (self._effort_button, effort_text)):
            button.setText(f"{text} ▾")
            # 样式余量实测 37~38px（qss padding 12*2 + 边框/内容区余量，
            # base.qss #chatModelButton 唯一出处、四主题不覆写，取整 40）
            width = button.fontMetrics().horizontalAdvance(f"{text} ▾") + 40
            button.setFixedWidth(width)

    # ------------------------------------------------------------------
    # 忙碌态（流式响应中）
    # ------------------------------------------------------------------
    def set_busy(self, is_busy: bool) -> None:
        """busy：禁用四按钮（各标签独立粒度，本标签响应中即禁本标签，
        2026-0803-0112 计划 D4——原「任一忙禁全部」随广播废除失去依据）。

        强度按钮叠加能力闸（2026-0806 计划）：接口不支持强度轴时
        busy 恢复不得误启用。
        按钮宽度贴合当前文本且 busy 不改文本，sizeHint 不随 busy 变化。
        """
        self._vendor_button.setEnabled(not is_busy)
        self._interface_button.setEnabled(not is_busy)
        self._model_button.setEnabled(not is_busy)
        self._effort_button.setEnabled(self._effort_supported and not is_busy)

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

    def current_effort(self) -> str | None:
        """当前推理强度（协议值）；接口不支持强度轴（菜单为空）时为 None。"""
        checked = self._effort_group.checkedAction()
        return checked.data() if checked is not None else None

    def set_effort_selection(self, effort: str | None) -> None:
        """注入强度选择：静默勾选，不发信号（与 set_selection 同款阻断
        语义）。

        None = 未定制 → 勾选默认档（0455 动态化计划 D4 解析链：模型级
        默认档 → 接口级 default_effort → 首项）——纯 UI 呈现勾选，与
        agent 默认强度一致，不代表已下发 set_config_option；记忆值失效
        （∉ 当前模型档位列表，如跨模型切换后旧档位不支持）→ 静默落默认
        档（红线 5：回退不写盘，本组件不管写盘）。
        """
        backend = self.current_backend()
        model = self.current_version()
        if (backend, model) != (self._efforts_backend, self._efforts_model):
            self._refresh_efforts(backend, model)
        target = self._find_action(self._effort_group, effort) if effort else None
        if target is None and self._effort_group.actions():
            spec = spec_of(backend or "")
            if spec is not None:
                _efforts, default = resolve_efforts(spec, model)
                target = self._find_action(self._effort_group, default)
        if target is None:
            target = self._first_enabled(self._effort_group)
        if target is not None:
            target.setChecked(True)
        self._refresh_tooltips()
        self._refresh_button_texts()

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
        # 四级：推理强度随接口+模型联动（0455 动态化计划 D4：重建挂模型
        # 级——接口或模型已变时重建并勾选默认档；纯 UI 呈现，未定制语义
        # 见 set_effort_selection）
        if (backend_effective, self.current_version()) != (
                self._efforts_backend, self._efforts_model):
            self._refresh_efforts(backend_effective, self.current_version())
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
        # 四级随接口+模型联动重建（0455 动态化计划 D4：重建后当前模型
        # 已落首项，以其为数据源）
        self._refresh_efforts(backend, self.current_version())

    def _refresh_efforts(self, backend: str | None, model: str | None) -> None:
        """推理强度菜单按接口+模型重建（0455 动态化计划 D4，先清后建）：
        数据源 = resolve_efforts(spec, model)——模型级 list_efforts 非空
        dict 时查模型别名（hit = 模型级档位；miss = 该模型无强度轴，不做
        静态兜底，D1），空 dict 回退接口级 spec.efforts 静态兜底。

        空值域（该模型/接口无强度轴）→ 按钮禁用、无勾选项（按钮文本回退
        层级标签「推理强度」，tooltip 标注不支持）；非空 → 重建后勾选默认
        档（模型级默认档 → 接口级 default_effort → 首项，静默）——纯 UI
        呈现勾选，用户未显式选择前不下发 set_config_option。
        """
        menu = self._effort_button.menu()
        for action in list(self._effort_group.actions()):
            self._effort_group.removeAction(action)
        menu.clear()
        spec = spec_of(backend or "")
        efforts: tuple[str, ...] = ()
        default: str | None = None
        if spec is not None and spec.available():
            efforts, default = resolve_efforts(spec, model)
        for value in efforts:
            self._add_action(
                self._effort_button, self._effort_group,
                value, value, self._on_effort_picked)
        self._effort_supported = bool(efforts)
        self._effort_button.setEnabled(self._effort_supported)
        if efforts:
            if default not in efforts:
                # 模型级默认缺失（kilo 目录无默认字段）→ 接口级兜底 → 首项
                default = spec.default_effort if spec is not None else None
            target = self._find_action(self._effort_group, default)
            if target is None:
                target = self._effort_group.actions()[0]
            target.setChecked(True)
        self._efforts_backend = backend
        self._efforts_model = model

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
        """用户勾选模型 → 第四级按新模型联动重建（0455 动态化计划 D4：
        档位是模型级的，切模型即重建强度菜单）→ 发射切换（写盘与广播
        归 ChatTabs 单一来源）。"""
        self._refresh_efforts(self.current_backend(), alias)
        self._refresh_tooltips()
        self._refresh_button_texts()
        self.selection_changed.emit(self.current_backend(), alias)

    def _on_effort_picked(self, value: str) -> None:
        """用户勾选推理强度 → 发射 effort_changed（2026-0806 计划：
        记忆表写盘与 provider 应用归 ChatTabs/ChatPanel，本组件只管
        UI 与信号）。"""
        self._refresh_tooltips()
        self._refresh_button_texts()
        self.effort_changed.emit(self.current_backend(), value)
