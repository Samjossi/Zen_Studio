"""抓真实 kimi 工具调用全序列帧存档（0812-0735 计划 T3-1）。

以 capture_askuser_frames.py 为蓝本（AcpConnection 直连 kimi、
全序列帧落盘），prompt 集换为四个工具场景（各开独立 ACP 会话）：

- todolist：诱导一次 TodoList 清单调用（0812-0336 计划 B6 欠账）；
- write：Write 新建文件（重取丢失的 write_frames_kimi 证据）；
- edit：Edit 修改文件（重取丢失的 edit_frames_kimi 证据）；
- execute：Bash 命令执行（顺带取证 rawInput.command 到达时序——
  首帧齐备 vs 迟到，校正 shot_tool_cards.py 场景 22 的 🟡 假设）。

产物：帧存档/{todolist,write,edit,execute}_<时间戳>.json
（全序列 session/update 帧 + 审批载荷 + 轮次响应）。

运行（项目根）：
    .venv/bin/python scripts/capture_tool_frames.py            # 全部场景
    .venv/bin/python scripts/capture_tool_frames.py todolist   # 单个场景

注意：edit 场景依赖脚本先铺底文件 .temp/capture_edit_probe.txt；
write 场景目标文件 .temp/capture_write_probe.txt 若已存在会先删除
（保证取到「新建文件」语义帧）。
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from core.paths import PROJECT_ROOT
from llm.providers.acp import AcpConnection
from llm.providers.kimi_common import _find_bin

ARCHIVE_DIR = PROJECT_ROOT / "帧存档"
WRITE_PROBE = PROJECT_ROOT / ".temp" / "capture_write_probe.txt"
EDIT_PROBE = PROJECT_ROOT / ".temp" / "capture_edit_probe.txt"

PROMPT_TODOLIST = (
    "请先调用 TodoList 工具建立一个待办清单，必须包含恰好 3 项："
    "「整理取证脚本」「运行真机会话」「核对帧产物」（状态随意）。"
    "调用完成后直接结束本轮，不要执行任何其他工具，不要写文件。"
)

PROMPT_WRITE = (
    "请使用 Write 工具在项目内新建文件 .temp/capture_write_probe.txt，"
    "内容为恰好三行短文本：第一行、第二行、第三行（每行各一行）。"
    "写完直接结束本轮，不要执行任何其他工具。"
)

PROMPT_EDIT = (
    "请使用 Edit 工具修改项目内已有文件 .temp/capture_edit_probe.txt，"
    "把其中的「旧行二」替换为「新行二」（old_string 为「旧行二」，"
    "new_string 为「新行二」）。改完直接结束本轮，不要执行任何其他工具。"
)

PROMPT_EXECUTE = (
    "请使用 Bash 工具执行一条且仅一条命令：echo capture_execute_probe。"
    "执行完直接结束本轮，不要执行任何其他命令或工具。"
)

SCENARIOS: dict[str, str] = {
    "todolist": PROMPT_TODOLIST,
    "write": PROMPT_WRITE,
    "edit": PROMPT_EDIT,
    "execute": PROMPT_EXECUTE,
}


def _prepare(scenario: str) -> None:
    """场景铺底：edit 铺旧文件；write 清目标保证「新建」语义。"""
    if scenario == "edit":
        EDIT_PROBE.write_text("旧行一\n旧行二\n旧行三\n", encoding="utf-8")
    elif scenario == "write" and WRITE_PROBE.exists():
        WRITE_PROBE.unlink()


def capture(scenario: str, prompt: str, bin_path, stamp: str) -> int:
    """单场景取证：独立 ACP 会话，自动放行审批，全序列帧落盘。"""
    _prepare(scenario)
    frames: list[dict] = []
    permission_requests: list[dict] = []

    def permission_handler(params: dict) -> str | None:
        """自动放行：回第一个 allow_once/allow_always 选项 optionId。"""
        permission_requests.append(params)
        options = params.get("options") or []
        print(f"[capture] request_permission: "
              f"{json.dumps(params, ensure_ascii=False)[:300]}")
        for kind in ("allow_once", "allow_always"):
            for option in options:
                if option.get("kind") == kind:
                    print(f"[capture] 放行 optionId={option.get('optionId')!r}")
                    return option.get("optionId")
        return None

    conn = AcpConnection(bin_path, str(PROJECT_ROOT), "kimi acp")
    stop_reason = None
    try:
        conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False},
            "clientInfo": {"name": "zen-studio", "title": "Zen Studio",
                           "version": "capture-tool-frames-t3"},
        })
        res = conn.request("session/new",
                           {"cwd": str(PROJECT_ROOT), "mcpServers": []},
                           timeout=60)
        sid = res["sessionId"]
        print(f"[capture:{scenario}] session: {sid}")
        conn.set_permission_handler(permission_handler)
        conn.purge_updates()
        conn.begin_turn("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": prompt}],
        })
        t0 = time.monotonic()
        while True:
            kind, obj = conn.next_update()
            elapsed = time.monotonic() - t0
            if kind == "update":
                frames.append(obj)
                update = (obj.get("params") or {}).get("update") or {}
                print(f"[capture:{scenario}] [{elapsed:5.1f}s] update: "
                      f"{update.get('sessionUpdate')} "
                      f"{update.get('title') or update.get('status') or ''}")
            elif kind == "response":
                frames.append({"_turn_response": obj})
                stop_reason = (obj.get("result") or {}).get("stopReason")
                print(f"[capture:{scenario}] [{elapsed:5.1f}s] 轮次响应: "
                      f"stopReason={stop_reason}")
                break
            elif kind == "dead":
                print(f"[capture:{scenario}] [{elapsed:5.1f}s] 进程死亡: {obj}")
                break
            if elapsed > 180:
                print(f"[capture:{scenario}] [{elapsed:5.1f}s] 180s 超时")
                break
        conn.end_turn()
    finally:
        conn.terminate()

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive = ARCHIVE_DIR / f"{scenario}_{stamp}.json"
    archive.write_text(json.dumps({
        "purpose": "0812-0735 计划 T3：kimi 工具调用全序列帧存档"
                   f"（{scenario} 场景）",
        "prompt": prompt,
        "handler_strategy": "自动放行第一个 allow_once/allow_always 选项",
        "stop_reason": stop_reason,
        "permission_requests": permission_requests,
        "frames": frames,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[capture:{scenario}] 存档: {archive.relative_to(PROJECT_ROOT)}")
    print(f"[capture:{scenario}] 统计: update 帧 {len(frames)} 条，"
          f"审批请求 {len(permission_requests)} 次")
    return 0 if stop_reason is not None else 1


def main() -> int:
    bin_path = _find_bin()
    if bin_path is None:
        print("[capture] 结论：kimi CLI 未安装，跳过")
        return 1
    selected = sys.argv[1:] or list(SCENARIOS)
    unknown = [name for name in selected if name not in SCENARIOS]
    if unknown:
        print(f"[capture] 未知场景: {unknown}，可选: {list(SCENARIOS)}")
        return 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rc = 0
    for name in selected:
        print(f"[capture] ===== 场景 {name} =====")
        rc |= capture(name, SCENARIOS[name], bin_path, stamp)
    return rc


if __name__ == "__main__":
    sys.exit(main())
