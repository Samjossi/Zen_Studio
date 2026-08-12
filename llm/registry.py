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
from llm.providers.dream_acp import DreamAcpLLM, dream_available, list_dream_models
from llm.providers.kilocode_acp import (
    KiloCodeAcpLLM,
    kilocode_available,
    list_kilocode_efforts,
    list_kilocode_models,
)
from llm.providers.kimi_acp import KimiAcpLLM
from llm.providers.kimi_common import (
    efforts_from_catalog,
    kimi_available,
    load_kimi_provider_catalog,
    models_from_catalog,
)
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
    #: 图片附件能力位（0340 方案 B 计划 T5）：接口级——ACP image ContentBlock
    #: 是否有效送达模型。真值来源唯一：T0 spike 实测
    #: （.temp/spike_image_results.json，2026-08-01）；
    #: False 的后端聊天输入区自动退化方案 D 落盘 @路径 行为（D4）
    supports_images: bool = False
    #: 推理强度选项（2026-0806 一期静态声明；0455 动态化计划 D2 改语义为
    #: 「接口级兜底值」）：list_efforts 返回空 dict（无模型级数据/解析
    #: 失败）时 UI 回退本字段；空 tuple = 该接口不支持/未实测强度轴，
    #: ModelBar 第四级禁用标注；非空即第四级菜单值域，协议值原样透传
    #: （不透明字符串红线同模型别名，不解析不校验；configId 归各 provider
    #: 的 set_effort 自持）
    efforts: tuple[str, ...] = ()
    #: 未定制时的 UI 勾选默认项（须为 efforts 成员；None = efforts 首项）。
    #: 仅 UI 呈现勾选——用户未显式选择时不下发 set_config_option，
    #: agent 默认强度生效；填值应与 agent 默认一致（防显示与实况背离）。
    #: 0455 计划 D2：模型级默认档缺失（如 kilo 目录无默认字段）时的兜底
    default_effort: str | None = None
    #: 模型级强度档位枚举（0455 动态化计划 D2，惰性）：模型别名 →
    #: (档位列表, 默认档|None)。空 dict = 无模型级数据（UI 回退接口级
    #: efforts 兜底）；非空 dict 中查无该模型 = 该模型无强度轴（菜单禁用，
    #: 不做静态兜底，D1）。None = 该接口无动态数据源（纯静态兜底）。
    #: 经 _cached_list_efforts 同款进程级缓存包装，refresh_models() 同点失效
    list_efforts: Callable[[], dict[str, tuple[list[str], str | None]]] | None = None


# ----------------------------------------------------------------------
# 模型列表进程级缓存（计划 2026-0730-2338 D1/D2）：底层 list_*_models 均为
# CLI 子进程调用（timeout=15s 级），GUI 线程「广播 × 标签数」放大后成倍卡顿；
# 进程生命周期内每接口一次拉取，refresh_models() 为唯一失效口。
# 锁覆盖「查 + 拉 + 写」全程：防并发 miss 时重复起子进程（设置中心与
# ModelBar 可能并发拉取）；锁内阻塞仅发生在每接口的首次拉取。
# ----------------------------------------------------------------------
_models_cache: dict[str, tuple[str, ...]] = {}
_models_cache_lock = threading.Lock()

#: 模型级强度档位进程级缓存（0455 动态化计划 D2/R3）：与 _models_cache
#: 同生命周期、同锁、同失效口（refresh_models）
_efforts_cache: dict[str, dict[str, tuple[list[str], str | None]]] = {}

#: 共享原始目录载荷缓存（0455 计划 T1：kimi 的 list_models 与 list_efforts
#: 同源 `provider list --json`，缓存原始载荷一次、两路派生，防双倍子进程）
_raw_cache: dict[str, object] = {}


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


def _cached_list_efforts(
        name: str,
        fn: Callable[[], dict[str, tuple[list[str], str | None]]],
) -> Callable[[], dict[str, tuple[list[str], str | None]]]:
    """把接口级 list_efforts 包成缓存版（0455 计划 D2，list_models 同款）。"""
    def wrapper() -> dict[str, tuple[list[str], str | None]]:
        with _models_cache_lock:
            cached = _efforts_cache.get(name)
            if cached is None:
                cached = fn()
                _efforts_cache[name] = cached
        return dict(cached)
    return wrapper


