"""Token 用量统计核心：落盘去重 / 日周月聚合 / 绿色分档（2026-1114-1452 计划）。"""
import json
from datetime import date, datetime

import pytest

from ide.core import paths, token_stats
from ide.core.paths import PROJECT_ROOT, USER_CONFIG_DIR, USER_GLOBAL_CONFIG_DIR
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


def test_usage_file_default(monkeypatch):
    monkeypatch.delenv("ZEN_TOKEN_USAGE_FILE", raising=False)
    assert token_stats._usage_file() == USER_GLOBAL_CONFIG_DIR / "token_usage.jsonl"


def test_usage_file_env_override(monkeypatch, tmp_path):
    override = tmp_path / "shared.jsonl"
    monkeypatch.setenv("ZEN_TOKEN_USAGE_FILE", str(override))
    assert token_stats._usage_file() == override


def test_usage_file_relative_override_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("ZEN_TOKEN_USAGE_FILE", "relative.jsonl")
    with caplog.at_level("WARNING", logger="ide.core.token_stats"):
        result = token_stats._usage_file()
    assert result == USER_GLOBAL_CONFIG_DIR / "token_usage.jsonl"
    assert any("ZEN_TOKEN_USAGE_FILE" in r.message for r in caplog.records)


def test_global_config_dir_is_user_level():
    # 跨副本共享：用户级根不随源码副本走，开发态与 USER_CONFIG_DIR（项目 config/）不同指
    assert USER_GLOBAL_CONFIG_DIR.name == "zen-studio"
    assert USER_GLOBAL_CONFIG_DIR != USER_CONFIG_DIR
    assert PROJECT_ROOT / "config" not in USER_GLOBAL_CONFIG_DIR.parents
    assert USER_GLOBAL_CONFIG_DIR.is_absolute()


def test_xdg_config_home_parsing(monkeypatch, tmp_path):
    # 绝对 XDG 生效；相对 XDG 按 XDG 规范忽略，回退 ~/.config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert paths._xdg_config_dir() == tmp_path / "xdg" / "zen-studio"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/xdg")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert paths._xdg_config_dir() == tmp_path / "home" / ".config" / "zen-studio"


def test_shared_ledger_two_turns_same_file(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path, turn_id="s:1")
    assert _record(path, turn_id="s:2", backend="dsh-acp")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_record_creates_lock_next_to_ledger(tmp_path):
    path = tmp_path / "usage.jsonl"
    assert _record(path)
    assert (tmp_path / "usage.lock").exists()


@pytest.mark.parametrize("n, expected", [
    (0, "0"),
    (999, "999"),
    (1_000, "1K"),
    (3_113, "3.1K"),
    (86_500, "86.5K"),
    (30_000, "30K"),            # 尾零去除，不得出现 30.0K
    (500_000, "500K"),
    (999_999, "1000K"),         # 跨档进位属可接受简写
    (1_000_000, "1M"),
    (368_598_460, "368.6M"),
    (999_999_999, "1000M"),
    (1_200_000_000, "1.2B"),
    (12_500_000_000, "12.5B"),
    (12_250_000_000, "12.25B"),
])
def test_format_token_amount(n, expected):
    assert token_stats.format_token_amount(n) == expected


@pytest.mark.parametrize("bad", [1.5, "1000", None, -1, True])
def test_format_token_amount_rejects_non_int(bad):
    with pytest.raises(TypeError):
        token_stats.format_token_amount(bad)
