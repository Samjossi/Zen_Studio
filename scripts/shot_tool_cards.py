"""工具调用卡片 mock 截图脚本（0806 计划 §5：可视化验证闭环）。

不依赖真实后端：构造覆盖全部症状场景的 mock ACP 帧 → 走真实链路
（协议层 map_session_update → 路由层 _allow_progress_frame → 渲染层
make_tool_card + apply_update）→ 逐场景 QWidget.grab() 截图落盘 →
输出 manifest.md（场景 → PNG → 预期看点），供读图工具逐张核对。

用法：
    .venv/bin/python scripts/shot_tool_cards.py

产物：.temp/card_shots/{seq}_{场景名}.png + manifest.md（幂等覆盖写）。
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# 无显示环境（CI / SSH）亦可运行；有显示时 offscreen 同样无副作用
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from gui.panels.chat.cards import (  # noqa: E402
    CardColors,
    OpenStateMap,
    TodoCard,
    ToolCard,
    find_pending_question_card,
    find_question_card,
    make_tool_card,
)
from gui.panels.chat.permission_dialog import QuestionDialog  # noqa: E402
from gui.panels.chat.panel import ChatPanel  # noqa: E402
from gui.theme import CHAT_PACK, DEFAULT_THEME, get_theme_palette  # noqa: E402
from llm.providers.acp import map_session_update  # noqa: E402

SHOT_DIR = PROJECT_ROOT / ".temp" / "card_shots"
ASSET_DIR = SHOT_DIR / "_assets"
CARD_WIDTH = 560  # 模拟真实对话区卡片可用宽

#: 真实主题色袋（单一来源纪律：与运行态卡片同套色值——四主题均为亮色，
#: 硬编色值会让对比度类样式回归在 mock 阶段漏网）
COLORS = CardColors(
    CHAT_PACK["reasoning_fg"], CHAT_PACK["tool_fg"],
    CHAT_PACK["tool_error_fg"], CHAT_PACK["user_bubble_bg"],
    CHAT_PACK["tool_output_bg"], CHAT_PACK["timeline_read_fg"],
    CHAT_PACK["diff_add_fg"], CHAT_PACK["diff_del_fg"])

#: 截图容器底色（真实窗口底色，与运行态观感一致）
_WINDOW_BG = get_theme_palette(DEFAULT_THEME)["window_bg"]


def _make_png_b64(color: str, w: int = 64, h: int = 48) -> str:
    """现场生成小尺寸纯色 PNG 转 base64（不依赖外部文件）。"""
    pix = QPixmap(w, h)
    pix.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.ReadWrite)
    pix.save(buf, "PNG")
    return bytes(buf.data().toBase64()).decode()


def _make_asset_png(name: str, color: str, w: int = 320, h: int = 200) -> str:
    """现场生成真实 PNG 文件落盘（file:// 图片通道 mock）。"""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSET_DIR / name
    pix = QPixmap(w, h)
    pix.fill(QColor(color))
    pix.save(str(path), "PNG")
    return str(path)


def _tool_call(tid: str, title: str, kind: str, raw_input: dict | None = None,
               content: list | None = None,
               locations: list | None = None) -> dict:
    frame = {"sessionUpdate": "tool_call", "toolCallId": tid,
             "title": title, "kind": kind, "status": "pending"}
    if raw_input is not None:
        frame["rawInput"] = raw_input
    if content is not None:
        frame["content"] = content
    if locations is not None:
        frame["locations"] = locations
    return frame


def _tool_update(tid: str, status: str, title: str | None = None,
                 kind: str | None = None, raw_input: dict | None = None,
                 raw_output: dict | None = None, content: list | None = None,
                 locations: list | None = None) -> dict:
    frame = {"sessionUpdate": "tool_call_update", "toolCallId": tid,
             "status": status}
    if title is not None:
        frame["title"] = title
    if kind is not None:
        frame["kind"] = kind
    if raw_input is not None:
        frame["rawInput"] = raw_input
    if raw_output is not None:
        frame["rawOutput"] = raw_output
    if content is not None:
        frame["content"] = content
    if locations is not None:
        frame["locations"] = locations
    return frame


