"""选区带自绘公共辅助（2026-0731-2055 方案 A 通用化，聊天区推广）。

背景：Qt 原生选区带 = QTextLine 整行高（含 leading），Qt 排版把 leading
全部垫在行框底部；思源黑体 lineGap 6.5px@10pt 致带上缘留白 2px/下缘
7px 视觉偏下（详见 `2026-0731-2055_Markdown预览选区高亮偏下诊断与修复计划.md`）。
本模块提供选区带自绘公共辅助：
- SUPPRESSION_QSS——抑制原生带并保选中文字正文色（控件级 qss 声明
  优先于 base.qss QMainWindow 继承规则，offscreen+真机双验证；卡片轨
  正文块将其并入 ID 选择器块内，防无选择器声明级联进右键菜单）；
- selection_rects()——逐行算选区视口矩形（纵向=行 ascent+descent
  墨盒上下各加 SELECTION_PAD_Y 对称留白）；
- paint_selection_band()——基类绘制后叠绘半透明直角带。

挂载点：Markdown 预览 `gui/panels/viewer/markdown_view.py`、旧轨聊天
`gui/panels/chat/output.py` 与 `gui/panels/chat/input.py`、卡片轨正文块
`gui/panels/chat/cards.py`（BodyHtml/BodyText，TextStreamBlock/
SystemBlock/用户气泡体及各工具卡正文随之继承）、QLabel 家族
`BandLabel`（卡片标题/节标签/问答行/todo 条目与设置中心路径标签，
经 cards.py `_selectable` 与 popups.make_label_selectable 叠加右键
精简菜单）。

覆写纪律：Qt 样式表整表替换——对正文块裸调 setStyleSheet 会丢抑制
声明（原生偏下带回归并与自绘带双层叠加，2026-10-17 真机实证），
附加样式一律走 cards.py `set_extra_style`/`_body_stylesheet`，
`scripts/check_qss_selector_guard.py` 静态拦截裸覆写；BandLabel 则
覆写 setStyleSheet 自动并入 SUPPRESSION_QSS，调用点裸调 color 等
附加声明天然免疫同款坑。
"""
import re
from html import unescape as _html_unescape

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPalette,
    QTextCursor,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QApplication, QLabel

#: 墨盒（ascent+descent）上下对称留白（px）
SELECTION_PAD_Y = 1
#: 圆角半径（px；归零=直角带，与终端选区形态对齐——圆角贴片连排
#: 易被误读为双层高亮）
SELECTION_RADIUS = 0
#: 填充不透明度（0-255；半透明叠绘保文字可读）
SELECTION_ALPHA = 110

#: 原生选区抑制 qss（选中文字保持正文色，透明带下反白不可读）
SUPPRESSION_QSS = (
    "selection-background-color: rgba(0,0,0,0); selection-color: palette(text);")


def selection_rects(edit) -> list[QRectF]:
    """当前选区的视口坐标矩形列（逐行一段；纵向=行墨盒±SELECTION_PAD_Y）。

    坐标映射：块内行坐标 → 视口，锚点取块首 cursorRect（规避块内/视口
    坐标混用坑，见 2055 计划 §3.4）；横向端点取 QTextLine.cursorToX。
    :param edit: QTextEdit/QTextBrowser 实例
    """
    cursor = edit.textCursor()
    if not cursor.hasSelection():
        return []
    sel_start, sel_end = cursor.selectionStart(), cursor.selectionEnd()
    doc = edit.document()
    rects: list[QRectF] = []
    block = doc.findBlock(sel_start)
    while block.isValid() and block.position() < sel_end:
        layout = block.layout()
        if layout.lineCount() == 0:
            block = block.next()
            continue
        # 块首行首字符的视口光标矩形 = 块布局坐标系原点锚点
        anchor = edit.cursorRect(QTextCursor(block))
        base_y = layout.lineAt(0).position().y()
        base_x = layout.lineAt(0).position().x()
        rel_lo = sel_start - block.position()
        rel_hi = sel_end - block.position()
        for i in range(layout.lineCount()):
            line = layout.lineAt(i)
            lo = max(line.textStart(), rel_lo)
            hi = min(line.textStart() + line.textLength(), rel_hi)
            if lo >= hi:
                continue
            x1 = _line_x(line, lo)
            x2 = _line_x(line, hi)
            if hi >= line.textStart() + line.textLength() \
                    and sel_end > block.position() + block.length() - 1:
                x2 += 4  # 选及换行符：行尾补一小段（对齐原生观感）
            top = anchor.top() + (line.position().y() - base_y) - SELECTION_PAD_Y
            left = anchor.left() + (line.position().x() - base_x) + x1
            height = line.ascent() + line.descent() + 2 * SELECTION_PAD_Y
            rects.append(QRectF(left, top, max(x2 - x1, 1.0), height))
        block = block.next()
    return rects


