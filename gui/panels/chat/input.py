"""输入区：Enter 发送 / Shift+Enter 换行；支持文件拖入/粘贴插入 @路径 引用。

左栏宽度根治（2026-07-24，文档/修改记录/2026-0724-2305 计划 T2）：
发送校验收敛为公共入口 trigger_send()——Enter 键与输入区底行
发送按钮共用同一路径，两种触发方式行为严格等价。

粘贴图片落盘 @路径透传（2026-08-01，0035 计划方案 D）：
重写 insertFromMimeData——粘贴的图片数据落盘为 PNG，光标处插入
`@路径 ` 纯文本引用，完全复用拖放 @路径 透传链路，由后端 agent CLI
自行读图；落盘目录跟随截图双态先例（开发态 .tmp/pasted/、打包态
USER_CONFIG_DIR/pasted/）。

图片附件化（2026-08-01，0340 计划方案 B）：当前后端
supports_images（注册表能力位，T0 spike 实证填值）时，图片入口
（粘贴/拖入/文件 URL）改发 image_attached 信号由 AttachmentStrip
附件化；能力外后端维持方案 D @路径 退化（D4——D 代码零删除，
成为 B 的内建兜底）。落盘清理翻案（D7）：每次落盘后惰性保留
最近 20 个 pasted-*.png。
"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QTextEdit

from core.paths import IS_FROZEN, PROJECT_ROOT, USER_CONFIG_DIR, workspace_display_path
from gui.panels.chat.attachments import mime_type_of
from gui.popups import exec_standard_context_menu
from gui.selection_band import SUPPRESSION_QSS, paint_selection_band

_PASTED_KEEP = 20  # 落盘惰性清理保留数（0340 计划 D7，无定时器无后台线程）


def _pasted_image_dir() -> Path:
    """粘贴图片落盘目录（截图双态先例，main.py SCREENSHOT_DIR 同构）。"""
    d = (USER_CONFIG_DIR / "pasted") if IS_FROZEN else (PROJECT_ROOT / ".tmp" / "pasted")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_pasted_image(image) -> Path | None:
    """图片数据（QImage/QPixmap，均有 .save）统一落盘 PNG；失败返回 None。

    命名 pasted-YYYYMMDD-HHMMSS.png，同秒连贴撞名追加 -2、-3 序号兜底。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = _pasted_image_dir()
    path = directory / f"pasted-{stamp}.png"
    seq = 2
    while path.exists():
        path = directory / f"pasted-{stamp}-{seq}.png"
        seq += 1
    if not image.save(str(path), "PNG"):
        return None
    _prune_pasted_images(directory)  # D7 惰性清理：每次落盘顺带
    return path


