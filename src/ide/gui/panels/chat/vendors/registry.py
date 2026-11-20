"""卡片组合根：按后台 registry 的 `vendor` 装配厂商卡片与 presenter。

这是通用 GUI 接触具体厂商卡片包的唯一入口；厂商包之间不得互相 import。
"""
from pathlib import Path

from ide.gui.panels.chat.card_base import CardColors, OpenStateMap
from ide.gui.panels.chat.vendors.dsh import cards as dsh_cards
from ide.gui.panels.chat.vendors.dsh import factory as dsh_factory
from ide.gui.panels.chat.vendors.dsh.presenter import DshCardPresenter
from ide.gui.panels.chat.vendors.kimi import cards as kimi_cards
from ide.gui.panels.chat.vendors.kimi import factory as kimi_factory
from ide.gui.panels.chat.vendors.kimi.presenter import KimiCardPresenter
from ide.gui.panels.chat.vendors.local import cards as local_cards
from ide.gui.panels.chat.vendors.local import factory as local_factory
from ide.gui.panels.chat.vendors.local.presenter import LocalCardPresenter

_FACTORIES: dict[str, object] = {
    "kimi": kimi_factory.make_tool_card,
    "dsh": dsh_factory.make_tool_card,
    "local": local_factory.make_tool_card,
}
_PRESENTERS: dict[str, type] = {
    "kimi": KimiCardPresenter,
    "dsh": DshCardPresenter,
    "local": LocalCardPresenter,
}
_CARDS: dict[str, object] = {
    "kimi": kimi_cards,
    "dsh": dsh_cards,
    "local": local_cards,
}


def _factory(vendor: str):
    """取厂商卡片工厂；未知 vendor 回落 Local（纯 ACP 字段分派）。"""
    return _FACTORIES.get(vendor, _FACTORIES["local"])


def _cards(vendor: str):
    return _CARDS.get(vendor, _CARDS["local"])


def make_tool_card(vendor: str, colors: CardColors, open_state: OpenStateMap,
                   payload: dict,
                   workspace_root: Path | None = None):
    """按 vendor 选择本厂商工厂；未识别 vendor 回落 Local。"""
    return _factory(vendor)(colors, open_state, payload, workspace_root)


def make_presenter(vendor: str):
    """按 vendor 构造本厂商 presenter；未识别 vendor 回落 Local。"""
    return _PRESENTERS.get(vendor, _PRESENTERS["local"])()


def make_todo_card(vendor: str, colors: CardColors, parent):
    """按 vendor 构造会话级 todo 卡。"""
    return _cards(vendor).TodoCard(colors, parent)


def find_question_card(vendor: str, tool_call_id: str):
    """按 vendor 查待答提问卡（各厂商注册表彼此隔离）。"""
    return _cards(vendor).find_question_card(tool_call_id)


def find_pending_question_card(vendor: str, questions: list[str]):
    """按 vendor 与问题文本匹配待答提问卡。"""
    return _cards(vendor).find_pending_question_card(questions)
