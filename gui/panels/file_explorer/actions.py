"""文件树右键菜单与动作（组合进 FileExplorer 使用）。

宿主依赖经构造函数显式注入（树视图、路径换算、选中集、新建落点、打开回调），
契约从类型系统可见（原 mixin 隐式接口的整改，见 AFCP 计划任务 3.2）。
后续 git 动作（blame/log 等）在此层扩展。

「使用 Typora 打开」（2026-07-29，见 文档/修改记录/2026-0729-1155_Markdown渲染
预览与Typora打开功能实施计划 T6）：.md/.markdown 文件且检测到系统 Typora
时显示于「打开」之后；调起失败沿用 QMessageBox.critical 提示风格。
"""
import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QModelIndex, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QTreeView, QWidget

from core.external_apps import TyporaLauncher, default_launcher
from gui.panels.viewer.markdown_view import MARKDOWN_EXTS
from gui.popups import make_translucent_popup


class ExplorerActions:
    """文件树右键菜单与动作集（组合类，宿主依赖全部构造注入）。"""

    def __init__(
        self,
        host: QWidget,
        tree: QTreeView,
        file_path_of: Callable[[QModelIndex], str],
        selected_paths: Callable[[], list[str]],
        anchor_dir: Callable[[], Path],
        open_file: Callable[[str], None],
        typora: TyporaLauncher | None = None,
    ) -> None:
        """
        :param host: 对话框/菜单的父控件（FileExplorer 本体）
        :param tree: 树视图（命中测试/展开/编程式重命名编辑）
        :param file_path_of: 代理索引 → 文件系统路径
        :param selected_paths: 当前选中项绝对路径列表
        :param anchor_dir: 新建文件/目录的落点目录
        :param open_file: 打开文件回调（FileExplorer.file_opened.emit）
        :param typora: Typora 启动器（构造注入，探针可传假实现；缺省共享实例）
        """
        self._host = host
        self._tree = tree
        self._file_path_of = file_path_of
        self._selected_paths = selected_paths
        self._anchor_dir = anchor_dir
        self._open_file = open_file
        self._typora = typora or default_launcher

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
    def _assemble_menu(self, index) -> tuple[QMenu, dict]:
        """装配右键菜单（构建与弹出分离，可探针断言菜单项显隐）。

        返回 (menu, actions)；actions 键：open/typora/reveal/touch/mkdir/rename/delete。
        「使用 Typora 打开」仅 .md/.markdown 文件且检测到系统 Typora 时创建（否则 None）。
        """
        menu = make_translucent_popup(QMenu(self._host))

        action_open = menu.addAction("打开")
        action_typora = None
        if index.isValid() and self._typora.is_available():
            p = Path(self._file_path_of(index))
            if p.is_file() and p.suffix.lower().lstrip(".") in MARKDOWN_EXTS:
                action_typora = menu.addAction("使用 Typora 打开")
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
        return menu, {
            "open": action_open, "typora": action_typora, "reveal": action_reveal,
            "touch": action_touch, "mkdir": action_mkdir,
            "rename": action_rename, "delete": action_delete,
        }

    def open_context_menu(self, pos) -> None:
        """右键菜单装配与分发（customContextMenuRequested 槽）。"""
        index = self._tree.indexAt(pos)
        menu, actions = self._assemble_menu(index)

        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen is actions["open"]:
            self._action_open(index)
        elif actions["typora"] is not None and chosen is actions["typora"]:
            self._action_open_typora(index)
        elif chosen is actions["reveal"]:
            self._action_reveal(index)
        elif chosen is actions["touch"]:
            self._action_touch()
        elif chosen is actions["mkdir"]:
            self._action_mkdir()
        elif chosen is actions["rename"]:
            self._tree.edit(index)
        elif chosen is actions["delete"]:
            self._action_delete()

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def _action_open(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self._file_path_of(index))
        if path.is_dir():
            self._tree.expand(index)
        else:
            self._open_file(str(path))

    def _action_reveal(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self._file_path_of(index))
        target = path if path.is_dir() else path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _action_open_typora(self, index) -> None:
        """使用 Typora 打开当前选中的 Markdown 文件（非阻塞调起系统应用）。"""
        if not index.isValid():
            return
        path = Path(self._file_path_of(index))
        if error := self._typora.open(path):
            QMessageBox.critical(self._host, "使用 Typora 打开失败", error)

    def _action_touch(self) -> None:
        name, ok = QInputDialog.getText(self._host, "新建文件", "文件名：")
        if not ok or not name.strip():
            return
        target = self._anchor_dir() / name.strip()
        if target.exists():
            QMessageBox.warning(self._host, "新建文件", f"已存在：{target}")
            return
        try:
            target.touch()
        except OSError as e:
            QMessageBox.critical(self._host, "新建文件失败", str(e))

    def _action_mkdir(self) -> None:
        name, ok = QInputDialog.getText(self._host, "新建目录", "目录名：")
        if not ok or not name.strip():
            return
        target = self._anchor_dir() / name.strip()
        if target.exists():
            QMessageBox.warning(self._host, "新建目录", f"已存在：{target}")
            return
        try:
            target.mkdir()
        except OSError as e:
            QMessageBox.critical(self._host, "新建目录失败", str(e))

    def _action_delete(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        listing = "\n".join(paths)
        reply = QMessageBox.question(
            self._host,
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
                QMessageBox.critical(self._host, "删除失败", f"{p}\n{e}")