def _prune_pasted_images(directory: Path) -> None:
    """保留最近 _PASTED_KEEP 个 pasted-*.png，超出按 mtime 最旧先删。

    单点同步数行，无定时器无后台线程（0340 计划 D7）；清理异常静默
    （落盘目录为缓存语义，删不掉不影响主流程）。
    """
    try:
        files = sorted(directory.glob("pasted-*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[_PASTED_KEEP:]:
            old.unlink()
    except OSError:
        pass


class ChatInput(QTextEdit):
    """聊天输入框。

    拖放文件引用（方案 A：纯文本透传，由后端 agent CLI 解析 @路径）：
    从文件树或系统文件管理器拖入本地文件，在落点插入 `@工作区相对路径 `；
    目录带尾 `/`，多选逐个插入、空格分隔；工作区外路径退化为绝对路径。

    粘贴引用（0035 计划方案 D，与拖放同一 @路径 语义）：粘贴图片数据
    → 落盘 PNG（_save_pasted_image）后插入落盘路径 @引用；粘贴本地文件
    URL → 与拖放对齐引用原路径（不复制）；其余（纯文本等）走默认粘贴。
    落盘失败或未注入工作区根时静默退化默认粘贴，不报错不弹窗。

    图片附件化（0340 计划方案 B）：_image_attachments_enabled 开启时
    （panel 按注册表 supports_images 能力位注入），图片入口改发
    image_attached 信号由 AttachmentStrip 附件化；关闭时维持上述 D
    行为（能力外后端内建兜底，D 代码零删除）。落盘文件惰性清理
    保留最近 20 个（D7，.tmp/ 已 gitignore）。

    选区带自绘（2055 计划方案 A 再推广，2145 计划增补）：原生选区带 =
    QTextLine 整行高，Qt 排版 leading 全部垫底，思源黑体下视觉偏下
    （实测带上缘 1px/下缘 7px，中心偏差 +3px）；抑原生带后 paintEvent
    叠绘墨盒对称带，带色与 ChatOutput 同源（timeline_read_fg 复用，
    不新增主题键）。
    """

    #: 请求发送（携带输入文本；空文本 + 有附件时为空串，0340 计划 D5）
    send_requested = Signal(str)

    #: 图片附件化信号（0340 方案 B）：载荷 (路径, mimeType, pasted)；
    #: 仅当前后端 supports_images 时发射（能力外后端走 @路径 退化）
    image_attached = Signal(str, str, bool)

    #: 图片退化提示信号（2026-08-01 用户反馈：D4 退化不得静默）：
    #: 能力外后端粘贴/拖入图片按 @路径 退化时发射，panel 转输出区
    #: 系统提示告知用户当前后端不支持图片附件
    image_fallback = Signal()

    def __init__(self, band_color: str | None = None, parent=None) -> None:
        super().__init__(parent)
        #: base.qss `#chatInput[pending="true"]` 待发态虚线描边的挂点
        #: （0807-2305 计划 D4/T6；panel 经动态属性驱动）
        self.setObjectName("chatInput")
        self.setPlaceholderText("输入消息，Enter 发送 / Shift+Enter 换行")
        self.setAcceptRichText(False)
        # 选区带自绘：抑原生带（控件级 qss 优先于 base.qss 继承规则）
        self.setStyleSheet(SUPPRESSION_QSS)
        #: 自绘带色；None 时退化为应用 palette highlight（测试裸建场景）
        self._band_color = QColor(band_color) if band_color else None
        #: 工作区根路径（set_workspace_root 注入）；None 时文件拖入静默忽略
        self._workspace_root: Path | None = None
        #: 图片附件化开关（panel 按注册表能力位注入，0340 计划 D4）；
        #: False = 方案 D @路径 退化行为
        self._image_attachments_enabled = False
        #: 空文本发送开关（D5）：panel 随附件行非空注入；True 时
        #: trigger_send 放行空文本（附件由 panel 随消息携带）
        self._allow_empty_send = False

    def paintEvent(self, event) -> None:
        """基类绘制后叠绘选区对称带（方案 A）。"""
        super().paintEvent(event)
        color = self._band_color or QApplication.palette().color(
            QPalette.ColorRole.Highlight)
        paint_selection_band(self, color)

    def wheelEvent(self, event) -> None:
        """禁用 Qt 内建 Ctrl+滚轮缩放字体：Ctrl 按下时吞掉事件，其余走基类滚动。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 工作区根路径（拖入文件时据此计算 @相对路径）
    # ------------------------------------------------------------------
    def set_workspace_root(self, root: str) -> None:
        self._workspace_root = Path(root).resolve()

    # ------------------------------------------------------------------
    # 图片附件化开关（0340 方案 B；panel 按注册表能力位/附件行状态注入）
    # ------------------------------------------------------------------
    def set_image_attachments_enabled(self, enabled: bool) -> None:
        """切换图片入口语义：True=附件化（image_attached 信号）；
        False=方案 D @路径 退化（能力外后端兜底）。"""
        self._image_attachments_enabled = enabled

    def set_allow_empty_send(self, allowed: bool) -> None:
        """空文本发送开关（D5 纯图发送）：panel 随附件行非空注入。"""
        self._allow_empty_send = allowed

    # ------------------------------------------------------------------
    # 右键菜单：标准编辑菜单透明化（Qt 内部创建的 QMenu 不经修复点，
    # 须替代默认 contextMenuEvent——见 gui/popups.py 与 0751 计划 §3.1）
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event)

    # ------------------------------------------------------------------
    # 发送公共入口（Enter 键与底行发送按钮共用，T2）
    # ------------------------------------------------------------------
    def trigger_send(self) -> None:
        """文本非空（或空文本发送开关放行，D5 纯图发送）且自身可用时
        发射 send_requested；否则静默忽略。"""
        text = self.toPlainText().strip()
        if (text or self._allow_empty_send) and self.isEnabled():
            self.send_requested.emit(text)

    # ------------------------------------------------------------------
    # 键盘
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.trigger_send()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 拖放：本地文件 → @路径 引用
    # ------------------------------------------------------------------
    @staticmethod
    def _has_local_file(mime) -> bool:
        return mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())

    def dragEnterEvent(self, event) -> None:
        if self._has_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)  # 文本等其余拖拽走默认行为

    def dragMoveEvent(self, event) -> None:
        if self._has_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not self._has_local_file(event.mimeData()):
            super().dropEvent(event)  # 文本等其余拖拽走默认行为
            return
        if self._workspace_root is None:
            event.ignore()  # 未注入工作区根：静默忽略（防御，正常装配不会发生）
            return
        # 在落点（而非当前光标）插入，符合拖拽直觉
        self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._insert_file_reference(url.toLocalFile())
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # 粘贴：图片落盘 / 本地文件 URL / 其余默认粘贴（0035 计划方案 D）；
    # 附件化开关开启时图片改发 image_attached 信号（0340 计划方案 B，D4）
    # Qt 粘贴唯一汇聚点（Ctrl+V、右键粘贴均经此），一处拦截全路径生效
    # ------------------------------------------------------------------
    def insertFromMimeData(self, source) -> None:
        # 图片数据分支：落盘 PNG → 附件化信号 / @路径 退化（QImage/QPixmap 均有 .save）
        if source.hasImage() and self._workspace_root is not None:
            image = source.imageData()
            if hasattr(image, "save"):
                path = _save_pasted_image(image)
                if path is not None:
                    if self._image_attachments_enabled:
                        self.image_attached.emit(str(path), "image/png", True)
                    else:  # D4 退化：能力外后端维持方案 D @路径 透传（不静默）
                        self.insertPlainText(self._mention_text(str(path)))
                        self.image_fallback.emit()
                    return
            # 落盘失败：静默退化默认粘贴（与拖放防御分支同哲学）
        # 本地文件 URL 分支：与拖放对齐，引用原路径不复制文件
        if self._has_local_file(source) and self._workspace_root is not None:
            for url in source.urls():
                if url.isLocalFile():
                    self._insert_file_reference(url.toLocalFile())
            return
        super().insertFromMimeData(source)  # 文本等默认行为原样保留

    def _insert_file_reference(self, path: str) -> None:
        """本地文件入口统一分流（拖放/粘贴文件 URL 共用，0340 计划 D4）：
        图片 + 附件化开关 → image_attached（引用原路径不复制，pasted=False）；
        图片 + 开关关闭 → @路径 退化 + image_fallback 提示（不静默）；
        非图片 → @路径 引用（零改动）。"""
        mime = mime_type_of(path)
        if mime is not None and self._image_attachments_enabled:
            self.image_attached.emit(path, mime, False)
        else:
            self.insertPlainText(self._mention_text(path))
            if mime is not None:
                self.image_fallback.emit()

    def _mention_text(self, path: str) -> str:
        """本地路径 → '@相对路径 '（目录带尾 '/'，工作区外用绝对路径）。

        换算规则经 workspace_display_path 单一来源（2026-0806-1908 计划
        T1 改动点 2），与 ACP 图片附件路径透传共用防漂移。
        """
        p = Path(path).resolve()
        text = workspace_display_path(path, self._workspace_root)
        if p.is_dir() and not text.endswith("/"):
            text += "/"
        return f"@{text} "
