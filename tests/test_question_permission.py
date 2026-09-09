"""AskUserQuestion/ask 决策特判与选项提取单元测试（收编自
scripts/test_question_permission.py；0807-0148 计划 §5.1、0812-0952 计划 T5 扩展）。

覆盖：
- is_question_request title 形态（归一化：mcp__ 前缀/大小写混写）；
- is_question_request 结构通道（0812-0952 T1）：rawInput.questions 列表 /
  reasonix 签名（rawInput.question+options，决策帧 title 是问题原文）命中，
  Bash/Edit 等普通工具 rawInput 不命中；
- decide_permission 四态 × question（kimi/reasonix 双形态）一律 DECISION_ASK
  （含 auto_all）；
- 非 question 请求四态行为回归不变；
- _extract_question_options 结构化载荷提取（真实帧蓝本：
  文档/帧存档/askuser_*.json——multi_select 蛇形字段名；
  文档/帧存档/ask_reasonix_*.json——multiSelect 驼峰兼容）。
"""
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

REASONIX_PARAMS = {
    "toolCall": {"title": "今天晚上吃什么？", "kind": "other",
                 "rawInput": {"id": "q1", "multi": False, "question": "今天晚上吃什么？",
                              "options": [{"Label": "火锅", "Description": "热闹又暖和"}]}},
    "options": [{"optionId": "q1:1", "name": "火锅 - 热闹又暖和", "kind": "allow_once"},
                {"optionId": "q1:cancel", "name": "Cancel", "kind": "reject_once"}]}


def q_params(title: str, kind: str = "other") -> dict:
    return {"toolCall": {"title": title, "kind": kind},
            "options": [{"optionId": "q0_opt_0", "name": "红色", "kind": "allow_once"},
                        {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}]}


def test_four_modes_cover_all_permission_modes():
    assert set(FOUR_MODES) == set(PERMISSION_MODES)


def test_title_form_detection():
    assert is_question_request(q_params("AskUserQuestion")), "AskUserQuestion 命中"
    assert is_question_request(q_params("mcp__xxx__AskUserQuestion")), "mcp__xxx__AskUserQuestion 命中"
    assert is_question_request(q_params("askUSERquestion")), "小写混写 askUSERquestion 命中"
    assert is_question_request(q_params("ask")), "reasonix 工具名 ask 命中（白名单辅助）"
    assert not is_question_request(q_params("Agent")), "Agent 不命中"
    assert not is_question_request(q_params("")), "空 title 不命中"
    assert not is_question_request({}), "缺 toolCall 不命中"


def test_structural_channel_detection():
    """结构通道（0812-0952 计划 T1，实证 文档/帧存档/ask_reasonix_*.json）。
    reasonix 决策帧：title 是问题原文而非工具名，白名单管不到。"""
    assert is_question_request(REASONIX_PARAMS), "reasonix 签名（question+options）命中"
    assert is_question_request({"toolCall": {"title": "未来某新名", "kind": "other",
                                             "rawInput": {"questions": []}}}), \
        "rawInput.questions 非白名单 title 命中"
    assert not is_question_request({"toolCall": {"title": "Bash", "kind": "execute",
                                                 "rawInput": {"command": "ls"}}}), \
        "Bash rawInput（command）不命中"
    assert not is_question_request({"toolCall": {"title": "Edit", "kind": "edit",
                                                 "rawInput": {"file_path": "a.md",
                                                              "old_string": "x", "new_string": "y"}}}), \
        "Edit rawInput 不命中"
    assert not is_question_request({"toolCall": {"title": "X", "rawInput": {"question": "q"}}}), \
        "仅 question 无 options 不命中（签名需成对）"


def test_question_always_asks_in_all_modes():
    """decide_permission 四态 × question 一律 ASK。"""
    for mode in FOUR_MODES:
        decision, reason = decide_permission(q_params("AskUserQuestion"), mode)
        assert decision == DECISION_ASK and reason is None, f"{mode} × question → ASK"
        d2, r2 = decide_permission(REASONIX_PARAMS, mode)
        assert d2 == DECISION_ASK and r2 is None, f"{mode} × reasonix ask → ASK"


