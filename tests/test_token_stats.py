"""Token 用量统计核心：落盘去重 / 日周月聚合 / 绿色分档（2026-1114-1452 计划）。"""
import json
from datetime import date, datetime

from ide.core import token_stats
from ide.core.token_stats import DayUsage, level_of, load_daily, record_turn, summarize

TS = datetime(2026, 11, 14, 12, 0)  #  naive 本地时刻（聚合口径与机器时区无关）


def _record(path, **overrides):
    kwargs = dict(backend="kimi-acp", model="k3", turn_id="s:1",
                  input_other=100, input_cache_read=200,
                  input_cache_creation=30, output=40, ts=TS)
    kwargs.update(overrides)
    return record_turn(path=path, **kwargs)


def test_record_and_load_daily(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path)
    assert _record(path, turn_id="s:2", backend="dsh-acp",
                   input_other=10, input_cache_read=0,
                   input_cache_creation=0, output=5)
    daily = load_daily(path)
    usage = daily[date(2026, 11, 14)]
    assert usage.input_tokens == 100 + 200 + 30 + 10
    assert usage.output_tokens == 45
    assert usage.total == 385
    assert usage.by_backend == {"kimi-acp": 370, "dsh-acp": 15}


def test_duplicate_turn_id_skipped(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path)
    assert not _record(path)  # 同 turn_id 幂等去重
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_zero_and_negative_consumption_rejected(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert not _record(path, input_other=0, input_cache_read=0,
                       input_cache_creation=0, output=0)
    assert not _record(path, output=-1)
    assert not _record(path, turn_id="")
    assert not path.exists()


def test_bad_lines_and_missing_file_tolerated(tmp_path):
    assert load_daily(tmp_path / "absent.jsonl") == {}
    path = tmp_path / "usage.jsonl"
    assert _record(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write("{bad json\n")
        f.write(json.dumps({"ts": "not-a-date"}) + "\n")
    daily = load_daily(path)
    assert daily[date(2026, 11, 14)].total == 370


def test_day_assignment_by_local_date(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path, turn_id="s:1", ts=datetime(2026, 11, 14, 23, 30))
    assert _record(path, turn_id="s:2", ts=datetime(2026, 11, 15, 0, 30))
    daily = load_daily(path)
    assert set(daily) == {date(2026, 11, 14), date(2026, 11, 15)}


def test_aware_timestamp_parsed(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path, ts=datetime.fromisoformat("2026-11-14T12:00:00+08:00"))
    assert sum(u.total for u in load_daily(path).values()) == 370


def test_summarize_rolling_windows():
    # 2026-11-15：最近七天 = 11-09 起；最近三十天 = 10-17 起（均含今日）
    today = date(2026, 11, 15)
    daily = {
        date(2026, 11, 8): DayUsage(input_tokens=1),    # 仅最近三十天
        date(2026, 11, 9): DayUsage(input_tokens=2),    # 七天窗口首日
        today: DayUsage(output_tokens=4),               # 今日
        date(2026, 11, 1): DayUsage(input_tokens=8),    # 仅最近三十天
        date(2026, 10, 16): DayUsage(input_tokens=16),  # 三十天窗口之外
        date(2026, 11, 20): DayUsage(input_tokens=32),  # 未来日：全不计
    }
    summary = summarize(daily, today)
    assert summary.today == 4
    assert summary.last_7_days == 2 + 4
    assert summary.last_30_days == 1 + 2 + 4 + 8


def test_summarize_empty():
    assert summarize({}, date(2026, 11, 14)) == token_stats.UsageSummary(0, 0, 0)


def test_level_of_zero_and_empty():
    assert level_of(0, [10, 20]) == 0
    assert level_of(10, []) == 0


def test_level_of_single_nonzero_day():
    assert level_of(100, [100]) == 1


def test_level_of_quartiles():
    totals = [10, 20, 30, 40, 50, 60, 70, 80]
    # 阈值 = 30 / 50 / 70（25/50/75 分位最近秩）
    assert level_of(10, totals) == 1
    assert level_of(30, totals) == 1
    assert level_of(31, totals) == 2
    assert level_of(51, totals) == 3
    assert level_of(71, totals) == 4
    assert level_of(80, totals) == 4
