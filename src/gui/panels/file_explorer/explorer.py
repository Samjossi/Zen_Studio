"""文件树主控件：移植自 PyGPT explorer 的裁剪版。

基于 QTreeView + QFileSystemModel（Qt 原生，自带懒加载与系统图标）。
无任何 window 式上帝对象依赖，可独立实例化。

对外信号：
    file_opened(str) — 双击文件时发射，参数为文件绝对路径，
                       供后续编辑器模块接入。

子包拆分：模型层见 model.py（Git 状态着色代理），右键动作见 actions.py。
"""
from collections.abc import Callable
import logging
import os
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QItemSelectionModel, QMimeData, QUrl, Signal, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.git.service import GitStatusService
from gui.panels.file_explorer.actions import ExplorerActions
from gui.panels.file_explorer.model import GitStatusProxyModel
from gui.settings import KEY_THEME
from gui.theme import load_settings

logger = logging.getLogger(__name__)


#: 悬浮刷新钮与面板右下缘的间距（样式复刻对话栏「回到底部」钮）
_REFRESH_BTN_MARGIN = 16

#: 悬浮刷新钮直径（圆形钮，内嵌顺时针环形箭头 ⟳ U+27F3）
_REFRESH_BTN_SIZE = 32

#: 悬浮刷新钮常态透明度：低调不挡树内容，hover 时恢复 1.0
_REFRESH_BTN_OPACITY = 0.5

#: 悬浮搜索钮与面板左下缘的间距（数值与刷新钮一致，独立命名便于单独微调）
_SEARCH_BTN_MARGIN = 16

#: 悬浮搜索钮直径（圆形钮，内嵌 ⌕ U+2315；字形墨盒偏小，qss 字号已放大补偿）
_SEARCH_BTN_SIZE = 32

#: 悬浮搜索钮常态透明度
_SEARCH_BTN_OPACITY = 0.5

#: 单次搜索命中上限：防爆兜底，大目录（.venv/node_modules 范围）遍历
#: 到上限即截断，避免 UI 线程被 os.walk 长时间占用
_SEARCH_MAX_RESULTS = 200


