"""文件树右键菜单与动作（混入 FileExplorer 使用）。

后续 git 动作（blame/log 等）在此层扩展。
"""
import shutil
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox


class ExplorerActionsMixin:
    """右键菜单与动作集。宿主需提供：tree、_file_path、_selected_paths、
    _anchor_dir、file_opened。"""

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
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
    # 动作
    # ------------------------------------------------------------------
    def _action_open(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self._file_path(index))
        if path.is_dir():
            self.tree.expand(index)
        else:
            self.file_opened.emit(str(path))

    def _action_reveal(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self._file_path(index))
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
