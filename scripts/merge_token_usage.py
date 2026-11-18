"""按 turn_id 合并多份 token_usage.jsonl 到共享账本；先关闭所有 IDE 再执行。

用法（两个同源副本都可执行同一命令）：
    .venv/bin/python scripts/merge_token_usage.py \
        --dest "${XDG_CONFIG_HOME:-$HOME/.config}/zen-studio/token_usage.jsonl" \
        /path/to/copyA/config/token_usage.jsonl \
        /path/to/copyB/config/token_usage.jsonl

同一 turn_id 视为同一笔消费；数值字段冲突时保留首条并打印 warning，
人工核查，不做相加。目标文件先做带时间戳的 .bak- 备份，再原子替换。
"""
import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

_FIELDS = ("input_other", "input_cache_read", "input_cache_creation", "output")


def _records(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            turn_id = record["turn_id"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(turn_id, str) and turn_id:
            yield turn_id, record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()

    dest = args.dest.expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, dict] = {}
    conflicts = 0
    paths = [dest, *(p.expanduser() for p in args.sources)]
    for path in paths:
        for turn_id, record in _records(path):
            if turn_id not in merged:
                merged[turn_id] = record
                continue
            if any(merged[turn_id].get(k) != record.get(k) for k in _FIELDS):
                conflicts += 1
                print(f"conflict: {turn_id} (keep first)")

    if dest.exists():
        backup = dest.with_name(
            f"{dest.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(dest, backup)
        print(f"backup: {backup}")

    tmp = dest.with_name(dest.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        for record in merged.values():
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, dest)
    print(f"records={len(merged)} conflicts={conflicts} dest={dest}")


if __name__ == "__main__":
    main()
