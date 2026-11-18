"""用量顶层菜单：设置之后、帮助之前；点击直达 show_token_usage（2026-1118）。"""
from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar

from ide.gui.menus import assembler, usage_menu
from ide.gui.menus.registry import ActionRegistry


class _Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.token_usage_calls = 0

    def show_token_usage(self):
        self.token_usage_calls += 1


def _build_usage_only():
    if QApplication.instance() is None:
        QApplication([])
    window = _Window()
    menubar = QMenuBar(window)
    actions = ActionRegistry()
    usage_menu.build(menubar, window, actions)
    return window, menubar, actions


def test_usage_registered_after_settings_before_help():
    modules = assembler.MODULES
    assert modules.index(assembler.settings_menu) < modules.index(
        usage_menu) < modules.index(assembler.help_menu)


def test_usage_entry_is_top_level_clickable_action():
    window, menubar, actions = _build_usage_only()
    tops = [a.text() for a in menubar.actions()]
    assert tops == ["用量(&U)"]  # 顶层 action 本身，非下拉菜单


def test_usage_triggers_show_token_usage():
    window, menubar, actions = _build_usage_only()
    action = actions.get("usage.token")
    assert action is not None
    action.trigger()
    assert window.token_usage_calls == 1
