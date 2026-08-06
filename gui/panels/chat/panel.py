"""聊天面板装配：上输出 + 下输入，连接 LLM 流式线程。

多标签改造（2026-07-22，文档/修改记录/2026-0722-0756 计划 P3 任务 13）：
- 自持 provider 实例（不再从共享 registry 取单例）：注册表工厂懒实例化
  （2026-0730-0150 计划 D4——实例推迟到首轮对话/切换广播前经
  _get_provider 即建即用，防"标签数 × 后端数"长驻进程膨胀；
  实例建成后标签间完全隔离，D6 方案 A）
- ModelBar 各标签自持：2026-0724-2354 计划由 ChatTabs 顶部全局单例
  改为本面板输入区底行实例（纯视图）；选择状态每标签自持（本面板
  ModelBar 为有效值唯一来源，2026-0803-0112 计划翻案广播语义），
  ChatTabs 只留「新建注入值」；外部切换经 set_model_selection 同步
  本面板 UI（阻断）与 provider
- 审批请求统一提交全局审批队列（PERMISSION_QUEUE），多标签串行弹窗

AI 活动信息路由（2026-07-31，文档/修改记录/2026-0731-1602 计划 T6）：
- _on_chunk 新增 tool_call / tool_call_update / todo 三分支——只上屏，
  不入 _stream_buffer/_history（与 reasoning 同约束，防历史污染回传）；
  toolCallId→title 簿记补全状态行标题；轮次收尾 reset_activity_anchors
  作废 todo 锚点（T5-4 防跨轮串位）

对话区双轨渲染（2026-08-03，文档/修改记录/2026-0803-0645 融合计划 D2-A）：
- 构造时按 chat_renderer 设置选轨：cards = ChatTranscriptView 卡片折叠轨
  （新，默认；QScrollArea + 块级 QWidget，KiloCode 式卡片）；classic =
  ChatOutput 旧轨（冻结保留即回退通道）；存量标签不热切换
- 新轨路由差异：取消非 execute 工具的 output 剥除（全工具卡 body 承接，
  §2.3-2）；in_progress 尾滚帧仅 execute + 200ms 节流（T8）；旧轨维持
  剥除纪律且 execute 输出钳回末 20 行（1836 L2-3 规格，协议层 0645 起
  放宽至软上限 1000 行，旧轨渲染规格由路由层钳制不变）

会话活动时间线色块条（2026-07-31，文档/修改记录/2026-0731-1824 计划 T3；
挂载点 2242 计划方案 F 迁移）：
- ActivityTimeline 细条与上下文用量徽章同行组成状态行（左条右徽），
  置输入区内输入框上方——随 splitter 拖拽移动，视觉恒在输出/输入分界
  （原 1824 D2 卡片底部挂载废止；不进 splitter 的约束不变）；
  _on_chunk 入口旁路分接 feed（不改动既有分支语义）；_on_finished/
  _on_stopped 随 reset_activity_anchors 同位置 end_turn 作废段指针；
  切后端新会话清条

关闭异步化（2026-07-22，文档/修改记录/2026-0722-1117 计划 P2）：
- close() 两段式：GUI 段毫秒级（request_stop + 断信号 + 起 daemon
  清理线程），terminate/wait/deleteLater 全在线程段——GUI 零冻结
- 线程段顺序先 terminate 后 wait：杀 acp 进程注入死讯解封 worker
  全部阻塞点（治"先干等 3s 才 terminate"的顺序倒置）

左栏宽度根治（2026-07-24，文档/修改记录/2026-0724-2305 计划 T3/T7）：
- 输入区底行新增发送/停止双态按钮：空闲=「发送」（等价 Enter，
  空文本禁用），busy=「■ 停止」（直停本标签）；按钮常驻恒宽，
  任何状态下 sizeHint 不变——替代原 ModelBar 停止按钮的显隐模式
  （busy 显隐改变 sizeHint 触发 QSplitter 撑宽左栏的病根）
- busy 期间 Esc 快捷键中断本标签生成（参考实现同效：theia「取消
  (Esc)」、Multi_Cli_Studio Escape 中断）

插话（排队消息）与停止按钮修复（2026-08-06，work plans/2026-0806-0634 计划）：
- 停止按钮失效修复（D1）：_busy 簿记单点置位（_set_busy），停止钮使能
  只由 _set_busy 管理——消除「busy UI 已立、worker 未建/已销毁」竞态
  窗口内 worker 存在性代理守卫失效的误禁用根因；attachments.py 信号
  精确化（clear() 已空不再空发 changed）双保险
- 插话（D2）：busy 期间输入在 IDE 侧每标签 FIFO 队列排队上屏（上限
  10 条），轮次正常收尾自动出队发送（零协议改动，四后端同构）；
  三态出队（D5）：完成=自动接续 / 失败=保留不自动接续 / 停止=清空
  且文本合并退回输入框草稿
- 底行双常驻恒宽钮（D3）：「发送/插话」双态钮 +「■ 停止」常驻钮
  （仅 busy 可用）；排队气泡 = 用户气泡 + 「排队中」徽标 + ×删（D4，
  新旧双轨同构三方法接口）
"""
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from gui.panels.chat.attachments import AttachmentStrip
from gui.panels.chat.input import ChatInput
from gui.panels.chat.model_bar import ModelBar
from gui.panels.chat.output import ChatOutput
from gui.panels.chat.permission_queue import PERMISSION_QUEUE
from gui.panels.chat.timeline import ActivityTimeline
from gui.panels.chat.transcript import ChatTranscriptView
from gui.panels.chat.worker import ChatWorker
from gui.settings import (
    CHAT_RENDERER_CARDS,
    KEY_CHAT_RENDERER,
    KEY_PERMISSION_MODE,
    KEY_THEME,
)
from gui.theme import get_theme_palette, load_settings
from gui.window_state import decode_state, encode_state
from llm import (
    BACKEND_LABELS,
    Chunk,
    LanguageModel,
    Message,
    PermissionParams,
    UsageStats,
    resolve_efforts,
    spec_of,
)
from llm.permission_policy import DECISION_ALLOW, decide_permission, select_option_id


