"""文件树模型层：噪音过滤代理模型 + Git 状态着色。

Git 状态装饰（2026-07-20，见 work plans/2026-0720-0131_Git文件装饰与
差异统计实施计划.md 阶段二）：代理模型注入 GitStatusService，
ForegroundRole 按文件状态返回主题色（色值按主题名查 gui/theme.py
THEME_PALETTES 的 git_status 资源包）。默认仅文件着色，目录保持原色
（目录聚合着色为计划预留开关，本期不启用）。
"""
from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from core.git.service import GitStatusService
from gui.theme import git_status_color


class NoiseFilterProxyModel(QSortFilterProxyModel):
    """按名称排除噪音目录/文件的代理模型。

    注意：QFileSystemModel.setNameFilters 是"仅显示匹配项"语义，
    无法实现"排除式"过滤，故改用代理模型。
    """

    def __init__(self, noise_names: set[str], parent=None) -> None:
        super().__init__(parent)
        self.noise_names = noise_names
        self.filter_enabled = True
        #: Git 状态数据源与配色主题（None = 未启用着色，行为与纯过滤一致）
        self._git_service: GitStatusService | None = None
        self._theme = "cloud"

    # ------------------------------------------------------------------
    # 噪音过滤
    # ------------------------------------------------------------------
    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self.filter_enabled:
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
        if (
            role == Qt.ItemDataRole.ForegroundRole
            and self._git_service is not None
            and self._git_service.enabled
        ):
            source_index = self.mapToSource(proxy_index)
            model = self.sourceModel()
            # 目录不着色（目录聚合为预留开关，默认关闭）
            if not model.isDir(source_index):
                status = self._git_service.status_of(model.filePath(source_index))
                if status is not None:
                    color = git_status_color(self._theme, status)
                    if color is not None:
                        return QColor(color)
        return super().data(proxy_index, role)