def test_non_question_regression():
    """非 question 请求回归（原四态行为不变）。"""
    bash_safe = {"toolCall": {"title": "Bash", "kind": "execute",
                              "rawInput": {"command": "ls *.md"}}}
    bash_danger = {"toolCall": {"title": "Bash", "kind": "execute",
                                "rawInput": {"command": "git push --force"}}}
    read_tool = {"toolCall": {"title": "Read", "kind": "read"}}
    assert decide_permission(read_tool, MODE_CONFIRM_ALL)[0] == DECISION_ASK, "confirm_all × read → ASK"
    assert decide_permission(bash_safe, MODE_CONFIRM_ALL)[0] == DECISION_ASK, "confirm_all × 安全命令 → ASK"
    assert decide_permission(read_tool, MODE_CONFIRM_EXECUTE)[0] == DECISION_ALLOW, "confirm_execute × read → ALLOW"
    assert decide_permission(bash_safe, MODE_CONFIRM_EXECUTE)[0] == DECISION_ASK, "confirm_execute × 安全命令 → ASK"
    assert decide_permission(read_tool, MODE_AUTO_GUARDED)[0] == DECISION_ALLOW, "auto_guarded × read → ALLOW"
    assert decide_permission(bash_safe, MODE_AUTO_GUARDED)[0] == DECISION_ALLOW, "auto_guarded × 安全命令 → ALLOW"
    d, r = decide_permission(bash_danger, MODE_AUTO_GUARDED)
    assert d == DECISION_ASK and r is not None, "auto_guarded × 危险命令 → ASK 附原因"
    assert decide_permission(bash_danger, MODE_AUTO_ALL)[0] == DECISION_ALLOW, "auto_all × 危险命令 → ALLOW"
    assert decide_permission(bash_danger, "bogus")[0] == DECISION_ASK, "未知 mode 回退 auto_guarded"


def test_extract_question_options_kimi():
    """_extract_question_options（真实帧蓝本结构）。"""
    update = {"rawInput": {"questions": [
        {"question": "你想在问候卡片上放哪些元素？（可多选）",
         "options": [{"label": "佛像"}, {"label": "莲花", "description": "金色莲花"}],
         "multi_select": True},
        {"question": "问候语写成哪种颜色？",
         "options": [{"label": "红色"}],
         "header": "颜色"},
    ]}}
    items = _extract_question_options(update)
    assert items is not None and len(items) == 2, "提取两问"
    assert items[0]["multi_select"] is True, "multi_select 蛇形字段提取为 True"
    assert "header" not in items[0], "首问无 header 键"
    assert items[1].get("header") == "颜色", "次问 header=颜色"
    assert items[1]["multi_select"] is False, "次问 multi_select 缺省 False"
    assert items[0]["options"] == [{"label": "佛像"},
                                   {"label": "莲花", "description": "金色莲花"}], "选项 label 提取"
    assert items[0]["options"][1]["description"] == "金色莲花", "description 可选保留"


def test_extract_question_options_reasonix():
    """reasonix 帧形态（0812-0952 计划 T2，实证蓝本
    文档/帧存档/ask_reasonix_multi_*.json）：multiSelect 驼峰字段名。"""
    update_reasonix = {"rawInput": {"questions": [
        {"header": "周末活动", "question": "周末想做的活动有哪些？", "multiSelect": True,
         "options": [{"label": "去户外徒步", "description": "亲近大自然"}]},
    ]}}
    items_rx = _extract_question_options(update_reasonix)
    assert items_rx is not None and items_rx[0]["multi_select"] is True, "reasonix 驼峰 multiSelect 提取为 True"
    assert items_rx[0]["header"] == "周末活动" \
        and items_rx[0]["options"] == [{"label": "去户外徒步", "description": "亲近大自然"}], \
        "reasonix header/label 同构提取"
    assert _extract_question_options({}) is None, "无 rawInput 返回 None"
    assert _extract_question_options({"rawInput": {"questions": "x"}}) is None, "questions 非列表返回 None"
    assert _extract_question_options({"rawInput": {"questions": [{"options": []}]}}) is None, \
        "缺 question 键的条目跳过"
