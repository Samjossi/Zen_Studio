"""DSH 后台注册声明：静态模型/显示名、静态强度、开发中告知与工厂。

dsh 无 CLI 模型枚举命令（计划 2026-1018-0732 §2），模型与显示名是静态表；
值为 session/new 配置项的不透明 JSON 对串（ACP 2026-11-05 实测），与服务端
目录漂移时以 `set_config_option` 拒绝为信号（provider 内降级不崩 UI）。

依赖方向：本模块 → `llm.spec` 与本厂商模块；不得 import
`ide.llm.registry`、GUI 或其他厂商。import 本模块不执行探测或实例化。
"""
from ide.llm.providers.dsh.provider import DshAcpLLM, dsh_available
from ide.llm.spec import BackendSpec

#: 模型静态表（2026-11-05 ACP 实测，见 .temp/dsh_model_catalog_20261105.json）：
#: session/new 的 model 目录仍含旧别名 deepseek-v4-flash；新会话候选仅保留
#: 两个现行 ID。值为不透明 JSON 对串（协议原值，原样透传），与动态目录
#: 漂移时以 session/set_config_option 拒绝为信号（provider 内降级不崩 UI）
_DSH_MODELS: list[str] = [
    '["deepseek-official","deepseek-flash"]',
    '["deepseek-official","deepseek-v4-pro"]',
]

#: 模型显示名（ACP 实测选项 name 原值；纯 UI 呈现，协议/持久化恒用对串）
_DSH_DISPLAY_NAMES: dict[str, str] = {
    '["deepseek-official","deepseek-flash"]': "DeepSeek-V4.1-Flash",
    '["deepseek-official","deepseek-v4-pro"]': "DeepSeek-V4-Pro",
}


def list_dsh_models() -> list[str]:
    """模型别名枚举：静态表（dsh 无 CLI 枚举命令，见模块 docstring）。"""
    return list(_DSH_MODELS)


def list_dsh_display_names() -> dict[str, str]:
    """模型显示名枚举：静态表（别名 → 显示名；查无该别名 UI 回退别名原文）。"""
    return dict(_DSH_DISPLAY_NAMES)


SPEC = BackendSpec(
    name="dsh-acp",
    label="DeepSeek Harness",
    vendor="dsh",
    vendor_label="DSH",
    available=dsh_available,
    # 静态表直返，无需缓存包装
    list_models=list_dsh_models,
    factory=DshAcpLLM,
    # 兜底初值 False（spike 实测 acp profile 默认未挂附件存储）；
    # 运行时真值以 initialize 的 promptCapabilities.image 实测为准
    # （provider.capabilities().image_input，GUI 只消费能力快照）
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
    # 破坏变更（计划 §2 ⚠️）；接入面集中在 dsh/provider.py
    dev_note=(
        "DeepSeek Harness 后台处于 developer preview 阶段，官方明示"
        "可能有兼容性破坏变更。如遇异常，请核对本机 dsh 版本（接入"
        "验证版本 0.2.2-rc.1）与凭据配置（~/.dsh/.credentials.yaml）。"),
)
