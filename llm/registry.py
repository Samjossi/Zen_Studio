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
- 依赖方向：本模块 → providers → llm.base；providers 不得反向 import
  本模块（防循环 import）。providers 两模块 import 无副作用，模块级安全。
"""
from collections.abc import Callable
from dataclasses import dataclass

from llm.base import LanguageModel
from llm.providers.kimi_acp import KimiAcpLLM
from llm.providers.kimi_cli import KimiCliLLM, kimi_available, list_kimi_models
from llm.providers.reasonix_acp import ReasonixAcpLLM, list_reasonix_models, reasonix_available


@dataclass(frozen=True)
class BackendSpec:
    """单个接口实现的注册项（三级链「后台 → 接口 → 模型」中的接口层）。"""

    #: 接口实现名（settings `model_backend` 持久化值）："kimi-cli"
    name: str
    #: 接口显示名："Kimi CLI"
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
# 注册段：新增后台在 llm/providers/ 实现后于此后台追加一项（dict 保持插入序，
# 插入序即 UI 菜单序）。kimi 两实现共用同一 CLI 探测与模型枚举（同一二进制）。
# ----------------------------------------------------------------------
REGISTRY: dict[str, BackendSpec] = {
    spec.name: spec
    for spec in (
        BackendSpec(
            name="kimi-cli",
            label="Kimi CLI",
            vendor="kimi",
            vendor_label="Kimi",
            available=kimi_available,
            list_models=list_kimi_models,
            factory=KimiCliLLM,
        ),
        BackendSpec(
            name="kimi-acp",
            label="Kimi ACP",
            vendor="kimi",
            vendor_label="Kimi",
            available=kimi_available,
            list_models=list_kimi_models,
            factory=KimiAcpLLM,
        ),
        BackendSpec(
            name="reasonix-acp",
            label="Reasonix ACP",
            vendor="reasonix",
            vendor_label="Reasonix",
            available=reasonix_available,
            list_models=list_reasonix_models,
            factory=ReasonixAcpLLM,
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
    "BACKEND_LABELS",
    "VENDOR_LABELS",
]
