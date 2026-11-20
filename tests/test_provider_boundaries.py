"""生产源码依赖方向与公共层词表守卫（T5）。

只扫描 `src/ide/**/*.py` 与 `main.py`；`tests/`、`scripts/`、历史文档和
帧存档不在扫描范围。规则用标准库 `ast` 读取 import 与源码词表，不引入
新的架构检查依赖。

守卫必须能失败：每个检查都有受控反例用例；反例只在测试内存中构造，
不写入生产树。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from ide.llm import DEFAULT_BACKEND
from ide.llm.registry import REGISTRY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_IDE = PROJECT_ROOT / "src" / "ide"
MAIN = PROJECT_ROOT / "main.py"

VENDORS = ("kimi", "dsh", "local")
LLM_ROOT = "src/ide/llm"
LLM_INIT = f"{LLM_ROOT}/__init__.py"
LLM_BASE = f"{LLM_ROOT}/base.py"
LLM_POLICY = f"{LLM_ROOT}/permission_policy.py"
LLM_REGISTRY = f"{LLM_ROOT}/registry.py"
LLM_REGISTRY_MODULE = "ide.llm.registry"
PROVIDERS_ROOT = f"{LLM_ROOT}/providers"
ACP_ROOT = f"{PROVIDERS_ROOT}/acp"
GUI_CHAT_ROOT = "src/ide/gui/panels/chat"
GUI_VENDORS_ROOT = f"{GUI_CHAT_ROOT}/vendors"
GUI_REGISTRY = f"{GUI_VENDORS_ROOT}/registry.py"

#: 旧平铺模块路径：生产代码出现即说明迁移未收口
LEGACY_IMPORT_PREFIXES = (
    "ide.llm.providers.kimi_acp",
    "ide.llm.providers.dsh_acp",
    "ide.llm.providers.kimi_common",
    "ide.llm.providers.kimi_turn_consistency",
    "ide.llm.cards",
    "ide.llm.tool_cards",
)

#: 通用聊天 GUI 不得探的能力私有接口：统一走 capabilities_of() 快照
CAPABILITY_PROBE_ATTRS = (
    "supports_images_override",
    "post_turn_usage_grace_s",
    "stall_watchdog_enabled",
    "recovery_pending",
    "set_subagent_sidecar",
)

#: 三处公共厂商文案：审批框标题 / 图片降级提示 / 用量来源
FORBIDDEN_GENERIC_CHAT_TEXTS = (
    "Kimi ACP 请求执行工具",
    "切换至 Kimi 后端",
    "kimi 会话记录",
)

#: 厂商私有工具名/分派表/载荷字段/配置键/落盘路径：不得留在公共层
FORBIDDEN_PUBLIC_TOKENS = (
    "AskUserQuestion",
    "ReadMediaFile",
    "read_image",
    "todo_write",
    "TodoList",
    "_TOOL_NAME_CARDS",
    "_FALLBACK_TOOL_KINDS",
    "_INPUT_DETAIL_KEYS",
    "_RECEIPT_PATTERNS",
    "_TODO_STATUS_NORMALIZE",
    "_QUESTION_TOOL_NAMES",
    "ToolCallPayload",
    "ToolUpdatePayload",
    "TodoPayload",
    "TodoEntry",
    "UpdateMapper",
    "BashCard",
    "DiffCard",
    "QuestionCard",
    "MediaReadCard",
    "SubagentCard",
    "TodoCard",
    "TodoListCard",
    "McpCard",
    "diff_hunks",
    "result_summary",
    "media_path",
    "reasoning_effort",
    "wire.jsonl",
    "agents/main",
    "token_counting",
    "usage.record",
    "maxContextSize",
    "llm.request",
)


@dataclass(frozen=True)
class ImportRef:
    """一条 import 的绝对模块名与导入名（`import x` 时名字为空）。"""

    module: str
    names: tuple[str, ...] = ()

    def targets(self) -> list[str]:
        """模块本身 + `from m import name` 的候选子模块（保守多报）。"""
        targets = [self.module] if self.module else []
        if self.module:
            targets.extend(
                f"{self.module}.{name}"
                for name in self.names
                if name != "*"
            )
        return targets


@dataclass(frozen=True)
class ModuleInfo:
    rel_path: str
    module: str
    is_package: bool
    source: str
    imports: tuple[ImportRef, ...]

    def targets(self) -> list[str]:
        result: list[str] = []
        for ref in self.imports:
            result.extend(ref.targets())
        return result


def _module_name(rel_path: str, is_package: bool) -> str:
    parts = list(Path(rel_path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if is_package and parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(
        module_name: str, is_package: bool, level: int, module: str | None,
) -> str:
    """把相对 import 解析为绝对模块名；无法解析时返回空串。"""
    if level == 0:
        return module or ""
    package_parts = module_name.split(".") if module_name else []
    if not is_package and package_parts:
        package_parts = package_parts[:-1]
    up = level - 1
    if up > len(package_parts):
        return ""
    if up:
        package_parts = package_parts[:len(package_parts) - up]
    if module:
        package_parts.extend(module.split("."))
    return ".".join(package_parts)


def _load_module(rel_path: str, source: str | None = None) -> ModuleInfo:
    is_package = rel_path.endswith("/__init__.py") or rel_path == "__init__.py"
    text = source if source is not None else (
        PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(text, filename=rel_path)
    module_name = _module_name(rel_path, is_package)
    imports: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(ImportRef(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(
                module_name, is_package, node.level, node.module)
            imports.append(ImportRef(
                resolved, tuple(alias.name for alias in node.names)))
    return ModuleInfo(
        rel_path=rel_path,
        module=module_name,
        is_package=is_package,
        source=text,
        imports=tuple(imports),
    )


def _production_modules() -> tuple[ModuleInfo, ...]:
    paths = sorted(SRC_IDE.rglob("*.py"))
    paths.append(MAIN)
    return tuple(
        _load_module(path.relative_to(PROJECT_ROOT).as_posix())
        for path in paths
    )


def _is_under(rel_path: str, root: str) -> bool:
    return rel_path.startswith(root + "/")


def _vendor_in(rel_path: str, root: str) -> str | None:
    prefix = root + "/"
    if not rel_path.startswith(prefix):
        return None
    first = rel_path[len(prefix):].split("/", 1)[0]
    return first if first in VENDORS else None


def _backend_vendor(m: ModuleInfo) -> str | None:
    return _vendor_in(m.rel_path, PROVIDERS_ROOT)


def _gui_vendor(m: ModuleInfo) -> str | None:
    return _vendor_in(m.rel_path, GUI_VENDORS_ROOT)


def _is_backend_provider(m: ModuleInfo) -> bool:
    return _is_under(m.rel_path, PROVIDERS_ROOT)


def _is_acp(m: ModuleInfo) -> bool:
    return _is_under(m.rel_path, ACP_ROOT)


def _is_llm(m: ModuleInfo) -> bool:
    return _is_under(m.rel_path, LLM_ROOT)


def _is_generic_chat_gui(m: ModuleInfo) -> bool:
    return _is_under(m.rel_path, GUI_CHAT_ROOT) and _gui_vendor(m) is None


def _is_public_llm(m: ModuleInfo) -> bool:
    return m.rel_path in {
        LLM_INIT, LLM_BASE, LLM_POLICY, f"{PROVIDERS_ROOT}/__init__.py",
    } or _is_acp(m)


def _is_public_gui(m: ModuleInfo) -> bool:
    # 卡片组合根按职责认识具体厂商包，不属于「公共 UI 基础件」；
    # 它自身仍受只有组合根可导入厂商包的规则约束。
    return _is_generic_chat_gui(m) and m.rel_path != GUI_REGISTRY


def _backend_vendor_of_module(module: str) -> str | None:
    for vendor in VENDORS:
        base = f"ide.llm.providers.{vendor}"
        if module == base or module.startswith(base + "."):
            return vendor
    return None


def _gui_vendor_of_module(module: str) -> str | None:
    for vendor in VENDORS:
        base = f"ide.gui.panels.chat.vendors.{vendor}"
        if module == base or module.startswith(base + "."):
            return vendor
    return None


def _check_vendor_isolation(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """后台包与 GUI 卡片包都不得横向 import 其他厂商。"""
    problems: list[str] = []
    for m in modules:
        own_backend = _backend_vendor(m)
        own_gui = _gui_vendor(m)
        for target in m.targets():
            other_backend = _backend_vendor_of_module(target)
            if own_backend and other_backend and other_backend != own_backend:
                problems.append(
                    f"{m.rel_path}: 后台厂商 {own_backend} 横向导入了 "
                    f"{other_backend}（{target}）")
            other_gui = _gui_vendor_of_module(target)
            if own_gui and other_gui and other_gui != own_gui:
                problems.append(
                    f"{m.rel_path}: 卡片厂商 {own_gui} 横向导入了 "
                    f"{other_gui}（{target}）")
    return problems


def _check_composition_roots(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """具体后台只由 llm.registry 装配，具体卡片包只由卡片 registry 装配。"""
    problems: list[str] = []
    for m in modules:
        own_backend = _backend_vendor(m)
        own_gui = _gui_vendor(m)
        for target in m.targets():
            backend_vendor = _backend_vendor_of_module(target)
            if backend_vendor and backend_vendor != own_backend \
                    and backend_vendor != own_gui \
                    and m.rel_path != LLM_REGISTRY:
                problems.append(
                    f"{m.rel_path}: 只有后台注册表可以 import 具体厂商包 "
                    f"({target})")
            gui_vendor = _gui_vendor_of_module(target)
            if gui_vendor and gui_vendor != own_gui \
                    and m.rel_path != GUI_REGISTRY:
                problems.append(
                    f"{m.rel_path}: 只有卡片组合根可以 import 具体厂商卡片包 "
                    f"({target})")
    return problems


def _check_llm_dependency_direction(
        modules: tuple[ModuleInfo, ...],
) -> list[str]:
    """LLM 层不得 import GUI；provider 不得反向 import registry。"""
    problems: list[str] = []
    for m in modules:
        if not _is_llm(m):
            continue
        for target in m.targets():
            if target == "ide.gui" or target.startswith("ide.gui."):
                problems.append(
                    f"{m.rel_path}: LLM 层不得导入 GUI（{target}）")
            if _is_backend_provider(m) and (
                    target == LLM_REGISTRY_MODULE
                    or target.startswith(LLM_REGISTRY_MODULE + ".")):
                problems.append(
                    f"{m.rel_path}: provider 不得导入 registry（{target}）")
            if _is_backend_provider(m) and target == "ide.llm":
                problems.append(
                    f"{m.rel_path}: provider 不得导入公共出口（会反卷 registry）"
                    f"（{target}）")
        if m.rel_path == LLM_BASE:
            for target in m.targets():
                if target.startswith("ide.llm.providers") \
                        or target == "ide.llm.registry":
                    problems.append(
                        f"{m.rel_path}: 稳定契约不得依赖具体 provider/registry "
                        f"（{target}）")
        if m.rel_path == LLM_POLICY:
            for target in m.targets():
                if _backend_vendor_of_module(target):
                    problems.append(
                        f"{m.rel_path}: 公共策略不得依赖具体厂商（{target}）")
    return problems


def _check_gui_vendor_imports(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """厂商 GUI 包最多只能取本厂商 models 纯类型，不得碰运行时。"""
    problems: list[str] = []
    for m in modules:
        vendor = _gui_vendor(m)
        if not vendor:
            continue
        own_models = f"ide.llm.providers.{vendor}.models"
        for ref in m.imports:
            if not ref.module.startswith("ide.llm"):
                continue
            if ref.module == own_models or ref.module.startswith(own_models + "."):
                continue
            if ref.module == f"ide.llm.providers.{vendor}" \
                    and set(ref.names) <= {"models"}:
                continue
            problems.append(
                f"{m.rel_path}: GUI 厂商包只能导入本厂商 models 纯类型，"
                f"禁止运行时或其他 LLM 模块（{ref.module}）")
    return problems


def _check_legacy_paths(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """生产代码不得再 import 迁移前的平铺模块路径。"""
    problems: list[str] = []
    for m in modules:
        for target in m.targets():
            for prefix in LEGACY_IMPORT_PREFIXES:
                if target == prefix or target.startswith(prefix + "."):
                    problems.append(
                        f"{m.rel_path}: 仍在 import 旧模块路径（{target}）")
    return problems


def _check_public_vocabulary(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """公共 LLM 层与通用聊天 GUI 不得含厂商私有分派表/载荷/配置/路径。"""
    problems: list[str] = []
    for m in modules:
        if not (_is_public_llm(m) or _is_public_gui(m)):
            continue
        lowered = m.source.lower()
        for token in FORBIDDEN_PUBLIC_TOKENS:
            if token.lower() in lowered:
                problems.append(
                    f"{m.rel_path}: 公共层出现厂商私有词表（{token}）")
    return problems


def _check_generic_capability_contract(
        modules: tuple[ModuleInfo, ...]) -> list[str]:
    """通用聊天 GUI 只消费能力快照与厂商设置声明，不探私有接口与键名。"""
    problems: list[str] = []
    for m in modules:
        if not _is_public_gui(m):
            continue
        for attr in CAPABILITY_PROBE_ATTRS:
            pattern = rf"getattr\(\s*[^,\n]+,\s*['\"]{re.escape(attr)}['\"]"
            if re.search(pattern, m.source):
                problems.append(
                    f"{m.rel_path}: 通用 GUI 不得用 getattr 探能力私有接口"
                    f"（{attr}），应走 capabilities_of()")
        if "KEY_KIMI_" in m.source or "kimi_wire_sidecar" in m.source:
            problems.append(
                f"{m.rel_path}: 通用 GUI 不得出现厂商设置键名"
                "（应经 BackendSpec.vendor_settings 过滤注入）")
        for text in FORBIDDEN_GENERIC_CHAT_TEXTS:
            if text in m.source:
                problems.append(
                    f"{m.rel_path}: 通用 GUI 出现厂商专属文案（{text}）")
    return problems


def _all_violations(modules: tuple[ModuleInfo, ...]) -> list[str]:
    return (
        _check_vendor_isolation(modules)
        + _check_composition_roots(modules)
        + _check_llm_dependency_direction(modules)
        + _check_gui_vendor_imports(modules)
        + _check_legacy_paths(modules)
        + _check_public_vocabulary(modules)
        + _check_generic_capability_contract(modules)
    )


# ----------------------------------------------------------------------
# 现行生产源码
# ----------------------------------------------------------------------
def test_production_source_passes_all_boundaries():
    problems = _all_violations(_production_modules())
    assert not problems, "\n".join(problems)


def test_guard_scans_expected_production_files_only():
    modules = _production_modules()
    rel_paths = {m.rel_path for m in modules}
    assert len(modules) >= 120
    assert {
        "main.py",
        LLM_INIT,
        LLM_BASE,
        LLM_POLICY,
        LLM_REGISTRY,
        f"{PROVIDERS_ROOT}/__init__.py",
        f"{ACP_ROOT}/__init__.py",
        f"{ACP_ROOT}/connection.py",
        f"{GUI_CHAT_ROOT}/panel.py",
        f"{GUI_CHAT_ROOT}/card_base.py",
        f"{GUI_CHAT_ROOT}/transcript.py",
        GUI_REGISTRY,
    } <= rel_paths
    for vendor in VENDORS:
        assert f"{PROVIDERS_ROOT}/{vendor}/provider.py" in rel_paths
        assert f"{GUI_VENDORS_ROOT}/{vendor}/factory.py" in rel_paths
    assert all(
        not rel.startswith(("tests/", "scripts/", ".temp/"))
        for rel in rel_paths
    )


def test_public_export_surface_is_contract_only():
    import ide.llm as public

    forbidden = {
        "KimiAcpLLM", "DshAcpLLM", "LocalAcpLLM",
        "kimi_available", "dsh_available", "local_available",
        "list_kimi_models", "list_dsh_models", "list_local_models",
        "load_kimi_provider_catalog", "display_names_from_catalog",
        "efforts_from_catalog", "models_from_catalog",
    }
    assert forbidden.isdisjoint(public.__all__)
    for name in forbidden:
        assert not hasattr(public, name), name
    assert DEFAULT_BACKEND in REGISTRY


# ----------------------------------------------------------------------
# 受控反例：证明各规则会失败（反例只活在测试内存）
# ----------------------------------------------------------------------
def _synthetic(rel_path: str, source: str) -> ModuleInfo:
    return _load_module(rel_path, source=source)


def test_guard_aggregate_reports_counterexample():
    """生产检查入口本身必须会失败，不能只是各个子规则自测。"""
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "from ide.llm.providers.kimi import KimiAcpLLM\n")
    assert _all_violations((module,))


def test_guard_rejects_cross_vendor_backend_import():
    module = _synthetic(
        f"{PROVIDERS_ROOT}/kimi/provider.py",
        "from ide.llm.providers.dsh.updates import DshUpdateMapper\n")
    problems = _check_vendor_isolation((module,))
    assert any("横向导入了 dsh" in problem for problem in problems), problems


def test_guard_rejects_cross_vendor_card_import():
    module = _synthetic(
        f"{GUI_VENDORS_ROOT}/kimi/factory.py",
        "from ide.gui.panels.chat.vendors.dsh.cards import McpCard\n")
    problems = _check_vendor_isolation((module,))
    assert any("横向导入了 dsh" in problem for problem in problems), problems


def test_guard_rejects_generic_module_importing_concrete_provider():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "from ide.llm.providers.kimi import KimiAcpLLM\n")
    problems = _check_composition_roots((module,))
    assert any("只有后台注册表" in problem for problem in problems), problems


def test_guard_rejects_generic_gui_importing_vendor_cards():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/transcript.py",
        "from ide.gui.panels.chat.vendors.kimi.cards import QuestionCard\n")
    problems = _check_composition_roots((module,))
    assert any("只有卡片组合根" in problem for problem in problems), problems


def test_guard_rejects_provider_importing_registry():
    module = _synthetic(
        f"{PROVIDERS_ROOT}/dsh/provider.py",
        "import ide.llm.registry\n")
    problems = _check_llm_dependency_direction((module,))
    assert any("provider 不得导入 registry" in p for p in problems), problems


def test_guard_rejects_provider_importing_public_exit():
    module = _synthetic(
        f"{PROVIDERS_ROOT}/kimi/provider.py",
        "from ide.llm import REGISTRY\n")
    problems = _check_llm_dependency_direction((module,))
    assert any("不得导入公共出口" in problem for problem in problems), problems


def test_guard_rejects_llm_layer_importing_gui():
    module = _synthetic(
        LLM_POLICY, "from ide.gui import theme\n")
    problems = _check_llm_dependency_direction((module,))
    assert any("LLM 层不得导入 GUI" in problem for problem in problems), problems


def test_guard_rejects_gui_vendor_importing_provider_runtime():
    module = _synthetic(
        f"{GUI_VENDORS_ROOT}/kimi/factory.py",
        "from ide.llm.providers.kimi.provider import KimiAcpLLM\n")
    problems = _check_gui_vendor_imports((module,))
    assert any("只能导入本厂商 models" in p for p in problems), problems


def test_guard_allows_gui_vendor_importing_own_models():
    module = _synthetic(
        f"{GUI_VENDORS_ROOT}/kimi/cards.py",
        "from ide.llm.providers.kimi.models import ToolCallPayload\n")
    assert _check_gui_vendor_imports((module,)) == []


def test_guard_rejects_legacy_module_path():
    module = _synthetic(
        f"{PROVIDERS_ROOT}/kimi/provider.py",
        "from ide.llm.providers.kimi_acp import KimiAcpLLM\n")
    problems = _check_legacy_paths((module,))
    assert any("旧模块路径" in problem for problem in problems), problems


@pytest.mark.parametrize("token", [
    "AskUserQuestion",
    "ReadMediaFile",
    "read_image",
    "todo_write",
    "diff_hunks",
    "result_summary",
    "media_path",
    "reasoning_effort",
    "wire.jsonl",
    "agents/main",
    "token_counting",
    "usage.record",
    "maxContextSize",
    "llm.request",
    "_TOOL_NAME_CARDS",
    "_FALLBACK_TOOL_KINDS",
    "_INPUT_DETAIL_KEYS",
    "_RECEIPT_PATTERNS",
    "_TODO_STATUS_NORMALIZE",
    "_QUESTION_TOOL_NAMES",
    "ToolCallPayload",
    "ToolUpdatePayload",
    "TodoPayload",
    "TodoEntry",
    "UpdateMapper",
    "BashCard",
    "DiffCard",
    "QuestionCard",
    "MediaReadCard",
    "SubagentCard",
    "TodoCard",
    "TodoListCard",
    "McpCard",
])
def test_guard_rejects_public_private_vocabulary(token):
    module = _synthetic(LLM_BASE, f"MARKER = {token!r}\n")
    problems = _check_public_vocabulary((module,))
    assert any("公共层出现厂商私有词表" in p for p in problems), problems


def test_guard_scans_generic_gui_vocabulary():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/card_base.py", "MARKER = 'media_path'\n")
    problems = _check_public_vocabulary((module,))
    assert any("公共层出现厂商私有词表" in p for p in problems), problems


def test_guard_does_not_scan_tests_or_script_fixtures():
    """夹具可以出现厂商词表；生产扫描不得把它们算进来。"""
    modules = _production_modules()
    assert all(not m.rel_path.startswith(("tests/", "scripts/"))
               for m in modules)
    assert all("参考代码" not in m.rel_path for m in modules)


def test_guard_rejects_generic_capability_probe():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "grace = getattr(provider, 'post_turn_usage_grace_s', None)\n")
    problems = _check_generic_capability_contract((module,))
    assert any("能力私有接口" in problem for problem in problems), problems


def test_guard_rejects_generic_vendor_setting_key():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "value = load_settings()['kimi_wire_sidecar']\n")
    problems = _check_generic_capability_contract((module,))
    assert any("厂商设置键名" in problem for problem in problems), problems


def test_guard_rejects_generic_vendor_copy():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/permission_dialog.py",
        "header = 'Kimi ACP 请求执行工具'\n")
    problems = _check_generic_capability_contract((module,))
    assert any("厂商专属文案" in problem for problem in problems), problems


def test_guard_allows_capability_snapshot_field_read():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "pending = capabilities_of(provider).recovery_pending\n")
    assert _check_generic_capability_contract((module,)) == []
