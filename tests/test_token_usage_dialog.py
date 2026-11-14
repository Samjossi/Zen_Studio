"""Token 用量热力图弹窗：汇总标签 / 悬停文案 / 格子日期映射（offscreen）。"""
from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from ide.core import token_stats
from ide.core.token_stats import DayUsage
from ide.gui.token_usage_dialog import (
    TokenHeatmapWidget,
    TokenUsageDialog,
    _tooltip_text,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_summary_labels(qapp, monkeypatch):
    today = date.today()
    daily = {today: DayUsage(input_tokens=100, output_tokens=50,
                             by_backend={"kimi-acp": 150})}
    monkeypatch.setattr(token_stats, "load_daily", lambda: daily)
    dialog = TokenUsageDialog()
    dialog.reload()
    text = dialog._summary_label.text()
    assert "今日用量 150 tokens" in text
    assert "最近七天用量 150 tokens" in text
    assert "最近三十天用量 150 tokens" in text


def test_tooltip_text():
    day = date(2026, 11, 14)  # 周六
    assert _tooltip_text(day, None) == "2026-11-14 周六\n无用量"
    usage = DayUsage(input_tokens=1234, output_tokens=567,
                     by_backend={"dsh-acp": 1801})
    text = _tooltip_text(day, usage)
    assert "输入 1,234 / 输出 567 / 合计 1,801 tokens" in text
    assert "dsh-acp：1,801" in text


def test_heatmap_cell_mapping(qapp):
    widget = TokenHeatmapWidget()
    today = date(2026, 11, 14)
    widget.set_data({}, today)
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=52)
    assert widget._day_at(0, 0) == start
    # 当前周周一之后、今天之后的未来日期不命中
    col = 52
    future_row = today.weekday() + 1
    if future_row < 7:
        assert widget._day_at(col * 15, future_row * 15) is None
    assert widget._day_at(col * 15, today.weekday() * 15) == today
    assert widget._day_at(-1, 0) is None
