"""分厂商代表卡片 mock 截图（T6.2 视觉回归）。

不依赖真实后端：按厂商显式选择各自的 update mapper、presenter、卡片
工厂与卡片模块，回放可控 ACP 帧 → 真实卡片实例 → QWidget.grab() 落盘。
不经过公共卡片工厂，也不让一家厂商借用另一家的卡片类；每张截图同时
断言卡片类确实来自目标厂商包。

覆盖范围（现行可用路径）：
- Kimi：Agent / AskUserQuestion / ReadMediaFile / TodoList
- DSH：read_image / todo_write / subagent
- Local：AskUserQuestion / ReadMediaFile / TodoList / agent
  （Local 用可控假数据验证独立工厂与卡片，不借用 Kimi 截图代替）

用法：
    .venv/bin/python scripts/shot_vendor_cards.py [--vendor kimi|dsh|local|all]

产物：.temp/vendor_card_shots/{vendor}_{seq}_{场景}.png + manifest.md
（幂等覆盖写；不写文档目录，截图与临时资产全部留在 .temp/）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ide.gui.panels.chat.card_base import (  # noqa: E402
    CardColors,
    OpenStateMap,
    ToolCard,
)
from ide.gui.panels.chat.vendors.dsh import cards as dsh_cards  # noqa: E402
from ide.gui.panels.chat.vendors.dsh import factory as dsh_factory  # noqa: E402
from ide.gui.panels.chat.vendors.dsh.presenter import (  # noqa: E402
    DshCardPresenter,
)
from ide.gui.panels.chat.vendors.kimi import cards as kimi_cards  # noqa: E402
from ide.gui.panels.chat.vendors.kimi import factory as kimi_factory  # noqa: E402
from ide.gui.panels.chat.vendors.kimi.presenter import (  # noqa: E402
    KimiCardPresenter,
)
from ide.gui.panels.chat.vendors.local import cards as local_cards  # noqa: E402
from ide.gui.panels.chat.vendors.local import factory as local_factory  # noqa: E402
from ide.gui.panels.chat.vendors.local.presenter import (  # noqa: E402
    LocalCardPresenter,
)
from ide.gui.theme import DEFAULT_THEME, get_theme_palette  # noqa: E402
from ide.llm.providers.dsh.updates import DshUpdateMapper  # noqa: E402
from ide.llm.providers.kimi.updates import KimiUpdateMapper  # noqa: E402
from ide.llm.providers.local.updates import LocalUpdateMapper  # noqa: E402

SHOT_DIR = PROJECT_ROOT / ".temp" / "vendor_card_shots"
ASSET_DIR = SHOT_DIR / "_assets"
CARD_WIDTH = 560

CHAT_PACK = get_theme_palette(DEFAULT_THEME)["chat"]
COLORS = CardColors(
    CHAT_PACK["reasoning_fg"], CHAT_PACK["tool_fg"],
    CHAT_PACK["tool_error_fg"], CHAT_PACK["user_bubble_bg"],
    CHAT_PACK["tool_output_bg"], CHAT_PACK["timeline_read_fg"],
    CHAT_PACK["diff_add_fg"], CHAT_PACK["diff_del_fg"])
_WINDOW_BG = get_theme_palette(DEFAULT_THEME)["window_bg"]


def _make_sample_image(filename: str, color: str, label: str) -> str:
    """生成可读的图片夹具（不引用用户文件，视觉内容可控）。"""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSET_DIR / filename
    pix = QPixmap(320, 200)
    pix.fill(QColor(color))
    painter = None
    try:
        from PySide6.QtGui import QPainter
        painter = QPainter(pix)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(20)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, label)
    finally:
        if painter is not None:
            painter.end()
    pix.save(str(path), "PNG")
    return str(path)


def _frame(update: dict, session_id: str) -> dict:
    return {
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


def _tool_call(tid: str, title: str, kind: str, raw_input: dict | None = None,
               content: list | None = None, session_id: str = "mock") -> dict:
    update = {"sessionUpdate": "tool_call", "toolCallId": tid,
              "title": title, "kind": kind, "status": "pending"}
    if raw_input is not None:
        update["rawInput"] = raw_input
    if content is not None:
        update["content"] = content
    return _frame(update, session_id)


def _tool_update(tid: str, status: str, *, title: str | None = None,
                 kind: str | None = None, raw_input: dict | None = None,
                 raw_output: dict | None = None, session_id: str = "mock") -> dict:
    update = {"sessionUpdate": "tool_call_update", "toolCallId": tid,
              "status": status}
    if title is not None:
        update["title"] = title
    if kind is not None:
        update["kind"] = kind
    if raw_input is not None:
        update["rawInput"] = raw_input
    if raw_output is not None:
        update["rawOutput"] = raw_output
    return _frame(update, session_id)


def _questions_frame(tid: str, questions: list[dict],
                     session_id: str = "mock") -> dict:
    return _tool_call(tid, "AskUserQuestion", "other",
                      {"questions": questions}, session_id=session_id)


def _todo_frame(tid: str, entries: list[dict], title: str,
                session_id: str = "mock") -> dict:
    return _tool_call(tid, title, "other", {"todos": entries},
                      session_id=session_id)


def _todo_update(tid: str, entries: list[dict], title: str,
                 session_id: str = "mock") -> dict:
    return _tool_update(tid, "completed", title=title, kind="other",
                        raw_input={"todos": entries}, session_id=session_id)


def _answered_update(tid: str, answers: dict,
                     session_id: str = "mock") -> dict:
    return _tool_update(
        tid, "completed",
        raw_output={"output": json.dumps({"answers": answers},
                                         ensure_ascii=False)},
        session_id=session_id)


def _vendor_cases(media_path: str, result_path: str) -> dict[str, list[tuple]]:
    """厂商 → 代表场景。元组：场景名、帧序列、预期卡片类名、看点。"""
    kimi_questions = [
        {"question": "选择哪个渲染方案？",
         "options": [{"label": "方案A: BodyHtml"},
                     {"label": "方案B: BodyText"}]},
        {"question": "是否需要真机复核？"},
    ]
    kimi_answers = {"选择哪个渲染方案？": "方案A: BodyHtml",
                    "是否需要真机复核？": "是"}
    return {
        "kimi": [
            ("agent_子代理",
             [_tool_call("kimi-agent", "Agent", "think", {
                 "prompt": "梳理 src/ide/gui/panels/chat 下的卡片渲染链路",
                 "description": "探索代码结构"}),
              _tool_update("kimi-agent", "completed", kind="think",
                           raw_output={"output": (
                               "<task_result>结论：Agent 父卡与子卡分派正常"
                               "</task_result>")})],
             "SubagentCard",
             "Agent 子代理卡：标题/状态/成果摘要与入参 prompt/description"),
            ("askuserquestion_问答对",
             [_questions_frame("kimi-ask", kimi_questions),
              _answered_update("kimi-ask", kimi_answers)],
             "QuestionCard",
             "AskUserQuestion 问答卡：两问题 + 选中答案原文，不显示裸 JSON"),
            ("readmediafile_图片",
             [_tool_call("kimi-media", "ReadMediaFile", "other",
                         {"path": media_path}),
              _tool_update("kimi-media", "completed",
                           raw_output={"output": json.dumps(
                               [{"type": "text",
                                 "text": f'<image path="{result_path}">'}],
                               ensure_ascii=False)})],
             "MediaReadCard",
             "Kimi 私有 <image path> 出参：入参路径摘要 + 入参/出参缩略图"),
            ("todolist_跨调用高亮",
             [_todo_frame("kimi-todo", [
                 {"title": "梳理卡片入口", "status": "in_progress"},
                 {"title": "补齐 DSH 截图", "status": "pending"},
                 {"title": "回写计划", "status": "pending"}], "TodoList"),
              _todo_update("kimi-todo", [
                  {"title": "梳理卡片入口", "status": "done"},
                  {"title": "补齐 DSH 截图", "status": "in_progress"},
                  {"title": "回写计划", "status": "pending"},
                  {"title": "冻结态冒烟", "status": "pending"}], "TodoList")],
             "TodoListCard",
             "TodoList 清单卡：完成数 1/4，新增项与状态变化高亮"),
        ],
        "dsh": [
            ("read_image_图片",
             [_tool_call("dsh-media", "read_image", "other",
                         {"file_path": media_path}),
              _tool_update("dsh-media", "completed",
                           raw_output={"output": json.dumps(
                               [{"type": "image_url",
                                 "imageUrl": {"url": f"file://{result_path}"}}],
                               ensure_ascii=False)})],
             "MediaReadCard",
             "DSH read_image：snake_case file_path 进图片卡，kind 兜底为 read"),
            ("todo_write_清单",
             [_todo_frame("dsh-todo", [
                 {"content": "确认 DSH 工具名兜底", "status": "completed"},
                 {"content": "重出 read_image 卡片", "status": "in_progress"},
                 {"content": "回写 T6 计划", "status": "pending"}], "todo_write"),
              _todo_update("dsh-todo", [
                  {"content": "确认 DSH 工具名兜底", "status": "completed"},
                  {"content": "重出 read_image 卡片", "status": "completed"},
                  {"content": "回写 T6 计划", "status": "in_progress"},
                  {"content": "冻结态冒烟", "status": "pending"}], "todo_write")],
             "TodoListCard",
             "DSH todo_write：content 字段与完成数变化，不借用 Kimi 标题字段"),
            ("subagent_子代理",
             [_tool_call("dsh-subagent", "subagent", "other", {
                 "prompt": "检查 DSH 卡片分派路径",
                 "description": "代码结构探索"}),
              _tool_update("dsh-subagent", "completed", kind="other",
                           raw_output={"output": (
                               "<task_result>结论：subagent → SubagentCard "
                               "分派正常</task_result>")})],
             "SubagentCard",
             "DSH subagent：kind=other 经工具名兜底为 think，成果摘要上卡"),
        ],
        "local": [
            ("askuserquestion_问答对",
             [_questions_frame("local-ask", kimi_questions),
              _answered_update("local-ask", kimi_answers)],
             "QuestionCard",
             "Local 独立提问卡：自有工厂分派，绝不为 Kimi QuestionCard 实例"),
            ("readmediafile_图片",
             [_tool_call("local-media", "ReadMediaFile", "other",
                         {"path": media_path}),
              _tool_update("local-media", "completed",
                           raw_output={"output": json.dumps(
                               [{"type": "image_url",
                                 "imageUrl": {"url": f"file://{result_path}"}}],
                               ensure_ascii=False)})],
             "MediaReadCard",
             "Local 独立图片卡：标准 ACP image_url 出参，不解析 Kimi 私有标记"),
            ("todolist_清单",
             [_todo_frame("local-todo", [
                 {"content": "确认 Local 工厂词表", "status": "completed"},
                 {"content": "验证独立卡片实例", "status": "in_progress"},
                 {"content": "回写 T6 计划", "status": "pending"}], "TodoList"),
              _todo_update("local-todo", [
                  {"content": "确认 Local 工厂词表", "status": "completed"},
                  {"content": "验证独立卡片实例", "status": "completed"},
                  {"content": "回写 T6 计划", "status": "in_progress"},
                  {"content": "冻结态冒烟", "status": "pending"}], "TodoList")],
             "TodoListCard",
             "Local 独立清单卡：自有词表与状态推进，不借用 DSH 的 todo_write"),
            ("agent_子代理",
             [_tool_call("local-agent", "agent", "think", {
                 "prompt": "检查 Local 卡片工厂", "description": "本地假数据"}),
              _tool_update("local-agent", "completed", kind="think",
                           raw_output={"output": (
                               "<task_result>结论：Local agent → SubagentCard"
                               "</task_result>")})],
             "SubagentCard",
             "Local 独立子代理卡：agent 词表只在本厂商工厂内生效"),
        ],
    }


_VENDOR_CONFIG = {
    "kimi": (KimiUpdateMapper, KimiCardPresenter, kimi_factory.make_tool_card,
             kimi_cards),
    "dsh": (DshUpdateMapper, DshCardPresenter, dsh_factory.make_tool_card,
            dsh_cards),
    "local": (LocalUpdateMapper, LocalCardPresenter, local_factory.make_tool_card,
              local_cards),
}


def _render_case(vendor: str, frames: list[dict], session_id: str,
                 expect_class: str) -> tuple[QWidget, dict]:
    mapper_cls, presenter_cls, factory, vendor_cards = _VENDOR_CONFIG[vendor]
    mapper = mapper_cls()
    router = presenter_cls()
    open_state = OpenStateMap()
    card_map: dict[str, ToolCard] = {}
    cards: list[QWidget] = []
    for frame in frames:
        chunk = mapper.map_session_update(frame)
        if chunk is None:
            continue
        if chunk.kind == "tool_call":
            payload = router.prepare_call(chunk.payload)
            card = factory(COLORS, open_state, payload)
            tid = payload.get("tool_call_id")
            if tid:
                card_map[tid] = card
            cards.append(card)
        elif chunk.kind == "tool_call_update":
            payload = router.prepare_update(dict(chunk.payload))
            if payload is None:
                continue
            tid = payload.get("tool_call_id") or ""
            card = card_map.get(tid)
            if card is None:
                card = factory(COLORS, open_state, payload)
                card_map[tid] = card
                cards.append(card)
            card.apply_update(payload)
        elif chunk.kind == "todo":
            todo_card = vendor_cards.TodoListCard(COLORS)
            todo_card.set_entries(chunk.payload["entries"])
            cards.append(todo_card)

    assert cards, f"{vendor}/{session_id}: 帧序列未产出卡片"
    for card in cards:
        module = type(card).__module__
        prefix = f"ide.gui.panels.chat.vendors.{vendor}"
        assert module == f"{prefix}.cards" or module.startswith(prefix + "."), \
            f"{vendor}/{session_id}: 卡片 {type(card).__name__} 来自 {module}"

    first = cards[0]
    assert type(first).__name__ == expect_class, (
        f"{vendor}/{session_id}: 期望 {expect_class}，实得 {type(first).__name__}")

    container = QWidget()
    container.setStyleSheet(f"background: {_WINDOW_BG};")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)
    for card in cards:
        if isinstance(card, ToolCard):
            card.set_open(True)
        layout.addWidget(card)
    layout.addStretch(1)
    container.setFixedWidth(CARD_WIDTH)
    QApplication.processEvents()
    for _ in range(3):
        container.layout().activate()
        container.adjustSize()
        container.resize(CARD_WIDTH, container.sizeHint().height())
        QApplication.processEvents()
    return container, card_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", choices=("all", "kimi", "dsh", "local"),
                        default="all")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    media_path = _make_sample_image("vendor_sample.png", "#c76b1e", "VENDOR")
    result_path = _make_sample_image("vendor_result.png", "#2a9d8f", "RESULT")
    cases = _vendor_cases(media_path, result_path)
    vendors = list(cases) if args.vendor == "all" else [args.vendor]

    manifest = ["# 分厂商代表卡片截图清单（shot_vendor_cards.py 产物）", ""]
    for vendor in vendors:
        for index, (name, frames, expect_class, notes) in enumerate(
                cases[vendor], start=1):
            session_id = f"{vendor}-{index}"
            container, _ = _render_case(
                vendor, frames, session_id, expect_class)
            png = SHOT_DIR / f"{vendor}_{index:02d}_{name}.png"
            if not container.grab().save(str(png)):
                print(f"[shot_vendor_cards] 错误：截图失败 {png}",
                      file=sys.stderr)
                return 1
            manifest.extend([
                f"## {vendor} / {name}",
                f"- 截图：`{png.relative_to(PROJECT_ROOT)}`",
                f"- 卡片类：`{vendor}.cards.{expect_class}`（运行时断言来自本厂商包）",
                f"- 预期看点：{notes}",
                "",
            ])
            print(f"[shot_vendor_cards] {png.relative_to(PROJECT_ROOT)}")

    manifest_path = SHOT_DIR / "manifest.md"
    manifest_path.write_text("\n".join(manifest), encoding="utf-8")
    print(f"[shot_vendor_cards] {manifest_path.relative_to(PROJECT_ROOT)}")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
