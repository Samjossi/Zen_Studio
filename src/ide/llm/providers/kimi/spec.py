"""Kimi 后台注册声明：显示名、可用性、目录派生、能力与设置键。

本模块只做装配：目录载荷经 `llm.cache` 的原始载荷缓存拉取一次，模型别名、
强度档位与显示名三路从同一载荷派生，防多倍 `provider list --json` 子进程。
可用性探测与目录子进程仍是惰性可调用，import 本模块不执行 CLI。

依赖方向：本模块 → `llm.spec`/`llm.cache`/`llm.base` 与本厂商模块；
不得 import `ide.llm.registry`、GUI 或其他厂商。
"""
from typing import cast

from ide.llm.cache import cached_raw
from ide.llm.providers.kimi.catalog import (
    display_names_from_catalog,
    efforts_from_catalog,
    kimi_available,
    load_kimi_provider_catalog,
    models_from_catalog,
)
from ide.llm.providers.kimi.provider import KimiAcpLLM
from ide.llm.spec import BackendSpec

#: 共享目录载荷（0455 计划 T1：注册段 list_models/list_efforts/
#: list_display_names 均从本缓存派生，一次子进程三路消费）
_cached_catalog = cached_raw("kimi-acp", load_kimi_provider_catalog)


def _catalog() -> dict:
    return cast(dict, _cached_catalog())


SPEC = BackendSpec(
    name="kimi-acp",
    label="Kimi ACP",
    vendor="kimi",
    vendor_label="Kimi",
    available=kimi_available,
    # 0455 动态化计划 T1：list_models/list_efforts/list_display_names
    # 共享同一次 `provider list --json` 调用，别名、档位、显示名同源
    # 同载荷，防多倍子进程
    list_models=lambda: models_from_catalog(_catalog()),
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
    list_efforts=lambda: efforts_from_catalog(_catalog()),
    # 显示名与模型/档位同源同载荷（_catalog 第三路派生）；
    # 服务端目录下发或 config.toml overrides 固定（K2.8 Preview
    # 元数据即走 overrides），纯 UI 呈现，别名仍恒为协议/持久化值
    list_display_names=lambda: display_names_from_catalog(_catalog()),
    # 子代理 wire 旁路开关：键名归本厂商 spec，值与默认值仍由
    # `DEFAULT_SETTINGS` 持有（持久化名不可改；GUI 不复制该字面量）
    vendor_settings=("kimi_wire_sidecar",),
)
