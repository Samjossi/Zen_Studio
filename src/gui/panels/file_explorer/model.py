"""文件树模型层：Git 状态着色代理模型。

Git 状态装饰（2026-07-20，见 文档/修改记录/2026-0720-0131_Git文件装饰与
差异统计实施计划.md 阶段二）：代理模型注入 GitStatusService，
ForegroundRole 按文件状态返回主题色（色值按主题名查 gui/theme.py
THEME_PALETTES 的 git_status 资源包）。

目录聚合着色（2026-07-25，见 文档/修改记录/2026-0725-0933_文件树Git状态颜色
目录冒泡计划.md；2026-07-30 修订，见 文档/修改记录/2026-0730-1940_忽略灰色
着色不透传父目录修复计划.md）：目录按子树内可冒泡状态的最高优先级着色
（conflict > modified > untracked；deleted/ignored 不冒泡——ignored
仅自身暗显，不透传父目录），聚合缓存在服务层 refresh() 时预构建，
此处查询 O(1)。被 gitignore 整体命中的目录经 status_of_dir 下透
兜底，自身与子孙目录同显暗色（2026-07-30，见 文档/修改记录/
2026-0730-2025_忽略目录灰色下透子目录修复计划.md 及其 ls-files
数据源修订——对齐 VS Code：ignored 目录内容整体暗显，任意深度）。

噪音过滤移除（2026-07-30，见 文档/修改记录/2026-0730-1933_移除噪音过滤
与全量文件可见改造计划.md）：IDE 全量可见（含 dotfile 与
.git/.venv/__pycache__/node_modules），代理不再承担过滤职责；
基类 QSortFilterProxyModel → QIdentityProxyModel（索引映射直通，
着色 data() 唯一保留职责），类随之改名归位。
"""
from PySide6.QtCore import QIdentityProxyModel, Qt
from PySide6.QtGui import QColor

from core.git.service import GitStatusService
from gui.theme import FALLBACK_THEME, git_status_color


class GitStatusProxyModel(QIdentityProxyModel):
    """Git 状态着色代理：不改结构（索引映射直通），仅覆写 ForegroundRole。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        #: Git 状态数据源与配色主题（None = 未启用着色）
        self._git_service: GitStatusService | None = None
        self._theme = FALLBACK_THEME

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

        目录查聚合状态（status_of_dir：子树内最高优先级，deleted/ignored
        不冒泡；缓存未命中时按 ignored 目录键下透暗显，含目录自身），
        文件查自身状态（status_of）；仓库根目录恒不着色
        （服务层不缓存根）。
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
