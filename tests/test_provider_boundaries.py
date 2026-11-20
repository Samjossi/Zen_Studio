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
GUI_ROOT = "src/ide/gui"
GUI_SETTINGS = f"{GUI_ROOT}/settings.py"
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

#: 厂商显示名/界面标签：公共 LLM 层与通用聊天 GUI 的字符串常量中不得出现。
#: 大小写敏感——小写 `local` 是 BackendSpec.vendor 路由值（通用面板必须认识），
#: 大写 `Local` 才是显示名。厂商显示名只由注册表与厂商 spec 持有。
FORBIDDEN_PUBLIC_DISPLAY_NAMES = (
    "Kimi ACP",
    "Kimi",
    "DeepSeek Harness",
    "DSH",
    "Local ACP",
    "Local",
)

#: 通用聊天 GUI 载荷字段黑名单：这些字段的语义解释归厂商包（presenter/cards）。
GENERIC_PAYLOAD_FORBIDDEN_KEYS = (
    "command",
    "output",
    "output_total_lines",
    "summary",
    "status",
    "error",
    "error_detail",
    "diff_hunks",
    "is_subagent",
    "questions",
    "result_summary",
    "media_path",
)

#: 通用聊天 GUI 载荷白名单：路由/生命周期元数据 + 厂商 presenter 产出的
#: 中性时间线投影（tip/weight/failed/failure_detail）。白名单之外的键同样
#: 视为未登记违约——新增载荷必须先说明由谁解释，不能默认落到通用层。
GENERIC_PAYLOAD_ALLOWED_KEYS = frozenset({
    "tool_call_id",
    "parent_tool_call_id",
    "tool_kind",
    "title",
    #: todo 快照：通用时间线只取条目数，不解释单条内容
    "entries",
    #: ActivityTimeline 的中性投影键（presenter 从厂商载荷压出）
    "tip",
    "weight",
    "failed",
    "failure_detail",
})

#: 字典只读/改写方法：属性名不是载荷字段，检查字段读取时要跳过
_PAYLOAD_MAPPING_METHODS = frozenset({
    "get", "pop", "setdefault", "keys", "values", "items", "update", "copy",
    "clear",
})

#: 新增厂商私有模块：只有本厂商后台包可以导入；公共 ACP 与通用 GUI 不得导入
VENDOR_PRIVATE_MODULES = {
    "ide.llm.providers.kimi.wire": "kimi",
    "ide.llm.providers.kimi.usage": "kimi",
    "ide.llm.providers.kimi.subagent": "kimi",
    "ide.llm.providers.dsh.session_log": "dsh",
}

#: 厂商设置键的受控例外：组合根声明 + 设置 schema（持久化键不能删除）
VENDOR_SETTING_KEY = "kimi_wire_sidecar"
VENDOR_SETTING_CONST_PREFIX = "KEY_KIMI_"


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
class SourceSemantics:
    """AST 语义视图：只收字符串常量、属性名与标识符（注释天然不在 AST）。

    每项带行号，便于违规信息定位；docstring 虽以字符串常量节点存在，但
    它是文档而非代码语义，由 `_docstring_nodes()` 排除。
    """

    strings: tuple[tuple[str, int], ...]
    attributes: tuple[tuple[str, int], ...]
    identifiers: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ModuleInfo:
    rel_path: str
    module: str
    is_package: bool
    source: str
    tree: ast.Module
    semantics: SourceSemantics
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


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """模块/类/函数的首条字符串表达式节点 id（只排除 docstring，不放过普通串）。"""
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                       ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            nodes.add(id(body[0].value))
    return nodes


def _semantics(tree: ast.Module) -> SourceSemantics:
    """收集语义 token；注释不在 AST 中，docstring 按节点 id 排除。"""
    docstrings = _docstring_nodes(tree)
    strings: list[tuple[str, int]] = []
    attributes: list[tuple[str, int]] = []
    identifiers: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            strings.append((node.value, node.lineno))
        elif isinstance(node, ast.Attribute):
            attributes.append((node.attr, node.lineno))
        elif isinstance(node, ast.Name):
            identifiers.append((node.id, node.lineno))
    return SourceSemantics(
        strings=tuple(strings),
        attributes=tuple(attributes),
        identifiers=tuple(identifiers),
    )


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
        tree=tree,
        semantics=_semantics(tree),
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
    problems = _check_capability_probes(modules)
    for m in modules:
        if not _is_public_gui(m):
            continue
        if "KEY_KIMI_" in m.source or "kimi_wire_sidecar" in m.source:
            problems.append(
                f"{m.rel_path}: 通用 GUI 不得出现厂商设置键名"
                "（应经 BackendSpec.vendor_settings 过滤注入）")
        for text in FORBIDDEN_GENERIC_CHAT_TEXTS:
            if text in m.source:
                problems.append(
                    f"{m.rel_path}: 通用 GUI 出现厂商专属文案（{text}）")
    return problems


