"""Token 用量热力图对话框：GitHub 式贡献格子图 + 今日/本周/本月汇总。

计划：work plans/2026-1114-1452_Token用量贡献热力图落地计划.md。
数据源为 ide/core/token_stats.py 的逐轮真实消耗记录（仅 kimi / DSH
后端）；格子分档为相对分位数（0~4 档），空格/绿色随主题明暗自适应。
"""
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ide.core import token_stats
from ide.core.token_stats import DayUsage

_CELL = 12          # 格子边长（px）
_GAP = 3            # 格子间距（px）
_STEP = _CELL + _GAP
_WEEKS = 53         # 近 52 周 + 当前周（GitHub 一年视图同构）

#: 绿色梯度（GitHub 同款；按主题底色明暗选组，空格色取主题 AlternateBase）
_GREENS_LIGHT = ("#9be9a8", "#40c463", "#30a14e", "#216e39")
_GREENS_DARK = ("#0e4429", "#006d32", "#26a641", "#39d353")


def _tooltip_text(day: date, usage: DayUsage | None) -> str:
    weekday = "一二三四五六日"[day.weekday()]
    head = f"{day.isoformat()} 周{weekday}"
    if usage is None or usage.total <= 0:
        return f"{head}\n无用量"
    lines = [head,
             f"输入 {usage.input_tokens:,} / 输出 {usage.output_tokens:,}"
             f" / 合计 {usage.total:,} tokens"]
    lines += [f"{backend}：{total:,}" for backend, total
              in sorted(usage.by_backend.items())]
    return "\n".join(lines)


class TokenHeatmapWidget(QWidget):
    """近一年贡献格子图：列 = ISO 周（周一开头），行 = 周一至周日。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._levels: dict[date, int] = {}
        self._daily: dict[date, DayUsage] = {}
        self._today = date.today()
        self._start = self._today  # set_data 重算
        self.setMouseTracking(True)
        self.setFixedSize(_WEEKS * _STEP - _GAP, 7 * _STEP - _GAP)

    def set_data(self, daily: dict[date, DayUsage], today: date) -> None:
        """注入聚合数据并重绘；分档基于全部非零日（非仅可视窗口）。"""
        self._daily = daily
        self._today = today
        current_monday = today - timedelta(days=today.weekday())
        self._start = current_monday - timedelta(weeks=_WEEKS - 1)
        nonzero = [u.total for u in daily.values() if u.total > 0]
        self._levels = {day: token_stats.level_of(usage.total, nonzero)
                        for day, usage in daily.items()}
        self.update()

    def _day_at(self, x: int, y: int) -> date | None:
        col, row = x // _STEP, y // _STEP
        if not (0 <= col < _WEEKS and 0 <= row < 7):
            return None
        day = self._start + timedelta(days=col * 7 + row)
        return day if self._start <= day <= self._today else None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        colors = [self.palette().color(QPalette.ColorRole.AlternateBase),
                  *(_GREENS_DARK if dark else _GREENS_LIGHT)]
        for col in range(_WEEKS):
            for row in range(7):
                day = self._start + timedelta(days=col * 7 + row)
                if day > self._today:
                    continue  # 未来日期不画
                painter.fillRect(col * _STEP, row * _STEP, _CELL, _CELL,
                                 colors[self._levels.get(day, 0)])

    def mouseMoveEvent(self, event) -> None:
        day = self._day_at(int(event.position().x()), int(event.position().y()))
        if day is None:
            QToolTip.hideText()
            return
        QToolTip.showText(event.globalPosition().toPoint(),
                          _tooltip_text(day, self._daily.get(day)), self)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)


class TokenUsageDialog(QDialog):
    """Token 用量弹窗：说明行 + 热力图 + 底部今日/本周/本月三项汇总。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Token 用量热力图")
        self.resize(900, 300)

        info = QLabel(
            "统计本 IDE 内 kimi / DSH 对话的主代理真实 token 消耗"
            "（输入含缓存读写 + 输出），按自然日归档；"
            "子代理与 IDE 之外的 CLI 用量不计入。", self)
        info.setWordWrap(True)
        refresh_btn = QPushButton("刷新(&R)", self)
        refresh_btn.clicked.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(info, 1)
        top.addWidget(refresh_btn, 0)

        self._heatmap = TokenHeatmapWidget(self)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(self._heatmap, 0, Qt.AlignmentFlag.AlignCenter)
        center.addStretch(1)

        self._summary_label = QLabel(self)
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(center, 1)
        layout.addWidget(self._summary_label)

    def showEvent(self, event: QShowEvent) -> None:
        """每次打开自动重读落盘记录刷新。"""
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        daily = token_stats.load_daily()
        today = date.today()
        self._heatmap.set_data(daily, today)
        summary = token_stats.summarize(daily, today)
        self._summary_label.setText(
            f"今日用量 {summary.today:,} tokens　　"
            f"最近七天用量 {summary.last_7_days:,} tokens　　"
            f"最近三十天用量 {summary.last_30_days:,} tokens")

    def apply_theme(self, theme: str) -> None:
        """主题切换：格子色绘制时取调色板，触发重绘即可。"""
        self._heatmap.update()

    def refresh_font(self) -> None:
        """全局字号跟随：控件字体随应用字体链自动继承，仅需重绘格子。"""
        self._heatmap.update()