def _scenarios() -> list[tuple[str, list[dict], str, object]]:
    """(场景名, ACP update 帧序列, 预期看点, 后置钩子|None) 清单。

    后置钩子签名：hook(card_map: dict) —— 帧回放完、截图前调用
    （0807-0148 计划 T5：pending 态按钮激活/点击定格等交互态 mock）。
    """
    img_b64 = _make_png_b64("#3a7bd5")
    asset_path = _make_asset_png("readmedia_sample.png", "#c06014")
    # 0158 计划 T3：略缩图场景专用资产；相对路径形态（相对项目根）
    # 验证渲染层工作区根解析
    rel_thumb_path = Path(
        _make_asset_png("readmedia_thumb.png", "#2a9d8f")
    ).relative_to(PROJECT_ROOT).as_posix()
    long_json = json.dumps(
        {"files": [{"path": f"src/module_{i:02d}/handler.py",
                    "summary": "处理请求分发与错误兜底逻辑" * 3}
                   for i in range(8)]},
        ensure_ascii=False)
    assert len(long_json) > 300  # 场景 6 前提：minified 单行超 300 字符

    return [
        ("01_readmedia_kimi_首帧空壳", [
            # kimi 系时序：首帧空壳无 rawInput → in_progress 帧补齐入参 →
            # completed 帧出参为「序列化为字符串的 content 数组」
            _tool_call("tc-read", "ReadMediaFile", "other"),
            _tool_update("tc-read", "in_progress",
                         raw_input={"path": asset_path}),
            _tool_update("tc-read", "completed", title="ReadMediaFile",
                         raw_output={"output": json.dumps([
                             {"type": "text",
                              "text": f'<image path="{asset_path}">'},
                             {"type": "image_url",
                              "imageUrl": {"url": f"data:image/png;base64,{img_b64}"}},
                         ], ensure_ascii=False)}),
        ], "入参区由「（无）」回填为 path=...；出参区显示两张图像（file:// "
           "与 data-URI），无 base64 原文裸露、无 <image> 标签裸露"),
        ("02_askuserquestion_问答对", [
            _tool_call("tc-ask", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "选择哪个渲染方案？",
                            "options": [{"label": "方案A：BodyHtml"},
                                        {"label": "方案B：BodyText"}]},
                           {"question": "是否需要真机复核？"}]}),
            _tool_update("tc-ask", "completed", title="AskUserQuestion",
                         raw_output={"output": json.dumps(
                             {"answers": {"选择哪个渲染方案？": "方案A：BodyHtml",
                                          "是否需要真机复核？": "是"}},
                             ensure_ascii=False)}),
        ], "QuestionCard 问答对样式：问题粗体行 + ✅ 答案行，无裸 JSON、"
           "入参不再为「（无）」"),
        ("03_agent_子代理", [
            _tool_call("tc-agent", "Agent", "other",
                       {"description": "探索代码库结构",
                        "prompt": "请分析 gui/panels/chat 目录下的卡片渲染链路，"
                                  "找出图片出参被丢弃的根因并给出修复建议。"}),
            _tool_update("tc-agent", "completed", title="Agent",
                         raw_output={"output":
                                     "探索结果：根因位于 _extract_raw_output 的 content "
                                     "兜底循环只认 text 块，image/image_url 块被静默丢弃；"
                                     "修复方案为协议层双通道拆分 + 渲染层 BodyHtml 内嵌 <img>。"}),
        ], "SubagentCard：副标题/入参区显示 description 与 prompt 摘要"
           "（截断 200 字符），出参提取结果正文"),
        ("04_todowrite_首帧清单卡", [
            # 0808-0627 计划 T3 改写（行为已改道，原断言失效）：todowrite
            # 首帧带 rawInput.todos（kilocode/opencode 系形态）→ 直接落
            # TodoListCard，不再产会话级 TodoCard
            _tool_call("tc-todo", "todowrite", "other",
                       {"todos": [{"content": "协议层出参管线改造", "status": "completed"},
                                  {"content": "渲染层 McpCard 升级", "status": "in_progress"},
                                  {"content": "可视化闭环验证", "status": "pending"}]}),
            _tool_update("tc-todo", "completed", title="todowrite",
                         raw_output={"output":
                                     "Todo list updated.\n"
                                     "Ensure that you continue to use the todo list "
                                     "to track your progress. Please proceed with "
                                     "the current tasks if applicable."}),
        ], "TodoListCard 清单区渲染 ☑/▶/☐ + 完成项删除线 + 副标题 x/y；"
           "入参区无 todos JSON 原文；出参区无「Todo list updated./"
           "Ensure that you...」回执文本；不再出现会话级 TodoCard"),
        ("05_mcp_裸JSON出参", [
            _tool_call("tc-json", "mcp__fs__list_directory", "other",
                       {"path": "~/project"}),
            _tool_update("tc-json", "completed",
                         title="mcp__fs__list_directory",
                         raw_output={"output":
                                     '{"entries": ["src", "tests"], "error": null, '
                                     '"note": "line1\\r\\nline2 with \\"quotes\\""}'}),
        ], "出参以 pretty JSON 展示，无 \\r\\n / \\\" 转义裸露"),
        ("06_mcp_长JSON不截断", [
            _tool_call("tc-long", "mcp__search__codebase", "other",
                       {"query": "卡片渲染"}),
            _tool_update("tc-long", "completed",
                         title="mcp__search__codebase",
                         raw_output={"output": long_json}),
        ], "超 300 字符单行 minified JSON 完整 pretty 展示，不被「…」截断", None),
        # 0807-0148 计划 T5：AskUserQuestion 交互侧场景（蓝本为真实帧
        # 帧存档/askuser_*.json 的 options 编码）
        ("07_askuser_pending_选项按钮", [
            _tool_call("tc-askbtn", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "你希望我把问候语写成哪种颜色？",
                            "options": [{"label": "红色"}, {"label": "蓝色"},
                                        {"label": "绿色"}],
                            "header": "颜色"}]}),
        ], "pending 态 QuestionCard 自动展开：问题粗体行 + 「请选择：」+ "
           "选项按钮组（红色/蓝色/绿色/Skip——真实帧 options 含 reject_once "
           "的 Skip）+ 按钮组末尾自由作答引导提示（0807-0445 方案 B），"
           "状态图标 ◐，无自动选答",
           lambda cards: find_question_card("tc-askbtn").activate_options(
               [{"optionId": "q0_opt_0", "name": "红色", "kind": "allow_once"},
                {"optionId": "q0_opt_1", "name": "蓝色", "kind": "allow_once"},
                {"optionId": "q0_opt_2", "name": "绿色", "kind": "allow_once"},
                {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}],
               lambda oid: None)),
        ("08_askuser_已点击_定格", [
            _tool_call("tc-askclick", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "你希望我把问候语写成哪种颜色？",
                            "options": [{"label": "红色"}, {"label": "蓝色"},
                                        {"label": "绿色"}],
                            "header": "颜色"}]}),
        ], "点击「蓝色」后：全组按钮禁点，蓝色项打 ✅ 即时反馈，状态仍 ◐ "
           "（等 completed 帧定格问答对）",
           lambda cards: _activate_and_click("tc-askclick", 1)),
        ("09_askuser_multiselect", [
            _tool_call("tc-askmulti", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "你想在问候卡片上放哪些元素？（可多选）",
                            "options": [{"label": "佛像"}, {"label": "莲花"},
                                        {"label": "经文"}, {"label": "香炉"}],
                            "multi_select": True}]}),
            _tool_update("tc-askmulti", "completed", title="AskUserQuestion",
                         raw_output={"output": json.dumps(
                             {"answers": {"你想在问候卡片上放哪些元素？（可多选）":
                                          "香炉"}}, ensure_ascii=False)}),
        ], "multi_select（蛇形字段名，真实帧实证）问题 completed 问答对正常"
           "渲染；question_options 载荷提取不破坏现行渲染", None),
        # 0807-0445 计划（方案 B）：dismissed 终态渲染——用户按引导 Skip 后
        # 自由作答的常态终态（蓝本 帧存档/askuser_other_*.json）
        ("11_askuser_已跳过_dismissed", [
            _tool_call("tc-askskip", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "你希望问候语的渐变色是哪种？",
                            "options": [{"label": "红色渐变"}, {"label": "蓝色渐变"},
                                        {"label": "绿色渐变"}],
                            "header": "颜色"}]}),
            _tool_update("tc-askskip", "completed", title="AskUserQuestion",
                         raw_output={"output": json.dumps(
                             {"answers": {},
                              "note": "User dismissed the question without "
                                      "answering."}, ensure_ascii=False)}),
        ], "dismissed（answers 空 + note）completed 渲染为 ⏭ + note 原文，"
           "不再裸露裸 JSON", None),
        # 0158 计划 T3：MediaReadCard 入参略缩图四场景（media_path 载荷
        # 经真实 map_session_update 协议链路产出；相对路径形态验证
        # 渲染层工作区根解析——mock 未注入 workspace_root，走 PROJECT_ROOT
        # 降级，与真机 agent 工作目录≈IDE 项目根的常态同构）
        ("12_readmedia_入参略缩图", [
            _tool_call("tc-thumb", "ReadMediaFile", "other",
                       {"path": rel_thumb_path}),
            _tool_update("tc-thumb", "completed", title="ReadMediaFile",
                         raw_output={"output": "图片读取完成"}),
        ], "MediaReadCard：入参区 path 文本下方显示 320px 略缩图"
           "（相对路径经 PROJECT_ROOT 解析渲染成功）", None),
        ("13_readmedia_迟到回填略缩图", [
            # kimi 系时序（场景 01 帧序蓝本）：首帧空壳无 rawInput →
            # in_progress 帧补齐入参（media_path 与 input_detail 同频回填）
            _tool_call("tc-thumb2", "ReadMediaFile", "other"),
            _tool_update("tc-thumb2", "in_progress",
                         raw_input={"path": rel_thumb_path}),
            _tool_update("tc-thumb2", "completed", title="ReadMediaFile",
                         raw_output={"output": "图片读取完成"}),
        ], "首帧空壳：略缩图随 in_progress 帧迟到载荷补渲出现，不重复"
           "不缺失（幂等，首帧优先）", None),
        ("14_readmedia_非图片入参", [
            _tool_call("tc-thumb3", "ReadMediaFile", "other",
                       {"path": ".temp/notes.txt"}),
            _tool_update("tc-thumb3", "completed", title="ReadMediaFile",
                         raw_output={"output": "文件内容：……"}),
        ], "非图片扩展名：协议层不装填 media_path，无略缩图，path 文本"
           "正常（与 McpCard 现状一致）", None),
        ("15_readmedia_路径不存在", [
            _tool_call("tc-thumb4", "ReadMediaFile", "other",
                       {"path": ".temp/card_shots/_assets/ghost_deleted.png"}),
            _tool_update("tc-thumb4", "completed", title="ReadMediaFile",
                         raw_output={"output": "读取失败：文件不存在"}),
        ], "media_path 装填但文件不存在：静默降级无破图占位，path 文本"
           "仍在", None),
        # 0808-0627 计划 T3：TodoListCard 场景组（todos 载荷经真实
        # map_session_update 协议链路产出，含跨调用 diff changed 标记）；
        # 0812-0336 计划 T2：16/18/20 为 kimi title 形态，04/17/19 保留
        # kilocode/opencode content 形态与 plan 通道回归
        ("16_todolist_首帧空壳迟到清单", [
            # kimi 系时序（场景 01/13 帧序蓝本）：首帧空壳无 rawInput →
            # in_progress 帧补齐 todos（快照语义，每帧都提不设去重账本）
            # 0812-0336 计划 T2：改 kimi 形态（title 键、无 priority），
            # 混入一条脏条目（两键均缺）覆盖防御语义（B7）
            # 0812-0918 计划 T3：完成项改 kimi 实证 status="done"
            # （todolist_a2_frames_20260812_080109.json 取证），覆盖
            # _TODO_STATUS_NORMALIZE 词表归一
            _tool_call("tc-todolate", "TodoList", "other"),
            _tool_update("tc-todolate", "in_progress",
                         raw_input={"todos": [
                             {"title": "协议层 todos 载荷 + diff",
                              "status": "done"},
                             {"title": "渲染层 TodoListCard",
                              "status": "in_progress"},
                             {"title": "mock 截图闭环",
                              "status": "pending"},
                             {"status": "pending"}]}),
            _tool_update("tc-todolate", "completed", title="TodoList",
                         raw_output={"output": "清单已更新"}),
        ], "首帧空壳：TodoListCard 清单区随 in_progress 帧迟到载荷出现，"
           "条目文本与 title 值一致（kimi 形态归一取证）；done 项归一为"
           " completed：☑ + 删除线 + 计入 x/y；脏条目静默跳过；"
           "入参区保持「（无）」无 todos JSON 重复，副标题 1/3", None),
        ("17_todolist_跨调用变更高亮", [
            # 两次调用（两 toolCallId）：首卡快照 → 次卡快照（状态迁移
            # completed/in_progress + 新增一项），跨调用 diff changed 标记
            _tool_call("tc-todo1", "TodoList", "other",
                       {"todos": [{"content": "协议层载荷", "status": "in_progress"},
                                  {"content": "渲染层专用卡", "status": "pending"},
                                  {"content": "mock 验证", "status": "pending"}]}),
            _tool_update("tc-todo1", "completed", title="TodoList",
                         raw_output={"output": "ok"}),
            _tool_call("tc-todo2", "TodoList", "other",
                       {"todos": [{"content": "协议层载荷", "status": "completed"},
                                  {"content": "渲染层专用卡", "status": "in_progress"},
                                  {"content": "mock 验证", "status": "pending"},
                                  {"content": "截图闭环", "status": "pending"}]}),
            _tool_update("tc-todo2", "completed", title="TodoList",
                         raw_output={"output": "ok"}),
        ], "两张 TodoListCard 各自定格（历史留痕）；次卡变更项（1/2/4 项）"
           "醒目色、未变更第 3 项常规色，副标题 1/4；首卡全项醒目"
           "（before 为空首卡全量高亮语义正确）", None),
        ("18_todolist_回执过滤_kimi", [
            # 0812-0336 计划 T2：改 kimi 形态（title 键），名实相符
            _tool_call("tc-todoreceipt", "TodoList", "other"),
            _tool_update("tc-todoreceipt", "in_progress",
                         raw_input={"todos": [
                             {"title": "回执过滤验证", "status": "in_progress"}]}),
            _tool_update("tc-todoreceipt", "completed", title="TodoList",
                         raw_output={"output":
                                     "Current todo list:\n"
                                     "[in_progress] 回执过滤验证\n"
                                     "[pending] 另一项待办"}),
        ], "出参区无「Current todo list:」块及其后连续 [status] 行"
           "（kimi 模板回执整块剔除），清单区正常渲染、条目文本与 title"
           "值一致", None),
        ("20_todolist_kimi跨调用变更高亮", [
            # 0812-0336 计划 T2 新增：场景 17 的 kimi 形态变体——两次调用
            # 两 toolCallId，title 键、状态迁移 + 新增一项，跨调用 diff
            _tool_call("tc-todokimi1", "TodoList", "other",
                       {"todos": [{"title": "kimi 协议层载荷", "status": "in_progress"},
                                  {"title": "kimi 渲染层专用卡", "status": "pending"},
                                  {"title": "kimi mock 验证", "status": "pending"}]}),
            _tool_update("tc-todokimi1", "completed", title="TodoList",
                         raw_output={"output": "ok"}),
            _tool_call("tc-todokimi2", "TodoList", "other",
                       {"todos": [{"title": "kimi 协议层载荷", "status": "done"},
                                  {"title": "kimi 渲染层专用卡", "status": "in_progress"},
                                  {"title": "kimi mock 验证", "status": "pending"},
                                  {"title": "kimi 截图闭环", "status": "pending"}]}),
            _tool_update("tc-todokimi2", "completed", title="TodoList",
                         raw_output={"output": "ok"}),
        ], "两张 TodoListCard 各自定格（历史留痕），条目文本与 title 值"
           "一致；次卡变更项（1/2/4 项）醒目色、未变更第 3 项常规色，"
           "done 项归一为 completed：☑ + 删除线 + 计入 x/y（0812-0918"
           " 计划 T3 混入 kimi 实证 status=\"done\"）；副标题 1/4；首卡"
           "全项醒目（before 为空首卡全量高亮语义正确）",
           None),
        ("19_plan_通道回归", [
            # plan 通道不动产：无 toolCallId 的会话级快照流仍走
            # 单卡 TodoCard 整刷（0808-0627 计划 D1 改道范围外）
            {"sessionUpdate": "plan",
             "entries": [{"content": "plan 通道条目一", "status": "completed"},
                         {"content": "plan 通道条目二", "status": "in_progress"}]},
        ], "会话级 TodoCard 通道行为零变化：📋 任务清单 + 1/2 副标题 + "
           "完成项灰化删除线，in_progress 不着色（非高亮口径）", None),
        # 0812-0735 计划 T2：kind 补盲场景组（F1——BashCard/DiffCard/
        # TextOutputCard/SubagentCard(think 路径) 零覆盖补盲），帧形态
        # 出处逐一标注，无出处假设标 🟡 待 T3 真机取证校正
        ("21_execute_首帧齐备", [
            # kilocode 系首帧齐备形态（推定）：tool_call 首帧即带
            # rawInput.command；title 为工具名（≠command）→ 副标题
            # 显示命令摘要。in_progress 尾滚帧在 mock 路由中因
            # _tool_commands 簿记未注入被真实路由代码丢弃（与真机
            # 200ms 节流同通道），输出定格由 completed 帧承担
            _tool_call("tc-exec1", "Bash", "execute",
                       {"command": "ls -la assets/"}),
            _tool_update("tc-exec1", "in_progress", kind="execute",
                         raw_output={"output": "total 24\ndrwxr-xr-x 5 user user"}),
            _tool_update("tc-exec1", "completed", kind="execute",
                         raw_output={"output":
                                     "total 24\n"
                                     "drwxr-xr-x 5 user user 4096 8月11 12:00 .\n"
                                     "drwxr-xr-x 2 user user 4096 8月11 12:00 fonts\n"
                                     "drwxr-xr-x 2 user user 4096 8月11 12:00 logo\n"
                                     "drwxr-xr-x 2 user user 4096 8月11 12:00 themes"}),
        ], "$ 粗体命令头；输出正文定格；✔；title≠command 时副标题显示"
           "命令摘要「ls -la assets/」", None),
        ("22_execute_title即命令", [
            # 首帧齐备对照形态（1425 计划记载「shell 工具 title 即命令
            # 本身」——对应 kilocode 系）。0735 计划 T3 真机取证
            # （帧存档/execute_20260812_075201.json）实证
            # kimi 为首帧空壳迟到形态：首帧 title="Bash"、无 rawInput，
            # command 随 in_progress 帧迟到（迟到帧 title="Running:
            # <命令>"）——kimi 真实迟到形态由场景 30 承接，本场景保留
            # 作首帧齐备回归对照
            _tool_call("tc-exec2", "pytest scripts/ -x", "execute",
                       {"command": "pytest scripts/ -x"}),
            _tool_update("tc-exec2", "completed", kind="execute",
                         raw_output={"output": "3 passed in 0.42s"}),
        ], "副标题不重复命令（_tool_call_summary title==command 去重判据"
           "实证）；$ 粗体命令头正常；✔", None),
        ("23_edit_kimi迟到diff", [
            # kimi 系时序（0919 计划 §6.1 取证）：首帧 title="Edit"、
            # kind="edit"、content 空文本壳、无 rawInput/locations/diff 项
            # → in_progress 帧 title="Editing <路径>"（路径内嵌）、
            # rawInput={path,old_string,new_string}、content 带顶层键
            # diff 项 → completed 仅结果文本（同帧再带一次相同 diff 项
            # 验 _diff_attached 去重）。同时实证 0919 迟到帧放行链路
            # （_allow_progress_frame 对带 diff_hunks/summary 帧放行）
            _tool_call("tc-editkimi", "Edit", "edit",
                       content=[{"type": "content",
                                 "content": {"type": "text", "text": ""}}]),
            _tool_update("tc-editkimi", "in_progress",
                         title="Editing .temp/probe.txt", kind="edit",
                         raw_input={"path": ".temp/probe.txt",
                                    "old_string": "旧行一\n旧行二\n旧行三",
                                    "new_string": "旧行一\n新行二\n旧行三"},
                         content=[{"type": "diff",
                                   "path": str(PROJECT_ROOT / ".temp" / "probe.txt"),
                                   "oldText": "旧行一\n旧行二\n旧行三",
                                   "newText": "旧行一\n新行二\n旧行三"}]),
            _tool_update("tc-editkimi", "completed", kind="edit",
                         content=[{"type": "content",
                                   "content": {"type": "text",
                                               "text": "Replaced 1 occurrence "
                                                       "in .temp/probe.txt"}},
                                  {"type": "diff",
                                   "path": str(PROJECT_ROOT / ".temp" / "probe.txt"),
                                   "oldText": "旧行一\n旧行二\n旧行三",
                                   "newText": "旧行一\n新行二\n旧行三"}]),
        ], "标题恒定 Edit（_accept_title_update=False 拒收「Editing …」）；"
           "副标题两段式「probe.txt · .temp/」；徽标 +1 −1；hunk 三色；"
           "双帧同 diff 只挂一份（_diff_attached）；✔", None),
        ("24_write_kimi合成diff", [
            # kimi 系时序（0959 计划 §2 取证）：首帧 title="Write"、
            # kind="edit" 空壳 → in_progress 帧 rawInput={path,content}、
            # content 无 diff 项（仅 rawInput JSON 文本快照）→
            # completed 结果文本。rawInput.content 全文经
            # _extract_write_diff 合成 +N −0
            _tool_call("tc-writekimi", "Write", "edit",
                       content=[{"type": "content",
                                 "content": {"type": "text", "text": ""}}]),
            _tool_update("tc-writekimi", "in_progress",
                         title="Writing .temp/write_probe.txt", kind="edit",
                         raw_input={"path": ".temp/write_probe.txt",
                                    "content": "第一行\n第二行\n第三行"},
                         content=[{"type": "content",
                                   "content": {"type": "text",
                                               "text": '{"path": ".temp/write_probe.txt", '
                                                       '"content": "第一行\\n第二行\\n第三行"}'}}]),
            _tool_update("tc-writekimi", "completed", kind="edit",
                         raw_output={"output": "File written successfully."}),
        ], "标题恒定 Write；合成徽标 +3 −0；hunk 全绿；副标题两段式"
           "「write_probe.txt · .temp/」；入参区仅 path（content 不在 edit"
           " 白名单键内，无全文重复）；✔", None),
        ("25_edit_首帧齐备", [
            # kilocode 系（ACP 标准形态推定）：首帧即带 locations +
            # content diff 项 + rawInput——徽标/hunk/副标题首帧一次到位
            _tool_call("tc-editkilo", "Edit", "edit",
                       {"filePath": "gui/panels/chat/cards.py",
                        "oldString": "旧实现行", "newString": "新实现行"},
                       content=[{"type": "diff",
                                 "path": "gui/panels/chat/cards.py",
                                 "oldText": "上下文甲\n旧实现行\n上下文乙",
                                 "newText": "上下文甲\n新实现行\n上下文乙"}],
                       locations=[{"path": "gui/panels/chat/cards.py"}]),
            _tool_update("tc-editkilo", "completed", kind="edit",
                         raw_output={"output": "Edit applied."}),
        ], "首帧即渲染徽标 +1 −1 与 hunk 三色；副标题取 locations[0].path"
           " 两段式「cards.py · gui/panels/chat/」；✔", None),
        ("26_read_行数徽标与截断尾注", [
            # 通用形态：completed 输出 1005 行超软上限 1000（0645 D2），
            # read 保头截断（§2.4）
            _tool_call("tc-readbig", "Read", "read",
                       {"path": "测试文件夹/文档1.md"}),
            _tool_update("tc-readbig", "completed", kind="read",
                         raw_output={"output": "\n".join(
                             f"第 {i:04d} 行：文档正文内容示例"
                             for i in range(1, 1006))}),
        ], "「1005 行」徽标常驻标题行；正文保头截断（第 0001 行起、第 1000"
           " 行止）；尾注「…… 共 1005 行（仅显示前 1000 行）」；✔", None),
        ("27_search_fetch_摘要副标题", [
            # 通用形态：两卡同场景，各带 completed 输出——
            # _tool_call_summary search/fetch 分支副标题实证
            _tool_call("tc-search", "Grep", "search",
                       {"pattern": "_tool_call_summary",
                        "path": "llm/providers"}),
            _tool_update("tc-search", "completed", kind="search",
                         raw_output={"output":
                                     "llm/providers/acp.py:737:"
                                     "def _tool_call_summary(update: dict)"}),
            _tool_call("tc-fetch", "FetchURL", "fetch",
                       {"url": "https://example.com/acp-spec"}),
            _tool_update("tc-fetch", "completed", kind="fetch",
                         raw_output={"output":
                                     "# ACP Spec\nAgent Client Protocol "
                                     "defines session/update notifications."}),
        ], "search 卡副标题显示 pattern「_tool_call_summary」、fetch 卡"
           "副标题显示 url「https://example.com/acp-spec」；输出正文各自"
           "定格；均 ✔", None),
        ("28_think_task_result提取", [
            # 0645 计划 D5 规格：think（task 子代理）completed 帧
            # result_summary 走 <task_result> 正则提取，标记外前言不入卡
            _tool_call("tc-think1", "Agent", "think",
                       {"description": "分析卡片渲染链路",
                        "prompt": "请分析 gui/panels/chat 下的卡片分派逻辑"
                                  "并给出结论。"}),
            _tool_update("tc-think1", "completed", kind="think",
                         raw_output={"output":
                                     "我先查看了 cards.py 的工厂函数，又核对了 "
                                     "acp.py 的协议映射。\n"
                                     "<task_result>结论：make_tool_card 按工具名"
                                     "二级分派优先、未命中按 tool_kind 分派专类，"
                                     "协议层单点格式化载荷。</task_result>"}),
        ], "body 仅显示 <task_result> 包裹内结论（标记外前言不入卡）；"
           "副标题取 description「分析卡片渲染链路」；✔", None),
        ("29_think_无标记全文兜底", [
            # 0645 计划 D5 规格：completed 输出无 <task_result> 标记时
            # _extract_result_summary 取全文兜底
            _tool_call("tc-think2", "Agent", "think",
                       {"description": "汇总审计结论",
                        "prompt": "请汇总本轮审计的全部结论。"}),
            _tool_update("tc-think2", "completed", kind="think",
                         raw_output={"output":
                                     "结论一：TodoList 字段失配为孤例；\n"
                                     "结论二：假绿的结构性土壤仍在，需制度化"
                                     "帧证据防回归。"}),
        ], "body 显示输出全文两行（无标记全文兜底路径实证）；副标题取"
           " description「汇总审计结论」；✔", None),
        ("30_execute_kimi迟到命令", [
            # kimi 实证（0735 计划 T3 取证
            # 帧存档/execute_20260812_075201.json）：首帧
            # title="Bash"、kind="execute"、content 空文本壳、无 rawInput
            # → in_progress 迟到帧 title="Running: echo capture_execute_
            # probe"、rawInput={command} → completed 帧 rawOutput 为纯
            # 字符串（非 {"output": ...} dict——与场景 21/22 构造差异
            # 如实保留）。场景 16 式迟到变体，承接场景 22 的 🟡 假设校正。
            # 0812-0918 计划 T2/T3：迟到 command 经协议层提取 +
            # 路由 _tool_commands 迟到簿记后，`$ ` 命令头补挂、尾滚
            # 输出帧放行（本场景补一帧尾滚输出实证放行链路）
            _tool_call("tc-execkimi", "Bash", "execute",
                       content=[{"type": "content",
                                 "content": {"type": "text", "text": ""}}]),
            _tool_update("tc-execkimi", "in_progress",
                         title="Running: echo capture_execute_probe",
                         kind="execute",
                         raw_input={"command": "echo capture_execute_probe"}),
            _tool_update("tc-execkimi", "in_progress", kind="execute",
                         raw_output={"output": "capture_execute_probe\n"}),
            _tool_update("tc-execkimi", "completed", kind="execute",
                         raw_output="capture_execute_probe\n"),
        ], "运行中标题短暂变为「Running: echo capture_execute_probe」"
           "（BashCard _accept_title_update=True），completed 帧缺 title"
           "经路由 _tool_titles 簿记回填首帧标题——定格标题恒为「Bash」；"
           "$ 粗体命令头随迟到帧建立（协议层迟到提取 + 路由迟到簿记 +"
           " BashCard _set_command 补挂，0812-0918 计划 T2）；尾滚输出"
           "帧放行（_tool_commands 闸门）；入参区迟到回填显示「command: "
           "echo capture_execute_probe」；输出定格「capture_execute_probe」"
           "；✔", None),
        # 0812-0952 计划 T5：reasonix ask 两场景（蓝本 T0 取证帧
        # 帧存档/ask_reasonix_*_20260812_100424.json）
        ("31_ask_reasonix_问答对", [
            # 实证形态：update 帧 title="ask"、kind="other"、
            # rawInput.questions 与 kimi 同构（label/description 小写，
            # header 可选）；completed 帧无 rawOutput，出参为 content
            # 文本块「The user answered:\n- 键: 答案」（非 JSON）
            _tool_call("tc-askrx", "ask", "other",
                       {"questions": [
                           {"header": "今晚吃啥",
                            "question": "今天晚上吃什么？",
                            "options": [{"label": "火锅", "description": "热闹又暖和，适合聚餐"},
                                        {"label": "麻辣烫", "description": "快捷方便，单人友好"},
                                        {"label": "饺子", "description": "传统美味，家的味道"},
                                        {"label": "沙拉轻食", "description": "健康低卡，清爽无负担"}]}]}),
            _tool_update("tc-askrx", "completed",
                         content=[{"type": "content",
                                   "content": {"type": "text",
                                               "text": "The user answered:\n- 今晚吃啥: 火锅"}}]),
        ], "reasonix ask 走 QuestionCard（_TOOL_NAME_CARDS 注册实证名）："
           "问题粗体行 + ✅ 答案行——The user answered 文本形态经 "
           "_parse_answered_text 解析落行，不再裸露原文兜底", None),
        ("32_ask_reasonix_multiSelect", [
            # 实证形态：多选字段名为 multiSelect 驼峰（kimi 为
            # multi_select 蛇形，0812-0952 计划 T2 兼容补入）
            _tool_call("tc-askrxm", "ask", "other",
                       {"questions": [
                           {"header": "周末活动",
                            "question": "周末想做的活动有哪些？",
                            "multiSelect": True,
                            "options": [{"label": "去户外徒步", "description": "亲近大自然"},
                                        {"label": "约朋友聚餐", "description": "和好友吃饭聊天"}]}]}),
            _tool_update("tc-askrxm", "completed",
                         content=[{"type": "content",
                                   "content": {"type": "text",
                                               "text": "The user answered:\n- 周末活动: 去户外徒步"}}]),
        ], "multiSelect 驼峰字段（reasonix 实证）问题 completed 问答对"
           "正常渲染；question_options 载荷提取兼容不破坏现行渲染", None),
        ("33_ask_reasonix_文本匹配激活", [
            # 0812-0952 计划 ⚠️3 E6 修订：reasonix 的 request_permission
            # toolCallId（ask-1-q1 系）与 update 帧（call_00_ 系）双轨
            # 不一致，QUESTION_BRIDGE id 定位必然 miss——按问题文本匹配
            # 待答卡激活按钮组（question_bridge._activate_one 兜底路径）
            _tool_call("tc-askrxt", "ask", "other",
                       {"questions": [
                           {"header": "今晚吃啥",
                            "question": "今天晚上吃什么？",
                            "options": [{"label": "火锅", "description": "热闹又暖和"},
                                        {"label": "麻辣烫", "description": "快捷方便"},
                                        {"label": "饺子", "description": "家的味道"},
                                        {"label": "沙拉轻食", "description": "清爽低卡"}]}]}),
        ], "id 定位 miss 后按问题文本匹配激活：pending 卡自动展开 + "
           "「请选择：」+ reasonix 实证形态选项按钮（name=Label - "
           "Description 原文 + Cancel）+ 自由作答引导，不再降级巨大弹窗",
           lambda cards: find_pending_question_card(
               ["今天晚上吃什么？"]).activate_options(
               [{"optionId": "q1:1", "name": "火锅 - 热闹又暖和", "kind": "allow_once"},
                {"optionId": "q1:2", "name": "麻辣烫 - 快捷方便", "kind": "allow_once"},
                {"optionId": "q1:3", "name": "饺子 - 家的味道", "kind": "allow_once"},
                {"optionId": "q1:4", "name": "沙拉轻食 - 清爽低卡", "kind": "allow_once"},
                {"optionId": "q1:cancel", "name": "Cancel", "kind": "reject_once"}],
               lambda oid: None)),
    ]