def paint_selection_band(edit, color: QColor) -> None:
    """在 edit 视口叠绘当前选区的半透明对称带（于基类 paintEvent 后调用）。"""
    rects = selection_rects(edit)
    if not rects:
        return
    band = QColor(color)
    band.setAlpha(SELECTION_ALPHA)
    painter = QPainter(edit.viewport())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(band)
    for rect in rects:
        if SELECTION_RADIUS:
            painter.drawRoundedRect(rect, SELECTION_RADIUS, SELECTION_RADIUS)
        else:
            painter.drawRect(rect)
    painter.end()


def _line_x(line, pos: int) -> float:
    """QTextLine.cursorToX 兼容取值（绑定版本返回值/元组不一）。"""
    result = line.cursorToX(pos)
    return float(result[0] if isinstance(result, tuple) else result)


# ----------------------------------------------------------------------
# QLabel 家族（2026-1018-0644 计划：原生带抑制 + 对称自绘）
# ----------------------------------------------------------------------
_LABEL_TAG_RE = re.compile(r"<[^>]*>")


def _label_plain(text: str) -> str:
    """QLabel 富文本 → 纯文本（几何近似用：同字体 span 宽度与纯文本一致）。"""
    if "<" not in text:
        return text
    return _html_unescape(_LABEL_TAG_RE.sub("", text))


def _block_top(area_height: float, block_height: float, alignment) -> float:
    """文本块垂直原点（QLabel 默认 AlignVCenter 块居中；Top/Bottom 一并兼容）。"""
    if alignment & Qt.AlignmentFlag.AlignTop:
        return 0.0
    if alignment & Qt.AlignmentFlag.AlignBottom:
        return max(area_height - block_height, 0.0)
    return max((area_height - block_height) / 2, 0.0)


