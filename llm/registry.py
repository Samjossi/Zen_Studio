"""后端注册表：接口实现名 → BackendSpec 单一装配来源（计划 2026-0730-0150 §4-D3）。

设计决策（本项目文档即代码注释）：
- 「后端发现 / 可用性探测 / 模型枚举 / 实例工厂」由散落的硬编码
  （llm/__init__ 常量 + ChatPanel._build_providers）收敛为本模块单点；
  后续新增 CLI 后台只动 llm/providers/ 与本模块注册段一处。
- import 无副作用（AFCP 2.3 依赖显式）：available/list_models/factory
  均为惰性可调用，CLI 探测与 provider 实例化不发生在模块级。
- D6 红线 1：模型目录挂接口级——每个 BackendSpec.list_models 各调各的
  （kimi → `kimi provider list --json`），全库禁止跨后台共享模型表；
  红线 2：模型别名对注册表是不透明字符串，不解析、不拼接、不校验格式。
- 模型列表进程级缓存（计划 2026-0730-2338 D1/D2）：注册段将各接口的
  list_models 包一层缓存（key 即接口名，不破红线 1）——底层为 CLI 子进程
  调用（最坏 timeout=15s），GUI 线程上每面板每次切换都现拉会成倍卡顿；
  模型目录变化以天计，进程生命周期内一次拉取即可。refresh_models() 为
  唯一失效口（设置中心等显式刷新场景用），无 TTL、无文件监听。
- 依赖方向：本模块 → providers → llm.base；providers 不得反向 import
  本模块（防循环 import）。providers 两模块 import 无副作用，模块级安全。
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass

from llm.base import LanguageModel
from llm.providers.kilocode_acp import KiloCodeAcpLLM, kilocode_available, list_kilocode_models
from llm.providers.kimi_acp import KimiAcpLLM
from llm.providers.kimi_common import kimi_available, list_kimi_models
from llm.providers.opencode_acp import OpenCodeAcpLLM, list_opencode_models, opencode_available
from llm.providers.reasonix_acp import ReasonixAcpLLM, list_reasonix_models, reasonix_available


@dataclass(frozen=True)
class BackendSpec:
    """单个接口实现的注册项（三级链「后台 → 接口 → 模型」中的接口层）。"""

    #: 接口实现名（settings `model_backend` 持久化值）："kimi-acp"
    name: str
    #: 接口显示名："Kimi ACP"
    label: str
    #: 后台名（CLI 产品/厂商；D2：后台不持久化，由本字段推导）："kimi"
    vendor: str
    #: 后台显示名："Kimi"（阶段二 ModelBar 一级按钮用）
    vendor_label: str
    #: 可用性探测（惰性）：本机 agent CLI 是否安装
    available: Callable[[], bool]
    #: 模型别名枚举（惰性；接口级隔离，D6 红线 1）
    list_models: Callable[[], list[str]]
    #: 实例工厂：(model=None, workspace_root=None) → LanguageModel 实例
    factory: Callable[..., LanguageModel]


# ----------------------------------------------------------------------
# 模型列表进程级缓存（计划 2026-0730-2338 D1/D2）：底层 list_*_models 均为
# CLI 子进程调用（timeout=15s 级），GUI 线程「广播 × 标签数」放大后成倍卡顿；
# 进程生命周期内每接口一次拉取，refresh_models() 为唯一失效口。
# 锁覆盖「查 + 拉 + 写」全程：防并发 miss 时重复起子进程（设置中心与
# ModelBar 可能并发拉取）；锁内阻塞仅发生在每接口的首次拉取。
# ----------------------------------------------------------------------
_models_cache: dict[str, tuple[str, ...]] = {}
_models_cache_lock = threading.Lock()


def _cached_list_models(
        name: str, fn: Callable[[], list[str]]) -> Callable[[], list[str]]:
    """把接口级 list_models 包成缓存版：命中返回副本，miss 拉取并回填。"""
    def wrapper() -> list[str]:
        with _models_cache_lock:
            cached = _models_cache.get(name)
            if cached is None:
                cached = tuple(fn())
                _models_cache[name] = cached
        return list(cached)
    return wrapper


def refresh_models(name: str | None = None) -> None:
    """清模型列表缓存（下次 list_models 惰性重新拉取）；name=None 清全部。

    唯一缓存失效口（D2）：模型目录低频变化，进程内不做 TTL/文件监听；
    设置中心「重新检测」等显式刷新场景调本函数。
    """
    with _models_cache_lock:
        if name is None:
            _models_cache.clear()
        else:
            _models_cache.pop(name, None)


# ----------------------------------------------------------------------
# 注册段：新增后台在 llm/providers/ 实现后于此后台追加一项（dict 保持插入序，
# 插入序即 UI 菜单序）。全接口均为 ACP 传输层（kimi CLI `-p` 模式已于
# 2026-07-31 移除，见 work plans/2026-0731-0036）。
# list_models 统一经 _cached_list_models 包装（缓存 key 即接口名，红线 1 不破）。
# ----------------------------------------------------------------------
REGISTRY: dict[str, BackendSpec] = {
    spec.name: spec
    for spec in (
        BackendSpec(
            name="kimi-acp",
            label="Kimi ACP",
            vendor="kimi",
            vendor_label="Kimi",
            available=kimi_available,
            list_models=_cached_list_models("kimi-acp", list_kimi_models),
            factory=KimiAcpLLM,
        ),
        BackendSpec(
            name="reasonix-acp",
            label="Reasonix ACP",
            vendor="reasonix",
            vendor_label="Reasonix",
            available=reasonix_available,
            list_models=_cached_list_models("reasonix-acp", list_reasonix_models),
            factory=ReasonixAcpLLM,
        ),
        BackendSpec(
            name="opencode-acp",
            label="OpenCode ACP",
            vendor="opencode",
            vendor_label="OpenCode",
            available=opencode_available,
            list_models=_cached_list_models("opencode-acp", list_opencode_models),
            factory=OpenCodeAcpLLM,
        ),
        BackendSpec(
            name="kilocode-acp",
            label="Kilo Code ACP",
            vendor="kilocode",
            vendor_label="Kilo Code",
            available=kilocode_available,
            list_models=_cached_list_models("kilocode-acp", list_kilocode_models),
            factory=KiloCodeAcpLLM,
        ),
    )
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


# ----------------------------------------------------------------------
# 派生映射（由 REGISTRY 生成，llm/__init__ re-export 保旧 import 路径不破）
# ----------------------------------------------------------------------
#: 接口实现名 → 显示名（设置菜单项 / ModelBar 下拉 / 切换提示共用单一映射）
BACKEND_LABELS: dict[str, str] = {name: spec.label for name, spec in REGISTRY.items()}

#: 后台名 → 后台显示名（保持注册序；阶段二 ModelBar 一级按钮用）
VENDOR_LABELS: dict[str, str] = {
    spec.vendor: spec.vendor_label for spec in REGISTRY.values()}


__all__ = [
    "BackendSpec",
    "REGISTRY",
    "spec_of",
    "vendor_of",
    "vendor_groups",
    "refresh_models",
    "BACKEND_LABELS",
    "VENDOR_LABELS",
]