def _is_generic_gui_tree(m: ModuleInfo) -> bool:
    """整个 GUI 树（含设置中心），排除厂商卡片包与卡片组合根。"""
    return _is_under(m.rel_path, GUI_ROOT) and not _is_under(
        m.rel_path, GUI_VENDORS_ROOT)


def _check_semantic_vocabulary(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """规则 1：公共层/通用 GUI 的字符串常量不得含厂商词表与显示名。"""
    problems: list[str] = []
    for m in modules:
        if not (_is_public_llm(m) or _is_public_gui(m)):
            continue
        for value, lineno in m.semantics.strings:
            lowered = value.lower()
            for token in FORBIDDEN_PUBLIC_TOKENS:
                if token.lower() in lowered:
                    problems.append(
                        f"{m.rel_path}:{lineno}: 字符串常量出现厂商私有词表"
                        f"（{token}）")
            for name in sorted(
                    FORBIDDEN_PUBLIC_DISPLAY_NAMES, key=len, reverse=True):
                if name in value:
                    problems.append(
                        f"{m.rel_path}:{lineno}: 字符串常量出现厂商显示名"
                        f"（{name}）")
                    break
    return problems


def _is_payload_expr(node: ast.AST | None, aliases: set[str]) -> bool:
    """载荷字典表达式：名字/属性以 payload(s) 结尾，或经赋值别名指向载荷。"""
    if isinstance(node, ast.Name):
        return bool(re.search(r"payloads?$", node.id)) or node.id in aliases
    if isinstance(node, ast.Attribute):
        return bool(re.search(r"payloads?$", node.attr))
    if isinstance(node, ast.BoolOp):
        return any(_is_payload_expr(value, aliases) for value in node.values)
    if isinstance(node, ast.IfExp):
        return _is_payload_expr(node.body, aliases) \
            or _is_payload_expr(node.orelse, aliases)
    return False


def _payload_aliases(tree: ast.Module) -> set[str]:
    """收集 `p = payload` / `p = chunk.payload` 这类别名（迭代到不动点）。"""
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            if value is None or not _is_payload_expr(value, aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _payload_key_problem(key: str) -> str | None:
    if key in GENERIC_PAYLOAD_FORBIDDEN_KEYS:
        return (f"载荷字段（{key}）只允许出现在厂商包，通用 GUI 不得读取")
    if key not in GENERIC_PAYLOAD_ALLOWED_KEYS:
        return (f"未登记载荷字段（{key}）：新增字段须先登记白名单并说明"
                "由厂商 presenter 解释")
    return None


def _check_generic_payload_fields(
        modules: tuple[ModuleInfo, ...]) -> list[str]:
    """规则 2：通用聊天 GUI 只读路由/生命周期字段，不解释厂商载荷。"""
    problems: list[str] = []
    for m in modules:
        if not _is_public_gui(m):
            continue
        aliases = _payload_aliases(m.tree)
        for node in ast.walk(m.tree):
            key: str | None = None
            lineno = getattr(node, "lineno", 0)
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _PAYLOAD_MAPPING_METHODS \
                    and node.args \
                    and _is_payload_expr(node.func.value, aliases) \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                key = node.args[0].value
            elif isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "getattr" \
                    and len(node.args) >= 2 \
                    and _is_payload_expr(node.args[0], aliases) \
                    and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                key = node.args[1].value
            elif isinstance(node, ast.Subscript) \
                    and _is_payload_expr(node.value, aliases) \
                    and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                key = node.slice.value
            elif isinstance(node, ast.Attribute) \
                    and node.attr not in _PAYLOAD_MAPPING_METHODS \
                    and _is_payload_expr(node.value, aliases):
                key = node.attr
            if key is None:
                continue
            problem = _payload_key_problem(key)
            if problem:
                problems.append(f"{m.rel_path}:{lineno}: {problem}")
    return problems


def _is_capabilities_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "capabilities_of"
    return isinstance(node.func, ast.Attribute) \
        and node.func.attr == "capabilities_of"


def _capability_snapshot_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """收集 `caps = capabilities_of(...)` 的变量名与 `self._caps = ...` 属性名。"""
    names: set[str] = set()
    attrs: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            if value is None:
                continue
            known = _is_capabilities_call(value) or (
                isinstance(value, ast.Name) and value.id in names) or (
                isinstance(value, ast.Attribute) and value.attr in attrs)
            if not known:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
                elif isinstance(target, ast.Attribute) \
                        and target.attr not in attrs:
                    attrs.add(target.attr)
                    changed = True
    return names, attrs


def _is_capability_snapshot(
        node: ast.AST | None, names: set[str], attrs: set[str]) -> bool:
    if _is_capabilities_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in names
    return isinstance(node, ast.Attribute) and node.attr in attrs


def _check_capability_probes(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """规则 3：通用 GUI 只读 capabilities_of() 快照，不探 provider 私有接口。"""
    problems: list[str] = []
    for m in modules:
        if not _is_public_gui(m):
            continue
        names, attrs = _capability_snapshot_names(m.tree)
        for node in ast.walk(m.tree):
            lineno = getattr(node, "lineno", 0)
            if isinstance(node, ast.Attribute) \
                    and node.attr in CAPABILITY_PROBE_ATTRS \
                    and not _is_capability_snapshot(node.value, names, attrs):
                problems.append(
                    f"{m.rel_path}:{lineno}: 通用 GUI 不得读取能力私有接口"
                    f"（{node.attr}），应走 capabilities_of()")
            elif isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id in ("getattr", "hasattr") \
                    and len(node.args) >= 2 \
                    and isinstance(node.args[1], ast.Constant) \
                    and node.args[1].value in CAPABILITY_PROBE_ATTRS \
                    and not _is_capability_snapshot(node.args[0], names, attrs):
                problems.append(
                    f"{m.rel_path}:{lineno}: 通用 GUI 不得用 "
                    f"{node.func.id} 探能力私有接口（{node.args[1].value}），"
                    "应走 capabilities_of()")
    return problems


def _vendor_setting_key_problem(m: ModuleInfo, value: str, lineno: int) -> str | None:
    if VENDOR_SETTING_KEY not in value \
            and VENDOR_SETTING_CONST_PREFIX not in value:
        return None
    return (f"{m.rel_path}:{lineno}: 通用 GUI 不得出现厂商设置键"
            f"（{VENDOR_SETTING_KEY}/{VENDOR_SETTING_CONST_PREFIX}*），"
            "应经 BackendSpec.vendor_settings 过滤注入")


def _check_vendor_setting_keys(modules: tuple[ModuleInfo, ...]) -> list[str]:
    """规则 4：Kimi 设置键只在 Kimi 包、后台组合根与设置 schema 出现。"""
    problems: list[str] = []
    allowed_paths = {
        LLM_REGISTRY,       # 组合根声明 vendor_settings
        GUI_SETTINGS,       # 设置 schema：持久化键/默认值不能删（S0 回写约定）
    }
    for m in modules:
        if not (_is_public_llm(m) or _is_generic_gui_tree(m)):
            continue
        if m.rel_path in allowed_paths \
                or _is_under(m.rel_path, f"{PROVIDERS_ROOT}/kimi"):
            continue
        for value, lineno in m.semantics.strings:
            problem = _vendor_setting_key_problem(m, value, lineno)
            if problem:
                problems.append(problem)
        for name, lineno in m.semantics.attributes:
            if name.startswith(VENDOR_SETTING_CONST_PREFIX) \
                    or name == VENDOR_SETTING_KEY:
                problems.append(
                    f"{m.rel_path}:{lineno}: 通用 GUI 不得读写厂商设置键"
                    f"（{name}）")
        for name, lineno in m.semantics.identifiers:
            if name.startswith(VENDOR_SETTING_CONST_PREFIX):
                problems.append(
                    f"{m.rel_path}:{lineno}: 通用 GUI 不得引用厂商设置键常量"
                    f"（{name}）")
        for ref in m.imports:
            for name in ref.names:
                if name.startswith(VENDOR_SETTING_CONST_PREFIX):
                    problems.append(
                        f"{m.rel_path}: 通用 GUI 不得 import 厂商设置键常量"
                        f"（{name}）")
    return problems


def _check_vendor_private_imports(
        modules: tuple[ModuleInfo, ...]) -> list[str]:
    """规则 5：新增厂商私有模块只允许本厂商后台包导入。"""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for m in modules:
        for target in m.targets():
            for base, vendor in VENDOR_PRIVATE_MODULES.items():
                if target != base and not target.startswith(base + "."):
                    continue
                key = (m.rel_path, base)
                if key in seen:
                    continue
                seen.add(key)
                if _is_under(m.rel_path, f"{PROVIDERS_ROOT}/{vendor}"):
                    continue
                problems.append(
                    f"{m.rel_path}: 只有 {vendor} 后台包可以导入厂商私有模块"
                    f"（{base}）")
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
        + _check_semantic_vocabulary(modules)
        + _check_generic_payload_fields(modules)
        + _check_capability_probes(modules)
        + _check_vendor_setting_keys(modules)
        + _check_vendor_private_imports(modules)
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
        GUI_SETTINGS,
        f"{GUI_CHAT_ROOT}/panel.py",
        f"{GUI_CHAT_ROOT}/card_base.py",
        f"{GUI_CHAT_ROOT}/timeline.py",
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


# ----------------------------------------------------------------------
# S2 语义/字段级规则：helper 自测、反例与通过用例
# ----------------------------------------------------------------------
def test_semantic_view_separates_comment_docstring_and_code_string():
    """同一段词放进注释/docstring 不命中，放进字符串常量才命中。"""
    source = '''"""DOC_TOKEN payload.get("status")"""
# COMMENT_TOKEN payload.get("status")
VALUE = "STRING_TOKEN"
payload = {}
payload.get("status")
'''
    semantics = _semantics(ast.parse(source))
    strings = [value for value, _ in semantics.strings]
    assert "STRING_TOKEN" in strings
    assert all("DOC_TOKEN" not in value for value in strings)
    assert all("COMMENT_TOKEN" not in value for value in strings)
    # 代码里的键名是字符串常量，能被规则 2 看到
    assert "status" in strings
    assert "payload" in [name for name, _ in semantics.identifiers]
    assert "get" in [name for name, _ in semantics.attributes]


def test_guard_rejects_vendor_display_name_in_string_constant():
    module = _synthetic(f"{GUI_CHAT_ROOT}/panel.py", 'LABEL = "Kimi ACP"\n')
    problems = _check_semantic_vocabulary((module,))
    assert any("厂商显示名" in problem for problem in problems), problems


def test_guard_ignores_vendor_display_name_in_comment_and_docstring():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        '"""Kimi ACP 请求执行工具（历史文案，已中性化）"""\n'
        "# Kimi ACP 请求执行工具\n"
        'LABEL = "工具审批"\n')
    assert _check_semantic_vocabulary((module,)) == []


def test_guard_rejects_generic_payload_field_read():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/timeline.py",
        "def render(payload):\n    return payload.get('status')\n")
    problems = _check_generic_payload_fields((module,))
    assert any("载荷字段（status）" in problem for problem in problems), problems


def test_guard_rejects_aliased_and_subscript_payload_reads():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/timeline.py",
        "def render(chunk):\n"
        "    p = chunk.payload\n"
        "    return p['command']\n")
    problems = _check_generic_payload_fields((module,))
    assert any("载荷字段（command）" in problem for problem in problems), problems


