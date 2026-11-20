"""DSH 会话落盘日志读取与轮次消耗差量：纯文件计算，无连接、无 Qt。

真值在 dsh 会话日志的 `assistant/message` 事件 usage（inputTokens/
outputTokens/cacheReadTokens/reasoningTokens，格式证据：参考代码/
deepseek-harness/snapshots/sdk/ 快照）。本模块只做「定位 generation
文件 + 解压读取 + 事件筛选 + 基线差量」，不持有跨轮次状态：基线由
调用方保存，文件缺失/损坏/截断一律降级为空，不抛出。

zstd 是可选外部命令（后端默认压缩落盘）：工具缺失时保持既有告警并
跳过采集，不引 Python 依赖，也不在 import 期探测。
"""
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from ide.llm.base import TurnConsumption

logger = logging.getLogger(__name__)

#: 规范 generation 文件名：session.jsonl（v0）/ session.vN.jsonl[.zstd]
LOG_NAME_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl(\.zstd)?$")


def dsh_home() -> Path:
    """dsh home（DSH_HOME 覆盖，默认 ~/.dsh；与 _find_bin 同一来源约定）。"""
    return Path(os.environ.get("DSH_HOME") or str(Path.home() / ".dsh"))


def session_log_path(session_id: str) -> Path | None:
    """按 sessionId 定位当前 generation 会话日志；找不到返回 None。

    布局（session-persistence-jsonl README）：`<home>/sessions/
    --<normalized-cwd>--/<encoded-id>/session.vN.jsonl[.zstd]`。项目目录
    名归一化规则私有，不拼装——一层 glob 直匹配会话目录（id 含需转义
    字符时定位不到，静默停采不臆造路径）。取数值最高 generation；
    同代压缩/裸文本并存时优先裸文本（省一次解压）。
    """
    best: Path | None = None
    best_key: tuple[int, int] | None = None
    try:
        candidates = list((dsh_home() / "sessions").glob(
            f"*/{session_id}/session*.jsonl*"))
    except OSError:
        return None
    for path in candidates:
        match = LOG_NAME_RE.match(path.name)
        if not match:
            continue
        key = (int(match.group(1) or 0), 1 if match.group(2) is None else 0)
        if best_key is None or key > best_key:
            best, best_key = path, key
    return best


def read_session_records(path: Path) -> list[dict] | None:
    """读会话日志为 JSON 记录列表；坏行/撕裂尾行跳过，读取失败返回 None。

    默认压缩为 zstd（后端默认 'zstd'），经系统 `zstd -dc` 解压——与
    sound_player 的外部命令先例一致：工具缺失静默停采，不引新依赖。
    """
    try:
        if path.suffix == ".zstd":
            if not shutil.which("zstd"):
                logger.warning("dsh 用量统计需 zstd 解压会话日志，系统未安装，跳过采集")
                return None
            proc = subprocess.run(
                ["zstd", "-dc", str(path)], capture_output=True, timeout=15)
            if proc.returncode != 0:
                return None
            text = proc.stdout.decode("utf-8", errors="replace")
        else:
            text = path.read_text(encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        logger.exception("dsh 会话日志读取失败")
        return None
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def usage_events(records: list[dict]) -> list[dict]:
    """带逐次调用 usage 的 assistant/message 事件（usage 缺字段的整条跳过）。"""
    events = []
    for record in records:
        if record.get("type") != "assistant/message":
            continue
        usage = (record.get("data") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        values = [usage.get("inputTokens"), usage.get("outputTokens"),
                  usage.get("cacheReadTokens", 0)]
        if any(type(v) is not int or v < 0 for v in values):
            continue
        events.append(record)
    return events


def turn_consumption(
    session_id: str | None, baseline: int
) -> tuple[TurnConsumption | None, int]:
    """读会话日志 → (本轮消耗, 最新用量事件数)；失败返回 (None, baseline)。

    基线差量法与 kimi 同款：baseline 为轮前用量事件数，本轮 = 其后的
    新增事件求和。reasoningTokens 是输出子集，不重复计入。turn_id 取
    `session_id:轮末累计事件数`，同轮重复收尾幂等去重。
    """
    if not session_id:
        return None, baseline
    path = session_log_path(session_id)
    if path is None:
        return None, baseline
    records = read_session_records(path)
    if records is None:
        return None, baseline
    events = usage_events(records)
    new_events = events[baseline:]
    if not new_events:
        return None, len(events)
    input_other = input_cache_read = output = 0
    for event in new_events:
        usage = event["data"]["usage"]
        input_other += usage["inputTokens"]
        input_cache_read += usage.get("cacheReadTokens", 0)
        output += usage["outputTokens"]
    if not (input_other or input_cache_read or output):
        return None, len(events)
    return TurnConsumption(
        input_other=input_other, input_cache_read=input_cache_read,
        input_cache_creation=0, output=output,
        turn_id=f"{session_id}:{len(events)}"), len(events)