def _activate_and_click(tid: str, index: int) -> None:
    """激活选项按钮组并程序化点击第 index 个按钮（点击定格态 mock）。"""
    card = find_question_card(tid)
    card.activate_options(
        [{"optionId": "q0_opt_0", "name": "红色", "kind": "allow_once"},
         {"optionId": "q0_opt_1", "name": "蓝色", "kind": "allow_once"},
         {"optionId": "q0_opt_2", "name": "绿色", "kind": "allow_once"},
         {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}],
        lambda oid: None)
    from PySide6.QtWidgets import QPushButton
    card._options_box.findChildren(QPushButton)[index].click()


class _MiniRouter:
    """panel 路由逻辑真实代码复用（0806 计划 §5.2：禁止绕过分派直建卡）。

    _allow_progress_frame 是 ChatPanel 实例方法，仅依赖
    _tool_commands/_tail_last 两个簿记——SimpleNamespace 伪装实例调
    未绑定方法，T3 放行判定走真实代码。0812-0918 计划 T3：
    _tool_commands 簿记语义与真实路由对齐——首帧 command 登记、
    update 帧迟到 command setdefault、输出帧补 command 注入
    （panel.py:906-907/917-920/928-929 同语义），场景 30 实证
    迟到簿记后的尾滚放行与 `$ ` 头补挂。
    """

    def __init__(self) -> None:
        self._fake_panel = SimpleNamespace(_tool_commands={}, _tail_last={})
        self._titles: dict[str, str] = {}

    def allow_progress(self, payload: dict, tid: str) -> bool:
        return ChatPanel._allow_progress_frame(self._fake_panel, payload, tid)

    def note_command(self, payload: dict, tid: str) -> None:
        """command 簿记（panel.py 同语义）：首帧登记、迟到帧 setdefault。"""
        if tid and payload.get("command"):
            self._fake_panel._tool_commands.setdefault(tid, payload["command"])

    def command_for(self, tid: str) -> str | None:
        return self._fake_panel._tool_commands.get(tid)


