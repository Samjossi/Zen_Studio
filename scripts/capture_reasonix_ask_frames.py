"""抓真实 reasonix ask 提问工具全序列帧存档（0812-0952 计划 T0）。

以 capture_tool_frames.py 为蓝本（AcpConnection 直连、全序列帧落盘），
后端换 reasonix、场景换为诱导 ask 提问工具调用——实证项（计划 §4 T0）：

1. tool_call 首帧 title 确切值（是否恒为小写 ask，有无命名空间前缀变体）；
2. request_permission options 的 kind 编码（是否 allow_once 系）与 optionId 形态；
3. rawInput.questions 结构是否与 kimi 同构（options/multiSelect 字段名）；
4. 是否支持多选及其编码形态。

产物：文档/帧存档/ask_reasonix_<时间戳>.json
（全序列 session/update 帧 + 审批载荷 + 轮次响应）。

运行（项目根）：
    .venv/bin/python scripts/capture_reasonix_ask_frames.py
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from core.paths import PROJECT_ROOT
from llm.providers.acp import AcpConnection
from llm.providers.reasonix_acp import _find_bin

ARCHIVE_DIR = PROJECT_ROOT / "文档" / "帧存档"

#: 单选场景：诱导一次 ask 提问调用（四选项，对齐 0812 复发截图形态）
PROMPT_ASK = (
    "请使用 ask 工具问我一个问题：今天晚上吃什么？给我恰好四个选项让我选择。"
    "得到我的回答后直接结束本轮，不要执行任何其他工具，不要写文件。"
)

#: 多选探测场景：诱导多选形态 ask（实证 multiSelect 字段名与回传编码）
PROMPT_ASK_MULTI = (
    "请使用 ask 工具问我一个可以多选的问题：周末想做的活动有哪些？"
    "给我恰好四个选项并允许多选。得到我的回答后直接结束本轮，"
    "不要执行任何其他工具，不要写文件。"
)

SCENARIOS: dict[str, str] = {
    "ask_reasonix": PROMPT_ASK,
    "ask_reasonix_multi": PROMPT_ASK_MULTI,
}


def capture(scenario: str, prompt: str, bin_path: str, stamp: str) -> int:
    """单场景取证：独立 ACP 会话，审批载荷记录后回第一个选项放行，全序列帧落盘。"""
    frames: list[dict] = []
    permission_requests: list[dict] = []

    def permission_handler(params: dict) -> str | None:
        """取证优先：完整记录 params 落盘；回执选第一个选项让轮次走完
        （本脚本只取证帧结构，不验证交互回环——那是 T5 的事）。"""
        permission_requests.append(params)
        options = params.get("options") or []
        print(f"[capture] request_permission 完整载荷:\n"
              f"{json.dumps(params, ensure_ascii=False, indent=2)}")
        for kind in ("allow_once", "allow_always"):
            for option in options:
                if option.get("kind") == kind:
                    print(f"[capture] 放行 optionId={option.get('optionId')!r}")
                    return option.get("optionId")
        # 无 allow 类 kind：取第一个选项原值（不臆造，原样回传 agent 提供值）
        if options:
            print(f"[capture] 无 allow 类 kind，取首项 "
                  f"optionId={options[0].get('optionId')!r} "
                  f"kind={options[0].get('kind')!r}")
            return options[0].get("optionId")
        return None

    conn = AcpConnection(bin_path, str(PROJECT_ROOT), "reasonix acp")
    stop_reason = None
    try:
        init_result = conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False},
            "clientInfo": {"name": "zen-studio", "title": "Zen Studio",
                           "version": "capture-reasonix-ask-t0"},
        })
        agent = init_result.get("agentInfo") or {}
        print(f"[capture] agent: {agent.get('name', '?')} "
              f"{agent.get('version', '?')}")
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
        "purpose": "0812-0952 计划 T0：reasonix ask 提问工具全序列帧存档"
                   f"（{scenario} 场景）",
        "prompt": prompt,
        "handler_strategy": "记录完整审批载荷后回第一个选项放行（仅取证帧结构）",
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
        print("[capture] 结论：reasonix CLI 未安装，跳过")
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
