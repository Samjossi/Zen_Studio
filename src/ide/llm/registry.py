"""后端注册表：接口实现名 → BackendSpec 单一装配来源（计划 2026-0730-0150 §4-D3）。

设计决策（本项目文档即代码注释）：
- 「后端发现 / 可用性探测 / 模型枚举 / 实例工厂」由散落的硬编码
  （ide/llm/__init__ 常量 + ChatPanel._build_providers）收敛为本模块单点；
  后续新增 CLI 后台只动 ide/llm/providers/ 与本模块注册段一处。
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
  本模块（防循环 import）。providers 各模块 import 无副作用，模块级安全。
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ide.llm.base import LanguageModel
from ide.llm.providers.dsh_acp import (
    DshAcpLLM,
    dsh_available,
    list_dsh_display_names,
    list_dsh_models,
)
from ide.llm.providers.kimi_acp import KimiAcpLLM
from ide.llm.providers.kimi_common import (
    display_names_from_catalog,
    efforts_from_catalog,
    kimi_available,
    load_kimi_provider_catalog,
    models_from_catalog,
)
from ide.llm.providers.local_acp import LocalAcpLLM, list_local_models, local_available


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
    #: 0455 计划 D2：模型级目录未下发默认档时的兜底
    default_effort: str | None = None
    #: 模型级强度档位枚举（0455 动态化计划 D2，惰性）：模型别名 →
    #: (档位列表, 默认档|None)。空 dict = 无模型级数据（UI 回退接口级
    #: efforts 兜底）；非空 dict 中查无该模型 = 该模型无强度轴（菜单禁用，
    #: 不做静态兜底，D1）。None = 该接口无动态数据源（纯静态兜底）。
    #: 经 _cached_list_efforts 同款进程级缓存包装，refresh_models() 同点失效
    list_efforts: Callable[[], dict[str, tuple[list[str], str | None]]] | None = None
    #: 模型显示名枚举（别名 → displayName；惰性）。纯 UI 呈现数据：协议
    #: 透传与持久化恒用别名（红线 2 不破）。None 或空 dict = 该接口无
    #: 显示名数据源，UI 回退别名原文；有数据源但查无该别名同样回退别名
    list_display_names: Callable[[], dict[str, str]] | None = None
    #: 开发中状态如实告知文案（2026-0814-0603 计划 T1）：非空时切到该
    #: 接口、以及持久化恢复后本标签首次发送，对话流各输出一条「系统」
    #: 消息（panel 侧消费；纯本地 UI，不进 ACP 帧）。None = 成熟接口，
    #: 零行为变化
    dev_note: str | None = None
    #: 封存标记：True = 接口保留注册与菜单入口但禁止使用——切换时经
    #: dev_note 告知封存口径，发送路径拦停（panel 侧消费）。封存不删
    #: provider 本体：解封即回本注册项撤标记重新编译
    archived: bool = False


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
#: 本缓存派生；显示名派生同缓存第三路消费，一次子进程三路消费）
_kimi_catalog = _cached_raw("kimi-acp", load_kimi_provider_catalog)


def resolve_efforts(
        spec: BackendSpec, model: str | None) -> tuple[tuple[str, ...], str | None]:
    """模型级强度档位解析链（0455 动态化计划 D2/D4，UI 与应用层共用）：

    1. spec.list_efforts 返回**非空** dict → 查模型别名：命中 = 模型级
       (档位, 默认档)；查无 = 该模型无强度轴（空 tuple，不做静态兜底，D1）；
    2. list_efforts 为 None 或返回**空** dict → 接口级兜底
       (spec.efforts, spec.default_effort)；
    3. 再空 = 该接口不支持强度轴（空 tuple）。

    返回 (档位列表, 默认档)；默认档可能为 None（目录未下发默认字段时
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
# 注册段：新增后台在 ide/llm/providers/ 实现后于此后台追加一项（dict 保持插入序，
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
            # 0455 动态化计划 T1：list_models/list_efforts/list_display_names
            # 共享同一次 `provider list --json` 调用（_kimi_catalog 缓存层
            # 合一），别名、档位、显示名同源同载荷，防多倍子进程
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
            # 服务端目录下发；条目缺字段的模型（kimi-for-coding-highspeed
            # 等）无强度轴（D1 不做静态兜底）；kimi-for-coding 的档位经
            # config.toml overrides 固定（K2.8 Preview 元数据，managed
            # 刷新前的过渡手段，efforts_from_catalog 并入后读取）
            list_efforts=lambda: efforts_from_catalog(_kimi_catalog()),
            # 显示名与模型/档位同源同载荷（_kimi_catalog 第三路派生）；
            # 服务端目录下发或 config.toml overrides 固定（K2.8 Preview
            # 元数据即走 overrides），纯 UI 呈现，别名仍恒为协议/持久化值
            list_display_names=lambda: display_names_from_catalog(_kimi_catalog()),
        ),
        BackendSpec(
            name="dsh-acp",
            label="DeepSeek Harness",
            vendor="dsh",
            vendor_label="DSH",
            available=dsh_available,
            # dsh 无 CLI 枚举命令（计划 2026-1018-0732 §2）：静态表直返
            # 无需缓存包装；值为 session/new 配置项的不透明 JSON 对串
            # （ACP 2026-11-05 实测），与服务端目录漂移时以
            # set_config_option 拒绝为信号（provider 内降级不崩 UI）
            list_models=list_dsh_models,
            factory=DshAcpLLM,
            # 兜底初值 False（spike 实测 acp profile 默认未挂附件存储）；
            # 运行时真值以 initialize 的 promptCapabilities.image 实测为准
            # （provider.supports_images_override，GUI 侧优先消费）
            supports_images=False,
            # 接口级静态档位（ACP 2026-11-05 实测 reasoning_effort 值域
            # off/low/high/max、默认 high；configId 拼写归 provider 的
            # set_effort 自持）；无模型级动态数据源（configOptions 需先起会话）
            efforts=("off", "low", "high", "max"),
            default_effort="high",
            # 显示名静态表（ACP 实测选项 name 原值；纯 UI 呈现，
            # 协议/持久化恒用对串）
            list_display_names=list_dsh_display_names,
            # 如实告知：dsh 处于 developer preview，官方明示会有兼容性
            # 破坏变更（计划 §2 ⚠️）；接入面集中在 dsh_acp.py 单文件
            dev_note=(
                "DeepSeek Harness 后台处于 developer preview 阶段，官方明示"
                "可能有兼容性破坏变更。如遇异常，请核对本机 dsh 版本（接入"
                "验证版本 0.2.2-rc.1）与凭据配置（~/.dsh/.credentials.yaml）。"),
        ),
        BackendSpec(
            name="local-acp",
            label="Local ACP",
            vendor="local",
            vendor_label="Local",
            available=local_available,
            # 枚举走 `local-cli models` 子进程（Local CLI 服务端扫描 model_dir
            # 的 *.gguf 产出），无静态表；兜底空列表纪律同各后台
            list_models=_cached_list_models("local-acp", list_local_models),
            factory=LocalAcpLLM,
            # 当前接入的 GGUF 均为纯文本模型；日后接视觉 GGUF 实测后翻案
            supports_images=False,
            # 本地模型无强度目录，单档 auto 仅作状态呈现
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
# 派生映射（由 REGISTRY 生成，ide/llm/__init__ re-export 保旧 import 路径不破）
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
