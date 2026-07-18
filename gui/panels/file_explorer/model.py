"""文件树模型层：噪音过滤代理模型。

后续 git 状态装饰（脏文件着色/标记）等模型扩展亦归此层。
"""
from PySide6.QtCore import QSortFilterProxyModel


class NoiseFilterProxyModel(QSortFilterProxyModel):
    """按名称排除噪音目录/文件的代理模型。

    注意：QFileSystemModel.setNameFilters 是"仅显示匹配项"语义，
    无法实现"排除式"过滤，故改用代理模型。
    """

    def __init__(self, noise_names: set[str], parent=None) -> None:
        super().__init__(parent)
        self.noise_names = noise_names
        self.filter_enabled = True

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self.filter_enabled:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        return model.fileName(index) not in self.noise_names
