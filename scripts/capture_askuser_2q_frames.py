"""抓真实 kimi AskUserQuestion 两问场景全序列帧（一次性诊断取证）。

蓝本：capture_reasonix_ask_frames.py。场景：诱导 kimi 用 AskUserQuestion
在一张卡内连问两个问题，逐条记录 request_permission 载荷——
实证「答完第一问后第二问是否还有独立 request_permission 到达」：

- 若到达两条（q0/q1 各一）→ 线上协议逐题串行，跳过第二问是 IDE 侧缺陷；
- 若只到达一条（仅 q0）→ 第二问是 agent（kimi CLI）侧自行跳过，IDE 无责。

产物：文档/帧存档/askuser_2q_<时间戳>.json

运行（项目根）：
    .venv/bin/python scripts/capture_askuser_2q_frames.py
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from core.paths import PROJECT_ROOT
from llm.providers.acp import AcpConnection
from llm.providers.kimi_common import _find_bin

ARCHIVE_DIR = PROJECT_ROOT / "文档" / "帧存档"

PROMPT_2Q = (
    "请使用 AskUserQuestion 工具，在一次调用里连问我两个问题（questions 数组给两项）："
    "第一问「你喜欢什么颜色？」选项红色/蓝色；"
    "第二问「你喜欢什么水果？」选项苹果/香蕉。"
    "得到我的回答后直接结束本轮，不要执行任何其他工具，不要写文件。"
)


def main() -> int:
    bin_path = _find_bin()
    if bin_path is None:
        print("[capture] 结论：kimi CLI 未找到，跳过")
        return 1
    frames: list[dict] = []
    permission_requests: list[dict] = []

    def permission_handler(params: dict) -> str | None:
        permission_requests.append(params)
        options = params.get("options") or []
        print(f"[capture] 第 {len(permission_requests)} 次 request_permission:\n"
              f"{json.dumps(params, ensure_ascii=False, indent=2)}")
        for kind in ("allow_once", "allow_always"):
            for option in options:
                if option.get("kind") == kind:
                    print(f"[capture] 放行 optionId={option.get('optionId')!r}")
                    return option.get("optionId")
        if options:
            return options[0].get("optionId")
        return None

    conn = AcpConnection(bin_path, str(PROJECT_ROOT), "kimi acp")
    stop_reason = None
    try:
        init_result = conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False},
            "clientInfo": {"name": "zen-studio", "title": "Zen Studio",
                           "version": "capture-askuser-2q"},
        })
        agent = init_result.get("agentInfo") or {}
        print(f"[capture] agent: {agent.get('name', '?')} "
              f"{agent.get('version', '?')}")
        res = conn.request("session/new",
                           {"cwd": str(PROJECT_ROOT), "mcpServers": []},
                           timeout=60)
        sid = res["sessionId"]
        print(f"[capture] session: {sid}")
        conn.set_permission_handler(permission_handler)
        conn.purge_updates()
        conn.begin_turn("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": PROMPT_2Q}],
        })
        t0 = time.monotonic()
        while True:
            kind, obj = conn.next_update()
            elapsed = time.monotonic() - t0
            if kind == "update":
                frames.append(obj)
                update = (obj.get("params") or {}).get("update") or {}
                print(f"[capture] [{elapsed:5.1f}s] update: "
                      f"{update.get('sessionUpdate')} "
                      f"{update.get('title') or update.get('status') or ''}")
            elif kind == "response":
                frames.append({"_turn_response": obj})
                stop_reason = (obj.get("result") or {}).get("stopReason")
                print(f"[capture] [{elapsed:5.1f}s] 轮次响应: "
                      f"stopReason={stop_reason}")
                break
            elif kind == "dead":
                print(f"[capture] [{elapsed:5.1f}s] 进程死亡: {obj}")
                break
            if elapsed > 180:
                print(f"[capture] [{elapsed:5.1f}s] 180s 超时")
                break
        conn.end_turn()
    finally:
        conn.terminate()

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = ARCHIVE_DIR / f"askuser_2q_{stamp}.json"
    archive.write_text(json.dumps({
        "purpose": "AskUserQuestion 两问场景：实证第二问是否有独立 "
                   "request_permission 到达（定责 IDE 侧 vs agent 侧）",
        "prompt": PROMPT_2Q,
        "handler_strategy": "逐条记录审批载荷后回第一个 allow 类选项放行",
        "stop_reason": stop_reason,
        "permission_requests": permission_requests,
        "frames": frames,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[capture] 存档: {archive.relative_to(PROJECT_ROOT)}")
    print(f"[capture] 统计: update 帧 {len(frames)} 条，"
          f"审批请求 {len(permission_requests)} 次")
    return 0 if stop_reason is not None else 1


if __name__ == "__main__":
    sys.exit(main())
