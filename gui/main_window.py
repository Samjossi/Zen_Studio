"""主窗口：三栏式布局 + 状态栏；菜单栏由 gui/menus 包装配。

窗口几何与分隔栏状态持久化（2026-07-19，见 文档/修改记录/
2026-0719-0712_GUI窗口状态与模型选择持久化计划.md）：
启动时 restore，closeEvent 时一次性保存；损坏数据静默回退默认布局。

Git 状态可视化（2026-07-20，见 文档/修改记录/2026-0720-0131 计划阶段四）：
事件驱动刷新——窗口激活 / 查看器外部重载联动 / 视图菜单手动刷新，
300ms 去抖后刷新 GitStatusService 并同步文件树着色、查看器差异徽标
与状态栏统计；非 git 环境下所有入口静默跳过。

菜单栏模块化（2026-07-20，见 文档/修改记录/2026-0720-0510_菜单栏与设置体系
实施计划.md）：全部 addMenu/addAction 迁出至 gui/menus/（每菜单一文件 +
ActionRegistry 全局注册表）；本类保留面板、槽函数与面板显隐单一入口。
控制器外移（2026-07-21，AFCP 整改任务 2.3）：Git 编排（GitStatusController）
与窗口状态持久化（WindowStateStore）迁出至 gui/controllers.py 组合持有。
多开工作区（2026-07-22，见 文档/修改记录/2026-0722-0756 计划）：一进程绑定
一工作区根（启动参数注入，不再窗口内切换）；「打开文件夹」改为起新进程。
文件菜单扩展（2026-07-22，文档/修改记录/2026-0722-1901）：「新建窗口」同根
多开入口。「最近打开的项目」（2026-07-24，文档/修改记录/2026-0724-1003）：
窗口启动即记录自身工作区根 → RecentProjectsStore 全局共享列表
（config/recent_projects.json），子菜单回放在新窗口绑定该根。
「打开文件夹」（2026-07-24，文档/修改记录/2026-0724-1806）：换根关旧窗——
预写布局（活窗口采集 patch 直写目标根哈希文件 + default 双写，复制语义）
后起新进程，2s 探活（Popen.poll）存活才关旧窗，启动即败保留旧窗。

窗口四边距体系（2026-07-20，见 文档/修改记录/2026-0720-1218 与 2026-0720-1815
两份计划）：面板内 6px 外边距承担卡片↔把手间距；中央容器补窗口级边距
（左/右 12px → 有效 18px；上 6px）；菜单栏 qss padding-top 21px 下移菜单行
（文字距上缘 24px），其镜像余量由 _fit_menubar_height 定高截断——菜单栏底
→卡片顶 = 容器 6px + 面板 6px = 12px；底部 32px 一体化 = 面板下边距 6px +
状态栏定高 26px（_fit_statusbar_height，字号调大按字体度量兜底）。
"""
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.paths import IS_FROZEN, LOGO_DIR, PROJECT_ROOT
from core.version import APP_VERSION
from gui.controllers import GitStatusController, WindowStateStore
from gui.menus import MenuBar
from gui.menus.registry import (
    KEY_VIEW_CHAT,
    KEY_VIEW_CHANGES,
    KEY_VIEW_EXPLORER,
    KEY_VIEW_TERMINAL,
    theme_action_key,
)
from gui.panels import FileExplorer, ViewerPanel
from gui.panels.changes import ChangesPanel
from gui.panels.chat import ChatTabs
from gui.panels.terminal import TerminalPanel
from gui.git_graph_dialog import GitGraphDialog
from gui.recent_projects import RecentProjectsStore
from gui.settings import (
    CONFIG_DIR,
    DEFAULT_SETTINGS,
    KEY_FONT_SIZE,
    KEY_MODEL_BACKEND,
    KEY_MODEL_VERSIONS,
    KEY_TERMINAL_SWAP_COPY_PASTE,
    KEY_THEME,
    SETTINGS_FILE,
    update_settings,
)
from gui.settings_dialog import SettingsDialog
from gui.theme import (
    apply_theme,
    get_label,
    load_settings,
    save_theme,
)
from gui.title_bar import TitleBar
from gui.window_resize import EdgeResizeController
from gui.window_state import (
    KEY_SPLITTER_EDITOR,
    KEY_SPLITTER_MAIN,
    KEY_SPLITTER_SIDEBAR,
    window_state_file_for,
)