class ChatPanel(QWidget):
    """单个 AI 会话标签页（独立 provider 实例，由 ChatTabs 托管）。"""

    #: 发送/停止状态变化（ChatTabs 按当前活动标签粒度联动禁用本标签
    #: 三按钮与设置中心模型页，2026-0803-0112 计划 D4）
    busy_changed = Signal(bool)

    #: 一轮对话结束（正常/失败/用户停止均触发；主窗口经 ChatTabs 转发
    #: 联动 Git 状态去抖刷新——ACP 子进程直接写盘不经窗口激活/查看器
    #: 重载，补此事件源闭合，诊断报告 文档/修改记录/2026-0731-1256 方案 A）
    turn_finished = Signal()

    #: 正文文件路径链接点击（1836 计划 L2-5）：载荷 (绝对路径, 行号|None)；
    #: ChatTabs 转发 → 主窗口接查看器 open_file
    file_open_requested = Signal(str, object)

    #: 默认布局尺寸（px）：输出区 / 输入区（初排与 reset_layout 单点来源）。
    #: 输入区 212 = 输入框原可视高度 180 + 底行按钮区约 32（T8 实测补偿，
    #: 2026-0724-2305 计划：发送/停止按钮入底行后保持输入框可视行数不缩水）
    DEFAULT_SPLITTER_SIZES = [550, 212]

    def __init__(
        self,
        backend: str | None,
        version: str | None,
        workspace_root: str,
        parent: QWidget | None = None,
        effort: str | None = None,
    ) -> None:
        """
        :param backend: 初始后端（registry 名；None/失效项经 ModelBar 静默
            回退默认；注入值归 ChatTabs，后续切换见 set_model_selection）
        :param version: 初始模型别名（None = provider 默认模型）
        :param workspace_root: 工作区根（provider cwd 与拖入文件 @相对路径 基准）
        :param parent: 父控件
        :param effort: 初始推理强度（2026-0806 计划；None = 未定制，agent
            默认强度生效；注入值归 ChatTabs 记忆表解析，后续切换见 set_effort）
        """
        super().__init__(parent)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._history: list[Message] = []
        self._worker: ChatWorker | None = None
        #: busy 簿记（0634 计划 D1）：_set_busy 单点置位；
        #: _refresh_send_button 守卫改查本簿记——消除「busy UI 已立、
        #: worker 未建/已销毁」竞态窗口（worker 存在性代理守卫失效导致
        #: 停止按钮被误禁用的根因，§2.1 诊断）
        self._busy = False
        #: 插话队列（0634 计划 D2，每标签 FIFO）：元素 (文本, 附件快照,
        #: 排队气泡 handle)；内存态不持久化，轮次收尾按三态出队（D5）
        self._queue: list[tuple[str, list, object]] = []
        self._stream_buffer = ""
        self._has_seen_reasoning = False
        #: 工具调用 title 簿记（toolCallId → title，1602 计划 T6）：
        #: tool_call_update 帧常缺 title，状态行显示经此簿记补全；
        #: _on_send 时随 _stream_buffer 一并清空
        self._tool_titles: dict[str, str] = {}
        #: execute 工具命令簿记（toolCallId → command，1836 计划 L2-3）：
        #: bash 输出卡 `$ ` 头数据源，兼作 execute 判定键（非 execute 工具
        #: 的输出正文不上屏，防 read/edit 长文刷屏）
        self._tool_commands: dict[str, str] = {}
        #: 本标签最新一轮的上下文用量（usage_update 每轮一条，覆盖即
        #: 「最后一条 assistant 消息」语义）；None = 未收到/已切换后端
        self._usage: UsageStats | None = None
        #: 轮次内用量轮询计时器（0117 计划 T3/D5）：_on_send 启动、
        #: _on_finished/_on_stopped/切后端停止；tick 调 provider.poll_usage()，
        #: 非 kimi 后端默认返回 None 空转成本可忽略（红线：不臆造数值）
        self._usage_timer = QTimer(self)
        self._usage_timer.setInterval(2000)  # 对齐 usage.record 1~3s 写盘节奏
        self._usage_timer.timeout.connect(self._poll_usage_tick)

        chat_pack = get_theme_palette(load_settings()[KEY_THEME])["chat"]
        # 双轨并存（0645 融合计划 D2-A）：cards 卡片折叠轨（新，默认）/
        # classic 旧轨 ChatOutput（冻结保留，即回退通道）；构造时选轨，
        # 存量标签不热切换（设置项 hint 已注明新建标签生效）
        self._cards_track = load_settings()[KEY_CHAT_RENDERER] == CHAT_RENDERER_CARDS
        if self._cards_track:
            self.output = ChatTranscriptView(chat_pack, self)
        else:
            self.output = ChatOutput(
                chat_pack["reasoning_fg"], chat_pack["tool_fg"],
                chat_pack["tool_error_fg"], chat_pack["user_bubble_bg"],
                chat_pack["tool_output_bg"],
                # L2-5 链接色复用 timeline_read_fg（VS Code textLink-foreground
                # 同源值，单一来源纪律不新增键）
                chat_pack["timeline_read_fg"], self)
        #: bash 运行中尾滚节流簿记（0645 计划 T8：tid → 末次上屏
        #: time.monotonic()；200ms 内到达的快照帧丢弃，终态帧不经过本簿记）
        self._tail_last: dict[str, float] = {}
        self.timeline = ActivityTimeline(_timeline_colors(chat_pack), self)
        self.input = ChatInput(
            # 选区带色与 ChatOutput 同源（timeline_read_fg 复用，不新增主题键）
            chat_pack["timeline_read_fg"], self)
        self.input.set_workspace_root(workspace_root)
        # 0438 计划 T2：输出区 @路径 引用存在性校验的工作区基准
        self.output.set_workspace_root(workspace_root)
        # 图片附件行（0340 方案 B 计划 T2/T3）：状态行与输入框之间，
        # chip 底色复用 user_bubble_bg（不新增主题键）；初态 hide
        self.attachments = AttachmentStrip(chat_pack["user_bubble_bg"], self)
        #: 发送时收集的附件簿记（失败/中断回滚恢复数据源，D6）
        self._sent_attachments: list = []
        # 底行模型选择（纯视图）：注入初始选择后以回退后的有效值为准
        self.model_bar = ModelBar(self)
        self.model_bar.set_selection(backend, version)
        self._llm_name = self.model_bar.current_backend()
        #: 用户显式选定的推理强度（2026-0806 计划；None = 未定制，不下发
        #: set_config_option）——_get_provider 懒实例化时的预选数据源
        self._effort_explicit: str | None = None
        # D4 懒实例化：不预建实例；workspace_root 留存供工厂使用。
        # 启动一致性（预选模型写入实例）不暂存 pending——建实例时取
        # model_bar.current_version()（选择状态单一来源，语义不变）
        self._workspace_root = workspace_root
        self._providers: dict[str, LanguageModel] = {}
        self.set_effort(effort)  # 注入恢复（静默 UI + 簿记；provider 未建则跳过）

        self._build_layout()
        self._connect_signals()
        self._apply_usage_label_style(load_settings()[KEY_THEME])
        self._apply_image_capability()  # 0340 方案 B：初始后端能力位注入

    # ------------------------------------------------------------------
    # provider 实例（注册表工厂懒实例化，D4；建成后每标签独立连接）
    # ------------------------------------------------------------------
    def _get_provider(self, name: str) -> LanguageModel | None:
        """按接口实现名取 provider 实例：未建则经注册表工厂即建即用（D4）。

        spec 未注册或 available() 为 False 返回 None（调用方按「后端不可用」
        处理，语义同原"未检测到本机 agent CLI"）。首次实例化时鸭子类型接线：
        有 set_permission_handler 即注入审批回环（替代 isinstance 硬编码），
        有 set_model 且当前有别名即写入（启动/切换一致性）。
        """
        provider = self._providers.get(name)
        if provider is not None:
            return provider
        spec = spec_of(name)
        if spec is None or not spec.available():
            return None
        provider = spec.factory(workspace_root=self._workspace_root)
        if (set_handler := getattr(provider, "set_permission_handler", None)) is not None:
            set_handler(self._ask_permission)
        version = self.model_bar.current_version()
        if version and (set_model := getattr(provider, "set_model", None)) is not None:
            set_model(version)
        # 预选推理强度（2026-0806 计划）：仅用户显式定制过才下发——未定制
        # 时 agent 默认强度生效（ModelBar UI 勾选默认项纯呈现，不据此下发）
        if self._effort_explicit \
                and (set_effort := getattr(provider, "set_effort", None)) is not None:
            set_effort(self._effort_explicit)
        self._providers[name] = provider
        return provider

    def set_effort(self, effort: str | None) -> None:
        """推理强度应用（本标签单面板入口）：同步自身 ModelBar 第四级
        UI + 写自身 provider 实例（鸭子类型 set_effort，不支持的 provider
        静默跳过）。

        None = 未定制（agent 默认强度生效，不下发 set_config_option）；
        持久化与新建注入值归 ChatTabs，本方法不管。provider 未懒实例化时
        只簿记 _effort_explicit，实例建成时由 _get_provider 应用。
        0455 动态化计划 D3/T4 值域校验：记忆值 ∉ 当前模型档位列表（跨
        模型切换后旧档位失效，如 kimi 记忆 low 切到只支持 high/max 的
        模型）→ 静默落未定制（agent/模型默认档生效），不写盘（记忆表
        保持用户显式选择原值，切回支持的模型即恢复）。
        """
        self.model_bar.set_effort_selection(effort)
        effort = self._validate_effort(effort)
        self._effort_explicit = effort
        provider = self._providers.get(self._llm_name)
        if effort and provider is not None:
            if (set_effort := getattr(provider, "set_effort", None)) is not None:
                set_effort(effort)

    def _validate_effort(self, effort: str | None) -> str | None:
        """记忆值域校验（0455 计划 D3）：effort ∈ 当前（接口, 模型）档位
        列表才放行下发；失效/无强度轴 → None（未定制，默认档生效）。

        数据源与 ModelBar 第四级菜单同源（resolve_efforts），保证「菜单
        可选值 = 可下发值」单一真值。
        """
        if not effort:
            return None
        spec = spec_of(self.model_bar.current_backend() or "")
        if spec is None:
            return None
        efforts, _default = resolve_efforts(spec, self.model_bar.current_version())
        return effort if effort in efforts else None

    def set_model_selection(self, backend: str, version: str | None) -> None:
        """模型选择应用（本标签单面板入口，2026-0803-0112 计划复用为
        每标签切换路径）：同步自身 ModelBar UI + 写自身 provider 实例。

        UI 同步走 ModelBar.set_selection（全程阻断信号，不回环）；
        持久化与新建注入值归 ChatTabs，本方法不管。
        上下文不迁移（各后端会话各自独立），切后端时输出提示行。
        D4：切换接口时旧实例 pop 后由 daemon 线程 close() 丢弃（2026-0730-2338
        计划 D4 异步化，terminate 不再阻塞 GUI），防长驻进程随切换累积。
        """
        self.model_bar.set_selection(backend, version)
        backend = self.model_bar.current_backend()  # 回退后的有效值（与 UI 一致）
        version = self.model_bar.current_version()
        if backend != self._llm_name:
            old = self._providers.pop(self._llm_name, None)
            self._usage_timer.stop()  # 0117 D5：切后端停轮询，不残留空转
            # 旧后端会话用量不得残留到新后端：清零徽章（reset_session 语义点）
            self._usage = None
            self._refresh_usage_label()
            self.timeline.clear()  # 新会话清空色块条（1824 计划 D5 随标签会话）
            if old is not None and getattr(old, "close", None) is not None:
                # 切换路径与 close() 同策略（计划 2026-0730-2338 D4）：
                # provider.close() 内 terminate()+wait(timeout=5) 为秒级
                # 阻塞，挪 daemon 线程，GUI 不干等；旧实例已 pop 出
                # _providers 不再被引用，无并发访问。快速切回旧后台时
                # 新实例重新构造（独立子进程），与后台 close 无共享状态
                threading.Thread(
                    target=_close_providers, args=([old],), daemon=True).start()
            self.output.append_message(
                "系统", f"已切换到 {BACKEND_LABELS.get(backend, backend)} 后端，开始新会话")
        self._llm_name = backend
        self._apply_image_capability()  # 0340 方案 B：能力位随后端切换刷新
        provider = self._get_provider(backend)
        if provider is not None and isinstance(version, str):
            if (set_model := getattr(provider, "set_model", None)) is not None:
                # 保持同步（计划 2026-0730-2338 D5 降级项）：provider 层无锁，
                # 挪线程会与对话线程并发触达同一 ACP stdio 连接；正常亚秒级
                set_model(version)

    def request_stop(self) -> None:
        """停止当前轮次（输入区停止按钮 / Esc 触发），幂等。"""
        if self._worker is not None:
            self._worker.request_stop()
            self.input.setPlaceholderText("正在停止…")

    def close(self) -> None:
        """标签关闭清理（两段式，2026-0722-1117 计划 T6；评审修复轮加固）。

        GUI 段（毫秒级，本方法体）：停轮次 + 断开 worker/busy 信号
        （迟到的收尾信号不再触碰已摘标签的 UI 与 ChatTabs 汇总）+
        worker 摘出父子关系（窗口在清理周期内销毁不再连带 QThread）+
        捕获 providers/worker 后启动 daemon 清理线程。不 wait 则
        deleteLater 会销毁仍在运行的 ChatWorker(QThread)（"QThread:
        Destroyed while thread is still running"，未定义行为）——
        wait 保留，但移入线程段。
        """
        self.request_stop()
        self._usage_timer.stop()  # 0117 D5：标签销毁前停轮询（随 tab 生命周期）
        worker = self._worker
        providers = list(self._providers.values())
        if worker is not None:
            try:
                worker.disconnect(self)  # 收尾信号不再投递本面板
            except (TypeError, RuntimeError):
                pass  # 无连接/测试替身：忽略
            try:
                worker.setParent(None)  # 摘除父子关系：销毁责任移交清理线程
            except (TypeError, RuntimeError, AttributeError):
                pass  # 测试替身：忽略
        try:
            self.busy_changed.disconnect()  # 迟到的 busy 不再进 ChatTabs 汇总
        except (TypeError, RuntimeError):
            pass
        try:
            self.turn_finished.disconnect()  # 摘出的标签不再触发 Git 刷新
        except (TypeError, RuntimeError):
            pass
        threading.Thread(
            target=_cleanup_blocking, args=(providers, worker, self), daemon=True).start()

    # ------------------------------------------------------------------
    # UI 构建与接线
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        """布局装配：PanelCard 单卡片整合（输出区 + 输入区含状态行）。

        卡片内保留垂直 splitter（输出/输入比例可调、状态持久化不变）；
        ChatOutput 透明融入卡片白底，输入框保留自身 6px 圆角嵌于卡内。
        输入区 = 状态行（时间线条 + 用量徽章）+ 输入框 + 底行（左：模型/
        版本双下拉；右：发送/停止双态按钮）。
        活动时间线色块条于 2242 计划（方案 F）由卡片底部迁入输入区状态行
        ——仍不进 splitter（子件会被拖拽均分，1824 计划 D2），细条定高
        随输入区拖拽移动，视觉恒在输出/输入分界。
        """
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self.output)
        self._splitter.addWidget(self._build_input_box())
        self._splitter.setSizes(self.DEFAULT_SPLITTER_SIZES)

        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(self._splitter, 1)
        card_layout.setContentsMargins(8, 6, 8, 8)
        card_layout.setSpacing(4)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）；
        # 下边距 6px + 状态栏定高 26px = 底部总间距 32px（一体化设计）
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

    def _build_input_box(self) -> QWidget:
        """输入区容器：状态行 + 输入框 + 底行（左：模型/版本双下拉；右：发送/插话 + ■ 停止 双常驻钮）。

        状态行（2026-0731-2242 计划方案 F，work plans 立项）：时间线色块条 +
        上下文用量徽章同行（左条右徽），置输入框上方——随 splitter 拖拽与
        输入区整体移动，视觉恒在输出/输入分界；定高不参与拉伸（仍守 1824
        计划 D2：时间线条不进 splitter，子件会被拖拽均分）。

        底行双下拉为 2026-0724-2354 计划 T3（原顶部全局模型行下移）；
        双常驻恒宽钮为 0634 计划 D3（替代原「发送/停止」单双态钮——busy
        期间停止/插话两动作并存）：宽度各按自身文本一次写死，任何状态下
        sizeHint 不变——QSplitter 撑宽左栏病根纪律（2305）。
        徽章移出底行后，底行回归纯操作行（模型选择 + 发送/插话/停止）。
        """
        # 上下文用量徽章（2026-0731-1412 计划 D1-A/D2-A）：纯文本百分比 +
        # tooltip 明细；常驻占位恒宽（按 "~100%" 宽度一次写死——1454 起
        # estimate 来源带 ~ 前缀），无数据时空文本——显隐不改 sizeHint
        # （左栏宽度病根教训，2026-0724-2305）。2242 计划由底行迁入状态行
        # 右侧，恒宽/对齐语义不变
        self._usage_label = QLabel(self)
        self._usage_label.setObjectName("chatUsageLabel")
        self._usage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._usage_label.setFixedWidth(
            self._usage_label.fontMetrics().horizontalAdvance("~100%") + 8)

        # 状态行：左时间线条（横向延展）→ 右用量徽章（恒宽）；
        # 行高由时间线条决定（约 30px，1824 计划已定），徽章随布局垂直居中
        status_row = QHBoxLayout()
        status_row.addWidget(self.timeline, 1)
        status_row.addWidget(self._usage_label)
        status_row.setContentsMargins(0, 0, 0, 0)

        self._send_button = QPushButton("发送", self)
        self._send_button.setObjectName("chatSendButton")
        self._send_button.setToolTip("发送消息（Enter）")
        # 恒宽按「发送/插话」两态文本较大者一次写死（2305 纪律现状手法平移）
        text_width = max(
            self._send_button.fontMetrics().horizontalAdvance("发送"),
            self._send_button.fontMetrics().horizontalAdvance("插话"),
        )
        self._send_button.setFixedWidth(text_width + 26)  # qss padding 11px*2 + border

        # 「■ 停止」常驻钮（0634 计划 D3）：文本恒定故宽度恒定；仅 busy 可用
        # （空闲禁用，busy 恒可用——D1 簿记保障），与「发送/插话」并存
        self._stop_button = QPushButton("■ 停止", self)
        self._stop_button.setObjectName("chatStopButton")
        self._stop_button.setToolTip("停止当前生成（Esc）")
        self._stop_button.setFixedWidth(
            self._stop_button.fontMetrics().horizontalAdvance("■ 停止") + 26)
        self._stop_button.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self.model_bar)
        button_row.addStretch(1)
        button_row.addWidget(self._send_button)
        button_row.addWidget(self._stop_button)
        button_row.setContentsMargins(0, 0, 0, 0)

        box = QWidget(self)
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(status_row)
        # 附件行：状态行与输入框之间第 2 件（0340 计划 D1；垂直显隐，
        # 宽度随父不设 stretch——左栏宽度零影响）
        box_layout.addWidget(self.attachments)
        box_layout.addWidget(self.input, 1)
        box_layout.addLayout(button_row)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(6)
        return box

    def _connect_signals(self) -> None:
        """跨组件信号统一接线（本面板的接线图）。"""
        self.input.send_requested.connect(self._on_send)
        self._send_button.clicked.connect(self._on_send_button)
        self._stop_button.clicked.connect(self._on_stop_button)
        # 「发送/插话」钮使能跟随输入文本与附件（空闲=发送、busy=插话，
        # 使能门槛一致，0634 计划 D3）
        self.input.textChanged.connect(self._refresh_send_button)
        # 图片附件化（0340 方案 B）：入口信号 → 附件行；附件行变化 →
        # 发送键使能与空文本发送开关（D5）；超限拒绝 → 输出区系统提示；
        # 能力外退化提示（D4 不静默，用户反馈 2026-08-01）
        self.input.image_attached.connect(self._on_image_attached)
        self.input.image_fallback.connect(self._on_image_fallback)
        self.attachments.changed.connect(self._on_attachments_changed)
        self.attachments.rejected.connect(
            lambda reason: self.output.append_message("系统", reason))
        self._refresh_send_button()  # 初始：空文本禁用发送
        # T7：busy 期间 Esc 中断本标签（输入框内 Esc 无默认行为，安全占用）
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self._on_esc_stop)
        # L2-5：输出区文件路径链接点击 → 解析外抛（主窗口接查看器）
        self.output.anchorClicked.connect(self._on_output_link)

    def _on_output_link(self, url) -> None:
        """文件链接点击（L2-5）：`file:路径#L行号` → 相对转绝对后外抛。

        链接由 output 冲刷时对反引号 `路径[:行号]` 片段生成；相对路径按
        工作区根解析，不存在也照常外抛（查看器自带「文件不存在」占位兜底）。
        """
        href = url.toString()
        if not href.startswith("file:"):
            return
        path, sep, frag = href[len("file:"):].partition("#L")
        p = Path(path)
        if not p.is_absolute():
            p = Path(self._workspace_root) / p
        line = int(frag) if sep and frag.isdigit() else None
        self.file_open_requested.emit(str(p), line)

    # ------------------------------------------------------------------
    # 底行双常驻钮路由（0634 计划 D3）与 Esc 中断（T7）
    # ------------------------------------------------------------------
    def _on_send_button(self) -> None:
        """「发送/插话」钮：与 Enter 同一入口（busy 时 _on_send 自动入队）。"""
        self.input.trigger_send()

    def _on_stop_button(self) -> None:
        """「■ 停止」常驻钮：busy 直停本标签（空闲禁用，正常不可达）。"""
        if self._busy:
            self.request_stop()

    def _on_esc_stop(self) -> None:
        """busy 期间 Esc = 点击停止（空闲时静默忽略）。"""
        if self._busy:
            self.request_stop()

    def _refresh_send_button(self) -> None:
        """「发送/插话」钮使能按文本/附件重算（文本非空**或**有附件，
        0340 D5）；空闲=发送、busy=插话门槛一致（0634 计划 D3）。

        停止钮不在此触碰——其使能只由 _set_busy 管理（D1 簿记守卫：
        消除「busy UI 已立、worker 未建/已销毁」竞态窗口内 worker
        存在性代理守卫失效导致停止被误禁用的根因，0634 计划 §2.1）。
        """
        self._send_button.setEnabled(
            bool(self.input.toPlainText().strip()) or self.attachments.count() > 0)

    # ------------------------------------------------------------------
    # 图片附件（0340 方案 B 计划 T3：能力位注入 / 附件行变化）
    # ------------------------------------------------------------------
    def _supports_images(self) -> bool:
        """当前后端图片附件能力（注册表 BackendSpec.supports_images，
        T0 spike 实证填值；接口级判定，D9）。"""
        spec = spec_of(self._llm_name)
        return bool(spec and spec.supports_images)

    def _apply_image_capability(self) -> None:
        """按当前后端能力位刷新图片入口语义（初始化与后端切换共用单点）。

        能力内：粘贴/拖入图片走附件化信号；
        能力外：退化方案 D @路径 透传（D4）。
        """
        enabled = self._supports_images()
        self.input.set_image_attachments_enabled(enabled)
        if not enabled and self.attachments.count() > 0:
            # 切到能力外后端时残留附件：保留在附件行但发送时不再携带
            # （_on_send 能力守卫），用户可 × 删
            self.output.append_message(
                "系统", "当前后端不支持图片附件，附件已保留但不会随消息发送")

    def _on_image_attached(self, path: str, mime_type: str, pasted: bool) -> None:
        """输入区图片入口信号 → 附件行（校验与拒绝提示归 AttachmentStrip）。"""
        self.attachments.add(path, mime_type, pasted)

    def _on_image_fallback(self) -> None:
        """能力外后端图片退化提示（2026-08-01 用户反馈：D4 退化不得静默）。

        每次粘贴/拖入图片一条系统提示（与用户动作一一对应，不算刷屏）；
        @路径 退化行为本身不变——模型可自行读取该图片文件。
        """
        label = BACKEND_LABELS.get(self._llm_name, self._llm_name)
        self.output.append_message(
            "系统",
            f"当前后端（{label}）不支持图片附件，已按 @路径 引用发送；"
            f"切换至 Kimi / Kilo Code 后端可使用图片缩略图附件")

    def _on_attachments_changed(self) -> None:
        """附件行变化 → 空文本发送开关（D5）与发送键使能。"""
        self.input.set_allow_empty_send(
            self.attachments.count() > 0 and self._supports_images())
        self._refresh_send_button()

    def _restore_sent_attachments(self) -> None:
        """失败/中断回滚（0340 计划 D6）：已收集的附件恢复回附件行
        （文件仍在盘上；AttachmentStrip.restore 静默跳过消失文件）。"""
        if self._sent_attachments:
            self.attachments.restore(self._sent_attachments)
            self._sent_attachments = []

    # ------------------------------------------------------------------
    # 主题（思维链/活动块前景色随主题资源包切换；由 ChatTabs 统一转发）
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """主题切换：更新输出区思维链/活动块配色与用量徽章（仅影响此后追加的块）。"""
        chat_pack = get_theme_palette(theme)["chat"]
        self.output.set_reasoning_color(chat_pack["reasoning_fg"])
        self.output.set_activity_colors(chat_pack["tool_fg"], chat_pack["tool_error_fg"])
        self.output.set_card_colors(
            chat_pack["user_bubble_bg"], chat_pack["tool_output_bg"],
            chat_pack["timeline_read_fg"])
        if self._cards_track:  # 0645 计划 T9：diff 红绿（仅新轨消费）
            self.output.set_diff_colors(
                chat_pack["diff_add_fg"], chat_pack["diff_del_fg"])
        self.timeline.set_colors(_timeline_colors(chat_pack))
        self.attachments.set_chip_color(chat_pack["user_bubble_bg"])
        self._apply_usage_label_style(theme)

    # ------------------------------------------------------------------
    # 上下文用量徽章（2026-0731-1412 计划：usage_update → 百分比徽章；
    # 2242 计划方案 F 由输入区底行迁入状态行右侧，簿记/刷新语义不变）
    # ------------------------------------------------------------------
    def _refresh_usage_label(self) -> None:
        """按 _usage 簿记刷新徽章：无数据空文本（常驻占位不撤）；≥80% 热态变色。

        口径标注（1454 计划 D3/T6）：estimate 来源加 `~` 前缀并在 tooltip
        注明估算口径（transcript 文本 chars/4 粗估，非 agent 精确计量）；
        transcript 来源（kimi 会话落盘记录真值）与 push 同形态显示，tooltip
        注明数据来源分级，诚实呈现精度。
        """
        stats = self._usage
        if stats is None:
            self._usage_label.setText("")
            self._usage_label.setToolTip("")
            self._usage_label.setProperty("hot", False)
        else:
            percent = round(stats.used / stats.size * 100)
            prefix = "~" if stats.source == "estimate" else ""
            self._usage_label.setText(f"{prefix}{percent}%")
            source_note = {
                "push": "数据来源：agent 实时推送",
                "transcript": "数据来源：kimi 会话记录（agent 落盘的接口真值）",
                "estimate": "数据来源：IDE 估算（会话文本 ÷4，非精确计量，仅量级参考）",
            }[stats.source]
            self._usage_label.setToolTip(
                f"上下文已用 {stats.used:,} / 上限 {stats.size:,} tokens（{percent}%）\n"
                f"{source_note}")
            self._usage_label.setProperty("hot", percent >= 80)
        # qss 动态属性（[hot="true"] 警示色）切换后强制刷新
        self._usage_label.style().unpolish(self._usage_label)
        self._usage_label.style().polish(self._usage_label)

    def _apply_usage_label_style(self, theme: str) -> None:
        """徽章配色随主题刷新（控件级 qss：常态 muted_text，热态 chat.usage_hot_fg）。"""
        palette = get_theme_palette(theme)
        self._usage_label.setStyleSheet(
            f"#chatUsageLabel {{ color: {palette['muted_text']}; }}"
            f"#chatUsageLabel[hot='true'] {{"
            f" color: {palette['chat']['usage_hot_fg']}; font-weight: 600; }}")

    # ------------------------------------------------------------------
    # 输出/输入区分隔栏状态持久化（由 ChatTabs 转发）
    # ------------------------------------------------------------------
    def save_state(self) -> str:
        """分隔栏状态 → base64 字符串。"""
        return encode_state(self._splitter.saveState())

    def restore_state(self, state: str | None) -> None:
        """恢复分隔栏；None 或损坏数据静默保留默认尺寸。"""
        if state:
            self._splitter.restoreState(decode_state(state))

    def reset_layout(self) -> None:
        """恢复默认布局：输出/输入区回初始尺寸（视图菜单「恢复默认布局」）。"""
        self._splitter.setSizes(self.DEFAULT_SPLITTER_SIZES)

    # ------------------------------------------------------------------
    # ACP 审批回环（权限四态：纯逻辑前置决策，弹窗面由档位决定）
    # ------------------------------------------------------------------
    def _ask_permission(self, params: PermissionParams) -> str | None:
        """ACP 审批处理器：在 agent reader 线程被调用。

        decide_permission 按当前 permission_mode 四态前置决策——allow 直接
        同步返回 optionId（零 GUI、零阻塞，不触碰队列/QTimer）；ask 提交
        全局审批队列弹窗（黑名单命中附原因）。返回 None 由上层按拒绝兜底。
        """
        decision, reason = decide_permission(params, load_settings()[KEY_PERMISSION_MODE])
        if decision == DECISION_ALLOW:
            option_id = select_option_id(params.get("options") or [])
            if option_id is not None:
                return option_id
            # 决策为 allow 但 agent 未提供 allow 类选项：不静默拒绝
            # （None 会被上层兜底为 reject），降级普通弹窗交还用户裁决
            return PERMISSION_QUEUE.ask(params, self)
        return PERMISSION_QUEUE.ask(params, self, danger_reason=reason)

    # ------------------------------------------------------------------
    # 发送与流式接收（含插话队列路由，0634 计划 D2/D5）
    # ------------------------------------------------------------------
    def _on_send(self, text: str) -> None:
        if self._busy:
            self._enqueue(text)  # busy 期间输入入队（Enter 与按钮同入口）
            return
        provider = self._get_provider(self._llm_name)
        if provider is None:
            self.output.append_message("系统", f"后端不可用：{self._llm_name}（未检测到本机 agent CLI）")
            return
        # 图片附件随消息携带（0340 方案 B 计划 T3）：能力守卫——切到
        # 能力外后端后残留的附件不发送（_apply_image_capability 已提示）
        images = self.attachments.attachments() if self._supports_images() else []
        self.input.clear()
        self.attachments.clear()  # 不删落盘文件（气泡卡回显依赖在盘，D7）
        self._send_turn(text, images, provider)

    def _send_turn(
        self,
        text: str,
        images: list,
        provider: LanguageModel,
        *,
        bubble_on_screen: bool = False,
    ) -> None:
        """一轮发送主流程（_on_send 直发与队列出队共用，0634 计划 D2）。

        bubble_on_screen=True（出队路径）：排队气泡已 promote 转正式
        气泡（D4 同一控件改写），不再重复 append 用户气泡。
        """
        self._set_busy(True)
        message: Message = {"role": "user", "content": text}
        if images:
            message["images"] = images
        self._history.append(message)
        self._sent_attachments = images  # 失败/中断回滚恢复数据源（D6）
        if not bubble_on_screen:
            self.output.append_user_message(text, images)  # L2-1 气泡卡 + 缩略图
        self.output.begin_stream("AI")
        self._stream_buffer = ""
        self._has_seen_reasoning = False
        self._tool_titles = {}
        self._tool_commands = {}
        self._tail_last = {}

        messages: list[Message] = list(self._history)

        self._worker = ChatWorker(provider, messages, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_with_error.connect(self._on_finished)
        self._worker.stopped_by_user.connect(self._on_stopped)
        self._worker.start()
        self._usage_timer.start()  # 0117 T3：轮次内用量轮询（kimi 尾部读 wire.jsonl）

    # ------------------------------------------------------------------
    # 插话队列（0634 计划 D2/D4/D5：每标签 FIFO，内存态不持久化）
    # ------------------------------------------------------------------
    def _enqueue(self, text: str) -> None:
        """busy 期间输入入队上屏：排队气泡（徽标 + ×）+ 队列簿记。

        附件与正常发送同一收集路径：入队时快照、出队时随消息发送；
        _history 仅在实际发送时写入（现语义不变）。满队（10 条）再
        发送 → 输出区系统提示（对齐附件上限提示惯例）。
        """
        if len(self._queue) >= MAX_QUEUED_MESSAGES:
            self.output.append_message("系统", f"排队消息最多 {MAX_QUEUED_MESSAGES} 条")
            return
        images = self.attachments.attachments() if self._supports_images() else []
        handle = self.output.append_queued_user_message(text, images)
        # 新轨句柄（UserBubbleBlock）自带 ×删信号；旧轨句柄为 position
        # 三元组（纯文本文档无 ×，单条删除走停止清空——output.py 注记取舍）
        if (sig := getattr(handle, "remove_requested", None)) is not None:
            sig.connect(lambda h=handle: self._discard_queued(h))
        self._queue.append((text, images, handle))
        self.input.clear()
        self.attachments.clear()  # 附件快照已入队列项；不删落盘文件
        self._refresh_placeholder()

    def _discard_queued(self, handle) -> None:
        """排队气泡 ×删（D4）：队列项剔除 + 气泡移除。"""
        self._queue = [item for item in self._queue if item[2] is not handle]
        self.output.discard_queued(handle)
        self._refresh_placeholder()

    def _dequeue_next(self) -> bool:
        """轮次正常收尾自动出队发送（D5：FIFO；接续轮不加中断标注——
        kilocode superseded 同义）。队列空或后端不可用返回 False。"""
        if not self._queue:
            return False
        provider = self._get_provider(self._llm_name)
        if provider is None:
            # 极端：轮间后端失效——队列保留不丢，按失败惯例系统提示
            self.output.append_message(
                "系统", f"后端不可用：{self._llm_name}（未检测到本机 agent CLI）")
            return False
        text, images, handle = self._queue.pop(0)
        self.output.promote_queued(handle)  # 徽标摘除转正式气泡（D4）
        self._send_turn(text, images, provider, bubble_on_screen=True)
        return True

    def _drain_queue_to_input(self) -> None:
        """停止清空队列（D5，对齐 kilocode abort 语义：停=全停）：排队
        文本合并退回输入框草稿（多条换行相接）不丢——比 kilocode 整队
        删除宽容；附件尽力恢复回附件行（上限内，0340 D6 恢复惯例）。"""
        if not self._queue:
            return
        texts: list[str] = []
        images: list = []
        for text, item_images, handle in self._queue:
            texts.append(text)
            images.extend(item_images)
            self.output.discard_queued(handle)
        self._queue = []
        if images:
            self.attachments.restore(images)
        merged = "\n".join(t for t in texts if t)
        existing = self.input.toPlainText().strip()
        if existing:
            merged = f"{existing}\n{merged}" if merged else existing
        if merged:
            self.input.setPlainText(merged)

    def _refresh_placeholder(self) -> None:
        """输入框占位符（D6）：busy=「响应中…Enter 插话排队 / ■ 停止或
        Esc 中断」+ 已排队计数；空闲=常规提示；停止进行中提示不被覆盖。"""
        if self._busy:
            if self.input.placeholderText() == "正在停止…":
                return
            label = BACKEND_LABELS.get(self._llm_name, "AI")
            text = f"{label} 响应中…Enter 插话排队 / ■ 停止或 Esc 中断"
            if self._queue:
                text += f"（已排队 {len(self._queue)} 条）"
        else:
            text = "输入消息，Enter 发送 / Shift+Enter 换行"
        self.input.setPlaceholderText(text)

    def _poll_usage_tick(self) -> None:
        """轮次内用量轮询（0117 计划 T3/D1）：GUI 侧 QTimer 直调 provider
        .poll_usage()，只读文件、与 ACP 连接零交互；非 None 即更新徽章簿记。
        无数据（不支持的 backend/写盘延迟/文件残缺）→ 保持现状不刷新，
        绝不臆造估值（红线）。
        """
        provider = self._providers.get(self._llm_name)
        if provider is None:
            return
        stats = provider.poll_usage()
        if stats is not None:
            self._usage = stats
            self._refresh_usage_label()

    def _on_chunk(self, chunk: Chunk) -> None:
        self.timeline.feed(chunk)  # 时间线色块条旁路分接（1824 计划 T3）
        if chunk.kind == "usage":
            # 上下文用量通知：只更新徽章簿记，不进输出区文本流、不入 _history
            if chunk.usage is not None:
                self._usage = chunk.usage
                self._refresh_usage_label()
            return
        if chunk.kind in ("tool_call", "tool_call_update", "todo"):
            self._on_activity_chunk(chunk)
            return
        if chunk.kind == "reasoning":
            # 思维链只上屏，不入 buffer/历史（DeepSeek 约束：不得回传）
            self._has_seen_reasoning = True
            self.output.append_reasoning_chunk(chunk.text)
            return
        if self._has_seen_reasoning:
            self.output.end_reasoning()  # 思维链与正文之间插空行
            self._has_seen_reasoning = False
        self._stream_buffer += chunk.text
        self.output.append_stream_chunk(chunk.text)

    def _on_activity_chunk(self, chunk: Chunk) -> None:
        """AI 活动信息路由（1602 计划 T6）：tool_call / tool_call_update / todo。

        只上屏，不入 _stream_buffer/_history（与 reasoning 同约束，防历史
        污染回传禁令）；与 text 同位处理思维链收尾（reasoning → 工具活动
        → 正文 交错序列语义不变）。
        """
        if self._has_seen_reasoning:
            self.output.end_reasoning()
            self._has_seen_reasoning = False
        payload = chunk.payload or {}
        if chunk.kind == "tool_call":
            if tid := payload.get("tool_call_id"):
                if payload.get("title"):
                    self._tool_titles[tid] = payload["title"]
                if payload.get("command"):  # execute 簿记（L2-3 输出卡数据源）
                    self._tool_commands[tid] = payload["command"]
            self.output.append_tool_call(payload)
        elif chunk.kind == "tool_call_update":
            tid = payload.get("tool_call_id") or ""
            # 更新帧常缺 title（F3 部分更新）：自簿记补全，缺省回退 id 短串
            if not payload.get("title"):
                payload = {**payload,
                           "title": self._tool_titles.get(tid, tid[:8] or "?")}
            if self._cards_track:
                # 新轨（0645 计划 §2.3-2）：取消非 execute 工具的 output 剥除
                # ——所有工具的 output 随载荷进渲染层，由对应卡 body 承接
                if payload.get("status") == "in_progress" \
                        and not self._allow_progress_frame(payload, tid):
                    return
                if payload.get("output") and tid in self._tool_commands:
                    payload = {**payload, "command": self._tool_commands[tid]}
                self.output.append_tool_update(payload)
            else:
                # 旧轨（冻结）：in_progress 不上屏；L2-3 bash 输出卡仅
                # execute 工具（有 command 簿记者）放行输出正文并补 `$ ` 头，
                # 其余工具长输出丢弃（防 read/edit 刷屏）；execute 输出钳回
                # 末 20 行（1836 L2-3 规格——协议层 0645 起放宽至软上限
                # 1000 行，旧轨渲染规格不变由本路由钳制）
                if payload.get("output"):
                    if tid in self._tool_commands:
                        lines = payload["output"].split("\n")
                        if len(lines) > _LEGACY_OUTPUT_KEEP_LINES:
                            payload = {**payload,
                                       "output": "\n".join(lines[-_LEGACY_OUTPUT_KEEP_LINES:])}
                        payload = {**payload, "command": self._tool_commands[tid]}
                    else:
                        payload = {k: v for k, v in payload.items() if k != "output"}
                self.output.append_tool_update(payload)
        else:
            self.output.upsert_todo_block(payload.get("entries") or [])

    def _allow_progress_frame(self, payload: dict, tid: str) -> bool:
        """in_progress 帧放行判定（新轨）。

        尾滚帧（§2.1 BashCard 运行中实时帧）：仅 execute 工具、仅带输出
        帧，同 tid 200ms 节流（丢弃中间帧，终态帧不经本分支不受节流
        影响）。0919 计划 T3：带 diff/摘要的迟到信息帧（kimi 系 edit 的
        diff content 与 rawInput.path 在 in_progress 帧才发）不限工具
        放行，DiffCard 补挂徽标/body/副标题。0806 计划 T1/T2：带
        input_detail（迟到入参回填）或 images（出参图片通道）的帧同样
        放行——否则协议层回填的入参永远到不了卡片（kimi 系 ReadMediaFile
        首帧空壳场景）。
        """
        if payload.get("diff_hunks") or payload.get("summary"):
            return True
        if payload.get("input_detail") or payload.get("images"):
            return True
        if tid not in self._tool_commands or not payload.get("output"):
            return False
        now = time.monotonic()
        if now - self._tail_last.get(tid, 0.0) < _TAIL_THROTTLE_S:
            return False
        self._tail_last[tid] = now
        return True

    def _on_finished(self, error: str) -> None:
        if error:
            if self._has_seen_reasoning:
                self.output.end_reasoning()
                self._has_seen_reasoning = False
            self.output.append_stream_chunk(f"\n[请求失败] {error}")
            self._history.pop()  # 失败的用户消息不入历史
            self._restore_sent_attachments()  # 附件恢复回附件行（0340 D6）
        else:
            self._history.append({"role": "assistant", "content": self._stream_buffer})
            self._sent_attachments = []
        self.output.reset_activity_anchors()  # todo 锚点轮次收尾作废（T5-4）
        self.timeline.end_turn()  # 色块条段指针轮次收尾作废（1824 计划 T3）
        self.output.end_stream()
        self._worker = None
        self._usage_timer.stop()  # 0117 D5：轮次收尾停轮询（收尾真值已由 chunk 兜底）
        self.turn_finished.emit()
        # 0634 D5 出队三态：正常完成自动出队接续（接续轮 busy 不间断）；
        # 失败不自动接续——队列保留 + 系统提示（防连环失败烧 token），
        # 下次正常发送成功后经本条成功路径自动恢复接续
        if error:
            self._set_busy(False)
            if self._queue:
                self.output.append_message(
                    "系统", f"上轮失败，排队消息已保留（{len(self._queue)} 条），"
                    f"可手动发送或删除")
        elif not self._dequeue_next():
            self._set_busy(False)

    def _on_stopped(self) -> None:
        """用户中断收尾（第三态）：整体回滚——中断轮不入历史；
        屏幕已输出内容不擦除（可复制兜底），追加停止标注。"""
        self._usage_timer.stop()  # 0117 D5：中断即停轮询，不残留空转
        if self._has_seen_reasoning:
            self.output.end_reasoning()
            self._has_seen_reasoning = False
        self._history.pop()  # 回滚用户消息；半截回复随 _stream_buffer 丢弃
        self._restore_sent_attachments()  # 附件恢复回附件行（0340 D6）
        self.output.append_stream_chunk("\n⏹ 已手动停止")
        self.output.reset_activity_anchors()  # todo 锚点轮次收尾作废（T5-4）
        self.timeline.end_turn()  # 色块条段指针轮次收尾作废（1824 计划 T3）
        self.output.end_stream()
        self._worker = None
        # 0634 D5：停止清空队列（对齐 kilocode abort 语义：停=全停）；
        # 排队文本合并退回输入框草稿不丢（比 kilocode 整队删除宽容）
        self._drain_queue_to_input()
        self._set_busy(False)
        self.turn_finished.emit()

    def _set_busy(self, is_busy: bool) -> None:
        self._busy = is_busy  # 簿记单点置位（0634 D1），先于一切 UI 动作
        # 输入框保持可编辑（busy 期间 Enter=插话入队，0634 D2/D3）；
        # 全局 ModelBar 双下拉禁用归 ChatTabs 汇总处理
        self.busy_changed.emit(is_busy)
        if is_busy:
            self._send_button.setText("插话")
            self._send_button.setToolTip("排队插话：当前轮结束后自动发送（Enter）")
            self._stop_button.setEnabled(True)  # 停止恒可用（D1 保障）
        else:
            self._send_button.setText("发送")
            self._send_button.setToolTip("发送消息（Enter）")
            self._stop_button.setEnabled(False)
        # 「发送/插话」使能按文本/附件重算（两态门槛一致，D3）；
        # 两钮恒宽，文本切换不改 sizeHint（2305 病根纪律）
        self._refresh_send_button()
        self._refresh_placeholder()


#: 旧轨 bash 输出卡截尾行数（1836 计划 L2-3 规格；0645 起协议层放宽至
#: 软上限 1000 行，旧轨冻结不改由路由层钳回——与 acp._OUTPUT_KEEP_LINES
#: 同值同步，改一处须同步）
_LEGACY_OUTPUT_KEEP_LINES = 20

#: bash 运行中尾滚节流间隔（秒，0645 计划 T8/0619-T6-2 规格）
_TAIL_THROTTLE_S = 0.2

#: 插话队列上限（0634 计划 D2：防误触连发刷屏，对齐附件上限提示惯例）
MAX_QUEUED_MESSAGES = 10


def _timeline_colors(chat_pack: dict) -> dict[str, str]:
    """ChatPack → 色块条分类色表（1824 计划 §3.3 映射，构造/主题切换共用单点）。

    新增三键专用于读/写/其他工具；text/reasoning 复用 reasoning_fg、
    todo 复用 tool_fg、error 复用 tool_error_fg（单一来源纪律，不另设键）。
    """
    return {
        "text": chat_pack["reasoning_fg"],
        "reasoning": chat_pack["reasoning_fg"],
        "tool_read": chat_pack["timeline_read_fg"],
        "tool_write": chat_pack["timeline_write_fg"],
        "tool_other": chat_pack["timeline_tool_fg"],
        "todo": chat_pack["tool_fg"],
        "error": chat_pack["tool_error_fg"],
    }


def _close_providers(providers: list[LanguageModel]) -> None:
    """关闭全部已建 provider 实例（鸭子类型 close()，幂等可重复调用）。"""
    for provider in providers:
        if (close := getattr(provider, "close", None)) is not None:
            close()


def _cleanup_blocking(
    providers: list[LanguageModel],
    worker: ChatWorker | None,
    panel: "ChatPanel",
) -> None:
    """daemon 线程段（ChatPanel.close 评审修复轮重构为模块级函数）。

    先 provider.close()（terminate 杀 acp 进程并注入死讯 + `_closed` 置位
    拒绝迟到连接，worker 的 next_update()/request()/spawn 阻塞点立即醒来
    或被拒）再 worker.wait()——顺序倒置（先 wait 后 terminate）会白等 3s。
    wait 超时（worker 卡在无法解封的角落）二次 close 兜底收割迟到连接。
    参数在 GUI 段提前捕获（providers/worker），线程内不触碰 panel 的
    Python 属性（其 C++ 对象可能在清理周期内随窗口销毁，访问即
    RuntimeError）；deleteLater 均经 singleShot 投递回 GUI 线程
    （与 permission_queue.ask 既有用法同构），panel 销毁则 isValid
    守卫跳过；worker 已摘父子关系，销毁责任由本函数收口。
    """
    _close_providers(providers)
    if worker is not None:
        if not worker.wait(3000):  # 解封后正常毫秒级返回；3s 兜底防永久悬挂
            _close_providers(providers)  # 二次兜底：收割 wait 期间迟到的连接
            worker.wait(1000)
        if isinstance(worker, QThread):  # 测试替身无 C++ 对象，跳过销毁
            QTimer.singleShot(0, worker, worker.deleteLater)
    if isValid(panel):  # 清理周期内窗口已销毁 panel：跳过（其子对象随之回收）
        QTimer.singleShot(0, panel, panel.deleteLater)
