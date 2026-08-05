"""聊天图片附件组件（0340 方案 B 计划 T2）：附件行 chip + QDialog 大图预览。

AttachmentStrip 横向 chip 行（缩略图 + 文件名 + 删除×），有附件时
出现在输入区状态行与输入框之间；点击 chip 弹 ImagePreviewDialog 大图。
数据载体为 llm.base.ImageAttachment（D3 4c 混合：path 唯一事实，
发送时才由 provider 层读盘，本组件不持字节）。

左栏宽度纪律（2305 病根教训）：strip 横向 sizePolicy 取 Ignored——
sizeHint 永不撑宽左栏，极端窄宽下 chip 右侧裁剪；行高恒定 64px，
无附件 hide() / 有附件 show() 仅垂直方向变化，附件行不设 stretch。
主题键零新增：chip 底色复用 chat_pack user_bubble_bg（与气泡卡同源）。
"""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from llm.base import ImageAttachment

__all__ = ["AttachmentStrip", "ImageAttachment", "ImagePreviewDialog", "mime_type_of"]

#: 附件上限（对齐 Multi_Cli_Studio，0340 计划 §3.1）
MAX_ATTACHMENTS = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 单张 ≤10MB

#: 图片后缀 → mimeType（D7 风险行：按后缀直传不转码，agent 端各自兼容）
IMAGE_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_THUMB_SIZE = 48  # chip 缩略图边长（等比缩放居中）
_STRIP_HEIGHT = 64


def mime_type_of(path: str) -> str | None:
    """本地路径后缀 → mimeType；非图片后缀返回 None。"""
    return IMAGE_SUFFIXES.get(Path(path).suffix.lower())


