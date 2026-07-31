"""选区带自绘公共辅助（2026-0731-2055 方案 A 通用化，聊天区推广）。

背景：Qt 原生选区带 = QTextLine 整行高（含 leading），Qt 排版把 leading
全部垫在行框底部；思源黑体 lineGap 6.5px@10pt 致带上缘留白 2px/下缘
7px 视觉偏下（详见 `2026-0731-2055_Markdown预览选区高亮偏下诊断与修复计划.md`）。
本模块提供两处 QTextBrowser 子类（Markdown 预览 / 聊天输出）共用的：
- SUPPRESSION_QSS——抑制原生带并保选中文字正文色（控件级 qss 声明
  优先于 base.qss QMainWindow 继承规则，offscreen+真机双验证）；
- selection_rects()——逐行算选区视口矩形（纵向=行 ascent+descent
  墨盒上下各加 SELECTION_PAD_Y 对称留白）；
- paint_selection_band()——基类绘制后叠绘半透明圆角带。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QTextCursor

#: 墨盒（ascent+descent）上下对称留白（px）
SELECTION_PAD_Y = 3
#: 圆角半径（px）
SELECTION_RADIUS = 3
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
        painter.drawRoundedRect(rect, SELECTION_RADIUS, SELECTION_RADIUS)
    painter.end()


def _line_x(line, pos: int) -> float:
    """QTextLine.cursorToX 兼容取值（绑定版本返回值/元组不一）。"""
    result = line.cursorToX(pos)
    return float(result[0] if isinstance(result, tuple) else result)
