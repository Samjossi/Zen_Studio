"""文件树主控件：移植自 PyGPT explorer 的裁剪版。

基于 QTreeView + QFileSystemModel（Qt 原生，自带懒加载与系统图标）。
无任何 window 式上帝对象依赖，可独立实例化。

对外信号：
    file_opened(str) — 双击文件时发射，参数为文件绝对路径，
                       供后续编辑器模块接入。

子包拆分：模型层见 model.py（噪音过滤，git 装饰预留），右键动作见 actions.py。
"""
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl, Signal, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.git.service import GitStatusService
from gui.panels.file_explorer.actions import ExplorerActions
from gui.panels.file_explorer.model import NoiseFilterProxyModel
from gui.settings import KEY_THEME
from gui.theme import load_settings


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

    #: 噪音目录/文件过滤清单
    NOISE_NAMES = {"__pycache__", ".git", ".venv", "node_modules"}

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
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._build_model()
        self._build_tree()
        # 右键动作集：组合注入（依赖显式化，见 actions.py 构造函数签名）
        self._actions = ExplorerActions(
            host=self,
            tree=self.tree,
            file_path_of=self._file_path,
            selected_paths=self._selected_paths,
            anchor_dir=self._anchor_dir,
            open_file=self.file_opened.emit,
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

    def _build_model(self) -> None:
        """文件系统模型 + 噪音过滤代理装配。"""
        self.model = QFileSystemModel(self)
        self.model.setRootPath(self.root_dir)
        self.model.setReadOnly(False)  # 允许重命名编辑

        self.proxy = NoiseFilterProxyModel(self.NOISE_NAMES, self)
        self.proxy.setSourceModel(self.model)

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
    def set_root(self, root_dir: str) -> None:
        """切换文件树根目录（打开文件夹/工作区切换）：树内容、标题、
        右键新建落点（_anchor_dir 读 root_dir）随根更新。"""
        self.root_dir = str(Path(root_dir).resolve())
        self.model.setRootPath(self.root_dir)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root_dir)))
        self.path_label.setText(Path(self.root_dir).name)
        self.path_label.setToolTip(self.root_dir)

    def set_noise_filter(self, enabled: bool) -> None:
        """切换噪音过滤（隐藏 __pycache__、.git、.venv、node_modules）。"""
        self.proxy.is_filter_enabled = enabled
        self.proxy.invalidateFilter()

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