class MainWindow(QMainWindow):
    #: 默认布局尺寸（px）：__init__ 初排与 reset_layout 共用单点来源。
    #: 左栏 320 = ChatTabs.MIN_WIDTH（0401 计划 D4 起为最小宽单一来源；
    #: 320 ≥ ChatTabs 实测静态下限 315，2026-0724-2354 计划 T6 回摆：
    #: 模型选择下移底行 + 下拉瘦身 N=3 后，320 从「永远不可达」恢复为
    #: 「可达且正确」，总和 1200 对齐默认窗口宽）
    DEFAULT_SIZES_MAIN = [320, 630, 250]    # 外层水平：聊天 / 中栏 / 右栏
    DEFAULT_SIZES_EDITOR = [550, 250]       # 中栏垂直：查看器 / 终端
    DEFAULT_SIZES_SIDEBAR = [340, 170]        # 右栏垂直：文件树 / 变更面板

    #: 状态栏消息时长（ms）：常规操作反馈 / 轻量提示 / 需细读的说明
    STATUS_MSG_TIMEOUT_MS = 3000
    STATUS_MSG_SHORT_MS = 2000
    STATUS_MSG_LONG_MS = 5000

    #: 换根新进程启动探活延迟（ms）：PySide6 冷启动 import 约 1–2 s，
    #: 启动失败（解释器错误/import 崩溃）绝大多数在此窗口内退出
    SPAWN_PROBE_MS = 2000

    def __init__(self, workspace_root: str | None = None) -> None:
        """
        :param workspace_root: 工作区根（启动参数注入；None = 项目根）
        """
        super().__init__()
        # 新增（文档/修改记录/2026-0730-0007 计划 T6）：隐藏系统原生标题栏，
        # 改由 TitleBar 自绘（见 _build_layout 首行）；保留 Window 类型标志，
        # 任务栏/Alt-Tab/最小化行为不变
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self._workspace_root = workspace_root or str(PROJECT_ROOT)
        # 非默认工作区时标题栏标注根路径，多开窗口一眼可辨
        if self._workspace_root != str(PROJECT_ROOT):
            self.setWindowTitle(f"Zen Studio — {self._workspace_root}")
        else:
            self.setWindowTitle("Zen Studio")
        self.resize(1200, 800)

        #: 设置中心对话框（非模态单例，首次打开时惰性创建）
        self._settings_dialog: SettingsDialog | None = None
        #: 提交历史图对话框（非模态单例，同设置中心惰性创建模式）
        self._git_graph_dialog: GitGraphDialog | None = None
        #: 对话框同步挂起计数（>0 时 _sync_settings_dialog 短路）：批量应用路径
        #: （如 reset_settings 多槽连发）挂起期间各槽内置 sync 被抑制，结束后
        #: 单次终态 reload，避免 N 槽触发 N+1 次全量 reload（flock 磁盘读）
        self._dialog_sync_suspend = 0

        self._build_layout()
        # 无边框边缘缩放热区（文档/修改记录/2026-0730-0043 计划阶段二 T5/T6）：
        # 八向判定 + startSystemResize 优先/手动 setGeometry 兜底；
        # minimumSize 防缩坏三栏布局（决策点 D3 初估 900×500——左栏 ChatTabs
        # 静态下限 315 + 中栏 + 右栏 FileExplorer 240 + 边距/手柄的经验值）
        self.setMinimumSize(900, 500)
        self._resize_controller = EdgeResizeController(self)
        self._init_recent_projects()
        self._init_statusbar()
        self._init_git_status()  # 先于菜单装配：视图菜单「刷新 Git 状态」直挂控制器
        self._init_menus()
        self._restore_window_state()

    # ------------------------------------------------------------------
    # 初始化分段（__init__ 拆分；各段职责单一，顺序即装配依赖序）
    # ------------------------------------------------------------------
    def _init_recent_projects(self) -> None:
        """最近打开的项目记录（文档/修改记录/2026-0724-1003）：窗口启动即记录
        自身工作区根——一进程绑定一根，菜单选文件夹 / 命令行 main.py
        <folder> / 新建窗口三路径汇聚于此；全局共享列表（config/
        recent_projects.json），去重置顶语义下同根重复启动无噪音。
        无 UI 依赖（纯数据写盘），置于布局装配后、菜单装配前（子菜单
        aboutToShow 读 store 时已就绪）。
        """
        self.recent_projects = RecentProjectsStore(
            CONFIG_DIR / "recent_projects.json")
        self.recent_projects.add(self._workspace_root)

    def _build_layout(self) -> None:
        """布局装配：三栏 splitter（聊天 / 中栏查看器+终端 / 右栏文件树+变更）。"""
        # 中栏垂直拆分：上为文件查看器（只读+高亮），下为内嵌终端（真 PTY）
        self._splitter_editor = QSplitter(Qt.Orientation.Vertical)
        self.viewer_panel = ViewerPanel(workspace_root=self._workspace_root)
        self.terminal_panel = TerminalPanel(cwd=self._workspace_root)
        self._splitter_editor.addWidget(self.viewer_panel)
        self._splitter_editor.addWidget(self.terminal_panel)
        self._splitter_editor.setSizes(self.DEFAULT_SIZES_EDITOR)
        # 防折叠：终端栏最小高度由 TerminalPanel.MIN_HEIGHT 约束（collapsible 默认 true 会无视之）
        self._splitter_editor.setCollapsible(1, False)

        self._splitter_main = QSplitter(Qt.Orientation.Horizontal)
        workspace_root = self._workspace_root
        # 左栏：AI 会话标签容器（选择状态层 + 多标签 ChatPanel，上限 4）
        self.chat_tabs = ChatTabs(workspace_root=workspace_root)
        # 左栏最小宽度显式下限（0401 计划 D4/T5）：配合下方
        # setCollapsible(0, False) 双闸——拖到 MIN_WIDTH 即触底且不可塌缩
        self.chat_tabs.setMinimumWidth(ChatTabs.MIN_WIDTH)
        self._splitter_main.addWidget(self.chat_tabs)
        self._splitter_main.addWidget(self._splitter_editor)

        # 右栏：垂直拆分——上文件树（根目录为工作区根）、下 Git 变更面板；
        # 双击文件 → 中栏查看器打开
        self.file_explorer = FileExplorer(workspace_root)
        self.file_explorer.file_opened.connect(self.viewer_panel.open_file)
        # 对话区正文文件路径链接点击 → 查看器打开并跳行（1836 计划 L2-5）
        self.chat_tabs.file_open_requested.connect(self.viewer_panel.open_file)
        self.changes_panel = ChangesPanel()
        self._splitter_sidebar = QSplitter(Qt.Orientation.Vertical)
        self._splitter_sidebar.addWidget(self.file_explorer)
        self._splitter_sidebar.addWidget(self.changes_panel)
        self._splitter_sidebar.setSizes(self.DEFAULT_SIZES_SIDEBAR)
        # 防折叠：变更面板最小高度由 ChangesPanel.MIN_HEIGHT 约束
        self._splitter_sidebar.setCollapsible(1, False)
        self._splitter_main.addWidget(self._splitter_sidebar)

        self._splitter_main.setSizes(self.DEFAULT_SIZES_MAIN)
        # 防折叠：左栏聊天区最小宽度由 ChatTabs.MIN_WIDTH 约束（0401 计划
        # D4/T5——collapsible 默认 True 允许拖塌到 0，无硬性下限的缺口
        # 在此堵上；与右栏同构）
        self._splitter_main.setCollapsible(0, False)
        # 防折叠：右栏文件树最小宽度由 FileExplorer.MIN_WIDTH 约束
        self._splitter_main.setCollapsible(2, False)

        # 中央容器：外层 vbox 零边距零间距，自上而下 [TitleBar][QMenuBar][内容包装器]。
        # 窗口级外边距（左/右各 12px——叠面板 6px 得 18px；上 6px——菜单栏定高截断
        # 镜像余量后，菜单栏底→卡片顶 = 6 + 面板 6 = 12px；底部 0——底部间距由状态栏
        # 定高体系承接）由内容包装器原样承接，数值未改。窗口级边距与面板 6px
        # 职责分离，可独立调参
        central = QWidget(self)
        outer = QVBoxLayout(central)
        # 改动（文档/修改记录/2026-0730-0007 计划 T7）：原 (12, 6, 12, 0) 下沉至内容包装器
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # ---- 新增：标题栏 + 菜单栏行（QMainWindow 无 API 在菜单槽之上插部件，
        # 菜单栏改显式创建 reparent 进中央容器，不再占用菜单槽；T8/T9 联动）----
        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)
        self._menu_bar = QMenuBar(self)
        outer.addWidget(self._menu_bar)
        # ---- 新增结束 ----
        content = QWidget(self)                 # 新增：原窗口级 margins 原样承接
        inner = QVBoxLayout(content)
        inner.setContentsMargins(12, 6, 12, 0)  # 原有数值，未改
        inner.setSpacing(0)
        inner.addWidget(self._splitter_main)
        outer.addWidget(content)
        self.setCentralWidget(central)

    def _init_menus(self) -> None:
        """菜单栏装配 + 模型选择/忙闲信号接线。"""
        # 改动（文档/修改记录/2026-0730-0007 计划 T8）：菜单栏显式传入（_build_layout
        # 已创建并 reparent），不再走 window.menuBar() 惰性建栏
        self.menus = MenuBar(self, self._menu_bar)
        self.menus.setup()
        # 菜单栏定高截断镜像余量（qss padding-top 会被镜像到底部，定值见 base.qss
        # QMenuBar 段教训注释）；延迟一拍确保样式与布局已结算再测量项高
        QTimer.singleShot(0, self._fit_menubar_height)
        # 模型选择：标签底行下拉切换 → ChatTabs 收敛（当前活动标签语义，
        # 2026-0803-0112 计划）转发 → 设置中心同步；当前标签响应中模型页禁用
        self.chat_tabs.selection_changed.connect(self._on_modelbar_changed)
        self.chat_tabs.busy_changed.connect(self._on_chat_busy_changed)

    def _init_statusbar(self) -> None:
        """状态栏：去尺寸把手 + 定高 + Git 统计常驻区 + 就绪消息。"""
        self.statusBar().setSizeGripEnabled(False)  # 去掉右下角尺寸把手（无边框无缩放，把手冗余）
        self._fit_statusbar_height()  # 定高紧凑化：底部总间距 18px 一体化（含状态栏）
        # 状态栏右侧常驻：当前文件 Git 差异统计（无改动/非仓库时为空）
        self._git_stats_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._git_stats_label)
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------
    # Git 状态可视化：编排职责外移 GitStatusController（gui/controllers.py）
    # ------------------------------------------------------------------
    def _init_git_status(self) -> None:
        """Git 编排控制器装配（服务创建/去抖/四面板扇出刷新）。"""
        self.git_controller = GitStatusController(
            self.file_explorer,
            self.viewer_panel,
            self.changes_panel,
            self.statusBar(),
            self._git_stats_label,
            collapse_handler=lambda: self.set_changes_visible(False),
            parent=self,
        )
        # AI 轮次结束 → 去抖刷新（诊断报告 文档/修改记录/2026-0731-1256 方案 A：
        # ACP 子进程直接写盘不经窗口激活/查看器重载，补此事件源闭合）
        self.chat_tabs.turn_finished.connect(self.git_controller.schedule_refresh)

    def changeEvent(self, event) -> None:
        """窗口重获焦点 → 去抖刷新（兜底终端 checkout 等外部 git 操作）。"""
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self.git_controller.schedule_refresh()
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # 窗口几何与分隔栏状态持久化
    # ------------------------------------------------------------------
    #: 状态栏定高下界（px）：底部总间距 32px = 面板下边距 6px + 状态栏 26px
    STATUSBAR_HEIGHT_MIN = 26

    def _fit_statusbar_height(self) -> None:
        """状态栏定高：默认 26px；字号调大时按字体度量兜底防裁切。"""
        font_metrics = self.statusBar().fontMetrics()
        self.statusBar().setFixedHeight(
            max(self.STATUSBAR_HEIGHT_MIN, font_metrics.height() + 4))

    def _fit_menubar_height(self) -> None:
        """菜单栏定高截断镜像余量：实际高度 = padding-top + 实测项高。

        qss padding-top 会被 Qt 镜像到菜单栏底部（栏总高 = 内容高 + 2N，
        见 base.qss QMenuBar 段教训注释与 文档/修改记录/2026-0720-1815 计划）；
        margin 路径已被实验否决（QMainWindow 布局不采纳 menubar qss margin），
        故按 actionGeometry 实测项高定高。字号/主题变化后需重入本方法。
        """
        # 改动（文档/修改记录/2026-0730-0007 计划 T9）：读显式持有的菜单栏引用，
        # 不再走 window.menuBar()（菜单栏已迁出 QMainWindow 菜单槽）
        menu_bar = self._menu_bar
        if not menu_bar.actions():
            return
        first_item_rect = menu_bar.actionGeometry(menu_bar.actions()[0])
        menu_bar.setFixedHeight(first_item_rect.y() + first_item_rect.height())

    def _restore_window_state(self) -> None:
        """启动时恢复窗口几何与各处分隔栏（读写细节外移 WindowStateStore）。"""
        self._state_store = WindowStateStore(
            self,
            {
                KEY_SPLITTER_MAIN: self._splitter_main,
                KEY_SPLITTER_EDITOR: self._splitter_editor,
                KEY_SPLITTER_SIDEBAR: self._splitter_sidebar,
            },
            self.chat_tabs,
            self._workspace_root,
        )
        self._state_store.restore()

    def _restore_panels_visible(self) -> None:
        """隐藏面板先恢复可见：布局采集/保存前置（防 0 尺寸写入持久化）。"""
        for panel in (
            self.chat_tabs,
            self.file_explorer,
            self.terminal_panel,
            self.changes_panel,
        ):
            if not panel.isVisible():
                panel.setVisible(True)

    def _save_layout_state(self) -> None:
        """关闭时布局保存（closeEvent 单一入口）：恢复可见 + 写自身根 + default。"""
        self._restore_panels_visible()
        self._state_store.save()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时一次性保存窗口几何与四处分隔栏状态。"""
        # 面板隐藏时先恢复可见再保存：避免把 0 尺寸写入持久化（启动始终显示）
        self._save_layout_state()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 面板显隐（视图菜单勾选动作与面板内按钮汇入单一入口）
    # ------------------------------------------------------------------
    def _set_panel_visible(self, key: str, panel, is_visible: bool) -> None:
        """显隐单一入口：同步注册表勾选态与可见性（setChecked 不触发 triggered）。"""
        if action := self.menus.get(key):
            action.setChecked(is_visible)
        panel.setVisible(is_visible)

    def set_chat_visible(self, is_visible: bool) -> None:
        self._set_panel_visible(KEY_VIEW_CHAT, self.chat_tabs, is_visible)

    def set_explorer_visible(self, is_visible: bool) -> None:
        self._set_panel_visible(KEY_VIEW_EXPLORER, self.file_explorer, is_visible)

    def set_terminal_visible(self, is_visible: bool) -> None:
        self._set_panel_visible(KEY_VIEW_TERMINAL, self.terminal_panel, is_visible)

    def set_changes_visible(self, is_visible: bool) -> None:
        self._set_panel_visible(KEY_VIEW_CHANGES, self.changes_panel, is_visible)

    def reset_layout(self) -> None:
        """恢复默认布局：四组 splitter 回初始尺寸（面板显隐状态不变）。"""
        self._splitter_main.setSizes(self.DEFAULT_SIZES_MAIN)
        self._splitter_editor.setSizes(self.DEFAULT_SIZES_EDITOR)
        self._splitter_sidebar.setSizes(self.DEFAULT_SIZES_SIDEBAR)
        self.chat_tabs.reset_layout()
        self.statusBar().showMessage("已恢复默认布局", self.STATUS_MSG_SHORT_MS)

    # ------------------------------------------------------------------
    # 主题切换（视图菜单 ▸ 外观；QActionGroup 单回调读 data 载荷）
    # ------------------------------------------------------------------
    def switch_theme(self, theme: str) -> None:
        """切换主题：持久化 + 即时应用，并同步四面板各自的主题资源包。"""
        save_theme(theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
        self.viewer_panel.apply_theme(theme)
        self.terminal_panel.apply_theme(theme)
        self.file_explorer.apply_theme(theme)
        self.changes_panel.apply_theme(theme)
        self.chat_tabs.apply_theme(theme)
        # 标题栏内联 QSS 不受 app 级 qss 管辖，随链重刷（启动主题构造期已自检初始化）
        self.title_bar.apply_theme(theme)
        if self._settings_dialog is not None:
            # 设置中心内联 style（hint/分隔线）不受 app 级 qss 管辖，随链重刷
            self._settings_dialog.apply_theme(theme)
        if self._git_graph_dialog is not None:
            # 提交历史图委托自绘色/降级富文本色随链重渲染（缓存数据，不重复 spawn git）
            self._git_graph_dialog.apply_theme(theme)
        if action := self.menus.get(theme_action_key(theme)):
            action.setChecked(True)
        self._sync_settings_dialog()
        self.statusBar().showMessage(f"已切换为{get_label(theme)}主题", self.STATUS_MSG_TIMEOUT_MS)

    # ------------------------------------------------------------------
    # 文件菜单槽
    # ------------------------------------------------------------------
    def open_file_dialog(self) -> None:
        """打开文件：QFileDialog 选文件 → 查看器完整管线（高亮/徽标）。"""
        path, _ = QFileDialog.getOpenFileName(self, "打开文件", self.file_explorer.root_dir)
        if path:
            self.viewer_panel.open_file(path)

    def new_window(self) -> None:
        """新建窗口：以当前工作区根起新进程（同根多开，不弹对话框）。"""
        self._spawn_window(self._workspace_root)

    def open_folder_here(self) -> None:
        """打开文件夹（换根关旧窗）：选目录 → 预写布局 → 起新进程 → 探活关旧窗。

        进程级「替换当前工作区」（文档/修改记录/2026-0724-1806）：不做窗口内
        就地换根（2026-0722-1901 否决），等价手动「在新窗口打开文件夹」
        再关旧窗的合成一步。同根选择接受（语义 = 重启当前窗口），不开特例
        （对齐 open_recent_project 既有风格）。
        """
        path = QFileDialog.getExistingDirectory(
            self, "打开文件夹", self.file_explorer.root_dir)
        if not path:
            return
        target = Path(path).resolve()
        if not target.is_dir():
            # 防 main.py resolve_workspace_root 静默回退项目根：新窗开错根 +
            # 旧窗被关的双重灾难（对话框仅保证选中时刻存在，TOCTOU 兜底）
            self.statusBar().showMessage(
                "文件夹不存在，未做任何更改", self.STATUS_MSG_TIMEOUT_MS)
            return
        folder = str(target)
        # 预写布局（复制语义）：活窗口实时采集 → 目标根哈希文件 + default
        # 双写，新窗口无论目标根开过与否启动即读得当前布局
        self._restore_panels_visible()
        self._state_store.save_to(window_state_file_for(folder))
        proc = self._spawn_window(folder)
        self.statusBar().showMessage(
            f"正在打开：{folder}（新窗口就绪后本窗口将关闭）",
            self.STATUS_MSG_LONG_MS)
        QTimer.singleShot(self.SPAWN_PROBE_MS, self,
                          lambda: self._close_after_spawn(proc))

    def _close_after_spawn(self, proc: subprocess.Popen) -> None:
        """换根探活（singleShot 带 receiver：探活窗口期手动关窗自动取消）：
        新进程存活才关旧窗；启动即败保留旧窗报退出码。"""
        if proc.poll() is None:
            self.close()
        else:
            self.statusBar().showMessage(
                f"新窗口启动失败（退出码 {proc.returncode}），本窗口已保留",
                self.STATUS_MSG_LONG_MS)

    def open_folder_in_new_window(self) -> None:
        """在新窗口打开文件夹：QFileDialog 选目录 → 起新进程（取消无副作用）。

        多开模型（文档/修改记录/2026-0722-0756）：一进程绑定一工作区根，
        进程边界天然隔离工作区状态，不再窗口内切换。
        """
        path = QFileDialog.getExistingDirectory(
            self, "在新窗口打开文件夹", self.file_explorer.root_dir)
        if not path:
            return
        self._spawn_window(str(Path(path).resolve()))

    def _spawn_window(self, folder: str) -> subprocess.Popen:
        """起新进程开指定工作区根（新建窗口 / 在新窗口打开文件夹 / 打开
        文件夹换根共用）；返回进程句柄供换根场景启动探活。

        双态命令（文档/修改记录/2026-0725-1234 计划 T2）：打包态二进制自身
        即入口，拼 PROJECT_ROOT/main.py 指向的 _internal/main.py 在解包
        目录不存在（源码收在 PYZ 归档内），argv 多出的位置参数必致
        argparse exit 2、新窗秒死。

        AppImage 挂载生命周期修复（2026-07-25）：frozen 下 sys.executable
        指向 FUSE 临时挂载点内的二进制（/tmp/.mount_XXX/usr/bin/zen-studio），
        该挂载随父进程退出即卸载——换根探活 2s 后旧窗（AppImage 主进程）
        关闭 → 挂载消失 → 新窗 mmap 文件失效 SIGBUS 崩死（懒加载的 Qt
        插件/文件树图标使崩点延迟到操作文件树时，呈间歇性闪退）。
        改用 runtime 注入的 APPIMAGE 环境变量（.AppImage 实体文件路径）
        作入口，新进程自行挂载独立挂载点，与父进程生命周期解耦；
        路径不存在（变量被篡改/非 AppImage 的 frozen 形态）回退
        sys.executable（onedir 落盘目录稳定，无此问题）。
        """
        if IS_FROZEN:
            appimage = os.environ.get("APPIMAGE", "")
            entry = appimage if appimage and Path(appimage).is_file() else sys.executable
            cmd = [entry, folder]
        else:
            cmd = [sys.executable, str(PROJECT_ROOT / "main.py"), folder]
        proc = subprocess.Popen(cmd)
        self.statusBar().showMessage(
            f"已在新窗口打开：{folder}", self.STATUS_MSG_TIMEOUT_MS)
        return proc

    def open_recent_project(self, path: str) -> None:
        """最近项目回放：探活 → 起新进程绑定该工作区根（新窗口启动时经
        自身记录链自动置顶，无需手工再记）。

        目录已消失则从列表剔除并状态栏提示（与变更面板「文件已删除」
        提示风格一致）。点击项恰为当前工作区根时等价于「新建窗口」，
        接受、不开特例。
        """
        if Path(path).is_dir():
            self._spawn_window(path)
        else:
            self.recent_projects.remove(path)
            self.statusBar().showMessage(
                "文件夹已不存在，已从列表移除", self.STATUS_MSG_TIMEOUT_MS)

    def open_config_dir(self) -> None:
        """在系统文件管理器中打开 config/ 目录。"""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR)))

    # ------------------------------------------------------------------
    # 编辑菜单槽（复制/全选转发焦点控件；查找按焦点分发）
    # ------------------------------------------------------------------
    def focused_widget_supports(self, methods: tuple[str, ...]) -> bool:
        """焦点控件是否支持任一给定方法（编辑菜单 aboutToShow 启用态依据）。"""
        widget = QApplication.focusWidget()
        return widget is not None and any(hasattr(widget, m) for m in methods)

    def copy_focused(self) -> None:
        """复制：终端自绘控件走 copy_selection，其余走 Qt 标准 copy。"""
        widget = QApplication.focusWidget()
        if widget is None:
            return
        if hasattr(widget, "copy_selection"):
            widget.copy_selection()
        elif hasattr(widget, "copy"):
            widget.copy()

    def select_all_focused(self) -> None:
        widget = QApplication.focusWidget()
        if widget is not None and hasattr(widget, "selectAll"):
            widget.selectAll()

    def find_focused(self) -> None:
        """查找按焦点分发：焦点在终端 → 终端浮层；其余 → 查看器浮层。"""
        widget = QApplication.focusWidget()
        terminal = self.terminal_panel.terminal
        if widget is not None and (widget is terminal or terminal.isAncestorOf(widget)):
            self.terminal_panel.show_find()
        else:
            self.viewer_panel.show_find()

    # ------------------------------------------------------------------
    # 设置中心对话框（设置菜单 ▸ 设置中心…；非模态单例）
    # ------------------------------------------------------------------
    def open_settings_dialog(self) -> None:
        """打开设置中心：首次惰性创建，重复打开 raise 现有实例。"""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _sync_settings_dialog(self) -> None:
        """设置项经收敛点变化后同步对话框控件态（打开期间；reload 防回环）。

        _dialog_sync_suspend > 0（批量应用路径）时短路，由批末单次终态 reload。
        """
        if self._dialog_sync_suspend:
            return
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.reload()

    # ------------------------------------------------------------------
    # 提交历史图对话框（视图菜单 ▸ 提交历史图；非模态单例）
    # ------------------------------------------------------------------
    def show_git_graph(self) -> None:
        """打开提交历史图：首次惰性创建，重复打开 raise 现有实例。"""
        if self._git_graph_dialog is None:
            self._git_graph_dialog = GitGraphDialog(
                self.git_controller.service, self)
        self._git_graph_dialog.show()
        self._git_graph_dialog.raise_()
        self._git_graph_dialog.activateWindow()

    # ------------------------------------------------------------------
    # 设置菜单槽
    # ------------------------------------------------------------------
    #: 字号调整上下界（pt）
    FONT_SIZE_MIN = 8
    FONT_SIZE_MAX = 24

    def set_font_size(self, size: int) -> None:
        """字号绝对设定（设置中心对话框入口；钳制后走统一应用链）。"""
        size = max(self.FONT_SIZE_MIN, min(self.FONT_SIZE_MAX, size))
        self._apply_font_size(size)

    def _apply_font_size(self, size: int) -> None:
        """字号应用链：持久化 → 全局字体 → 查看器/终端等宽字号同步 → 设置中心重刷。"""
        update_settings({KEY_FONT_SIZE: size})
        if (app := QApplication.instance()) is not None:
            apply_theme(app)
        self._fit_statusbar_height()  # 字号变化后状态栏定高随字体度量重算
        QTimer.singleShot(0, self._fit_menubar_height)  # 菜单栏项高同理（延迟结算）
        self.viewer_panel.refresh_font()
        self.terminal_panel.refresh_font()
        if self._settings_dialog is not None:
            # 设置中心页标题/黑名单等宽区字号相对派生自全局字号，随链重刷
            # （须在 apply_theme(app) 重设全局字体之后调用）
            self._settings_dialog.apply_font_size()
        if self._git_graph_dialog is not None:
            # 提交历史图等宽文本区字号跟随全局（同查看器/终端先例）
            self._git_graph_dialog.refresh_font()
        self._sync_settings_dialog()
        self.statusBar().showMessage(f"字号：{size} pt", self.STATUS_MSG_SHORT_MS)

    def apply_model_selection(self, backend: str, version: str | None) -> None:
        """设置中心驱动的模型切换：收敛到 ChatTabs（只作用当前活动标签，
        2026-0803-0112 计划 D3）后同步设置中心控件态。"""
        self.chat_tabs.apply_model_selection(backend, version)
        self._sync_settings_dialog()

    def set_terminal_swap_copy_paste(self, checked: bool) -> None:
        """终端复制/粘贴快捷键反转：持久化 + 即时下发终端面板（无需重启）。"""
        update_settings({KEY_TERMINAL_SWAP_COPY_PASTE: checked})
        self.terminal_panel.set_swap_copy_paste(checked)
        self._sync_settings_dialog()
        hint = "Ctrl+C/V 复制粘贴" if checked else "Ctrl+Shift+C/V 复制粘贴"
        self.statusBar().showMessage(f"终端快捷键：{hint}", self.STATUS_MSG_SHORT_MS)

    def _on_modelbar_changed(self, _backend: str, _version: object) -> None:
        """模型选择变化（当前活动标签底行下拉/标签切换经 ChatTabs 转发）
        → 设置中心同步（reload 防回环）。"""
        self._sync_settings_dialog()

    def _on_chat_busy_changed(self, busy: bool) -> None:
        """当前活动标签响应中禁用设置中心模型页（与该标签底行三按钮
        禁用对齐，2026-0803-0112 计划 D4 粒度收窄）。"""
        if self._settings_dialog is not None:
            self._settings_dialog.set_model_enabled(not busy)

    def open_settings_file(self) -> None:
        """在只读查看器中打开 settings.json（AI-first：修改经 AI 落盘）。"""
        self.viewer_panel.open_file(str(SETTINGS_FILE))
        self.statusBar().showMessage("配置文件为只读查看；修改请经 AI 落盘", self.STATUS_MSG_LONG_MS)

    def reset_settings(self) -> None:
        """恢复默认设置：确认框（可保留窗口几何/分隔栏）→ 重置并即时应用。"""
        box = QMessageBox(self)
        box.setWindowTitle("恢复默认设置")
        box.setText("确定要恢复全部默认设置吗？")
        box.setInformativeText("主题、字号、模型选择等将重置为默认值。")
        keep = QCheckBox("保留窗口几何与分隔栏状态", box)
        keep.setChecked(True)
        box.setCheckBox(keep)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        patch = dict(DEFAULT_SETTINGS)
        update_settings(patch)
        if not keep.isChecked():
            self._state_store.reset()  # 仅删当前工作区的状态文件，重启回默认布局
            # 阻断同会话 closeEvent 回写当前布局（否则重置被静默撤销）
            self._state_store.disable_save()

        # 即时应用：主题（含四面板配色）→ 字号 → 模型 → 终端快捷键。
        # 挂起对话框 sync：各应用槽内置 _sync_settings_dialog 被抑制（否则 N 槽
        # 连发触发 N+1 次全量 reload，前 N 次读中间态即被覆盖），批末单次终态 reload
        settings = load_settings()
        self._dialog_sync_suspend += 1
        try:
            self.switch_theme(settings[KEY_THEME])
            self._apply_font_size(settings[KEY_FONT_SIZE])
            self.apply_model_selection(
                settings[KEY_MODEL_BACKEND],
                settings[KEY_MODEL_VERSIONS].get(settings[KEY_MODEL_BACKEND]))
            swap = settings[KEY_TERMINAL_SWAP_COPY_PASTE]  # 重置后为默认 False
            self.terminal_panel.set_swap_copy_paste(swap)
        finally:
            self._dialog_sync_suspend -= 1
        self._sync_settings_dialog()
        self.statusBar().showMessage("已恢复默认设置", self.STATUS_MSG_TIMEOUT_MS)

    # ------------------------------------------------------------------
    # 帮助菜单槽
    # ------------------------------------------------------------------
    def show_about(self) -> None:
        """关于对话框：Logo（64px 原生件）+ 版本（core/version 单一来源）/ 技术栈。"""
        box = QMessageBox(self)
        box.setWindowTitle("关于 Zen Studio")
        box.setText(
            f"<b>Zen Studio</b> {APP_VERSION}"
            "<p>Agent 驱动的桌面 IDE：本机 agent CLI 是唯一执笔人，代码修改一律经"
            "agent 落盘；IDE 本体只承担「看、问、跑」——只读查看器看代码，"
            "AI 聊天面板问问题，真 PTY 终端跑命令。</p>"
            "<p>技术栈：Python 3.12 + PySide6，Linux 桌面优先。</p>"
            "<p><b>站在巨人的肩膀上</b>：本项目设计参考了 Eclipse Theia"
            "（LanguageModel 协议与面板装配范式）、Visual Studio Code"
            "（workspaceStorage 窗口状态语义、SCM 变更面板形态）、PyGPT"
            "（界面视觉风格与部分控件实现）、Multi_Cli_Studio（多 CLI 后台接入形态）、"
            "kilocode（ACP 协议接入参考）。</p>"
            "<p>以 MIT 协议发布，致谢详见 NOTICE。</p>"
        )
        logo = LOGO_DIR / "logo_64.png"
        if logo.is_file():
            box.setIconPixmap(QPixmap(str(logo)))
        box.exec()