def _cached_raw(name: str, fn: Callable[[], object]) -> Callable[[], object]:
    """把原始目录载荷拉取包成缓存版（0455 计划 T1 缓存层合一）。"""
    def wrapper() -> object:
        with _models_cache_lock:
            cached = _raw_cache.get(name)
            if cached is None:
                cached = fn()
                _raw_cache[name] = cached
        return cached
    return wrapper


def refresh_models(name: str | None = None) -> None:
    """清模型列表与强度档位缓存（下次 list_models/list_efforts 惰性重新
    拉取）；name=None 清全部。

    唯一缓存失效口（D2/R3）：模型目录低频变化，进程内不做 TTL/文件监听；
    设置中心「重新检测」等显式刷新场景调本函数。0455 计划：模型列表、
    强度档位、共享原始目录载荷三缓存同点失效。
    """
    with _models_cache_lock:
        if name is None:
            _models_cache.clear()
            _efforts_cache.clear()
            _raw_cache.clear()
        else:
            _models_cache.pop(name, None)
            _efforts_cache.pop(name, None)
            _raw_cache.pop(name, None)


#: kimi 共享目录载荷（0455 计划 T1：注册段 list_models/list_efforts 均从
#: 本缓存派生，一次子进程两路消费）
_kimi_catalog = _cached_raw("kimi-acp", load_kimi_provider_catalog)


def resolve_efforts(
        spec: BackendSpec, model: str | None) -> tuple[tuple[str, ...], str | None]:
    """模型级强度档位解析链（0455 动态化计划 D2/D4，UI 与应用层共用）：

    1. spec.list_efforts 返回**非空** dict → 查模型别名：命中 = 模型级
       (档位, 默认档)；查无 = 该模型无强度轴（空 tuple，不做静态兜底，D1）；
    2. list_efforts 为 None 或返回**空** dict → 接口级兜底
       (spec.efforts, spec.default_effort)；
    3. 再空 = 该接口不支持强度轴（空 tuple）。

    返回 (档位列表, 默认档)；默认档可能为 None（kilo 目录无默认字段时
    UI 再落 spec.default_effort → 首项，见 ModelBar）。
    """
    if spec.list_efforts is not None:
        model_efforts = spec.list_efforts()
        if model_efforts:  # 非空 dict：模型级语义生效
            entry = model_efforts.get(model or "")
            if entry is None:
                return (), None
            efforts, default = entry
            if not efforts:
                return (), None
            return tuple(efforts), default
    return spec.efforts, spec.default_effort