class _Chip(QFrame):
    """单条附件 chip：缩略图 + 文件名（省略中间）+ 删除×；点击预览大图。"""

    preview_requested = Signal()
    remove_requested = Signal()

    def __init__(self, attachment: ImageAttachment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("attachmentChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        path = Path(attachment["path"])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        thumb = QLabel(self)
        thumb.setFixedSize(_THUMB_SIZE, _THUMB_SIZE)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            thumb.setPixmap(pixmap.scaled(
                _THUMB_SIZE, _THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:  # 读图失败（极端：落盘后秒删）——占位符上屏，×仍可移除
            thumb.setText("🖼")
        layout.addWidget(thumb)

        name = QLabel(self)
        name.setFixedWidth(48)  # 恒宽（2305 纪律）；省略中间防长名撑宽
        name.setText(name.fontMetrics().elidedText(
            path.name, Qt.TextElideMode.ElideMiddle, 48))
        name.setToolTip(path.name)
        layout.addWidget(name)

        remove = QPushButton("×", self)
        remove.setObjectName("attachmentChipRemove")
        remove.setFixedSize(16, 16)
        remove.setToolTip("移除附件")
        remove.setCursor(Qt.CursorShape.ArrowCursor)
        remove.clicked.connect(self.remove_requested)
        layout.addWidget(remove)

        self.setToolTip(f"{path}\n点击查看大图")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.preview_requested.emit()
        super().mousePressEvent(event)


class AttachmentStrip(QWidget):
    """图片附件行：chip 增删 + 上限校验 + 显隐自管理。

    信号：
    - changed()：附件数变化（panel 据此刷新发送键使能与空文本发送开关）；
    - rejected(str)：超限/文件异常拒绝原因（panel 转输出区系统提示——
      附件行无文字区，不新增控件）。
    """

    changed = Signal()
    rejected = Signal(str)

    def __init__(self, chip_bg: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("attachmentStrip")
        self.setFixedHeight(_STRIP_HEIGHT)
        # 横向 Ignored：sizeHint 永不参与左栏宽度协商（2305 病根纪律）
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._chips: list[_Chip] = []
        self.set_chip_color(chip_bg)
        self.hide()  # 初态无附件

    # ------------------------------------------------------------------
    # 主题（chip 底色复用 user_bubble_bg，不新增主题键）
    # ------------------------------------------------------------------
    def set_chip_color(self, chip_bg: str | None) -> None:
        bg = chip_bg or "palette(alternate-base)"  # 测试裸建场景回退
        self.setStyleSheet(
            f"QFrame#attachmentChip {{ background-color: {bg};"
            f" border-radius: 6px; }}"
            "QPushButton#attachmentChipRemove { border: none;"
            " font-weight: 600; padding: 0; }")

    # ------------------------------------------------------------------
    # 附件管理
    # ------------------------------------------------------------------
    def attachments(self) -> list[ImageAttachment]:
        """当前附件快照（发送时收集；返回副本防外部篡改 chip 归属）。"""
        return [dict(chip.attachment) for chip in self._chips]  # type: ignore[misc]

    def count(self) -> int:
        return len(self._chips)

    def add(self, path: str, mime_type: str, pasted: bool) -> bool:
        """校验并追加一条附件；拒绝时经 rejected 信号报原因，返回 False。"""
        if len(self._chips) >= MAX_ATTACHMENTS:
            self.rejected.emit(f"图片附件最多 {MAX_ATTACHMENTS} 张")
            return False
        if any(chip.attachment["path"] == path for chip in self._chips):
            return False  # 同路径重复添加：静默忽略（粘贴连发场景常见）
        try:
            size = Path(path).stat().st_size
        except OSError:
            self.rejected.emit(f"图片文件不存在：{path}")
            return False
        if size > MAX_IMAGE_BYTES:
            self.rejected.emit(
                f"图片超过 10MB：{Path(path).name}（{size / 1048576:.1f}MB）")
            return False
        self._append_chip(ImageAttachment(path=path, mime_type=mime_type, pasted=pasted))
        return True

    def restore(self, attachments: list[ImageAttachment]) -> None:
        """失败/中断回滚恢复（0340 计划 D6）：文件仍在盘的附件重新上 chip。

        静默跳过已消失的落盘文件（极端：发送期间被外部清理），不发
        rejected——恢复路径不打断用户。
        """
        for attachment in attachments:
            if (len(self._chips) < MAX_ATTACHMENTS
                    and Path(attachment["path"]).exists()):
                self._append_chip(dict(attachment))

    def clear(self) -> None:
        """发送成功路径清空 chip（不删落盘文件——气泡卡回显与 agent
        复读依赖文件在盘，D7）；×删语义在 _remove_chip。"""
        for chip in self._chips:
            chip.deleteLater()
        self._chips = []
        self._sync_visibility()

    def _append_chip(self, attachment: ImageAttachment) -> None:
        chip = _Chip(attachment, self)
        chip.attachment = attachment  # type: ignore[attr-defined] — 簿记回取
        chip.preview_requested.connect(
            lambda a=attachment: self._preview(a["path"]))
        chip.remove_requested.connect(lambda c=chip: self._remove_chip(c))
        self._chips.append(chip)
        self._layout.insertWidget(self._layout.count() - 1, chip)
        self._sync_visibility()

    def _remove_chip(self, chip: _Chip) -> None:
        """×删（D7）：pasted 落盘产物连文件删；用户原文件绝不删。"""
        if chip.attachment["pasted"]:  # type: ignore[attr-defined]
            try:
                Path(chip.attachment["path"]).unlink(missing_ok=True)  # type: ignore[attr-defined]
            except OSError:
                pass
        self._chips.remove(chip)
        chip.deleteLater()
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        """可见性实际变化才发射 changed（0634 计划 D1 信号精确化）。

        clear() 在已空时重复调用不再空发 changed——此前无条件发射会让
        panel._refresh_send_button 在「busy UI 已立、worker 未建」竞态
        窗口内误判禁用停止按钮（停止按钮失效根因，0634 计划 §2.1）。
        """
        visible = bool(self._chips)
        if visible == self.isVisible():
            return
        self.setVisible(visible)
        self.changed.emit()

    def _preview(self, path: str) -> None:
        ImagePreviewDialog(path, self).exec()


class ImagePreviewDialog(QDialog):
    """附件大图预览（0340 计划 D2 自绘 QDialog）：等比缩放至屏幕可用区
    80% 上限，Esc / 点击关闭——瞬态确认语义，不顶中栏查看内容。"""

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        p = Path(path)
        self.setWindowTitle(p.name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        pixmap = QPixmap(str(p))
        if pixmap.isNull():
            label.setText("（图片读取失败）")
            label.setMinimumSize(QSize(240, 120))
        else:
            screen = self.screen() or QApplication.primaryScreen()
            available = screen.availableGeometry() if screen else None
            max_w = int((available.width() if available else 1280) * 0.8)
            max_h = int((available.height() if available else 800) * 0.8)
            label.setPixmap(pixmap.scaled(
                max_w, max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(label)

    def mousePressEvent(self, event) -> None:
        self.accept()  # 点击任意处关闭（与 Esc 同语义）
        super().mousePressEvent(event)