def test_guard_rejects_unregistered_payload_field():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/timeline.py",
        "def render(payload):\n    return payload.get('raw_input')\n")
    problems = _check_generic_payload_fields((module,))
    assert any("未登记载荷字段" in problem for problem in problems), problems


def test_guard_allows_routing_and_projection_payload_fields():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/timeline.py",
        "def render(payload):\n"
        "    return (payload.get('tool_call_id'), payload.get('tip'),\n"
        "            payload.get('weight'), payload.get('failed'))\n")
    assert _check_generic_payload_fields((module,)) == []


def test_guard_ignores_payload_access_text_in_docstring():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/timeline.py",
        'def render(payload):\n'
        '    """坏例子：payload.get(\'status\')，仅文档说明。"""\n'
        "    return payload.get('title')\n")
    assert _check_generic_payload_fields((module,)) == []


def test_guard_does_not_treat_acp_permission_params_as_gui_payload():
    """权限请求的 ACP params 是另一套契约，不是通用 GUI 工具载荷。"""
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/permission_dialog.py",
        "def summarize(tool_call):\n"
        "    raw = tool_call.get('rawInput')\n"
        "    return raw.get('command')\n")
    assert _check_generic_payload_fields((module,)) == []


def test_guard_rejects_capability_attribute_probe():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "flag = provider.supports_images_override\n")
    problems = _check_capability_probes((module,))
    assert any("能力私有接口" in problem for problem in problems), problems


