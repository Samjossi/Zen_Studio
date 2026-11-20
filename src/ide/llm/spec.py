"""后台接口注册项与强度解析：中性类型层，不依赖具体厂商。

`BackendSpec` 只描述「接口」这一层（后台 → 接口 → 模型）的静态声明与
惰性可调用；`resolve_efforts` 只做模型级/接口级强度解析链。两者被厂商
`spec.py`、组合根与 UI 共用，因此本模块不得出现厂商名、厂商私有路径、
工具名或设置键，也不 import `ide.llm.registry` 或任何厂商包。

`import` 无副作用：字段里的 `available`/`list_models`/`factory` 等均为
调用方稍后调用的可调用对象，本模块不执行探测或实例化。
"""
from collections.abc import Callable
from dataclasses import dataclass

from ide.llm.base import LanguageModel


@dataclass(frozen=True)
class BackendSpec:
    """单个接口实现的注册项（三级链「后台 → 接口 → 模型」中的接口层）。"""

    #: 接口实现名（settings `model_backend` 持久化值）
    name: str
    #: 接口显示名
    label: str
    #: 后台名（CLI 产品/厂商；D2：后台不持久化，由本字段推导）
    vendor: str
    #: 后台显示名（阶段二 ModelBar 一级按钮用）
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
    #: 经缓存 helper 同款进程级缓存包装，`refresh_models()` 同点失效
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
    #: 本后台读取的厂商设置键（只声明键名，不解释值）：面板按此过滤
    #: 公共设置后经 `apply_vendor_settings` 整体交给 provider。空 = 该后台
    #: 无厂商设置。键名以本厂商 `spec.py` 持有的字面量为准，测试锁定其
    #: 存在于 `DEFAULT_SETTINGS`，通用 GUI 不复制任何键名
    vendor_settings: tuple[str, ...] = ()
    #: 封存标记：True = 接口保留注册与菜单入口但禁止使用——切换时经
    #: dev_note 告知封存口径，发送路径拦停（panel 侧消费）。封存不删
    #: provider 本体：解封即回本注册项撤标记重新编译
    archived: bool = False


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


__all__ = ["BackendSpec", "resolve_efforts"]
