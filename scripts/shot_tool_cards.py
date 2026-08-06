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


def _tool_call(tid: str, title: str, kind: str, raw_input: dict | None = None) -> dict:
    frame = {"sessionUpdate": "tool_call", "toolCallId": tid,
             "title": title, "kind": kind, "status": "pending"}
    if raw_input is not None:
        frame["rawInput"] = raw_input
    return frame


def _tool_update(tid: str, status: str, title: str | None = None,
                 kind: str | None = None, raw_input: dict | None = None,
                 raw_output: dict | None = None) -> dict:
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
    return frame


def _scenarios() -> list[tuple[str, list[dict], str, object]]:
    """(场景名, ACP update 帧序列, 预期看点, 后置钩子|None) 清单。

    后置钩子签名：hook(card_map: dict) —— 帧回放完、截图前调用
    （0807-0148 计划 T5：pending 态按钮激活/点击定格等交互态 mock）。
    """
    img_b64 = _make_png_b64("#3a7bd5")
    asset_path = _make_asset_png("readmedia_sample.png", "#c06014")
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
        ("04_todolist_回执过滤", [
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
        ], "TodoCard 清单正常渲染；补建卡的出参区无「Ensure that you "
           "continue...」系统文本（已被过滤为空）"),
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
        # .temp/frame_archive/askuser_*.json 的 options 编码）
        ("07_askuser_pending_选项按钮", [
            _tool_call("tc-askbtn", "AskUserQuestion", "other",
                       {"questions": [
                           {"question": "你希望我把问候语写成哪种颜色？",
                            "options": [{"label": "红色"}, {"label": "蓝色"},
                                        {"label": "绿色"}],
                            "header": "颜色"}]}),
        ], "pending 态 QuestionCard 自动展开：问题粗体行 + 「请选择：」+ "
           "选项按钮组（红色/蓝色/绿色/Skip——真实帧 options 含 reject_once "
           "的 Skip），状态图标 ◐，无自动选答",
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
    未绑定方法，T3 放行判定走真实代码。
    """

    def __init__(self) -> None:
        self._fake_panel = SimpleNamespace(_tool_commands={}, _tail_last={})
        self._titles: dict[str, str] = {}

    def allow_progress(self, payload: dict, tid: str) -> bool:
        return ChatPanel._allow_progress_frame(self._fake_panel, payload, tid)


def _run_scenario(frames: list[dict]) -> tuple[QWidget, dict]:
    """单场景全链路回放：帧 → Chunk → 路由 → 建卡/更新 → (容器 widget, 卡表)。"""
    router = _MiniRouter()
    open_state = OpenStateMap()
    card_map: dict[str, ToolCard] = {}
    cards: list[QWidget] = []
    for frame in frames:
        chunk = map_session_update({"params": {"update": frame}})
        if chunk is None:
            continue
        if chunk.kind == "tool_call":
            card = make_tool_card(COLORS, open_state, chunk.payload)
            cards.append(card)
            if tid := chunk.payload.get("tool_call_id"):
                card_map[tid] = card
                if title := chunk.payload.get("title"):
                    router._titles[tid] = title
        elif chunk.kind == "tool_call_update":
            payload = dict(chunk.payload)
            tid = payload.get("tool_call_id") or ""
            if not payload.get("title"):
                payload["title"] = router._titles.get(tid, tid[:8] or "?")
            if payload.get("status") == "in_progress" \
                    and not router.allow_progress(payload, tid):
                continue  # 路由丢弃（T3 验证点：带 input_detail/images 不丢）
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
        container, card_map = _run_scenario(frames)
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
    manifest_lines.append("- 预期看点：标题「AI 提问」+ 问题粗体行 + 选项按钮"
                          "（红色/蓝色/绿色/Skip 原文，无「允许一次」审批语义映射）")
    manifest_lines.append("")
    print(f"[shot_tool_cards] {png.relative_to(PROJECT_ROOT)}")
    manifest.write_text("\n".join(manifest_lines), encoding="utf-8")
    print(f"[shot_tool_cards] {manifest.relative_to(PROJECT_ROOT)}")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
