"""文件树模型层：噪音过滤代理模型 + Git 状态着色。

Git 状态装饰（2026-07-20，见 文档/修改记录/2026-0720-0131_Git文件装饰与
差异统计实施计划.md 阶段二）：代理模型注入 GitStatusService，
ForegroundRole 按文件状态返回主题色（色值按主题名查 gui/theme.py
THEME_PALETTES 的 git_status 资源包）。

目录聚合着色（2026-07-25，见 work plans/2026-0725-0933_文件树Git状态颜色
目录冒泡计划.md）：目录按子树内可冒泡状态的最高优先级着色
（conflict > modified > untracked > ignored；deleted 不冒泡），
聚合缓存在服务层 refresh() 时预构建，此处查询 O(1)。
"""
from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from core.git.service import GitStatusService
from gui.theme import FALLBACK_THEME, git_status_color


class NoiseFilterProxyModel(QSortFilterProxyModel):
    """按名称排除噪音目录/文件的代理模型。

    注意：QFileSystemModel.setNameFilters 是"仅显示匹配项"语义，
    无法实现"排除式"过滤，故改用代理模型。
    """

    def __init__(self, noise_names: set[str], parent=None) -> None:
        super().__init__(parent)
        self.noise_names = noise_names
        self.is_filter_enabled = True
        #: Git 状态数据源与配色主题（None = 未启用着色，行为与纯过滤一致）
        self._git_service: GitStatusService | None = None
        self._theme = FALLBACK_THEME

    # ------------------------------------------------------------------
    # 噪音过滤
    # ------------------------------------------------------------------
    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self.is_filter_enabled:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        return model.fileName(index) not in self.noise_names

    # ------------------------------------------------------------------
    # Git 状态着色
    # ------------------------------------------------------------------
    def set_git_service(self, service: GitStatusService | None, theme: str) -> None:
        """注入 Git 状态服务与配色主题；随后调用 refresh_colors() 生效。"""
        self._git_service = service
        self._theme = theme

    def refresh_colors(self) -> None:
        """触发整树重绘（状态映射刷新后调用）。"""
        self.layoutChanged.emit()

    def data(self, proxy_index, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ForegroundRole:
            color = self._git_status_color_of(proxy_index)
            if color is not None:
                return color
        return super().data(proxy_index, role)

    def _git_status_color_of(self, proxy_index) -> QColor | None:
        """代理索引 → Git 状态色（服务未启用/无状态/无配色均为 None）。

        目录查聚合状态（status_of_dir：子树内最高优先级，deleted 不冒泡），
        文件查自身状态（status_of）；仓库根目录恒不着色（服务层不缓存根）。
        """
        if self._git_service is None or not self._git_service.is_enabled:
            return None
        source_index = self.mapToSource(proxy_index)
        model = self.sourceModel()
        abs_path = model.filePath(source_index)
        if model.isDir(source_index):
            file_status = self._git_service.status_of_dir(abs_path)
        else:
            file_status = self._git_service.status_of(abs_path)
        if file_status is None:
            return None
        color = git_status_color(self._theme, file_status)
        return QColor(color) if color is not None else None