# ----------------------------------------------------------------------
# 注册段：新增后台在 llm/providers/ 实现后于此后台追加一项（dict 保持插入序，
# 插入序即 UI 菜单序）。全接口均为 ACP 传输层（kimi CLI `-p` 模式已于
# 2026-07-31 移除，见 文档/修改记录/2026-0731-0036）。
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
            # 0455 动态化计划 T1：list_models 与 list_efforts 共享同一次
            # `provider list --json` 调用（_kimi_catalog 缓存层合一），
            # 模型别名与强度档位同源同载荷，防双倍子进程
            list_models=lambda: models_from_catalog(_kimi_catalog()),
            factory=KimiAcpLLM,
            # T0 spike：默认模型与 kimi-code/k3 均正确识图；空 text 块被拒
            # （-32603），纯图发送经 build_prompt_blocks 占位文案回退（D5）
            supports_images=True,
            # 接口级兜底值（0455 计划 D2 语义）：模型级目录整体不可得
            # （CLI 失败返回空 dict）时回退；k3-256k 值域 low/high/max、
            # 默认 high（2026-0725-0205 实测）
            efforts=("low", "high", "max"),
            default_effort="high",
            # 模型级档位（0455 计划 G1）：supportEfforts/defaultEffort
            # 服务端目录下发；条目缺字段的模型（kimi-for-coding 等）无
            # 强度轴（D1 不做静态兜底）
            list_efforts=lambda: efforts_from_catalog(_kimi_catalog()),
        ),
        BackendSpec(
            name="reasonix-acp",
            label="Reasonix ACP",
            vendor="reasonix",
            vendor_label="Reasonix",
            available=reasonix_available,
            list_models=_cached_list_models("reasonix-acp", list_reasonix_models),
            factory=ReasonixAcpLLM,
            # T0 spike：协议层接受 image 块但默认模型回报「没有看到任何图片」
            # （疑静默丢弃或非视觉模型）——保守置 False，维持方案 D @路径
            # 退化（D4）；后续换视觉模型实测后可翻案
            supports_images=False,
            # configOptions effort 轴（category=thought_level，configId
            # "effort"）：值域 auto/disabled/high/max、默认 auto，两模型
            # （deepseek-pro/deepseek-v4-pro、deepseek-flash/deepseek-v4-flash）
            # 实测值域相同（0455 计划 T5 spike：.temp/spike_effort_axes.py /
            # spike_reasonix_effort_models.py，2026-08-06）；无模型级动态
            # 数据源（configOptions 需先起会话，R4 后续候选），静态声明
            efforts=("auto", "disabled", "high", "max"),
            default_effort="auto",
        ),
        BackendSpec(
            name="opencode-acp",
            label="OpenCode ACP",
            vendor="opencode",
            vendor_label="OpenCode",
            available=opencode_available,
            list_models=_cached_list_models("opencode-acp", list_opencode_models),
            factory=OpenCodeAcpLLM,
            # T0 spike：image 块确认透传（模型明确回报「当前模型不支持图片
            # 输入」——图已送达，模型级不支持）；接口级置 True（D9），用户
            # 切换视觉模型即生效；空 text 块接受
            supports_images=True,
            # 0455 计划 T5 spike 实测（.temp/spike_effort_axes.py，
            # 2026-08-06）：opencode 原版 configOptions 仅 model + mode
            # 两项，**无 effort 轴**（kilo fork 的 effort 是 kilo 自加）——
            # 维持留空不暴露
        ),
        BackendSpec(
            name="kilocode-acp",
            label="Kilo Code ACP",
            vendor="kilocode",
            vendor_label="Kilo Code",
            available=kilocode_available,
            list_models=_cached_list_models("kilocode-acp", list_kilocode_models),
            factory=KiloCodeAcpLLM,
            # T0 spike：默认模型正确识图（回「红」），空 text 块接受
            supports_images=True,
            # 接口级兜底值（0455 计划 D2 语义）：`models --verbose` 解析
            # 失败/超时时回退——configOptions effort 选项 high/max、默认
            # high（2026-0730-2240 计划 §2.4 实测）
            efforts=("high", "max"),
            default_effort="high",
            # 模型级档位（0455 计划 G2）：`kilo models --verbose` variants
            # keys 全量枚举；解析失败返回空 dict → 回退上方静态声明（D1）
            list_efforts=_cached_list_efforts("kilocode-acp", list_kilocode_efforts),
        ),
        BackendSpec(
            name="dream-acp",
            label="Dream ACP",
            vendor="dream",
            vendor_label="Dream",
            available=dream_available,
            list_models=_cached_list_models("dream-acp", list_dream_models),
            factory=DreamAcpLLM,
            # 示例 agent 无视觉能力（计划 2026-0803-0041 D6）；Dream 真实
            # 视觉模型接入后按 T0 spike 方法学实测翻案置 True，GUI 侧零改动
            supports_images=False,
            # 服务端白名单唯一合法档（2026-0812-1752 计划 / 对接文档 §2/§4：
            # configId="effort" 仅接受 auto，实际档位由 Dream CLI 服务端
            # 配置决定，IDE 不提供选择；单档菜单仅作状态呈现）
            efforts=("auto",),
            default_effort="auto",
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
    "resolve_efforts",
    "BACKEND_LABELS",
    "VENDOR_LABELS",
]