def _run_scenario(frames: list[dict],
                  session_id: str = "mock") -> tuple[QWidget, dict]:
    """单场景全链路回放：帧 → Chunk → 路由 → 建卡/更新 → (容器 widget, 卡表)。

    session_id 按场景注入（0808-0627 计划 T3：todo 跨调用 diff 簿记
    _last_todo_snapshots 为协议层模块级状态、sessionId 键控——场景间
    隔离防快照串联误标，同场景内多次调用共享一键以实证跨调用 diff）。
    """
    router = _MiniRouter()
    open_state = OpenStateMap()
    card_map: dict[str, ToolCard] = {}
    cards: list[QWidget] = []
    for frame in frames:
        chunk = map_session_update(
            {"params": {"update": frame, "sessionId": session_id}})
        if chunk is None:
            continue
        if chunk.kind == "tool_call":
            card = make_tool_card(COLORS, open_state, chunk.payload)
            cards.append(card)
            if tid := chunk.payload.get("tool_call_id"):
                card_map[tid] = card
                if title := chunk.payload.get("title"):
                    router._titles[tid] = title
                router.note_command(chunk.payload, tid)  # panel.py:906 同语义
        elif chunk.kind == "tool_call_update":
            payload = dict(chunk.payload)
            tid = payload.get("tool_call_id") or ""
            if not payload.get("title"):
                payload["title"] = router._titles.get(tid, tid[:8] or "?")
            # 0918 计划 T2-2：迟到 command 簿记（panel.py 同语义，
            # 须在 in_progress 放行判定之前）
            router.note_command(payload, tid)
            if payload.get("status") == "in_progress" \
                    and not router.allow_progress(payload, tid):
                continue  # 路由丢弃（T3 验证点：带 input_detail/images 不丢）
            # panel.py:921 同语义：execute 输出帧补 command（`$ ` 头数据源）
            if payload.get("output") and (cmd := router.command_for(tid)):
                payload["command"] = cmd
            card = card_map.get(tid)
            if card is None:  # 容错补建（transcript.append_tool_update 同语义）
                card = make_tool_card(COLORS, open_state, payload)
                card_map[tid] = card
                cards.append(card)
            card.apply_update(payload)
        elif chunk.kind == "todo":
            todo_card = TodoCard(COLORS)
            todo_card.set_entries(chunk.payload["entries"])
            cards.append(todo_card)
    container = QWidget()
    container.setStyleSheet(f"background: {_WINDOW_BG};")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)
    for card in cards:
        if isinstance(card, ToolCard):
            card.set_open(True)  # 截图展开 body（默认开合约定 other 为折）
        layout.addWidget(card)
    layout.addStretch(1)
    container.setFixedWidth(CARD_WIDTH)
    container.adjustSize()
    container.resize(CARD_WIDTH, container.sizeHint().height())
    return container, card_map


