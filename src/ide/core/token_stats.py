"""Token 用量统计：轮次消耗落盘与日/周/月聚合（零 Qt 依赖）。

计划：work plans/2026-1114-1452_Token用量贡献热力图落地计划.md。

口径：每条记录 = 一轮对话内逐次 API 调用真实消耗的求和（输入含缓存
读写 + 输出），与上下文窗口占用（UsageStats.used/size）是两套语义。
日期归属 = 轮次收尾时刻的本地日期；底部汇总 = 今日 / 最近七天 /
最近三十天滚动窗口（均含今日）。仅 kimi / DSH 后端采集，其余后端
无真值通道不入账。

存储：用户级 ${XDG_CONFIG_HOME:-~/.config}/zen-studio/token_usage.jsonl
（append-only，一行一轮；所有源码副本/IDE 实例共用一本，复制源码不再
带出第二本账）。turn_id 去重防同轮重复收尾双记；多开实例共享文件，
写入走 flock 串行化（settings.lock 同例）。
"""
import fcntl
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ide.core.paths import USER_GLOBAL_CONFIG_DIR

logger = logging.getLogger(__name__)


def _usage_file() -> Path:
    """账本路径：同一 OS 用户下所有副本/实例共用，必须是用户级绝对路径。

    ZEN_TOKEN_USAGE_FILE 仅接受绝对路径（或 ~ 开头），相对路径视为
    配置错误：记 warning 并回退默认，避免写盘落点随进程 CWD 漂移。
    """
    override = os.environ.get("ZEN_TOKEN_USAGE_FILE")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate
        logger.warning(
            "ZEN_TOKEN_USAGE_FILE 必须是绝对路径，已回退默认路径: %s", override)
    return USER_GLOBAL_CONFIG_DIR / "token_usage.jsonl"


#: 用量记录文件与 flock 锁（仅 Linux；进程死亡锁自动释放）。
#: 模块导入时定型，运行中改环境变量不生效，须重启 IDE
USAGE_FILE = _usage_file()
USAGE_LOCK_FILE = USAGE_FILE.with_suffix(".lock")

#: 记录字段（落盘 schema；消费侧一律经本常量组引用，禁止裸字符串漂移）
_KEY_TS = "ts"
_KEY_BACKEND = "backend"
_KEY_MODEL = "model"
_KEY_TURN_ID = "turn_id"
_KEY_INPUT_OTHER = "input_other"
_KEY_INPUT_CACHE_READ = "input_cache_read"
_KEY_INPUT_CACHE_CREATION = "input_cache_creation"
_KEY_OUTPUT = "output"
_INPUT_KEYS = (_KEY_INPUT_OTHER, _KEY_INPUT_CACHE_READ, _KEY_INPUT_CACHE_CREATION)


@dataclass
class DayUsage:
    """单日用量聚合：输入/输出分项 + 分后端总量小计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    by_backend: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class UsageSummary:
    """底部常驻三项：今日 / 最近七天 / 最近三十天总量（滚动窗口，含今日）。"""
    today: int
    last_7_days: int
    last_30_days: int


def record_turn(
    *,
    backend: str,
    model: str,
    turn_id: str,
    input_other: int,
    input_cache_read: int,
    input_cache_creation: int,
    output: int,
    ts: datetime | None = None,
    path: Path | None = None,
) -> bool:
    """一轮消耗追加落盘；重复 turn_id / 全零消耗 / 非法输入返回 False。

    任何 I/O 失败仅记日志返回 False，不向调用方抛（统计不得打断对话）。
    """
    values = (input_other, input_cache_read, input_cache_creation, output)
    if not turn_id or any(not isinstance(v, int) or v < 0 for v in values):
        return False
    if not any(values):
        return False
    moment = ts or datetime.now().astimezone()
    record = {
        _KEY_TS: moment.isoformat(),
        _KEY_BACKEND: backend,
        _KEY_MODEL: model,
        _KEY_TURN_ID: turn_id,
        _KEY_INPUT_OTHER: input_other,
        _KEY_INPUT_CACHE_READ: input_cache_read,
        _KEY_INPUT_CACHE_CREATION: input_cache_creation,
        _KEY_OUTPUT: output,
    }
    target = path or USAGE_FILE
    lock_path = target.with_suffix(".lock")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if turn_id in _load_turn_ids(target):
                return False
            with open(target, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return True
    except OSError:
        logger.exception("token 用量落盘失败")
        return False


def load_daily(path: Path | None = None) -> dict[date, DayUsage]:
    """全量读 → 按本地日期聚合；文件缺失/坏行/缺字段一律跳过不阻断。"""
    target = path or USAGE_FILE
    daily: dict[date, DayUsage] = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return daily
    except OSError:
        logger.exception("token 用量读取失败")
        return daily
    for line in lines:
        try:
            record = json.loads(line)
            moment = datetime.fromisoformat(record[_KEY_TS])
            day = (moment.astimezone() if moment.tzinfo else moment).date()
            input_tokens = sum(int(record.get(k) or 0) for k in _INPUT_KEYS)
            output_tokens = int(record.get(_KEY_OUTPUT) or 0)
            backend = str(record.get(_KEY_BACKEND) or "unknown")
        except (ValueError, TypeError, AttributeError):
            continue  # 坏行/私有格式漂移：跳过而非显示错数
        usage = daily.setdefault(day, DayUsage())
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.by_backend[backend] = usage.by_backend.get(backend, 0) + (
            input_tokens + output_tokens)
    return daily


def summarize(daily: dict[date, DayUsage], today: date) -> UsageSummary:
    """今日 / 最近七天（today-6 起）/ 最近三十天（today-29 起）总量，均含今日。"""
    start_7 = today - timedelta(days=6)
    start_30 = today - timedelta(days=29)
    today_total = last_7_total = last_30_total = 0
    for day, usage in daily.items():
        total = usage.total
        if day == today:
            today_total += total
        if start_7 <= day <= today:
            last_7_total += total
        if start_30 <= day <= today:
            last_30_total += total
    return UsageSummary(today=today_total, last_7_days=last_7_total,
                        last_30_days=last_30_total)


def level_of(day_total: int, nonzero_totals: list[int]) -> int:
    """绿色分档 0~4：0 消耗为 0 档；非零日按全样本分位数切 4 档。

    GitHub 同款相对分档：避免个别超大日把其余格子压成同一浅色。
    """
    if day_total <= 0 or not nonzero_totals:
        return 0
    ordered = sorted(nonzero_totals)
    thresholds = [ordered[min(len(ordered) - 1, (len(ordered) * q) // 4)]
                  for q in (1, 2, 3)]
    return 1 + sum(day_total > t for t in thresholds)


def _load_turn_ids(target: Path) -> set[str]:
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    ids = set()
    for line in lines:
        try:
            turn_id = json.loads(line).get(_KEY_TURN_ID)
        except (ValueError, AttributeError):
            continue
        if isinstance(turn_id, str):
            ids.add(turn_id)
    return ids
