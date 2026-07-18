"""文件树面板：移植自 PyGPT explorer 的裁剪版。

基于 QTreeView + QFileSystemModel（Qt 原生，自带懒加载与系统图标）。
无任何 window 式上帝对象依赖，可独立实例化。

对外信号：
    file_opened(str) — 双击文件时发射，参数为文件绝对路径，
                       供后续编辑器模块接入。
"""
import shutil
from pathlib import Path

from PySide6.QtCore import QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class FileExplorer(QWidget):
    """目录文件浏览器（右栏面板）。"""

    file_opened = Signal(str)

    def __init__(self, root_dir: str, parent: QWidget | None = None) -> None:
        """
        :param root_dir: 文件树根目录（绝对路径）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.root_dir = str(Path(root_dir).resolve())

        self.model = QFileSystemModel(self)
        self.model.setRootPath(self.root_dir)
        self.model.setReadOnly(False)  # 允许重命名编辑

        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(self.root_dir))
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._open_context_menu)
        self.tree.doubleClicked.connect(self._on_double_clicked)

        # 只显示名称列，隐藏大小/类型/修改时间列
        self.tree.setHeaderHidden(True)
        for col in range(1, self.model.columnCount()):
            self.tree.hideColumn(col)

        self.path_label = QLabel(self.root_dir)
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(self.path_label)
        header.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.tree)
        layout.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # 内部：选中项辅助
    # ------------------------------------------------------------------
    def _selected_paths(self) -> list[str]:
        """返回当前选中的所有文件系统路径。"""
        paths = []
        for index in self.tree.selectionModel().selectedRows(0):
            paths.append(self.model.filePath(index))
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
        path = Path(self.model.filePath(index))
        if path.is_file():
            self.file_opened.emit(str(path))

    def _open_context_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        menu = QMenu(self)

        action_open = menu.addAction("打开")
        action_reveal = menu.addAction("在文件管理器中显示")
        menu.addSeparator()
        action_touch = menu.addAction("新建文件")
        action_mkdir = menu.addAction("新建目录")
        menu.addSeparator()
        action_rename = menu.addAction("重命名")
        action_delete = menu.addAction("删除")

        has_selection = index.isValid()
        action_open.setEnabled(has_selection)
        action_reveal.setEnabled(has_selection)
        action_rename.setEnabled(has_selection)
        action_delete.setEnabled(has_selection)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is action_open:
            self._action_open(index)
        elif chosen is action_reveal:
            self._action_reveal(index)
        elif chosen is action_touch:
            self._action_touch()
        elif chosen is action_mkdir:
            self._action_mkdir()
        elif chosen is action_rename:
            self.tree.edit(index)
        elif chosen is action_delete:
            self._action_delete()

    # ------------------------------------------------------------------
    # 右键菜单动作
    # ------------------------------------------------------------------
    def _action_open(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self.model.filePath(index))
        if path.is_dir():
            self.tree.expand(index)
        else:
            self.file_opened.emit(str(path))

    def _action_reveal(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self.model.filePath(index))
        target = path if path.is_dir() else path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _action_touch(self) -> None:
        name, ok = QInputDialog.getText(self, "新建文件", "文件名：")
        if not ok or not name.strip():
            return
        target = self._anchor_dir() / name.strip()
        if target.exists():
            QMessageBox.warning(self, "新建文件", f"已存在：{target}")
            return
        try:
            target.touch()
        except OSError as e:
            QMessageBox.critical(self, "新建文件失败", str(e))

    def _action_mkdir(self) -> None:
        name, ok = QInputDialog.getText(self, "新建目录", "目录名：")
        if not ok or not name.strip():
            return
        target = self._anchor_dir() / name.strip()
        if target.exists():
            QMessageBox.warning(self, "新建目录", f"已存在：{target}")
            return
        try:
            target.mkdir()
        except OSError as e:
            QMessageBox.critical(self, "新建目录失败", str(e))

    def _action_delete(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        listing = "\n".join(paths)
        reply = QMessageBox.question(
            self,
            "删除确认",
            f"确定删除以下 {len(paths)} 项？此操作不可恢复：\n\n{listing}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for p in paths:
            try:
                target = Path(p)
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except OSError as e:
                QMessageBox.critical(self, "删除失败", f"{p}\n{e}")
