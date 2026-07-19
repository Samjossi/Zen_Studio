"""文件树主控件：移植自 PyGPT explorer 的裁剪版。

基于 QTreeView + QFileSystemModel（Qt 原生，自带懒加载与系统图标）。
无任何 window 式上帝对象依赖，可独立实例化。

对外信号：
    file_opened(str) — 双击文件时发射，参数为文件绝对路径，
                       供后续编辑器模块接入。

子包拆分：模型层见 model.py（噪音过滤，git 装饰预留），右键动作见 actions.py。
"""
from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from gui.panels.file_explorer.actions import ExplorerActionsMixin
from gui.panels.file_explorer.model import NoiseFilterProxyModel


class FileExplorer(ExplorerActionsMixin, QWidget):
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
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setObjectName("SidePanel")  # 侧栏灰底分区（主题 qss 统一着色）
        # 自定义 QWidget 子类的 qss 背景需 WA_StyledBackground 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.model = QFileSystemModel(self)
        self.model.setRootPath(self.root_dir)
        self.model.setReadOnly(False)  # 允许重命名编辑

        self.proxy = NoiseFilterProxyModel(self.NOISE_NAMES, self)
        self.proxy.setSourceModel(self.model)

        self.tree = QTreeView(self)
        self.tree.setModel(self.proxy)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root_dir)))
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 禁止双击/按键触发重命名编辑（防误操作）；
        # 重命名仅经右键菜单以 tree.edit() 编程式触发，不受此限
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._open_context_menu)
        self.tree.doubleClicked.connect(self._on_double_clicked)

        # 只显示名称列，隐藏大小/类型/修改时间列
        self.tree.setHeaderHidden(True)
        for col in range(1, self.model.columnCount()):
            self.tree.hideColumn(col)

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

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def set_noise_filter(self, enabled: bool) -> None:
        """切换噪音过滤（隐藏 __pycache__、.git、.venv、node_modules）。"""
        self.proxy.filter_enabled = enabled
        self.proxy.invalidateFilter()

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