def test_guard_allows_capability_snapshot_via_assignment():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "caps = capabilities_of(provider)\n"
        "pending = caps.recovery_pending\n")
    assert _check_capability_probes((module,)) == []


def test_guard_rejects_vendor_setting_constant_identifier():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py", "KEY_KIMI_WIRE_SIDECAR = True\n")
    problems = _check_vendor_setting_keys((module,))
    assert any("不得引用厂商设置键常量" in problem for problem in problems), problems


def test_guard_allows_settings_schema_vendor_key():
    """设置 schema 持有持久化键名与默认值是 S0 回写的受控例外。"""
    module = _synthetic(
        GUI_SETTINGS,
        'KEY_KIMI_WIRE_SIDECAR = "kimi_wire_sidecar"\n'
        'DEFAULT = {KEY_KIMI_WIRE_SIDECAR: True}\n')
    assert _check_vendor_setting_keys((module,)) == []


def test_guard_rejects_acp_importing_vendor_private_module():
    module = _synthetic(
        f"{ACP_ROOT}/connection.py",
        "from ide.llm.providers.kimi.wire import parse_wire\n")
    problems = _check_vendor_private_imports((module,))
    assert any("厂商私有模块" in problem for problem in problems), problems


def test_guard_rejects_generic_gui_importing_vendor_private_module():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "import ide.llm.providers.dsh.session_log\n")
    problems = _check_vendor_private_imports((module,))
    assert any("只有 dsh 后台包" in problem for problem in problems), problems


