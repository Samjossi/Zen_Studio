"""token 用量账本迁移脚本：按 turn_id 去重合并、坏行容错、幂等（2026-1117-0627 计划）。"""
import json
import subprocess
import sys

SCRIPT = "scripts/merge_token_usage.py"


def _record(turn_id, **overrides):
    record = {
        "ts": "2026-11-14T12:00:00",
        "backend": "kimi-acp",
        "model": "k3",
        "turn_id": turn_id,
        "input_other": 100,
        "input_cache_read": 200,
        "input_cache_creation": 30,
        "output": 40,
    }
    record.update(overrides)
    return json.dumps(record, ensure_ascii=False)


def _write(path, lines):
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _run_merge(dest, *sources):
    return subprocess.run(
        [sys.executable, SCRIPT, "--dest", str(dest),
         *(str(s) for s in sources)],
        capture_output=True, text=True, check=True)


def test_merge_dedup_and_union(tmp_path):
    dest = tmp_path / "usage.jsonl"
    src_a = tmp_path / "a.jsonl"
    src_b = tmp_path / "b.jsonl"
    _write(dest, [_record("shared:1")])
    _write(src_a, [_record("shared:1"), _record("a:1")])
    _write(src_b, [_record("b:1")])
    _run_merge(dest, src_a, src_b)
    turn_ids = [json.loads(line)["turn_id"] for line in
                dest.read_text(encoding="utf-8").splitlines()]
    assert sorted(turn_ids) == ["a:1", "b:1", "shared:1"]


def test_merge_tolerates_bad_lines(tmp_path):
    dest = tmp_path / "usage.jsonl"
    src = tmp_path / "a.jsonl"
    _write(src, ["{bad json", json.dumps({"ts": "x"}), _record("a:1")])
    _run_merge(dest, src)
    assert [json.loads(line)["turn_id"] for line in
            dest.read_text(encoding="utf-8").splitlines()] == ["a:1"]


def test_merge_conflict_keeps_first(tmp_path):
    dest = tmp_path / "usage.jsonl"
    src = tmp_path / "a.jsonl"
    _write(dest, [_record("t:1", output=40)])
    _write(src, [_record("t:1", output=99)])
    result = _run_merge(dest, src)
    assert "conflict: t:1" in result.stdout
    record = json.loads(dest.read_text(encoding="utf-8").splitlines()[0])
    assert record["output"] == 40


def test_merge_is_idempotent(tmp_path):
    dest = tmp_path / "usage.jsonl"
    src = tmp_path / "a.jsonl"
    _write(src, [_record("a:1"), _record("a:2")])
    _run_merge(dest, src)
    _run_merge(dest, src)
    assert len(dest.read_text(encoding="utf-8").splitlines()) == 2