class _DragOutTreeView(QTreeView):
    """仅拖出、不接收的树视图（拖拽到 AI 输入框插入 @路径 用）。

    拖拽数据由 paths_provider 回调提供（当前选中项的绝对路径列表），
    只写 URLs —— QMimeData.setUrls 会自动生成 text/uri-list，等价于
    PyGPT 手写双写格式，天然兼容系统文件管理器等外部投放/拖出来源。
    drag 限定 CopyAction：QFileSystemModel 为可写模型，不限定的话
    拖到接收 MoveAction 的目标会触发文件移动语义。
    """

    #: 返回当前选中项绝对路径列表的回调（由 FileExplorer 注入）
    paths_provider: Callable[[], list[str]] | None = None

    def startDrag(self, supported_actions) -> None:
        if self.paths_provider is None:
            return super().startDrag(supported_actions)
        urls = [QUrl.fromLocalFile(p) for p in self.paths_provider()]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class FileExplorer(QWidget):
    """目录文件浏览器（右栏面板）。"""

    file_opened = Signal(str)

    #: 文件系统模型实例被重建后发射（refresh 换实例）：消费方须对
    #: self.model 重新接线（旧实例上的连接随 deleteLater 一并失效）
    model_rebuilt = Signal()

    #: 面板最小宽度（px）：根级最长文件名省略号截断/横向滚动条出现前的阈值
    #: （实测内容理想宽度 230px，定 240 含跨机器余量）；
    #: 配合主窗口 splitter.setCollapsible(2, False) 生效；调优方法见
    #: 文档/修改记录/2026-0719-0610_面板最小尺寸与苹果风界面改造计划.md 2.3 节
    MIN_WIDTH = 240

    def __init__(self, root_dir: str, parent: QWidget | None = None) -> None:
        """
        :param root_dir: 文件树根目录（绝对路径）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.root_dir = str(Path(root_dir).resolve())
        #: 已注入的 Git 状态服务（apply_git_status 设置，主题切换时复用）
        self._git_service = None
        #: 手动刷新待回填状态（expanded 路径集, selected 路径集）；None = 无刷新任务
        self._refresh_restore: tuple[set[str], set[str]] | None = None
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._build_model()
        self._build_tree()
        # 手动刷新的展开/选中回填接力（无刷新任务时槽内立即返回）
        self.model.directoryLoaded.connect(self._on_directory_loaded)
        # 右键动作集：组合注入（依赖显式化，见 actions.py 构造函数签名）
        self._actions = ExplorerActions(
            host=self,
            tree=self.tree,
            file_path_of=self._file_path,
            selected_paths=self._selected_paths,
            anchor_dir=self._anchor_dir,
            open_file=self.file_opened.emit,
            workspace_root=self.root_dir,
        )
        self.tree.customContextMenuRequested.connect(self._actions.open_context_menu)

        # 只显示根目录名，完整路径悬停可见（修复栏宽截断问题）
        self.path_label = QLabel(Path(self.root_dir).name)
        self.path_label.setObjectName("PanelTitle")  # 样式由主题 qss 统一
        self.path_label.setToolTip(self.root_dir)
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(self.path_label)
        header.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.tree)
        # 面板外边距：树卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）
        layout.setContentsMargins(6, 6, 6, 6)

        self._build_refresh_button()
        self._reset_search_state()
        self._build_search_bar()
        self._build_search_button()
        # 搜索范围快照随用户手动改选更新；程序化点亮（_drill_search_reveal）
        # 也会改 currentIndex，须经 _search_revealing 标志排除，否则范围会
        # 随命中位置收窄、后续输入搜不到快照外的匹配
        self.tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        # refresh 重建模型后索引全失效，搜索状态须随之复位（搜索词保留）
        self.model_rebuilt.connect(self._on_model_rebuilt_reset_search)

    def _build_refresh_button(self) -> None:
        """悬浮刷新钮：watcher 漏事件时树滞留旧结构而菜单入口太隐蔽，
        故常驻右下角低调圆形钮（样式复刻对话栏「回到底部」钮），
        点击即调 refresh() 强制重建。"""
        self._refresh_btn = QToolButton(self)  # parent 挂面板自身，浮于树之上
        self._refresh_btn.setObjectName("FileTreeRefreshButton")
        self._refresh_btn.setText("⟳")
        self._refresh_btn.setToolTip("刷新文件树")
        self._refresh_btn.setFixedSize(_REFRESH_BTN_SIZE, _REFRESH_BTN_SIZE)
        self._refresh_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        # 整体半透明（QGraphicsOpacityEffect 整钮含符号一起变淡，
        # 不碰主题色键、四主题通吃；Enter/Leave 经 eventFilter 恢复与回落）
        self._refresh_btn_opacity = QGraphicsOpacityEffect(self._refresh_btn)
        self._refresh_btn_opacity.setOpacity(_REFRESH_BTN_OPACITY)
        self._refresh_btn.setGraphicsEffect(self._refresh_btn_opacity)
        self._refresh_btn.installEventFilter(self)
        self._refresh_btn.show()
        self._refresh_btn.raise_()

    def _build_search_button(self) -> None:
        """悬浮搜索钮：与刷新钮同范式镜像到左下角，
        点击展开/收起底部搜索栏（_toggle_search_bar）。"""
        self._search_btn = QToolButton(self)  # parent 挂面板自身，浮于树之上
        self._search_btn.setObjectName("FileTreeSearchButton")
        self._search_btn.setText("⌕")
        self._search_btn.setToolTip("搜索文件树")
        self._search_btn.setFixedSize(_SEARCH_BTN_SIZE, _SEARCH_BTN_SIZE)
        self._search_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.clicked.connect(self._toggle_search_bar)
        self._search_btn_opacity = QGraphicsOpacityEffect(self._search_btn)
        self._search_btn_opacity.setOpacity(_SEARCH_BTN_OPACITY)
        self._search_btn.setGraphicsEffect(self._search_btn_opacity)
        self._search_btn.installEventFilter(self)
        self._search_btn.show()
        self._search_btn.raise_()

    def _build_search_bar(self) -> None:
        """底部搜索栏：默认隐藏的一行输入框，样式吃全局 QLineEdit 规则。
        无命中经动态属性 noMatch 走 qss 警示色，不落硬编码色值。"""
        self._search_bar = QLineEdit(self)
        self._search_bar.setObjectName("FileTreeSearchBar")
        self._search_bar.setPlaceholderText("搜索当前位置…")
        self._search_bar.setClearButtonEnabled(True)
        self._search_bar.hide()
        self.layout().addWidget(self._search_bar)
        self._search_bar.installEventFilter(self)
        self._search_bar.textChanged.connect(self._on_search_text_changed)

    def _build_model(self) -> None:
        """文件系统模型 + Git 状态着色代理装配。"""
        self.model = self._create_model()
        self.proxy = GitStatusProxyModel(self)
        self.proxy.setSourceModel(self.model)

    def _create_model(self) -> QFileSystemModel:
        """创建并配置文件系统模型（初始装配与 refresh 重建共用，保证两路参数一致）。"""
        model = QFileSystemModel(self)
        model.setRootPath(self.root_dir)
        model.setReadOnly(False)  # 允许重命名编辑
        # Qt 默认 filter（Dirs|Files|Drives|AllDirs|NoDot|NoDotDot）不含
        # Hidden，dotfile 永不入模型；IDE 须全量可见（含 .gitignore 与
        # .git/.venv/__pycache__/node_modules），见 文档/修改记录/2026-0730-1933 计划
        model.setFilter(
            QDir.Filter.AllEntries
            | QDir.Filter.AllDirs
            | QDir.Filter.NoDotAndDotDot
            | QDir.Filter.Hidden
        )
        return model

    def _build_tree(self) -> None:
        """树视图装配：拖出/选择/重命名策略 + 仅名称列。"""
        self.tree = _DragOutTreeView(self)
        self.tree.paths_provider = self._selected_paths  # 拖出数据 = 当前选中项
        self.tree.setDragEnabled(True)  # 允许拖出（dragDropMode 随之变 DragOnly）
        # 树自身不接收拖放：本期不做树内拖拽移动，防误拖触发文件移动
        self.tree.setAcceptDrops(False)
        self.tree.setDropIndicatorShown(False)
        self.tree.setModel(self.proxy)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root_dir)))
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 禁止双击/按键触发重命名编辑（防误操作）；
        # 重命名仅经右键菜单以 tree.edit() 编程式触发，不受此限
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.doubleClicked.connect(self._on_double_clicked)

        # 只显示名称列，隐藏大小/类型/修改时间列
        self.tree.setHeaderHidden(True)
        for col in range(1, self.model.columnCount()):
            self.tree.hideColumn(col)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def apply_git_status(self, service: GitStatusService, theme: str | None = None) -> None:
        """注入 Git 状态服务并重绘着色（theme 缺省取当前主题）。"""
        if theme is None:
            theme = load_settings()[KEY_THEME]
        self._git_service = service
        self.proxy.set_git_service(service if service.is_enabled else None, theme)
        self.proxy.refresh_colors()

    def apply_theme(self, theme: str) -> None:
        """主题切换时同步 Git 状态色所属主题（未注入服务时无副作用）。"""
        self.proxy.set_git_service(
            self._git_service if self._git_service is not None and self._git_service.is_enabled else None,
            theme,
        )
        self.proxy.refresh_colors()

    def refresh(self) -> None:
        """手动刷新文件树（视图菜单「刷新文件树」入口 + 右下角悬浮钮）。

        QFileSystemModel 依赖系统 watcher 增量更新，watcher 溢出
        （inotify 上限/网络盘/外部批量改动）时状态会滞留；滞留的深层
        节点 populated 标记存活（canFetchMore=False），脱根重挂只能重列
        根层、救不回深层——故整体重建模型实例物理消灭节点缓存，
        展开状态与选中项经 directoryLoaded 异步回填，刷新后用户视角树形不变。
        """
        expanded: set[str] = set()
        self._collect_expanded(self.tree.rootIndex(), expanded)
        selected = set(self._selected_paths())
        self._refresh_restore = (expanded, selected)
        logger.info(
            "文件树手动刷新开始：重建模型实例，待回填展开 %d 项、选中 %d 项",
            len(expanded), len(selected),
        )

        old_model = self.model
        self.model = self._create_model()
        self.model.directoryLoaded.connect(self._on_directory_loaded)
        self.proxy.setSourceModel(self.model)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root_dir)))
        old_model.deleteLater()  # 旧实例上的外部连接随之一并失效
        self.model_rebuilt.emit()

    # ------------------------------------------------------------------
    # 内部：手动刷新的展开/选中状态回填
    # ------------------------------------------------------------------
    def _collect_expanded(self, parent_index, out: set[str]) -> None:
        """递归收集已展开节点的文件路径（未展开节点子项未入模型，rowCount=0 自然剪枝）。"""
        for row in range(self.proxy.rowCount(parent_index)):
            index = self.proxy.index(row, 0, parent_index)
            if self.tree.isExpanded(index):
                out.add(self._file_path(index))
                self._collect_expanded(index, out)

    def _on_directory_loaded(self, path: str) -> None:
        """目录异步加载完成：接力搜索定位钻取与手动刷新的展开/选中回填。"""
        if self._search_pending is not None:
            self._drill_search_reveal()
        if self._refresh_restore is None:
            return
        expanded, selected = self._refresh_restore
        parent = self.proxy.mapFromSource(self.model.index(path))
        for row in range(self.proxy.rowCount(parent)):
            index = self.proxy.index(row, 0, parent)
            file_path = self._file_path(index)
            if file_path in expanded:
                # expand 触发该节点异步加载，directoryLoaded 接力深层回填
                self.tree.expand(index)
                expanded.discard(file_path)
            if file_path in selected:
                self.tree.selectionModel().select(
                    index, QItemSelectionModel.SelectionFlag.Select
                           | QItemSelectionModel.SelectionFlag.Rows)
                selected.discard(file_path)
        if not expanded and not selected:
            self._refresh_restore = None
            logger.info("文件树手动刷新完成：展开/选中状态已回填")

    # ------------------------------------------------------------------
    # 内部：悬浮钮（定位 / hover 透明度）
    # ------------------------------------------------------------------
    def _floating_bottom_offset(self) -> int:
        """悬浮钮底部让位量：搜索栏展开时抬到栏顶之上，避免遮挡。"""
        bar = getattr(self, "_search_bar", None)
        if bar is not None and bar.isVisible():
            return _SEARCH_BTN_MARGIN + bar.height()
        return _SEARCH_BTN_MARGIN

    def _place_refresh_button(self) -> None:
        """悬浮刷新钮右下角定位（面板坐标系，浮于树之上）。"""
        btn = self._refresh_btn
        btn.move(self.width() - btn.width() - _REFRESH_BTN_MARGIN,
                 self.height() - btn.height() - self._floating_bottom_offset())
        btn.raise_()

    def _place_search_button(self) -> None:
        """悬浮搜索钮左下角定位（面板坐标系，浮于树之上）。"""
        btn = self._search_btn
        btn.move(_SEARCH_BTN_MARGIN,
                 self.height() - btn.height() - self._floating_bottom_offset())
        btn.raise_()

    def resizeEvent(self, event) -> None:
        """基类布局后重定位悬浮钮。"""
        super().resizeEvent(event)
        self._place_refresh_button()
        self._place_search_button()

    def eventFilter(self, watched, event) -> bool:
        """悬浮钮 hover 透明度（进入恢复 1.0，离开回落常态值）与
        搜索栏按键（Esc 收起、Enter/Shift+Enter 循环命中）。
        （构造早期事件路径可能先于控件创建触发本过滤器，
        getattr 守卫防 AttributeError）"""
        if watched is getattr(self, "_refresh_btn", None):
            if event.type() == QEvent.Type.Enter:
                self._refresh_btn_opacity.setOpacity(1.0)
            elif event.type() == QEvent.Type.Leave:
                self._refresh_btn_opacity.setOpacity(_REFRESH_BTN_OPACITY)
        elif watched is getattr(self, "_search_btn", None):
            if event.type() == QEvent.Type.Enter:
                self._search_btn_opacity.setOpacity(1.0)
            elif event.type() == QEvent.Type.Leave:
                self._search_btn_opacity.setOpacity(_SEARCH_BTN_OPACITY)
        elif watched is getattr(self, "_search_bar", None) \
                and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._toggle_search_bar()
                self.tree.setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                backwards = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._step_search_match(-1 if backwards else 1)
                return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # 内部：搜索（范围 / 匹配 / 定位接力）
    # ------------------------------------------------------------------
    def _toggle_search_bar(self) -> None:
        """展开/收起搜索栏；展开时对焦点位置做范围快照，收起时清空搜索状态。"""
        if self._search_bar.isVisible():
            self._search_bar.hide()
            self._reset_search_state()
            self._search_scope = None
            self._set_search_no_match(False)
        else:
            self._search_scope = self._current_focus_dir()
            self._search_bar.show()
            self._search_bar.setFocus()
        # 显隐改变让位量，悬浮钮立即重定位（面板自身无 resize 事件可等）
        self.layout().activate()
        self._place_refresh_button()
        self._place_search_button()

    def _reset_search_state(self) -> None:
        """清空匹配列表、当前命中序号与定位挂起；不动搜索栏文本与范围快照。"""
        self._search_matches: list[str] = []
        self._search_cursor = -1
        self._search_pending: str | None = None
        self._search_revealing = False

    def _on_model_rebuilt_reset_search(self) -> None:
        """refresh 重建模型后索引全失效：匹配状态复位，搜索词保留待用户重搜。"""
        self._reset_search_state()
        self._set_search_no_match(False)

    def _set_search_no_match(self, flag: bool) -> None:
        """无命中警示：动态属性驱动 qss 变色，须 unpolish/polish 才生效。"""
        self._search_bar.setProperty("noMatch", flag)
        self._search_bar.style().unpolish(self._search_bar)
        self._search_bar.style().polish(self._search_bar)

    def _dir_of_path(self, path: str) -> str | None:
        """路径 → 搜索目录：目录即自身，文件取父目录；无效返回 None。"""
        if not path:
            return None
        p = Path(path)
        if p.is_dir():
            return str(p)
        if p.parent.is_dir():
            return str(p.parent)
        return None

    def _current_focus_dir(self) -> str:
        """树当前焦点位置对应的搜索目录；无焦点回退根目录。"""
        index = self.tree.currentIndex()
        if index.isValid():
            scope = self._dir_of_path(self._file_path(index))
            if scope is not None:
                return scope
        return self.root_dir

    def _on_tree_current_changed(self, current, _previous) -> None:
        """用户手动改选时更新搜索范围快照（程序化点亮经标志位排除）。"""
        if self._search_revealing:
            return
        bar = getattr(self, "_search_bar", None)
        if bar is None or not bar.isVisible() or not current.isValid():
            return
        scope = self._dir_of_path(self._file_path(current))
        if scope is not None:
            self._search_scope = scope

    def _search_scope_dir(self) -> str:
        """搜索范围 = 搜索栏展开时的焦点快照（用户改选会更新），兜底根目录。"""
        return getattr(self, "_search_scope", None) or self.root_dir

    def _collect_matches(self, text: str) -> list[str]:
        """范围目录下文件名大小写不敏感子串匹配（os.walk 先序，上限截断）。
        直接扫文件系统而不查模型：懒加载下未展开目录的节点不在模型里。"""
        needle = text.casefold()
        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._search_scope_dir()):
            for name in dirnames + filenames:
                if needle in name.casefold():
                    matches.append(os.path.join(dirpath, name))
                    if len(matches) >= _SEARCH_MAX_RESULTS:
                        return matches
        return matches

    def _on_search_text_changed(self, text: str) -> None:
        """输入即重搜并定位首个命中；空串或无命中时复位/警示。"""
        if not text:
            self._reset_search_state()
            self._set_search_no_match(False)
            return
        self._search_matches = self._collect_matches(text)
        self._search_cursor = -1
        self._search_pending = None
        self._set_search_no_match(not self._search_matches)
        if self._search_matches:
            self._step_search_match(1)

    def _step_search_match(self, step: int) -> None:
        """循环定位下一个/上一个命中。"""
        if not self._search_matches:
            return
        self._search_cursor = (self._search_cursor + step) % len(self._search_matches)
        self._reveal_search_match(self._search_matches[self._search_cursor])

    def _reveal_search_match(self, path: str) -> None:
        """点亮命中项：懒加载下深层索引可能未就绪，
        挂起为 _search_pending 由 directoryLoaded 接力钻取（_drill_search_reveal）。"""
        self._search_pending = path
        self._drill_search_reveal()

    def _drill_search_reveal(self) -> None:
        """向命中路径钻取一级：model.index(path) 对未加载层触发异步拉取，
        未就绪返回无效索引等下一轮 directoryLoaded；就绪后展开祖先并点亮。"""
        target = self._search_pending
        if target is None:
            return
        src = self.model.index(target)
        if not src.isValid():
            return
        index = self.proxy.mapFromSource(src)
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
        self._search_revealing = True
        try:
            self.tree.setCurrentIndex(index)
        finally:
            self._search_revealing = False
        self.tree.scrollTo(index)
        self._search_pending = None

    # ------------------------------------------------------------------
    # 内部：选中项辅助
    # ------------------------------------------------------------------
    def _file_path(self, proxy_index) -> str:
        """代理索引 → 文件系统路径。"""
        return self.model.filePath(self.proxy.mapToSource(proxy_index))

    def _selected_paths(self) -> list[str]:
        """返回当前选中的所有文件系统路径。"""
        paths = []
        for index in self.tree.selectionModel().selectedRows(0):
            paths.append(self._file_path(index))
        return paths

    def _anchor_dir(self) -> Path:
        """新建文件/目录的落点：选中目录或其父目录，未选中则为根目录。"""
        paths = self._selected_paths()
        if not paths:
            return Path(self.root_dir)
        p = Path(paths[0])
        return p if p.is_dir() else p.parent

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_double_clicked(self, index) -> None:
        path = Path(self._file_path(index))
        if path.is_file():
            self.file_opened.emit(str(path))