def main() -> int:
    app = QApplication(sys.argv)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_lines = ["# 工具卡片 mock 截图清单（shot_tool_cards.py 产物）", ""]
    for entry in _scenarios():
        name, frames, notes, hook = (*entry, None)[:4]  # 旧 3 元组兼容
        container, card_map = _run_scenario(frames, session_id=name)
        if hook is not None:
            hook(card_map)  # 交互态后置钩子（激活按钮/点击定格）
            container.adjustSize()
            container.resize(CARD_WIDTH, container.sizeHint().height())
        png = SHOT_DIR / f"{name}.png"
        if not container.grab().save(str(png)):
            print(f"[shot_tool_cards] 错误：截图失败 {png}", file=sys.stderr)
            return 1
        manifest_lines.append(f"## {name}")
        manifest_lines.append(f"- 截图：`{png.relative_to(PROJECT_ROOT)}`")
        manifest_lines.append(f"- 预期看点：{notes}")
        manifest_lines.append("")
        print(f"[shot_tool_cards] {png.relative_to(PROJECT_ROOT)}")
    manifest = SHOT_DIR / "manifest.md"
    # QuestionDialog 弹窗截图（0807-0148 计划 T2 止血载体；蓝本为真实
    # request_permission 载荷——含 reject_once 的 Skip 项）
    dialog = QuestionDialog({
        "sessionId": "session-mock",
        "toolCall": {"toolCallId": "0:tool_mock", "title": "AskUserQuestion",
                     "content": [{"type": "content", "content": {
                         "type": "text",
                         "text": "你希望我把问候语写成哪种颜色？"}}]},
        "options": [{"optionId": "q0_opt_0", "name": "红色", "kind": "allow_once"},
                    {"optionId": "q0_opt_1", "name": "蓝色", "kind": "allow_once"},
                    {"optionId": "q0_opt_2", "name": "绿色", "kind": "allow_once"},
                    {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}],
    })
    dialog.adjustSize()
    png = SHOT_DIR / "10_questiondialog_弹窗.png"
    if not dialog.grab().save(str(png)):
        print(f"[shot_tool_cards] 错误：截图失败 {png}", file=sys.stderr)
        return 1
    manifest_lines.append("## 10_questiondialog_弹窗")
    manifest_lines.append(f"- 截图：`{png.relative_to(PROJECT_ROOT)}`")
    manifest_lines.append("- 预期看点：标题「AI 提问」+ 问题粗体行 + 自由作答"
                          "引导提示行（0807-0445 方案 B）+ 选项按钮"
                          "（红色/蓝色/绿色/Skip 原文，无「允许一次」审批语义映射）")
    manifest_lines.append("")
    print(f"[shot_tool_cards] {png.relative_to(PROJECT_ROOT)}")
    manifest.write_text("\n".join(manifest_lines), encoding="utf-8")
    print(f"[shot_tool_cards] {manifest.relative_to(PROJECT_ROOT)}")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
