"""AskUserQuestion/ask 决策特判与选项提取单元测试（0807-0148 计划 §5.1；
0812-0952 计划 T5 扩展）。

覆盖：
- is_question_request title 形态（归一化：mcp__ 前缀/大小写混写）；
- is_question_request 结构通道（0812-0952 T1）：rawInput.questions 列表 /
  reasonix 签名（rawInput.question+options，决策帧 title 是问题原文）命中，
  Bash/Edit 等普通工具 rawInput 不命中；
- decide_permission 四态 × question（kimi/reasonix 双形态）一律 DECISION_ASK
  （含 auto_all）；
- 非 question 请求四态行为回归不变；
- _extract_question_options 结构化载荷提取（真实帧蓝本：
  .temp/frame_archive/askuser_*.json——multi_select 蛇形字段名；
  .temp/frame_archive/ask_reasonix_*.json——multiSelect 驼峰兼容）。

运行（项目根）：.venv/bin/python scripts/test_question_permission.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.permission_policy import (
    DECISION_ALLOW,
    DECISION_ASK,
    MODE_AUTO_ALL,
    MODE_AUTO_GUARDED,
    MODE_CONFIRM_ALL,
    MODE_CONFIRM_EXECUTE,
    PERMISSION_MODES,
    decide_permission,
    is_question_request,
)
from llm.providers.acp import _extract_question_options

FOUR_MODES = (MODE_CONFIRM_ALL, MODE_CONFIRM_EXECUTE, MODE_AUTO_GUARDED, MODE_AUTO_ALL)
assert set(FOUR_MODES) == set(PERMISSION_MODES)


def check(name: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败：{name}")


def q_params(title: str, kind: str = "other") -> dict:
    return {"toolCall": {"title": title, "kind": kind},
            "options": [{"optionId": "q0_opt_0", "name": "红色", "kind": "allow_once"},
                        {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}]}


# 1. is_question_request title 形态
check("AskUserQuestion 命中", is_question_request(q_params("AskUserQuestion")))
check("mcp__xxx__AskUserQuestion 命中", is_question_request(q_params("mcp__xxx__AskUserQuestion")))
check("小写混写 askUSERquestion 命中", is_question_request(q_params("askUSERquestion")))
check("reasonix 工具名 ask 命中（白名单辅助）", is_question_request(q_params("ask")))
check("Agent 不命中", not is_question_request(q_params("Agent")))
check("空 title 不命中", not is_question_request(q_params("")))
check("缺 toolCall 不命中", not is_question_request({}))

# 1b. 结构通道（0812-0952 计划 T1，实证 .temp/frame_archive/ask_reasonix_*.json）
# reasonix 决策帧：title 是问题原文而非工具名，白名单管不到
reasonix_params = {
    "toolCall": {"title": "今天晚上吃什么？", "kind": "other",
                 "rawInput": {"id": "q1", "multi": False, "question": "今天晚上吃什么？",
                              "options": [{"Label": "火锅", "Description": "热闹又暖和"}]}},
    "options": [{"optionId": "q1:1", "name": "火锅 - 热闹又暖和", "kind": "allow_once"},
                {"optionId": "q1:cancel", "name": "Cancel", "kind": "reject_once"}]}
check("reasonix 签名（question+options）命中", is_question_request(reasonix_params))
check("rawInput.questions 非白名单 title 命中",
      is_question_request({"toolCall": {"title": "未来某新名", "kind": "other",
                                        "rawInput": {"questions": []}}}))
check("Bash rawInput（command）不命中",
      not is_question_request({"toolCall": {"title": "Bash", "kind": "execute",
                                            "rawInput": {"command": "ls"}}}))
check("Edit rawInput 不命中",
      not is_question_request({"toolCall": {"title": "Edit", "kind": "edit",
                                            "rawInput": {"file_path": "a.md",
                                                         "old_string": "x", "new_string": "y"}}}))
check("仅 question 无 options 不命中（签名需成对）",
      not is_question_request({"toolCall": {"title": "X", "rawInput": {"question": "q"}}}))

# 2. decide_permission 四态 × question 一律 ASK
for mode in FOUR_MODES:
    decision, reason = decide_permission(q_params("AskUserQuestion"), mode)
    check(f"{mode} × question → ASK", decision == DECISION_ASK and reason is None)
    d2, r2 = decide_permission(reasonix_params, mode)
    check(f"{mode} × reasonix ask → ASK", d2 == DECISION_ASK and r2 is None)

# 3. 非 question 请求回归（原四态行为不变）
bash_safe = {"toolCall": {"title": "Bash", "kind": "execute",
                          "rawInput": {"command": "ls *.md"}}}
bash_danger = {"toolCall": {"title": "Bash", "kind": "execute",
                            "rawInput": {"command": "git push --force"}}}
read_tool = {"toolCall": {"title": "Read", "kind": "read"}}
check("confirm_all × read → ASK", decide_permission(read_tool, MODE_CONFIRM_ALL)[0] == DECISION_ASK)
check("confirm_all × 安全命令 → ASK", decide_permission(bash_safe, MODE_CONFIRM_ALL)[0] == DECISION_ASK)
check("confirm_execute × read → ALLOW", decide_permission(read_tool, MODE_CONFIRM_EXECUTE)[0] == DECISION_ALLOW)
check("confirm_execute × 安全命令 → ASK", decide_permission(bash_safe, MODE_CONFIRM_EXECUTE)[0] == DECISION_ASK)
check("auto_guarded × read → ALLOW", decide_permission(read_tool, MODE_AUTO_GUARDED)[0] == DECISION_ALLOW)
check("auto_guarded × 安全命令 → ALLOW", decide_permission(bash_safe, MODE_AUTO_GUARDED)[0] == DECISION_ALLOW)
d, r = decide_permission(bash_danger, MODE_AUTO_GUARDED)
check("auto_guarded × 危险命令 → ASK 附原因", d == DECISION_ASK and r is not None)
check("auto_all × 危险命令 → ALLOW", decide_permission(bash_danger, MODE_AUTO_ALL)[0] == DECISION_ALLOW)
check("未知 mode 回退 auto_guarded",
      decide_permission(bash_danger, "bogus")[0] == DECISION_ASK)

# 4. _extract_question_options（真实帧蓝本结构）
update = {"rawInput": {"questions": [
    {"question": "你想在问候卡片上放哪些元素？（可多选）",
     "options": [{"label": "佛像"}, {"label": "莲花", "description": "金色莲花"}],
     "multi_select": True},
    {"question": "问候语写成哪种颜色？",
     "options": [{"label": "红色"}],
     "header": "颜色"},
]}}
items = _extract_question_options(update)
check("提取两问", items is not None and len(items) == 2)
check("multi_select 蛇形字段提取为 True", items[0]["multi_select"] is True)
check("首问无 header 键", "header" not in items[0])
check("次问 header=颜色", items[1].get("header") == "颜色")
check("次问 multi_select 缺省 False", items[1]["multi_select"] is False)
check("选项 label 提取", items[0]["options"] == [{"label": "佛像"},
                                                {"label": "莲花", "description": "金色莲花"}])
check("description 可选保留", items[0]["options"][1]["description"] == "金色莲花")
# 4b. reasonix 帧形态（0812-0952 计划 T2，实证蓝本
#     .temp/frame_archive/ask_reasonix_multi_*.json）：multiSelect 驼峰字段名
update_reasonix = {"rawInput": {"questions": [
    {"header": "周末活动", "question": "周末想做的活动有哪些？", "multiSelect": True,
     "options": [{"label": "去户外徒步", "description": "亲近大自然"}]},
]}}
items_rx = _extract_question_options(update_reasonix)
check("reasonix 驼峰 multiSelect 提取为 True",
      items_rx is not None and items_rx[0]["multi_select"] is True)
check("reasonix header/label 同构提取",
      items_rx[0]["header"] == "周末活动"
      and items_rx[0]["options"] == [{"label": "去户外徒步", "description": "亲近大自然"}])
check("无 rawInput 返回 None", _extract_question_options({}) is None)
check("questions 非列表返回 None",
      _extract_question_options({"rawInput": {"questions": "x"}}) is None)
check("缺 question 键的条目跳过",
      _extract_question_options({"rawInput": {"questions": [{"options": []}]}}) is None)

print("\n全部断言通过")