def test_guard_allows_own_vendor_private_module_import():
    module = _synthetic(
        f"{PROVIDERS_ROOT}/kimi/provider.py",
        "from .wire import parse_wire\n")
    assert _check_vendor_private_imports((module,)) == []


@pytest.mark.parametrize(("rel_path", "source", "marker"), [
    (f"{GUI_CHAT_ROOT}/panel.py", 'LABEL = "Kimi"\n', "厂商显示名"),
    (f"{GUI_CHAT_ROOT}/timeline.py",
     "def render(payload):\n    return payload.get('status')\n", "载荷字段"),
    (f"{GUI_CHAT_ROOT}/panel.py",
     "flag = provider.recovery_pending\n", "能力私有接口"),
    (f"{GUI_CHAT_ROOT}/panel.py",
     "KEY_KIMI_WIRE_SIDECAR = True\n", "不得引用厂商设置键常量"),
    (f"{ACP_ROOT}/connection.py",
     "from ide.llm.providers.kimi.wire import parse_wire\n", "厂商私有模块"),
])
def test_guard_aggregate_reports_each_semantic_rule(rel_path, source, marker):
    """聚合入口必须真的接上五类新规则，而不是只有子函数自测。"""
    problems = _all_violations((_synthetic(rel_path, source),))
    assert any(marker in problem for problem in problems), problems


def test_guard_rejects_vendor_setting_constant_import():
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py",
        "from ide.gui.settings import KEY_KIMI_WIRE_SIDECAR\n")
    problems = _check_vendor_setting_keys((module,))
    assert any("不得 import" in problem for problem in problems), problems


@pytest.mark.parametrize("attr", CAPABILITY_PROBE_ATTRS)
def test_guard_rejects_each_capability_probe_attr(attr):
    module = _synthetic(
        f"{GUI_CHAT_ROOT}/panel.py", f"value = getattr(provider, {attr!r}, None)\n")
    problems = _check_capability_probes((module,))
    assert any("能力私有接口" in problem for problem in problems), problems


@pytest.mark.parametrize("target", VENDOR_PRIVATE_MODULES)
def test_guard_rejects_each_vendor_private_module(target):
    module = _synthetic(
        f"{ACP_ROOT}/connection.py", f"from {target} import helper\n")
    problems = _check_vendor_private_imports((module,))
    assert any("厂商私有模块" in problem for problem in problems), problems
