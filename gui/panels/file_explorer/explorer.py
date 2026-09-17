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
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QItemSelectionModel, QMimeData, QUrl, Signal, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
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
        """目录异步加载完成：接力回填手动刷新前的展开状态与选中项。"""
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
    # 内部：悬浮刷新钮（定位 / hover 透明度）
    # ------------------------------------------------------------------
    def _place_refresh_button(self) -> None:
        """悬浮刷新钮右下角定位（面板坐标系，浮于树之上）。"""
        btn = self._refresh_btn
        btn.move(self.width() - btn.width() - _REFRESH_BTN_MARGIN,
                 self.height() - btn.height() - _REFRESH_BTN_MARGIN)
        btn.raise_()

    def resizeEvent(self, event) -> None:
        """基类布局后重定位悬浮刷新钮。"""
        super().resizeEvent(event)
        self._place_refresh_button()

    def eventFilter(self, watched, event) -> bool:
        """悬浮刷新钮 hover 透明度：进入恢复 1.0，离开回落常态值。
        （构造早期事件路径可能先于按钮创建触发本过滤器，
        getattr 守卫防 AttributeError）"""
        if watched is getattr(self, "_refresh_btn", None):
            if event.type() == QEvent.Type.Enter:
                self._refresh_btn_opacity.setOpacity(1.0)
            elif event.type() == QEvent.Type.Leave:
                self._refresh_btn_opacity.setOpacity(_REFRESH_BTN_OPACITY)
        return super().eventFilter(watched, event)

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
