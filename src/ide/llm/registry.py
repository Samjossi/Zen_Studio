"""后端组合根：接口实现名 → `BackendSpec` 单一装配来源。

设计决策（本项目文档即代码注释）：
- 「后端发现 / 可用性探测 / 模型枚举 / 实例工厂」由各厂商
  `providers/<vendor>/spec.py` 自包含声明，本模块只按固定顺序组合；
  新增后台只需在厂商包内新增 `spec.py` 并在此追加一项，不改 GUI。
- import 无副作用（AFCP 2.3 依赖显式）：`available`/`list_models`/
  `factory` 均为惰性可调用，CLI 探测与 provider 实例化不发生在模块级。
- D6 红线 1：模型目录挂接口级，每个 `BackendSpec.list_models` 各调各的，
  全库禁止跨后台共享模型表；红线 2：模型别名对注册表是不透明字符串，
  不解析、不拼接、不校验格式。
- 模型目录缓存归 `ide.llm.cache`：以接口实现名为键、进程级、无 TTL；
  `refresh_models()` 是唯一失效口，内部只委托缓存模块清理三路缓存。
- 依赖方向：本模块 → 厂商 `spec.py` → 厂商运行时/目录 → `llm.base`；
  厂商包不得反向 import 本模块（防循环 import）。各模块 import 无副作用。
"""
from ide.llm.cache import clear as clear_caches
from ide.llm.providers.dsh.spec import SPEC as DSH_SPEC
from ide.llm.providers.kimi.spec import SPEC as KIMI_SPEC
from ide.llm.providers.local.spec import SPEC as LOCAL_SPEC
from ide.llm.spec import BackendSpec, resolve_efforts

#: 出厂默认后台（settings `model_backend` 初值）。具体后台 ID 归注册表所有；
#: 公共出口只再导出这个注册表常量，不暴露厂商类或探测函数。
DEFAULT_BACKEND = "kimi-acp"


# ----------------------------------------------------------------------
# 注册段：dict 保持插入序，插入序即 UI 菜单序。
# 三家均为 ACP 传输层；静态元数据、能力、设置键与工厂归各自 spec.py。
# ----------------------------------------------------------------------
REGISTRY: dict[str, BackendSpec] = {
    spec.name: spec
    for spec in (KIMI_SPEC, DSH_SPEC, LOCAL_SPEC)
}


# ----------------------------------------------------------------------
# 查询 API
# ----------------------------------------------------------------------
def spec_of(name: str) -> BackendSpec | None:
    """按接口实现名查注册项；未知名返回 None（不抛 KeyError）。

    后端名来自持久化 settings，可能指向已移除/未注册的实现，
    调用方按「后端不可用」处理（与现状"未检测到 CLI"语义一致）。
    """
    return REGISTRY.get(name)


def vendor_of(name: str) -> str | None:
    """接口实现名 → 所属后台名；未知名返回 None（D2：后台由注册表推导）。"""
    spec = REGISTRY.get(name)
    return spec.vendor if spec is not None else None


def vendor_groups() -> dict[str, list[BackendSpec]]:
    """后台名 → 该后台下接口 spec 列表（保持注册序；阶段二一级菜单数据源）。"""
    groups: dict[str, list[BackendSpec]] = {}
    for spec in REGISTRY.values():
        groups.setdefault(spec.vendor, []).append(spec)
    return groups


def refresh_models(name: str | None = None) -> None:
    """清模型列表、强度档位与原始目录载荷缓存；name=None 清全部。

    唯一缓存失效口（D2/R3）：模型目录低频变化，进程内不做 TTL/文件监听；
    设置中心「重新检测」等显式刷新场景调本函数。三路缓存同点失效的实现
    在 `ide.llm.cache.clear()`，本函数只做委托，保持公共签名不变。
    """
    clear_caches(name)


# ----------------------------------------------------------------------
# 派生映射（由 REGISTRY 生成；经 ide/llm/__init__ 作为公共 API 再导出）
# ----------------------------------------------------------------------
#: 接口实现名 → 显示名（设置菜单项 / ModelBar 下拉 / 切换提示共用单一映射）
BACKEND_LABELS: dict[str, str] = {name: spec.label for name, spec in REGISTRY.items()}

#: 后台名 → 后台显示名（保持注册序；阶段二 ModelBar 一级按钮用）
VENDOR_LABELS: dict[str, str] = {
    spec.vendor: spec.vendor_label for spec in REGISTRY.values()}


__all__ = [
    "BackendSpec",
    "DEFAULT_BACKEND",
    "REGISTRY",
    "spec_of",
    "vendor_of",
    "vendor_groups",
    "refresh_models",
    "resolve_efforts",
    "BACKEND_LABELS",
    "VENDOR_LABELS",
]