def label_selection_rects(label: QLabel) -> list[QRectF]:
    """QLabel 当前选区的控件坐标矩形列（逐行一段；纵向 = 行墨盒 ± SELECTION_PAD_Y）。

    QLabel 无 viewport/document 绘制钩子，几何只能复刻其内部排版：
    - 单行：fontMetrics.horizontalAdvance 直算（_ElidedLabel 省略态上屏
      文本即选区文本，索引天然一致）；
    - wordWrap：同字体同宽 QTextLayout 复刻换行，行距 = height+leading
      （QLabel 排版口径，QTextLine.height() 不含 leading），逐行
      cursorToX 取横缘——offscreen 探针实锤纵缘 0 误差、横缘 ≤1px
      （仅行尾空白行过罩 ≤3px 纯空白区，无墨盒遮挡缺失）；
    - 富文本：取纯文本近似（双色 span 同字体同字号，宽度一致；
      selectedText()/selectionStart() 对富文本返回纯文本口径，索引一致）。
    """
    if not label.hasSelectedText():
        return []
    plain = _label_plain(label.text())
    selected = _label_plain(label.selectedText())
    if not plain or not selected:
        return []
    sel_start = max(label.selectionStart(), 0)
    sel_end = min(sel_start + len(selected), len(plain))
    fm = label.fontMetrics()
    pitch = fm.height() + fm.leading()
    margin = label.margin()
    area = label.contentsRect().adjusted(margin, margin, -margin, -margin)
    pad = SELECTION_PAD_Y
    band_height = fm.ascent() + fm.descent() + 2 * pad

    def band(top: float, x1: float, x2: float) -> QRectF:
        return QRectF(area.left() + x1, top - pad, max(x2 - x1, 1.0), band_height)

    if not label.wordWrap():
        text_width = fm.horizontalAdvance(plain)
        x_offset = 0.0
        alignment = label.alignment()
        if alignment & Qt.AlignmentFlag.AlignHCenter:
            x_offset = max((area.width() - text_width) / 2, 0.0)
        elif alignment & Qt.AlignmentFlag.AlignRight:
            x_offset = max(area.width() - text_width, 0.0)
        top = area.top() + _block_top(area.height(), pitch, alignment)
        return [band(top,
                     x_offset + fm.horizontalAdvance(plain[:sel_start]),
                     x_offset + fm.horizontalAdvance(plain[:sel_end]))]

    layout = QTextLayout(plain, label.font())
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(area.width())
        lines.append(line)
    layout.endLayout()
    block_top = area.top() + _block_top(area.height(), pitch * len(lines),
                                        label.alignment())
    rects: list[QRectF] = []
    for i, line in enumerate(lines):
        lo = max(line.textStart(), sel_start)
        hi = min(line.textStart() + line.textLength(), sel_end)
        if lo >= hi:
            continue
        rects.append(band(block_top + pitch * i,
                          _line_x(line, lo), _line_x(line, hi)))
    return rects


def paint_label_selection_band(label: QLabel, color: QColor) -> None:
    """在 label 上叠绘当前选区的半透明对称带（于基类 paintEvent 后调用；
    QLabel 无 viewport，直接画在控件上）。"""
    rects = label_selection_rects(label)
    if not rects:
        return
    band = QColor(color)
    band.setAlpha(SELECTION_ALPHA)
    painter = QPainter(label)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(band)
    for rect in rects:
        if SELECTION_RADIUS:
            painter.drawRoundedRect(rect, SELECTION_RADIUS, SELECTION_RADIUS)
        else:
            painter.drawRect(rect)
    painter.end()


class BandLabel(QLabel):
    """选区带对称自绘的 QLabel：Qt 原生选区带 = 整行高（含 leading）且
    leading 垫底（思源黑体 10pt 带下缘超墨盒 6px，offscreen 探针实锤），
    抑制后按行墨盒 ±SELECTION_PAD_Y 对称叠绘，形态与正文块自绘带一致。

    setStyleSheet 覆写为「附加声明 + SUPPRESSION_QSS 合并」——Qt 样式表
    整表替换，裸覆写会冲掉抑制致原生带回归并与自绘带双层叠加（正文块
    2026-10-17 真机实证同款根因）；子类内合并则调用点裸调 color 等附加
    声明零改动、未来散兵构造性免疫。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        # QLabel 双形态兼容：QLabel(parent) 单参形态首参是父控件不是文本
        if not isinstance(text, str):
            text, parent = "", text
        super().__init__(text, parent)
        self._extra_qss = ""
        self._refresh_qss()

    def setStyleSheet(self, sheet: str) -> None:  # Qt 覆写（合并而非替换）
        self._extra_qss = sheet or ""
        self._refresh_qss()

    def _refresh_qss(self) -> None:
        extra = self._extra_qss.strip()
        # 抑制声明押后：同优先级 qss 后写胜出，防附加声明误覆写 selection-*
        super().setStyleSheet(
            (extra + " " if extra else "") + SUPPRESSION_QSS)

    def paintEvent(self, event) -> None:
        """基类绘制（原生带已被 SUPPRESSION_QSS 抑制）后叠绘对称自绘带。"""
        super().paintEvent(event)
        paint_label_selection_band(self, QApplication.palette().color(
            QPalette.ColorRole.Highlight))
